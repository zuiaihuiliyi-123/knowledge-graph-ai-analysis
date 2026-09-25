<template>
  <div class="graph-wrap">
    <!-- 顶部居中：视图切换（树图/环图/网图/个性化图谱，参考智慧树） -->
    <div class="layout-switch">
      <button
        v-for="opt in LAYOUT_OPTIONS"
        :key="opt.key"
        type="button"
        class="layout-btn"
        :class="{ active: layoutMode === opt.key }"
        @click="applyLayout(opt.key)"
      >{{ opt.label }}</button>
    </div>

    <div ref="containerRef" class="graph-container"></div>

    <!-- 筛选 / 聚焦控制（右上，轻量悬浮工具条） -->
    <div class="graph-controls">
      <el-popover trigger="click" width="236" placement="bottom-end">
        <template #reference>
          <el-button size="small" :icon="Filter">筛选</el-button>
        </template>
        <div class="filter-panel">
          <div class="filter-group-title">知识点类型</div>
          <el-checkbox-group v-model="nodeFilter">
            <el-checkbox v-for="(t, key) in NODE_TYPES" :key="key" :value="key">{{ t.label }}</el-checkbox>
          </el-checkbox-group>
          <div class="filter-group-title">关系类型</div>
          <el-checkbox-group v-model="edgeFilter">
            <el-checkbox v-for="(label, key) in EDGE_TYPE_LABELS" :key="key" :value="key">{{ label }}</el-checkbox>
          </el-checkbox-group>
          <div class="filter-actions">
            <el-button size="small" text type="primary" @click="resetFilters">重置筛选</el-button>
          </div>
        </div>
      </el-popover>

      <el-button
        size="small"
        :type="onlyPrecedes ? 'primary' : ''"
        :plain="!onlyPrecedes"
        @click="toggleOnlyPrecedes"
      >
        只看前置知识
      </el-button>

      <el-button
        v-if="focusedId"
        size="small"
        :type="isolateOn ? 'primary' : ''"
        :plain="!isolateOn"
        @click="toggleIsolate"
      >
        仅关联节点
      </el-button>
    </div>

    <!-- 图例（左上，可折叠，默认紧凑；展开后展示与真实节点一致的形状） -->
    <div class="graph-legend" :class="{ expanded: !legendCollapsed }">
      <button
        type="button"
        class="legend-header"
        :aria-expanded="!legendCollapsed"
        :aria-label="legendCollapsed ? '展开图例' : '收起图例'"
        @click="toggleLegend"
      >
        <span class="legend-title">图例</span>
        <el-icon class="legend-toggle"><ArrowDown /></el-icon>
      </button>
      <div v-show="!legendCollapsed" class="legend-body">
        <div class="legend-section">知识点类型</div>
        <span v-for="(t, key) in NODE_TYPES" :key="key" class="legend-item">
          <i class="legend-dot" :style="{ background: t.color }"></i>{{ t.label }}
        </span>
        <div class="legend-section">节点状态</div>
        <span class="legend-item"><i class="legend-shape legend-mastered">✓</i>已掌握</span>
        <span class="legend-item"><i class="legend-shape legend-diamond"></i>当前学习</span>
        <span class="legend-item"><i class="legend-shape legend-star">★</i>推荐学习</span>
        <span class="legend-item"><i class="legend-shape legend-ring-dark"></i>路径高亮</span>
        <span class="legend-item"><i class="legend-shape legend-halo-dark"></i>聚焦节点</span>
        <div class="legend-section">关系类型</div>
        <span v-for="(label, key) in EDGE_TYPE_LABELS" :key="key" class="legend-item">
          <i class="legend-line" :style="{ background: EDGE_TYPE_COLORS[key] }"></i>{{ label }}
        </span>
      </div>
    </div>

    <!-- 空状态（无数据 / 加载失败） -->
    <el-empty
      v-if="!loading && empty"
      :description="emptyText"
      :image-size="90"
      class="graph-empty"
    />
    <!-- 空状态（有数据但被筛选/展开条件过滤为空） -->
    <el-empty
      v-else-if="!loading && visibleEmpty"
      description="当前筛选条件下无可显示节点，请调整筛选或展开"
      :image-size="90"
      class="graph-empty"
    />
    <!-- 加载状态 -->
    <div v-if="loading" class="graph-loading">
      <div class="graph-loading-card">
        <el-icon class="is-loading graph-loading-spinner" :size="26"><Loading /></el-icon>
        <span class="graph-loading-text">图谱加载中…</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, computed, onMounted, onBeforeUnmount } from 'vue'
import { Graph } from '@antv/g6'
import { Filter, ArrowDown, Loading } from '@element-plus/icons-vue'
import { api } from '../api'
import {
  NODE_TYPES,
  EDGE_TYPE_LABELS,
  EDGE_TYPE_COLORS,
  nodeColor,
  edgeTypeLabel,
  edgeColor,
} from '../utils/graphStyle'

