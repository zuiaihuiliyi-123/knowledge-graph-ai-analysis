<template>
  <div class="dashboard">
    <!-- 页头 -->
    <div class="page-header">
      <h2 class="page-title">数据总览</h2>
      <p class="page-desc">课程知识图谱与系统资源概况</p>
    </div>

    <!-- 第一层：核心统计卡（原有 7 张 KPI，全部保留） -->
    <div class="stats-row">
      <div
        v-for="(item, idx) in statCards"
        :key="idx"
        class="stat-card"
        :style="{ borderLeftColor: item.color }"
      >
        <div class="stat-icon-box" :style="{ background: item.color + '1a', color: item.color }">
          <el-icon :size="22"><component :is="item.icon" /></el-icon>
        </div>
        <div class="stat-info">
          <div class="stat-value" :style="{ color: item.color }">{{ item.value }}</div>
          <div class="stat-label">{{ item.label }}</div>
        </div>
      </div>
    </div>

    <!-- 第二层：各课程知识图谱概览 + 知识点类别覆盖 -->
    <el-row :gutter="16" class="chart-row">
      <el-col :span="16">
        <div class="chart-card">
          <div class="chart-title"><el-icon><DataAnalysis /></el-icon> 各课程知识图谱概览</div>
          <div ref="barChartRef" class="chart-body"></div>
        </div>
      </el-col>
      <el-col :span="8">
        <div class="chart-card">
          <div class="chart-title"><el-icon><Aim /></el-icon> 知识点类别覆盖</div>
          <div ref="radarChartRef" class="chart-body"></div>
        </div>
      </el-col>
    </el-row>

    <!-- 第三层：关系结构 + 快速入口 -->
    <el-row :gutter="16" class="chart-row">
      <el-col :span="12">
        <div class="chart-card">
          <div class="chart-title"><el-icon><Connection /></el-icon> 关系结构</div>
          <div ref="relationChartRef" class="chart-body"></div>
        </div>
      </el-col>
      <el-col :span="12">
        <div class="chart-card quick-card">
          <div class="chart-title"><el-icon><Compass /></el-icon> 快速入口</div>
          <div class="quick-list">
            <div
              v-for="(q, i) in quickLinks"
              :key="i"
              class="quick-item"
              @click="goQuick(q.path)"
            >
              <div class="quick-icon"><el-icon :size="18"><component :is="q.icon" /></el-icon></div>
              <div class="quick-meta">
                <div class="quick-label">{{ q.label }}</div>
                <div class="quick-desc">{{ q.desc }}</div>
              </div>
              <el-icon class="quick-arrow"><ArrowRight /></el-icon>
            </div>
          </div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import {
  DataAnalysis, Aim, Connection, Compass, ArrowRight, Search,
  Collection, Document, User, UserFilled, Upload, Notebook,
} from '@element-plus/icons-vue'
import { api } from '../api'

const router = useRouter()

const barChartRef = ref(null)
const radarChartRef = ref(null)
const relationChartRef = ref(null)

let barChart = null
let radarChart = null
let relationChart = null

// 默认统计（后端不可用/缺数据时全 0）
const defaultStats = () => ({
  course_count: 0,
  teacher_count: 0,
  student_count: 0,
  document_count: 0,
  node_count: 0,
  edge_count: 0,
  concept_node_count: 0,
  category_distribution: { 概念: 0, 定理: 0, 公式: 0, 方法: 0, 其他: 0 },
  relation_distribution: { 前置知识: 0, 包含: 0, 相关概念: 0, 应用: 0 },
  per_course: [],
})

const stats = ref(defaultStats())

// 统计卡（7 张，真实数据 + 图标，数据来源与拆分前一致）
const statCards = computed(() => [
  { label: '课程总数', value: stats.value.course_count, color: '#409eff', icon: Collection },
  { label: '知识点数', value: stats.value.node_count, color: '#e6a23c', icon: DataAnalysis },
  { label: '关系数量', value: stats.value.edge_count, color: '#f56c6c', icon: Connection },
  { label: '概念节点', value: stats.value.concept_node_count, color: '#67c23a', icon: Aim },
  { label: '学生人数', value: stats.value.student_count, color: '#b37feb', icon: User },
  { label: '教师人数', value: stats.value.teacher_count, color: '#909399', icon: UserFilled },
  { label: '文档数', value: stats.value.document_count, color: '#ff85c0', icon: Document },
])

