<template>
  <div class="graph-wrap">
    <div ref="containerRef" class="graph-container"></div>

    <!-- 图例 -->
    <div class="graph-legend">
      <span v-for="(t, key) in NODE_TYPES" :key="key" class="legend-item">
        <i class="legend-dot" :style="{ background: t.color }"></i>{{ t.label }}
      </span>
    </div>

    <!-- 空状态 -->
    <el-empty
      v-if="!loading && empty"
      :description="emptyText"
      :image-size="90"
      class="graph-empty"
    />
    <!-- 加载状态 -->
    <div v-if="loading" class="graph-loading" v-loading="true"></div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { Graph } from '@antv/g6'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { NODE_TYPES, nodeColor, nodeTypeLabel, edgeTypeLabel } from '../utils/graphStyle'

const props = defineProps({
  courseId: { type: String, default: '' },
  /** 编辑模式：点击节点/边时携带完整数据抛出事件 */
  editable: { type: Boolean, default: false },
  /** 搜索关键词：命中节点高亮，其余变暗 */
  searchText: { type: String, default: '' },
})

const emit = defineEmits([
  'node-click', // (nodeData)
  'edge-click', // (edgeData)
  'loaded', // ({ nodeCount, edgeCount })
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
// 数据加载：优先新版 /api/v1/graph（G6 格式），失败回退旧接口
// ---------------------------------------------------------------
async function loadGraph() {
  if (!props.courseId) {
    empty.value = true
    emptyText.value = '请先选择课程'
    clearCanvas()
    return
  }
  loading.value = true
  empty.value = false
  try {
    let nodes = []
    let edges = []
    try {
      const data = await api.getGraphV1(props.courseId, { limit: 800 })
      nodes = data.nodes || []
      edges = data.edges || []
    } catch {
      // 回退旧版 ECharts 格式接口（节点以 name 为 id）
      const legacy = await api.getCourseGraphLegacy(props.courseId)
      const lg = legacy?.graph || {}
      nodes = (lg.nodes || []).map((n) => ({
        id: n.name,
        label: n.name,
        type: 'concept',
        description: n.description || '',
        properties: { category: n.category || '概念' },
      }))
      edges = (lg.links || []).map((l, i) => ({
        id: `e${i}`,
        source: l.source,
        target: l.target,
        type: l.type,
        label: l.type,
      }))
    }
    rawNodes = nodes
    rawEdges = edges
    await renderGraph()
    emit('loaded', { nodeCount: nodes.length, edgeCount: edges.length })
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
        stroke: '#c0c4cc',
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
    if (node) emit('node-click', node)
  })
  graph.on('edge:click', (evt) => {
    if (!props.editable) return
    const id = evt?.target?.id
    const edge = rawEdges.find((e) => String(e.id) === String(id))
    if (edge) emit('edge-click', edge)
  })
  graph.on('canvas:click', () => {
    if (props.editable) emit('edge-click', null) // 点击空白取消选择
  })

  await graph.render()
}

async function renderGraph() {
  await initGraph()
  if (!graph) return

  const gNodes = rawNodes.map((n) => ({
    id: String(n.id),
    style: {
      fill: nodeColor(n.type),
      labelText: String(n.label ?? n.id).slice(0, 30),
    },
    data: n,
  }))
  const gEdges = rawEdges.map((e) => ({
    id: String(e.id || `${e.source}-${e.target}`),
    source: String(e.source),
    target: String(e.target),
    style: { labelText: edgeTypeLabel(e.type, e.label) },
    data: e,
  }))

  graph.setData({ nodes: gNodes, edges: gEdges })
  await graph.draw()
  applySearchHighlight()
}

// ---------------------------------------------------------------
// 搜索高亮：命中节点发光 + 放大，其余节点变暗
// ---------------------------------------------------------------
async function applySearchHighlight() {
  if (!graph || !rawNodes.length) return
  const kw = (props.searchText || '').trim().toLowerCase()
  const updates = rawNodes.map((n) => {
    const label = String(n.label ?? n.id).toLowerCase()
    const desc = String(n.description ?? '').toLowerCase()
    const hit = !kw || label.includes(kw) || desc.includes(kw)
    const style = {}
    if (kw) {
      style.opacity = hit ? 1 : 0.12
      if (hit) {
        style.halo = true
        style.haloStroke = '#f56c6c'
        style.haloLineWidth = 3
      }
    } else {
      style.opacity = 1
      style.halo = false
    }
    return { id: String(n.id), style }
  })
  await graph.updateNodeData(updates)
  await graph.draw()
}

function fitView() {
  if (graph) graph.fitView()
}

// ---------------------------------------------------------------
// 对外方法（父组件通过 ref 调用）
// ---------------------------------------------------------------
defineExpose({
  refresh: loadGraph,
  fitView,
})

// ---------------------------------------------------------------
// 生命周期
// ---------------------------------------------------------------
onMounted(() => {
  resizeObserver = new ResizeObserver(() => {
    // 容器尺寸变化时重新适配视图
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
  () => applySearchHighlight()
)
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
.graph-legend {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 10;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 8px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: #606266;
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