const props = defineProps({
  courseId: { type: String, default: '' },
  /** 文档 ID（Phase 8：图谱按 course_id + document_id 隔离，必填） */
  documentId: { type: String, default: '' },
  /** 编辑模式：点击节点/边时携带完整数据抛出事件 */
  editable: { type: Boolean, default: false },
  /** 搜索关键词：命中节点高亮，其余变暗 */
  searchText: { type: String, default: '' },
  /** 已掌握知识点 kp_id 列表（学生端绿色描边高亮） */
  masteredKpIds: { type: Array, default: () => [] },
  /** 路径高亮：路径节点名（按顺序），用于高亮推荐路径 */
  highlightPath: { type: Array, default: () => [] },
  /** P6：点击节点时高亮目标 + 一阶邻居（学生端详情联动） */
  focusOnClick: { type: Boolean, default: false },
  /** P6：局部展开模式——默认只显示核心节点 + 一阶关系，逐层展开 */
  progressive: { type: Boolean, default: false },
  /** P7：当前学习知识点 kp_id（图谱中蓝描边 + ● 前缀，展示层唯一状态源） */
  currentKpId: { type: String, default: '' },
  /** P7：推荐学习知识点 kp_id 列表（图谱中金描边 + ★ 前缀） */
  recommendedKpIds: { type: Array, default: () => [] },
})

const emit = defineEmits([
  'node-click', // (nodeData, { predecessors, successors, related, expanded })
  'edge-click', // (edgeData)
  'loaded', // ({ nodeCount, edgeCount }) 首次加载完成
  'stats', // ({ nodeCount, edgeCount, visibleNodeCount, visibleEdgeCount }) 可见集变化
])

const containerRef = ref(null)
const loading = ref(false)
const empty = ref(false)
const emptyText = ref('暂无图谱数据')
let graph = null
let rawNodes = [] // 后端原始节点数据
let rawEdges = [] // 后端原始边数据
let resizeObserver = null
let resizeTimer = null

// 节点状态 / 强调统一使用中性深色（--text-primary），避免与知识点类型色（蓝/红/橙/绿）重合
const STATE_STROKE = '#303133'

// ---------------------------------------------------------------
// UI 外壳状态（与 G6 渲染/数据无关，仅图例折叠展示）
// ---------------------------------------------------------------
const legendCollapsed = ref(true)
function toggleLegend() {
  legendCollapsed.value = !legendCollapsed.value
}

// ---------------------------------------------------------------
// P6 交互状态
// ---------------------------------------------------------------
const nodeFilter = ref(['concept', 'theorem', 'formula', 'method']) // 选中的节点类型
const edgeFilter = ref(['PRECEDES', 'CONTAINS', 'RELATED_TO', 'APPLIES_TO']) // 选中的关系类型
const onlyPrecedes = ref(false) // 只看前置知识快捷开关
const expandedIds = ref([]) // 局部展开：已展开的节点 id
const focusedId = ref(null) // 当前聚焦节点
const isolateOn = ref(false) // 仅查看关联节点

// 图谱查看方式（参考智慧树）：net 网图(力导向) / tree 树图(层级) / ring 环图(同心圆) / personal 个性化(以学习者为中心辐射)
const layoutMode = ref('net')
const LAYOUT_OPTIONS = [
  { key: 'tree', label: '树图' },
  { key: 'ring', label: '环图' },
  { key: 'net', label: '网图' },
  { key: 'personal', label: '个性化图谱' },
]

// 已掌握知识点集合（用于绿色描边高亮）
function masteredIdSet() {
  return new Set((props.masteredKpIds || []).map(String))
}

// 路径节点名集合
function pathNameSet() {
  return new Set((props.highlightPath || []).filter(Boolean).map(String))
}

// 路径高亮条目 → kp_id 序列（条目可为知识点名称，也可为 kp_id，P8 保证始终以 kp_id 对齐）
function highlightToIds() {
  const seq = (props.highlightPath || []).filter(Boolean).map(String)
  const nameToId = new Map(rawNodes.map((n) => [String(n.label), String(n.id)]))
  const idSet = new Set(rawNodes.map((n) => String(n.id)))
  return seq.map((v) => (idSet.has(v) ? v : nameToId.get(v))).filter(Boolean)
}

function pathIdSequence() {
  return highlightToIds()
}

// ---------------------------------------------------------------
// 邻接结构（供聚焦邻居、局部展开、详情联动复用）
// ---------------------------------------------------------------
const neighborMap = computed(() => {
  const map = new Map()
  for (const e of rawEdges) {
    const s = String(e.source)
    const t = String(e.target)
    if (!map.has(s)) map.set(s, new Set())
    if (!map.has(t)) map.set(t, new Set())
    map.get(s).add(t)
    map.get(t).add(s)
  }
  return map
})