// 快速入口（仅导航，不加载学生学习列表）
const quickLinks = [
  { label: '课程管理', desc: '管理课程、图谱与教学监测', icon: Notebook, path: '/teacher?tab=courses' },
  { label: '图谱预览', desc: '浏览已生成的课程知识图谱', icon: Search, path: '/teacher?tab=preview' },
  { label: '上传课程资料', desc: '上传文档，自动构建知识图谱', icon: Upload, path: '/teacher?tab=upload' },
]

function goQuick(path) {
  router.push(path)
}

// 空数据提示（图表无数据时居中显示）
function emptyGraphic() {
  return {
    type: 'text',
    left: 'center',
    top: 'middle',
    style: { text: '暂无数据', fill: '#909399', fontSize: 14 },
  }
}

async function fetchStats() {
  try {
    const data = await api.getDashboardStats()
    stats.value = { ...defaultStats(), ...data }
  } catch (e) {
    ElMessage.warning(`数据总览加载失败：${e.message}`)
    stats.value = defaultStats()
  }
}

// ===== 图表初始化 =====
function initBarChart() {
  if (!barChartRef.value) return
  barChart = echarts.init(barChartRef.value)
  const courses = stats.value.per_course || []
  const option = {
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: {
      data: ['知识点数量', '关系数量', '平均关联度'],
      textStyle: { color: '#606266', fontSize: 12 },
      top: 5,
    },
    grid: { left: '3%', right: '4%', bottom: '10%', top: '18%', containLabel: true },
    xAxis: {
      type: 'category',
      data: courses.map((c) => c.course_name),
      axisLabel: { color: '#606266', fontSize: 11, rotate: courses.length > 5 ? 20 : 0 },
      axisLine: { lineStyle: { color: '#dcdfe6' } },
    },
    yAxis: [
      { type: 'value', name: '数量', nameTextStyle: { color: '#909399', fontSize: 11 },
        minInterval: 1, axisLabel: { color: '#909399' },
        splitLine: { lineStyle: { color: '#f0f0f0', type: 'dashed' } } },
      { type: 'value', name: '平均关联度', nameTextStyle: { color: '#909399', fontSize: 11 },
        axisLabel: { color: '#909399', formatter: '{value}' }, splitLine: { show: false } },
    ],
    series: [
      {
        name: '知识点数量',
        type: 'bar',
        barWidth: '28%',
        itemStyle: {
          borderRadius: [4, 4, 0, 0],
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: '#409eff' },
            { offset: 1, color: '#79bbff' },
          ]),
        },
        data: courses.map((c) => c.node_count),
      },
      {
        name: '关系数量',
        type: 'bar',
        barWidth: '28%',
        itemStyle: {
          borderRadius: [4, 4, 0, 0],
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: '#67c23a' },
            { offset: 1, color: '#95d475' },
          ]),
        },
        data: courses.map((c) => c.edge_count),
      },
      {
        name: '平均关联度',
        type: 'line',
        yAxisIndex: 1,
        smooth: true,
        symbol: 'circle',
        symbolSize: 8,
        lineStyle: { width: 2.5, color: '#e6a23c' },
        itemStyle: { color: '#e6a23c' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(230,162,60,0.25)' },
            { offset: 1, color: 'rgba(230,162,60,0.02)' },
          ]),
        },
        data: courses.map((c) => c.avg_degree),
      },
    ],
  }
  if (!courses.length) option.graphic = emptyGraphic()
  barChart.setOption(option)
}

function initRadarChart() {
  if (!radarChartRef.value) return
  radarChart = echarts.init(radarChartRef.value)
  const catLabels = ['概念', '定理', '公式', '方法']
  const values = catLabels.map((l) => stats.value.category_distribution[l] || 0)
  const max = Math.max(...values, 1)
  const option = {
    tooltip: {},
    radar: {
      indicator: catLabels.map((name) => ({ name, max })),
      shape: 'polygon',
      splitNumber: 4,
      axisName: { color: '#606266', fontSize: 11 },
      splitLine: { lineStyle: { color: '#e4e7ed' } },
      splitArea: { areaStyle: { color: ['#f5f7fa', '#fff', '#f5f7fa', '#fff'] } },
      axisLine: { lineStyle: { color: '#dcdfe6' } },
    },
    series: [{
      type: 'radar',
      data: [
        {
          value: values,
          name: '知识点数量',
          areaStyle: { color: 'rgba(64,158,255,0.2)' },
          lineStyle: { color: '#409eff', width: 2 },
          itemStyle: { color: '#409eff' },
        },
      ],
    }],
  }
  if (values.every((v) => v === 0)) option.graphic = emptyGraphic()
  radarChart.setOption(option)
}

