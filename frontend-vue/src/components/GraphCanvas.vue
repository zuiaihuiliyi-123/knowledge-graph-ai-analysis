<template>
  <div class="graph-wrap">
    <div ref="containerRef" class="graph-container"></div>

    <!-- 筛选 / 聚焦控制（左上） -->
    <div class="graph-controls">
      <el-popover trigger="click" width="236" placement="bottom-start">
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

    <!-- 图例（右上，统一说明节点类型/状态/关系类型） -->
    <div class="graph-legend">
      <div class="legend-title">图例</div>
      <div class="legend-section">知识点类型</div>
      <span v-for="(t, key) in NODE_TYPES" :key="key" class="legend-item">
        <i class="legend-dot" :style="{ background: t.color }"></i>{{ t.label }}
      </span>
      <div class="legend-section">节点状态</div>
      <span class="legend-item"><i class="legend-dot legend-stroke-green"></i>已掌握</span>
      <span class="legend-item"><i class="legend-dot legend-fill-gold"></i>路径高亮</span>
      <span class="legend-item"><i class="legend-dot legend-focus"></i>聚焦节点</span>
      <div class="legend-section">关系类型</div>
      <span v-for="(label, key) in EDGE_TYPE_LABELS" :key="key" class="legend-item">
        <i class="legend-line" :style="{ background: EDGE_TYPE_COLORS[key] }"></i>{{ label }}
      </span>
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
    <div v-if="loading" class="graph-loading" v-loading="true"></div>
  </div>
</template>

<script setup>
import { ref, watch, computed, onMounted, onBeforeUnmount } from 'vue'
import { Graph } from '@antv/g6'
import { Filter } from '@element-plus/icons-vue'
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

// ---------------------------------------------------------------
// P6 交互状态
// ---------------------------------------------------------------
const nodeFilter = ref(['concept', 'theorem', 'formula', 'method']) // 选中的节点类型
const edgeFilter = ref(['PRECEDES', 'CONTAINS', 'RELATED_TO', 'APPLIES_TO']) // 选中的关系类型
const onlyPrecedes = ref(false) // 只看前置知识快捷开关
const expandedIds = ref([]) // 局部展开：已展开的节点 id
const focusedId = ref(null) // 当前聚焦节点
const isolateOn = ref(false) // 仅查看关联节点

// 已掌握知识点集合（用于绿色描边高亮）
function masteredIdSet() {
  return new Set((props.masteredKpIds || []).map(String))
}

// 路径节点名集合
function pathNameSet() {
  return new Set((props.highlightPath || []).filter(Boolean).map(String))
}

// 路径节点名序列 → kp_id 序列
function pathIdSequence() {
  const seq = (props.highlightPath || []).filter(Boolean).map(String)
  const nameToId = new Map(rawNodes.map((n) => [String(n.label), String(n.id)]))
  return seq.map((name) => nameToId.get(name)).filter(Boolean)
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
const highlightForceIds = computed(() => {
  const nameToId = new Map(rawNodes.map((n) => [String(n.label), String(n.id)]))
  return (props.highlightPath || []).map((name) => nameToId.get(String(name))).filter(Boolean)
})

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
  if (!props.courseId) {
    empty.value = true
    emptyText.value = '请先选择课程'
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
    const data = await api.getGraphV1(props.courseId, { limit: 800 })
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
async function initGraph() {
  if (graph) return
  graph = new Graph({
    container: containerRef.value,
    autoFit: 'view',
    layout: {
      type: 'force',
      preventOverlap: true,
      nodeSize: 56,
      linkDistance: 150,
    },
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

  const gNodes = rawNodes
    .filter((n) => nodeSet.has(String(n.id)))
    .map((n) => {
      const id = String(n.id)
      const label = String(n.label ?? n.id)
      const isMastered = masteredSet.has(id)
      const inPath = pathSet.has(label) || pathSet.has(id)
      const style = { fill: inPath ? '#e6a23c' : nodeColor(n.type), labelText: label.slice(0, 30) }

      if (inPath) {
        style.stroke = '#b88230'
        style.lineWidth = 3
        style.halo = true
        style.haloStroke = '#e6a23c'
        style.haloLineWidth = 4
      } else if (isMastered) {
        style.stroke = '#67c23a'
        style.lineWidth = 3
      }

      // 强调优先级：聚焦 > 搜索 > 无
      if (focusId) {
        if (id === focusId) {
          style.opacity = 1
          style.halo = true
          style.haloStroke = '#409eff'
          style.haloLineWidth = 4
        } else if (fNbr.has(id)) {
          style.opacity = 0.9
          style.halo = false
        } else {
          style.opacity = 0.15
          style.halo = false
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
        } else {
          style.halo = false
        }
      } else {
        style.opacity = 1
        if (!style.halo) style.halo = false
      }

      return { id, style, data: n }
    })

  const gEdges = visibleEdges.value.map((e) => {
    const src = String(e.source)
    const tgt = String(e.target)
    const inPath = pathEdgeSet.has(`${src}-${tgt}`)
    const style = {
      labelText: edgeTypeLabel(e.type, e.label),
      stroke: inPath ? '#e6a23c' : edgeColor(e.type),
      lineWidth: inPath ? 3 : 1.5,
    }
    if (focusId) {
      const incident =
        src === focusId || tgt === focusId || (fNbr.has(src) && fNbr.has(tgt))
      style.opacity = incident ? 1 : 0.08
    }
    return {
      id: String(e.id || `${src}-${tgt}`),
      source: src,
      target: tgt,
      style,
      data: e,
    }
  })

  return { nodes: gNodes, edges: gEdges }
}

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
  await graph.updateNodeData(d.nodes.map((n) => ({ id: n.id, style: n.style })))
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
  const predecessors = []
  const successors = []
  const related = []
  for (const e of rawEdges) {
    const s = String(e.source)
    const t = String(e.target)
    if (e.type === 'PRECEDES') {
      if (t === sid) predecessors.push(nodeById(s))
      else if (s === sid) successors.push(nodeById(t))
    } else {
      if (s === sid) related.push(nodeById(t))
      else if (t === sid) related.push(nodeById(s))
    }
  }
  return {
    predecessors: predecessors.filter(Boolean),
    successors: successors.filter(Boolean),
    related: related.filter(Boolean),
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
    if (graph) {
      try {
        graph.fitView()
      } catch {
        /* 初始化前触发则忽略 */
      }
    }
  })
  resizeObserver.observe(containerRef.value)
  if (props.courseId) loadGraph()
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  if (graph) {
    graph.destroy()
    graph = null
  }
})

watch(
  () => props.courseId,
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

/* 筛选 / 聚焦控制（左上） */
.graph-controls {
  position: absolute;
  top: 12px;
  left: 12px;
  z-index: 10;
  display: flex;
  align-items: center;
  gap: 8px;
}
.filter-panel {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.filter-group-title {
  font-size: 12px;
  color: #909399;
  margin-top: 6px;
}
.filter-group-title:first-child {
  margin-top: 0;
}
.filter-actions {
  margin-top: 8px;
  text-align: right;
}

/* 图例（右上） */
.graph-legend {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 10;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: #606266;
  max-width: 180px;
}
.legend-title {
  font-weight: 600;
  color: #303133;
}
.legend-section {
  margin-top: 6px;
  font-size: 11px;
  color: #909399;
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
.legend-stroke-green {
  background: transparent;
  border: 2px solid #67c23a;
  width: 8px;
  height: 8px;
}
.legend-fill-gold {
  background: #e6a23c;
}
.legend-focus {
  background: #409eff;
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
</style>