// 核心节点 = 无前置（无 PRECEDES 入边）的根节点；无层级结构时退化为度数最高节点
const coreIds = computed(() => {
  const hasIncoming = new Set()
  for (const e of rawEdges) {
    if (e.type === 'PRECEDES') hasIncoming.add(String(e.target))
  }
  let cores = rawNodes.filter((n) => !hasIncoming.has(String(n.id))).map((n) => String(n.id))
  if (cores.length === 0 && rawNodes.length) {
    // 无 PRECEDES 边：退化为按度数排序取前若干（或全部，若节点少）
    const deg = new Map(rawNodes.map((n) => [String(n.id), 0]))
    for (const e of rawEdges) {
      deg.set(String(e.source), (deg.get(String(e.source)) || 0) + 1)
      deg.set(String(e.target), (deg.get(String(e.target)) || 0) + 1)
    }
    cores = rawNodes
      .slice()
      .sort((a, b) => (deg.get(String(b.id)) || 0) - (deg.get(String(a.id)) || 0))
      .slice(0, Math.max(12, Math.min(rawNodes.length, 15)))
      .map((n) => String(n.id))
  }
  return cores
})

const focusedNeighborSet = computed(() => {
  if (!focusedId.value) return new Set()
  return neighborMap.value.get(focusedId.value) || new Set()
})

// 路径高亮强制可见的节点 id（即使局部展开模式下也保证路径节点露出）
const highlightForceIds = computed(() => highlightToIds())

// 可见节点 id（综合：隔离聚焦 > 搜索 > 局部展开 > 全量；再叠加类型筛选 + 路径强制露出）
const visibleNodeIds = computed(() => {
  if (!rawNodes.length) return []
  const typeOk = (n) => nodeFilter.value.includes(n.type)

  // 1. 仅查看关联节点（聚焦 + 隔离）
  if (focusedId.value && isolateOn.value) {
    const keep = new Set([focusedId.value, ...focusedNeighborSet.value])
    return rawNodes.filter((n) => keep.has(String(n.id)) && typeOk(n)).map((n) => String(n.id))
  }
  // 2. 搜索激活：展示全部（搜索可命中任意节点），聚焦由 opacity 承担
  if ((props.searchText || '').trim()) {
    return rawNodes.filter(typeOk).map((n) => String(n.id))
  }
  // 3. 局部展开：核心 + 展开节点的邻居
  if (props.progressive) {
    const vis = new Set(coreIds.value)
    const expanded = new Set(expandedIds.value)
    for (const id of expanded) {
      vis.add(id)
      const nb = neighborMap.value.get(id)
      if (nb) for (const x of nb) vis.add(x)
    }
    for (const id of highlightForceIds.value) vis.add(id)
    return rawNodes.filter((n) => vis.has(String(n.id)) && typeOk(n)).map((n) => String(n.id))
  }
  // 4. 全量
  return rawNodes.filter(typeOk).map((n) => String(n.id))
})

const visibleEdges = computed(() => {
  const nodeSet = new Set(visibleNodeIds.value)
  return rawEdges.filter((e) => {
    if (!edgeFilter.value.includes(e.type)) return false
    if (onlyPrecedes.value && e.type !== 'PRECEDES') return false
    return nodeSet.has(String(e.source)) && nodeSet.has(String(e.target))
  })
})

const visibleEmpty = computed(
  () => !loading.value && rawNodes.length > 0 && visibleNodeIds.value.length === 0
)

// ---------------------------------------------------------------
// 数据加载
// ---------------------------------------------------------------
async function loadGraph() {
  if (!props.courseId || !props.documentId) {
    empty.value = true
    emptyText.value = '请先选择课程和学习资料'
    rawNodes = []
    rawEdges = []
    expandedIds.value = []
    focusedId.value = null
    clearCanvas()
    return
  }
  loading.value = true
  empty.value = false
  try {
    const data = await api.getGraphV1(props.courseId, props.documentId, { limit: 800 })
    rawNodes = data.nodes || []
    rawEdges = data.edges || []
    // 局部展开模式：默认展开核心节点 → 显示核心 + 一阶关系
    expandedIds.value = props.progressive ? coreIds.value.slice() : []
    focusedId.value = null
    await renderGraph()
    emit('loaded', { nodeCount: rawNodes.length, edgeCount: rawEdges.length })
    centerOnPath()
  } catch (e) {
    empty.value = true
    emptyText.value = `图谱加载失败：${e.message}`
    clearCanvas()
  } finally {
    loading.value = false
  }
}

function clearCanvas() {
  if (graph) {
    graph.setData({ nodes: [], edges: [] })
    graph.draw()
  }
}

// ---------------------------------------------------------------
// 渲染
// ---------------------------------------------------------------
// 布局配置：按查看方式生成 G6 layout 选项（均为 G6 5.1.1 内置布局，已在 dist 包实证注册）
function layoutOption(mode, focusNodeId) {
  if (mode === 'tree') {
    // 树图：dagre 有向分层，左→右排布，贴合「知识模块 → 知识点」层级
    return { type: 'dagre', rankdir: 'LR', nodesep: 26, ranksep: 80, align: 'UL' }
  }
  if (mode === 'ring') {
    // 环图：同心圆，度数高的核心知识点靠中心
    return { type: 'concentric', sortBy: 'degree', preventOverlap: true, nodeSize: 42, minNodeSpacing: 18 }
  }
  if (mode === 'personal') {
    // 个性化：以焦点节点为中心辐射；无焦点时回退同心圆
    if (focusNodeId) {
      return { type: 'radial', focusNode: focusNodeId, preventOverlap: true, nodeSize: 34, unitRadius: 110 }
    }
    return { type: 'concentric', sortBy: 'degree', preventOverlap: true, nodeSize: 42, minNodeSpacing: 18 }
  }
  // 网图：力导向（原默认参数）
  return { type: 'force', preventOverlap: true, nodeSize: 56, linkDistance: 150 }
}