function initRelationChart() {
  if (!relationChartRef.value) return
  relationChart = echarts.init(relationChartRef.value)
  const labels = Object.keys(stats.value.relation_distribution || {})
  const values = labels.map((l) => stats.value.relation_distribution[l])
  const colors = ['#409eff', '#67c23a', '#e6a23c', '#f56c6c']
  const option = {
    tooltip: { trigger: 'item' },
    grid: { left: '3%', right: '4%', bottom: '10%', top: '12%', containLabel: true },
    xAxis: {
      type: 'category',
      data: labels,
      axisLabel: { color: '#606266', fontSize: 11 },
      axisLine: { lineStyle: { color: '#dcdfe6' } },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      axisLabel: { color: '#909399' },
      splitLine: { lineStyle: { color: '#f0f0f0', type: 'dashed' } },
    },
    series: [{
      type: 'bar',
      barWidth: '45%',
      itemStyle: {
        borderRadius: [6, 6, 0, 0],
        color: function (params) {
          const c = colors[params.dataIndex % colors.length]
          return new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: c },
            { offset: 1, color: c + '99' },
          ])
        },
      },
      data: values,
    }],
  }
  if (values.every((v) => v === 0)) option.graphic = emptyGraphic()
  relationChart.setOption(option)
}

function handleResize() {
  barChart?.resize()
  radarChart?.resize()
  relationChart?.resize()
}

onMounted(async () => {
  await fetchStats()
  await nextTick()
  initBarChart()
  initRadarChart()
  initRelationChart()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  barChart?.dispose()
  radarChart?.dispose()
  relationChart?.dispose()
})
</script>

<style scoped>
.dashboard {
  min-height: 100%;
}
.page-header {
  margin-bottom: 16px;
}
.page-title {
  margin: 0 0 4px;
  color: #1a1f36;
  font-size: 20px;
  font-weight: 700;
}
.page-desc {
  margin: 0;
  color: #909399;
  font-size: 13px;
}

/* ===== 统计卡 ===== */
.stats-row {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 14px;
  margin-bottom: 18px;
}
.stat-card {
  background: #fff;
  border-radius: 10px;
  padding: 16px 14px;
  display: flex;
  align-items: center;
  gap: 12px;
  border-left: 3px solid #409eff;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  transition: transform 0.2s, box-shadow 0.2s;
}
.stat-card:hover {
  transform: translateY(-3px);
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.08);
}
.stat-icon-box {
  width: 44px;
  height: 44px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.stat-info {
  min-width: 0;
}
.stat-value {
  font-size: 26px;
  font-weight: 700;
  line-height: 1.1;
  font-family: 'DIN Alternate', 'Helvetica Neue', sans-serif;
}
.stat-label {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}

/* ===== 图表卡片 ===== */
.chart-row {
  margin-bottom: 16px;
}
/* 行内两列等高：el-col 转 flex 容器，卡片 flex:1 撑满列高，保证快速入口底边与关系结构底边对齐 */
.chart-row :deep(.el-col) {
  display: flex;
}
.chart-row :deep(.el-col) > .chart-card {
  flex: 1 1 auto;
  width: 100%;
}
.chart-card {
  background: #fff;
  border-radius: 10px;
  padding: 18px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}
.chart-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid #f0f2f5;
}
.chart-body {
  width: 100%;
  height: 320px;
}

/* ===== 快速入口 ===== */
.quick-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.quick-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 8px;
  border: 1px solid #f0f2f5;
  cursor: pointer;
  transition: background 0.2s, border-color 0.2s, transform 0.2s;
}
.quick-item:hover {
  background: #f5f9ff;
  border-color: #d9ecff;
  transform: translateX(2px);
}
.quick-icon {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  background: #ecf5ff;
  color: #409eff;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.quick-meta {
  flex: 1;
  min-width: 0;
}
.quick-label {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}
.quick-desc {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}
.quick-arrow {
  color: #c0c4cc;
}

/* 响应式：小屏幕时统计卡片换行 */
@media (max-width: 1400px) {
  .stats-row {
    grid-template-columns: repeat(4, 1fr);
  }
}
@media (max-width: 768px) {
  .stats-row {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