async function initGraph() {
  if (graph) return
  graph = new Graph({
    container: containerRef.value,
    autoFit: 'view',
    layout: layoutOption(layoutMode.value, personalFocusId()),
    node: {
      style: {
        size: 34,
        labelPlacement: 'bottom',
        labelFontSize: 12,
        labelFill: '#303133',
        labelMaxWidth: 140,
      },
    },
    edge: {
      style: {
        endArrow: true,
        lineWidth: 1.5,
        labelFontSize: 11,
        labelFill: '#909399',
        labelBackground: true,
        labelBackgroundFill: '#ffffff',
        labelBackgroundOpacity: 0.85,
      },
    },
    behaviors: ['drag-canvas', 'zoom-canvas', 'drag-element', 'click-select'],
  })

  graph.on('node:click', (evt) => {
    const id = evt?.target?.id
    const node = rawNodes.find((n) => String(n.id) === String(id))
    if (!node) return
    if (props.focusOnClick) {
      setFocus(String(id))
      graph.focusElement(String(id), { animation: { duration: 300 } })
    }
    emit('node-click', node, neighborInfo(String(id)))
  })
  graph.on('edge:click', (evt) => {
    if (!props.editable) return
    const id = evt?.target?.id
    const edge = rawEdges.find((e) => String(e.id) === String(id))
    if (edge) emit('edge-click', edge)
  })
  graph.on('canvas:click', () => {
    if (props.editable) emit('edge-click', null) // 点击空白取消选择
    clearFocus()
  })

  await graph.render()
}

// 个性化图谱焦点：优先当前学习知识点，其次首个推荐知识点，兜底取度数最高节点（内联计算）
function personalFocusId() {
  const visible = new Set(visibleNodeIds.value.map(String))
  if (!visible.size) return null
  if (props.currentKpId && visible.has(String(props.currentKpId))) return String(props.currentKpId)
  const rec = (props.recommendedKpIds || []).map(String).find((id) => visible.has(id))
  if (rec) return rec
  const deg = {}
  for (const e of visibleEdges.value) {
    const s = String(e.source)
    const t = String(e.target)
    deg[s] = (deg[s] || 0) + 1
    deg[t] = (deg[t] || 0) + 1
  }
  let best = null
  let bestDeg = -1
  for (const id of visible) {
    const d = deg[id] || 0
    if (d > bestDeg) {
      bestDeg = d
      best = id
    }
  }
  return best
}

// 切换查看方式：换布局并整体重建（失败时回退网图，避免白屏）
async function applyLayout(mode, force = false) {
  if (!force && layoutMode.value === mode) return
  layoutMode.value = mode
  if (!graph) return
  try {
    graph.setLayout(layoutOption(mode, personalFocusId()))
    await renderGraph()
    graph.fitView()
  } catch (e) {
    layoutMode.value = 'net'
    try {
      graph.setLayout(layoutOption('net'))
      await renderGraph()
      graph.fitView()
    } catch (e2) { /* 已回退，忽略 */ }
  }
}

// 组装完整渲染数据（含基础样式 + 聚焦/搜索强调），供 setData 与 update 复用
function buildGraphData() {
  const nodeSet = new Set(visibleNodeIds.value)
  const pathSet = pathNameSet()
  const pathSeq = pathIdSequence()
  const pathEdgeSet = new Set()
  for (let i = 0; i < pathSeq.length - 1; i++) {
    pathEdgeSet.add(`${pathSeq[i]}-${pathSeq[i + 1]}`)
  }
  const masteredSet = masteredIdSet()
  const kw = (props.searchText || '').trim().toLowerCase()
  const focusId = focusedId.value
  const fNbr = focusedNeighborSet.value
  const curId = props.currentKpId ? String(props.currentKpId) : ''
  const recSet = new Set((props.recommendedKpIds || []).map(String))

  const gNodes = rawNodes
    .filter((n) => nodeSet.has(String(n.id)))
    .map((n) => {
      const id = String(n.id)
      const label = String(n.label ?? n.id)
      const isMastered = masteredSet.has(id)
      const inPath = pathSet.has(label) || pathSet.has(id)
      // 已掌握优先：已掌握节点不再显示为「当前学习」或「推荐学习」
      const isCurrent = !inPath && !isMastered && !!curId && id === curId
      const isRecommended = !inPath && !isCurrent && !isMastered && recSet.has(id)

      // P7：节点状态不靠颜色表达（避免与类型色重合），改用「形状 + 字形前缀 + 徽标 + 中性描边」；
      // 填充始终为类型色，保持不变。
      let labelText = label.slice(0, 30)
      if (isCurrent) labelText = '● ' + labelText
      else if (isRecommended) labelText = '★ ' + labelText

      // 完整样式：显式给出所有可变键，避免 updateNodeData 浅合并残留旧状态
      const style = {
        fill: nodeColor(n.type),
        labelText,
        stroke: 'transparent',
        lineWidth: 1,
        halo: false,
        haloStroke: STATE_STROKE,
        haloLineWidth: 3,
        badges: [],
        opacity: 1,
        // 柔和投影提升层次感（浅色画布上轻微浮起）
        shadowColor: 'rgba(0, 0, 0, 0.14)',
        shadowBlur: 6,
        shadowOffsetY: 2,
      }
      let shape = 'circle'

      // 学习状态：已掌握（✓ 徽标）优先于当前（菱形）/ 推荐（星形）——标记掌握后不再显示为推荐/当前
      if (isMastered) {
        // 已掌握：右上角 ✓ 徽标（小型状态图标，非节点着色）
        style.badges = [
          {
            text: '✓',
            placement: 'right-top',
            fill: '#ffffff',
            background: true,
            backgroundFill: '#67c23a',
            backgroundRadius: '50%',
          },
        ]
      } else if (isCurrent) {
        // 当前学习：菱形形状（形状为主信号）+ 中性描边
        shape = 'diamond'
        style.size = 40
        style.stroke = STATE_STROKE
        style.lineWidth = 2
      } else if (isRecommended) {
        // 推荐学习：星形形状（形状为主信号）+ 中性描边
        shape = 'star'
        style.size = 40
        style.stroke = STATE_STROKE
        style.lineWidth = 1.5
      }

      // 路径高亮：中性深色粗描边 + 深色光晕（可与已掌握/当前/推荐叠加，不覆盖类型填充色）
      if (inPath) {
        style.stroke = STATE_STROKE
        style.lineWidth = 3
        style.halo = true
        style.haloLineWidth = 4
      }

      // 强调优先级：聚焦 > 搜索 > 无（聚焦光晕改用中性深色，与状态统一）
      if (focusId) {
        if (id === focusId) {
          style.opacity = 1
          style.halo = true
          style.haloLineWidth = 4
        } else if (fNbr.has(id)) {
          style.opacity = 0.9
        } else {
          style.opacity = 0.15
        }
      } else if (kw) {
        const hit =
          label.toLowerCase().includes(kw) ||
          String(n.description ?? '').toLowerCase().includes(kw)
        style.opacity = hit ? 1 : 0.12
        if (hit) {
          style.halo = true
          style.haloStroke = '#f56c6c'
          style.haloLineWidth = 3
        }
      }

      // 树图：圆角矩形卡片（类型色填充 + 白字右置）；当前/推荐保持卡片形态，用描边而非菱形/星形表达
      if (layoutMode.value === 'tree') {
        style.size = [Math.max(92, label.length * 14 + 28), 40]
        style.radius = 20
        style.labelPlacement = 'right'
        style.labelFill = '#ffffff'
        style.labelFontSize = 12
        style.labelFontWeight = 600
        if (isCurrent || isRecommended) {
          style.stroke = STATE_STROKE
          style.lineWidth = 2
        }
      }
      return { id, type: layoutMode.value === 'tree' ? 'rect' : shape, style, data: n }
    })

  const isTree = layoutMode.value === 'tree'
  const nodeFill = {}
  for (const gn of gNodes) nodeFill[gn.id] = gn.style.fill

  const edgeTypeName = isTree ? 'cubic-horizontal' : 'quadratic'
  // 树图只保留骨架边（包含/前置）， RELATED_TO/APPLIES_TO 等弱关系在树状视图隐藏，避免交叉杂乱
  const edgesInput = isTree
    ? visibleEdges.value.filter((e) => e.type === 'CONTAINS' || e.type === 'PRECEDES')
    : visibleEdges.value
  // 平行边（同一对节点之间的多条关系）分配曲线偏移，避免完全重叠导致连线与关系标签看不清
  const pairTotal = {}
  for (const pe of edgesInput) {
    const pa = String(pe.source)
    const pb = String(pe.target)
    const pkey = pa < pb ? `${pa}|${pb}` : `${pb}|${pa}`
    pairTotal[pkey] = (pairTotal[pkey] || 0) + 1
  }
  const pairSeen = {}
  const gEdges = edgesInput.map((e) => {
    const src = String(e.source)
    const tgt = String(e.target)
    const inPath = pathEdgeSet.has(`${src}-${tgt}`)
    const pairKey = src < tgt ? `${src}|${tgt}` : `${tgt}|${src}`
    const pIdx = pairSeen[pairKey] || 0
    pairSeen[pairKey] = pIdx + 1
    const pTotal = pairTotal[pairKey] || 1
    // 聚焦相关边高亮、其余压暗；无论是否聚焦都显式写入 opacity，
    // 避免 updateEdgeData 浅合并残留旧的压暗值（否则取消聚焦后关系仍显示灰暗）
    const incident =
      !focusId || src === focusId || tgt === focusId || (fNbr.has(src) && fNbr.has(tgt))
    const curveOffset = pTotal > 1 ? (pIdx - (pTotal - 1) / 2) * 36 : 0
    const style = isTree
      ? {
          // 树图：连线取子节点色，无文字标签，直角水平贝塞尔
          labelText: '',
          stroke: inPath ? '#e6a23c' : nodeFill[tgt] || edgeColor(e.type),
          lineWidth: inPath ? 3 : 1.5,
          endArrow: true,
          opacity: incident ? 1 : 0.08,
        }
      : {
          labelText: edgeTypeLabel(e.type, e.label),
          stroke: inPath ? '#e6a23c' : edgeColor(e.type),
          lineWidth: inPath ? 3 : 1.5,
          curveOffset,
          opacity: incident ? 1 : 0.08,
        }
    return {
      id: String(e.id || `${src}-${tgt}-${e.type}`),
      source: src,
      target: tgt,
      type: edgeTypeName,
      style,
      data: e,
    }
  })

  return { nodes: gNodes, edges: gEdges }
}

// 个性化视图下，当前学习知识点切换时以其为新焦点重建
watch(
  () => props.currentKpId,
  () => {
    if (layoutMode.value === 'personal' && graph) applyLayout('personal', true)
  }
)

// 结构变化（节点/边集合变化）：重新布局
async function renderGraph() {
  await initGraph()
  if (!graph) return
  graph.setData(buildGraphData())
  await graph.render()
  emitStats()
}

// 仅强调变化（opacity/halo/描边等，节点集不变）：不重新布局
async function rehighlight() {
  if (!graph) return
  const d = buildGraphData()
  await graph.updateNodeData(d.nodes.map((n) => ({ id: n.id, type: n.type, style: n.style })))
  await graph.updateEdgeData(d.edges.map((e) => ({ id: e.id, style: e.style })))
  await graph.draw()
}

function emitStats() {
  emit('stats', {
    nodeCount: rawNodes.length,
    edgeCount: rawEdges.length,
    visibleNodeCount: visibleNodeIds.value.length,
    visibleEdgeCount: visibleEdges.value.length,
  })
}

// ---------------------------------------------------------------
// 交互方法
// ---------------------------------------------------------------
function resetFilters() {
  nodeFilter.value = ['concept', 'theorem', 'formula', 'method']
  edgeFilter.value = ['PRECEDES', 'CONTAINS', 'RELATED_TO', 'APPLIES_TO']
  onlyPrecedes.value = false
}

function toggleOnlyPrecedes() {
  onlyPrecedes.value = !onlyPrecedes.value
}

function toggleIsolate() {
  isolateOn.value = !isolateOn.value
}

function isExpanded(id) {
  return expandedIds.value.includes(String(id))
}

function toggleExpand(id) {
  const sid = String(id)
  const arr = expandedIds.value.slice()
  const i = arr.indexOf(sid)
  if (i >= 0) arr.splice(i, 1)
  else arr.push(sid)
  expandedIds.value = arr
  renderGraph() // 展开/收起改变可见节点集，需重布局
  return arr.includes(sid)
}

// 节点的前置 / 后继 / 相关邻居（供详情抽屉「相关知识」与展开状态）
function neighborInfo(id) {
  const sid = String(id)
  const nodeById = (nid) => rawNodes.find((n) => String(n.id) === nid) || null
  // 用 Set 汇总邻居：同一知识点之间可能存在多条不同类型的关系（如 PRECEDES + RELATED_TO），
  // 若不去重会出现重复项，且同一节点会同时落入「前置/后继」与「相关」，导致关系显示异常。
  const predecessorIds = new Set()
  const successorIds = new Set()
  const relatedIds = new Set()
  for (const e of rawEdges) {
    const s = String(e.source)
    const t = String(e.target)
    if (e.type === 'PRECEDES') {
      if (t === sid) predecessorIds.add(s)
      else if (s === sid) successorIds.add(t)
    } else {
      if (s === sid) relatedIds.add(t)
      else if (t === sid) relatedIds.add(s)
    }
  }
  // 前置/后继优先：已作为前置或后继出现的节点，不再计入「相关知识」，避免重复与归类冲突
  for (const x of predecessorIds) relatedIds.delete(x)
  for (const x of successorIds) relatedIds.delete(x)
  // 过滤自环
  predecessorIds.delete(sid)
  successorIds.delete(sid)
  relatedIds.delete(sid)
  const map = (set) => [...set].map(nodeById).filter(Boolean)
  return {
    predecessors: map(predecessorIds),
    successors: map(successorIds),
    related: map(relatedIds),
    expanded: isExpanded(sid),
  }
}

// 设置聚焦节点：隔离模式依赖 focusedId 计算可见集需重布局；否则仅强调（不重布局）
function setFocus(id) {
  focusedId.value = String(id)
  return isolateOn.value ? renderGraph() : rehighlight()
}

// 聚焦节点（外部调用：教师列表定位 / 问答跳转）：目标居中 + 邻居保留 + 其余变暗
async function focusNode(id) {
  if (!graph) return
  await setFocus(id)
  await graph.focusElement(String(id), { animation: { duration: 300 } })
}

// 选中并聚焦节点，同时抛出 node-click 供父组件联动详情
function selectNode(id) {
  const node = rawNodes.find((n) => String(n.id) === String(id))
  if (!node) return
  setFocus(String(id))
  graph.focusElement(String(id), { animation: { duration: 300 } })
  emit('node-click', node, neighborInfo(String(id)))
}

function fitView() {
  if (graph) graph.fitView()
}

function clearFocus() {
  if (!focusedId.value) return
  focusedId.value = null
  if (isolateOn.value) renderGraph()
  else rehighlight()
}

// ---------------------------------------------------------------
// 对外方法
// ---------------------------------------------------------------
defineExpose({
  refresh: loadGraph,
  fitView,
  focusNode,
  selectNode,
  clearFocus,
  toggleExpand,
  isExpanded,
})

// ---------------------------------------------------------------
// 生命周期
// ---------------------------------------------------------------
onMounted(() => {
  resizeObserver = new ResizeObserver(() => {
    // 容器尺寸变化（tab 从隐藏变可见、窗口缩放等）时，同步画布 backing store 并重新布局。
    // 关键：图谱可能在编辑 Tab 尚处于 display:none 时就被 watch(courseId/documentId) 触发初始化，
    // 此时容器 clientWidth/Height 为 0，G6 会退化为 100×100 画布，表现为「左上角一小块」且无法缩放/平移。
    // 仅 fitView 不更新 backing store，必须 resize() + render() 重新布局。
    if (!graph) return
    clearTimeout(resizeTimer)
    resizeTimer = setTimeout(() => {
      try {
        graph.resize()
        graph.render()
      } catch {
        /* 初始化前触发则忽略 */
      }
    }, 150)
  })
  resizeObserver.observe(containerRef.value)
  if (props.courseId && props.documentId) loadGraph()
})

onBeforeUnmount(() => {
  clearTimeout(resizeTimer)
  resizeObserver?.disconnect()
  if (graph) {
    graph.destroy()
    graph = null
  }
})

watch(
  () => [props.courseId, props.documentId],
  () => loadGraph()
)

watch(
  () => props.searchText,
  (nv, ov) => {
    const wasEmpty = !(ov || '').trim()
    const isEmpty = !(nv || '').trim()
    // 跨空值边界会改变可见集（搜索时展示全部），需重新布局；否则仅强调变化
    if (wasEmpty !== isEmpty) renderGraph()
    else rehighlight()
    centerOnSearch()
  }
)

watch(
  () => props.masteredKpIds,
  () => rehighlight()
)

// 当前学习 / 推荐学习状态变化：仅改变形状/徽标/描边强调（不重布局）
watch(
  () => props.currentKpId,
  () => rehighlight()
)

watch(
  () => props.recommendedKpIds,
  () => rehighlight()
)

watch(
  () => props.highlightPath,
  () => {
    if (!rawNodes.length) return
    // 路径高亮可能改变可见集（强制露出路径节点），统一重布局
    renderGraph()
    centerOnPath()
  }
)

// 单节点高亮 → 居中；多节点路径 → 适配全视图
function centerOnPath() {
  if (!graph || !rawNodes.length) return
  const seq = pathIdSequence()
  if (seq.length === 1) {
    graph.focusElement(seq[0], { animation: { duration: 300 } })
  } else if (seq.length > 1) {
    graph.fitView()
  }
}

// 筛选 / 隔离聚焦改变可见集时，统一重布局（状态变更后由 watcher 触发渲染）
// 注意：expandedIds 由 loadGraph 初始化 + toggleExpand 手动触发渲染，不在此处监听（避免初始加载双重渲染）
watch([nodeFilter, edgeFilter, onlyPrecedes, isolateOn], () => renderGraph(), { deep: true })

// 搜索命中唯一节点时居中定位
function centerOnSearch() {
  if (!graph || !rawNodes.length) return
  const kw = (props.searchText || '').trim().toLowerCase()
  if (!kw) return
  const hits = rawNodes.filter((n) => {
    const label = String(n.label ?? n.id).toLowerCase()
    return label.includes(kw) || String(n.description ?? '').toLowerCase().includes(kw)
  })
  if (hits.length === 1) {
    graph.focusElement(String(hits[0].id), { animation: { duration: 300 } })
  } else if (hits.length > 1) {
    graph.fitView()
  }
}
</script>

<style scoped>
.graph-wrap {
  position: relative;
  width: 100%;
  height: 100%;
}
.graph-container {
  position: absolute;
  inset: 0;
}

/* 顶部居中：视图切换条（参考智慧树 pill 分段控件） */
.layout-switch {
  position: absolute;
  top: var(--space-3);
  left: 50%;
  transform: translateX(-50%);
  z-index: 11;
  display: flex;
  gap: 2px;
  padding: 3px;
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid var(--color-border);
  border-radius: 999px;
  box-shadow: var(--shadow-card);
}
.layout-btn {
  border: none;
  background: transparent;
  padding: 6px 14px;
  font-size: 13px;
  color: var(--color-text-secondary, #606266);
  border-radius: 999px;
  cursor: pointer;
  white-space: nowrap;
  transition: background-color 0.2s, color 0.2s;
}
.layout-btn:hover {
  color: var(--el-color-primary, #409eff);
}
.layout-btn.active {
  background: var(--el-color-primary, #409eff);
  color: #fff;
}

/* 筛选 / 聚焦控制（右上，轻量悬浮工具条：白底半透明 + 轻描边 + 小圆角 + 微弱阴影）
   竖排而非横排：编辑模式下图谱只占中间一栏（sm=13），横排时整条会向左伸出约 300px，
   压到顶部居中的视图切换条（.layout-switch，z-index 更高，表现为被盖住）。
   竖排后宽度收敛到最宽按钮的宽度（约 100px），两者互不干涉。
   顺序即 DOM 顺序：筛选在上、只看前置知识在下。 */
.graph-controls {
  position: absolute;
  top: var(--space-3);
  right: var(--space-3);
  z-index: 10;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: var(--space-1);
  padding: var(--space-1);
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow-card);
}
/* 列内按钮等宽：stretch 让每个按钮撑满列宽（列宽 = 最宽按钮），
   width/margin 覆盖 Element 的默认值——相邻按钮的 margin-left 在竖排下会顶出列外 */
.graph-controls :deep(.el-button) {
  width: 100%;
  margin: 0;
  justify-content: center;
  white-space: nowrap;
}
.filter-panel {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.filter-group-title {
  font-size: 12px;
  color: var(--color-text-secondary);
  margin-top: 6px;
}
.filter-group-title:first-child {
  margin-top: 0;
}
.filter-actions {
  margin-top: 8px;
  text-align: right;
}

/* 图例（左上，可折叠，默认紧凑） */
.graph-legend {
  position: absolute;
  top: var(--space-3);
  left: var(--space-3);
  z-index: 10;
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow-card);
  display: flex;
  flex-direction: column;
  font-size: 12px;
  color: var(--color-text-regular);
  max-width: 180px;
  overflow: hidden;
}
.legend-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border: none;
  background: transparent;
  cursor: pointer;
  font: inherit;
  color: inherit;
  text-align: left;
}
.legend-header:hover {
  background: var(--color-bg-hover);
}
.legend-title {
  font-weight: 600;
  color: var(--color-text-primary);
  font-size: 12px;
}
.legend-toggle {
  font-size: 12px;
  color: var(--color-text-secondary);
  transition: transform 0.2s ease;
}
.graph-legend.expanded .legend-toggle {
  transform: rotate(180deg);
}
.legend-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 0 var(--space-3) var(--space-3);
}
.legend-section {
  margin-top: var(--space-2);
  font-size: 11px;
  color: var(--color-text-secondary);
}
.legend-section:first-child {
  margin-top: 0;
}
.legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
}
.legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  display: inline-block;
  flex-shrink: 0;
}
.legend-shape {
  width: 12px;
  height: 12px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-size: 10px;
  line-height: 1;
  color: var(--color-text-primary);
}
/* 已掌握：绿底白勾徽标（小型状态图标，非节点着色） */
.legend-mastered {
  background: var(--color-success);
  color: #fff;
  border-radius: 50%;
  font-size: 9px;
}
/* 当前学习：菱形（旋转正方形，形状为主信号） */
.legend-diamond {
  width: 8px;
  height: 8px;
  border: 2px solid var(--color-text-primary);
  background: #fff;
  transform: rotate(45deg);
}
/* 推荐学习：星形（形状为主信号） */
.legend-star {
  background: transparent;
  color: var(--color-text-primary);
  font-size: 13px;
}
/* 路径高亮：中性深色粗描边圆环 */
.legend-ring-dark {
  width: 8px;
  height: 8px;
  border: 3px solid var(--color-text-primary);
  border-radius: 50%;
  background: transparent;
}
/* 聚焦：中性深色光晕圆点 */
.legend-halo-dark {
  background: var(--color-text-primary);
  border-radius: 50%;
  box-shadow: 0 0 0 3px rgba(48, 49, 51, 0.22);
}
.legend-line {
  width: 16px;
  height: 3px;
  border-radius: 2px;
  display: inline-block;
  flex-shrink: 0;
}

.graph-empty,
.graph-loading {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 5;
  pointer-events: none;
}

/* 空状态：居中浮层卡片（显示条件 v-if 不变，仅视觉） */
.graph-empty :deep(.el-empty) {
  padding: var(--space-6) var(--space-8);
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-hover);
}
.graph-empty :deep(.el-empty__description) {
  color: var(--color-text-secondary);
}

/* 加载状态：居中浮层卡片（转圈 + 文案，显示条件 v-if 不变） */
.graph-loading-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-5) var(--space-6);
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-hover);
}
.graph-loading-spinner {
  color: var(--color-primary);
}
.graph-loading-text {
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}
</style>
