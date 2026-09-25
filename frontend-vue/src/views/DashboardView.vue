<template>
  <div class="dashboard">
    <!-- 顶部星图横幅（静态背景，无动画） -->
    <div class="hero">
      <div class="hero-content">
        <div class="hero-left">
          <div class="hero-eyebrow">Knowledge Graph · 教学数据驾驶舱</div>
          <h2 class="hero-title">{{ greeting }}，{{ store.displayName }}</h2>
          <p class="hero-sub">{{ todayText }} · 课程建设、知识图谱与教学进展，一屏尽览</p>
        </div>
        <div class="hero-metrics">
          <div v-for="m in heroMetrics" :key="m.label" class="hero-metric">
            <div class="hero-metric-value">{{ loading ? '—' : fmt(m.value) }}</div>
            <div class="hero-metric-label">{{ m.label }}</div>
          </div>
        </div>
      </div>
    </div>

    <!-- 核心指标 -->
    <div class="stats-row" v-loading="loading">
      <div
        v-for="item in statCards"
        :key="item.label"
        class="stat-card"
        :style="{ '--card-color': item.color, '--card-bg': item.bg }"
      >
        <div class="stat-icon-box">
          <el-icon :size="19"><component :is="item.icon" /></el-icon>
        </div>
        <div class="stat-info">
          <div class="stat-value">{{ loading ? '—' : fmt(item.value) }}</div>
          <div class="stat-label">{{ item.label }}</div>
        </div>
      </div>
    </div>

    <!-- 课程图谱建设一览 + 平台数据构成 -->
    <el-row :gutter="14" class="chart-row" v-loading="loading">
      <el-col :xs="24" :md="16">
        <div class="chart-card">
          <div class="chart-title">
            <span class="title-chip chip-blue"><el-icon :size="13"><Notebook /></el-icon></span>
            课程图谱建设一览
            <span class="chart-extra">共 {{ stats.course_count }} 门课程</span>
          </div>
          <div class="course-list">
            <div
              v-for="(c, i) in courseList"
              :key="c.course_id"
              class="course-row"
              @click="goCourse(c.course_id)"
            >
              <span class="course-rank">{{ i + 1 }}</span>
              <div class="course-main">
                <div class="course-name">{{ c.course_name }}</div>
                <div class="course-bar"><i :style="{ width: barWidth(c) }"></i></div>
              </div>
              <div class="course-metrics">
                <span class="cm"><b>{{ fmt(c.node_count) }}</b>知识点</span>
                <span class="cm"><b>{{ fmt(c.edge_count) }}</b>关系</span>
                <span class="cm"><b>{{ c.avg_degree }}</b>关联度</span>
              </div>
            </div>
            <div v-if="!courseList.length && !loading" class="course-empty">暂无课程数据</div>
          </div>
        </div>
      </el-col>
      <el-col :xs="24" :md="8">
        <div class="chart-card">
          <div class="chart-title">
            <span class="title-chip chip-violet"><el-icon :size="13"><DataAnalysis /></el-icon></span>
            平台数据构成
            <span class="chart-extra">知识点 / 关系 / 文档</span>
          </div>
          <div ref="donutChartRef" class="chart-body"></div>
        </div>
      </el-col>
    </el-row>

    <!-- 关系类型分布 + 快速入口 -->
    <el-row :gutter="14" class="chart-row" v-loading="loading">
      <el-col :xs="24" :md="12">
        <div class="chart-card">
          <div class="chart-title">
            <span class="title-chip chip-pink"><el-icon :size="13"><Connection /></el-icon></span>
            关系类型分布
            <span class="chart-extra">共 {{ fmt(stats.edge_count) }} 条</span>
          </div>
          <div ref="relChartRef" class="chart-body"></div>
        </div>
      </el-col>
      <el-col :xs="24" :md="12">
        <div class="chart-card">
          <div class="chart-title">
            <span class="title-chip chip-green"><el-icon :size="13"><Compass /></el-icon></span>
            快速入口
          </div>
          <div class="quick-grid">
            <div
              v-for="(q, i) in quickLinks"
              :key="i"
              class="quick-tile"
              :style="{ background: q.bg }"
              @click="goQuick(q.path)"
            >
              <div class="tile-icon"><el-icon :size="18"><component :is="q.icon" /></el-icon></div>
              <div class="tile-label">{{ q.label }}</div>
              <div class="tile-desc">{{ q.desc }}</div>
              <el-icon class="tile-arrow"><ArrowRight /></el-icon>
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
import { useAppStore } from '../stores/app'

const router = useRouter()
const store = useAppStore()

const donutChartRef = ref(null)
const relChartRef = ref(null)

let donutChart = null
let relChart = null

const defaultStats = () => ({
  course_count: 0,
  teacher_count: 0,
  student_count: 0,
  document_count: 0,
  node_count: 0,
  edge_count: 0,
  concept_node_count: 0,
  relation_distribution: {},
  per_course: [],
})

const stats = ref(defaultStats())
const loading = ref(true)

const fmt = (n) => Number(n || 0).toLocaleString('zh-CN')

const statCards = computed(() => [
  { label: '课程总数', value: stats.value.course_count, color: '#4f8df7', bg: 'linear-gradient(135deg,#4f8df7,#6a5cf6)', icon: Collection },
  { label: '知识点数', value: stats.value.node_count, color: '#f5a623', bg: 'linear-gradient(135deg,#f5a623,#f7c948)', icon: DataAnalysis },
  { label: '关系数量', value: stats.value.edge_count, color: '#f4587a', bg: 'linear-gradient(135deg,#f4587a,#ff8fa3)', icon: Connection },
  { label: '概念节点', value: stats.value.concept_node_count, color: '#22c08a', bg: 'linear-gradient(135deg,#22c08a,#5eead4)', icon: Aim },
  { label: '学生人数', value: stats.value.student_count, color: '#8b5cf6', bg: 'linear-gradient(135deg,#8b5cf6,#c084fc)', icon: User },
  { label: '教师人数', value: stats.value.teacher_count, color: '#14b8d6', bg: 'linear-gradient(135deg,#14b8d6,#67e8f9)', icon: UserFilled },
  { label: '文档数', value: stats.value.document_count, color: '#f4794d', bg: 'linear-gradient(135deg,#f4794d,#fb923c)', icon: Document },
])

const heroMetrics = computed(() => [
  { label: '课程总数', value: stats.value.course_count },
  { label: '知识点', value: stats.value.node_count },
  { label: '知识关系', value: stats.value.edge_count },
])

const greeting = computed(() => {
  const h = new Date().getHours()
  if (h < 6) return '夜深了'
  if (h < 12) return '早上好'
  if (h < 14) return '中午好'
  if (h < 18) return '下午好'
  return '晚上好'
})
const todayText = computed(() => {
  const d = new Date()
  const week = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.getDay()]
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日 ${week}`
})

const quickLinks = [
  { label: '课程管理', desc: '管理课程、图谱与教学监测', icon: Notebook, path: '/teacher?tab=courses', bg: 'linear-gradient(135deg,#4f8df7,#6a5cf6)' },
  { label: '图谱管理', desc: '查看课程知识图谱，可就地切换编辑审核', icon: Search, path: '/teacher?tab=preview', bg: 'linear-gradient(135deg,#22c08a,#14b8d6)' },
  { label: '上传课程资料', desc: '上传文档，自动构建知识图谱', icon: Upload, path: '/teacher?tab=upload', bg: 'linear-gradient(135deg,#f5a623,#f4794d)' },
  { label: '题库管理', desc: '建设与维护课程配套题库', icon: Collection, path: '/teacher?tab=questions', bg: 'linear-gradient(135deg,#8b5cf6,#c084fc)' },
]

function goQuick(path) {
  router.push(path)
}

/* ===== 课程图谱建设一览：按知识点数降序，进度条按最大值归一 ===== */
const courseList = computed(() =>
  [...(stats.value.per_course || [])].sort((a, b) => (b.node_count || 0) - (a.node_count || 0))
)
const maxNodes = computed(() =>
  Math.max(1, ...courseList.value.map((c) => c.node_count || 0))
)
const barWidth = (c) =>
  Math.max(4, Math.round(((c.node_count || 0) / maxNodes.value) * 100)) + '%'

function goCourse(id) {
  router.push({ path: '/teacher', query: { tab: 'preview', course_id: String(id), open: '1' } })
}

function emptyGraphic() {
  return {
    type: 'text',
    left: 'center',
    top: 'middle',
    style: { text: '暂无数据', fill: '#b6bfd2', fontSize: 14 },
  }
}

async function fetchStats() {
  loading.value = true
  try {
    const data = await api.getDashboardStats()
    stats.value = { ...defaultStats(), ...data }
  } catch (e) {
    ElMessage.warning(`数据总览加载失败：${e.message}`)
    stats.value = defaultStats()
  } finally {
    loading.value = false
  }
}

const AXIS_LABEL = '#8590a8'
const SPLIT = '#eef1f7'
const TOOLTIP = {
  backgroundColor: 'rgba(28,36,56,.92)',
  borderWidth: 0,
  textStyle: { color: '#fff' },
}

/* 平台数据构成：知识点 / 知识关系 / 教学文档 全局占比，环心显示总量 */
function initDonutChart() {
  if (!donutChartRef.value) return
  donutChart = echarts.init(donutChartRef.value)
  const data = [
    { name: '知识点', value: stats.value.node_count, itemStyle: { color: '#5b8def' } },
    { name: '知识关系', value: stats.value.edge_count, itemStyle: { color: '#f4587a' } },
    { name: '教学文档', value: stats.value.document_count, itemStyle: { color: '#f5a623' } },
  ]
  const total = data.reduce((s, d) => s + d.value, 0)
  const option = {
    tooltip: {
      ...TOOLTIP,
      formatter: (p) => `${p.name}：${fmt(p.value)}（${p.percent}%）`,
    },
    legend: {
      bottom: 4,
      icon: 'circle',
      itemWidth: 8,
      itemHeight: 8,
      itemGap: 12,
      textStyle: { color: '#4a5468', fontSize: 11.5 },
    },
    series: [{
      type: 'pie',
      radius: ['48%', '70%'],
      center: ['50%', '44%'],
      itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
      label: { show: false },
      emphasis: { scale: true, scaleSize: 5 },
      data,
    }],
  }
  if (total === 0) {
    option.graphic = emptyGraphic()
  } else {
    option.graphic = [
      { type: 'text', left: 'center', top: '35%', style: { text: fmt(total), fontSize: 22, fontWeight: 700, fill: '#1c2438', textAlign: 'center' } },
      { type: 'text', left: 'center', top: '46%', style: { text: '数据总量', fontSize: 11.5, fill: '#8590a8', textAlign: 'center' } },
    ]
  }
  donutChart.setOption(option)
}

/* 关系类型分布：前置知识 / 包含 / 相关概念 / 应用（全局聚合，体现图谱教学结构） */
function initRelationChart() {
  if (!relChartRef.value) return
  relChart = echarts.init(relChartRef.value)
  const dist = stats.value.relation_distribution || {}
  const labels = Object.keys(dist)
  const values = labels.map((l) => dist[l])
  const colors = ['#5b8def', '#22c08a', '#f5a623', '#f4587a', '#8b5cf6', '#14b8d6']
  const option = {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, ...TOOLTIP },
    grid: { left: '3%', right: '12%', top: '10%', bottom: '6%', containLabel: true },
    xAxis: {
      type: 'value',
      minInterval: 1,
      axisLabel: { color: AXIS_LABEL },
      splitLine: { lineStyle: { color: SPLIT, type: 'dashed' } },
    },
    yAxis: {
      type: 'category',
      inverse: true,
      data: labels,
      axisLabel: { color: '#4a5468', fontSize: 12 },
      axisLine: { lineStyle: { color: SPLIT } },
      axisTick: { show: false },
    },
    series: [{
      type: 'bar',
      barWidth: '46%',
      label: { show: true, position: 'right', color: '#4a5468', fontSize: 12, fontWeight: 600 },
      itemStyle: {
        borderRadius: [0, 7, 7, 0],
        color: (p) => {
          const c = colors[p.dataIndex % colors.length]
          return new echarts.graphic.LinearGradient(0, 0, 1, 0, [
            { offset: 0, color: c },
            { offset: 1, color: c + '99' },
          ])
        },
      },
      data: values,
    }],
  }
  if (!labels.length || values.every((v) => !v)) option.graphic = emptyGraphic()
  relChart.setOption(option)
}

function handleResize() {
  donutChart?.resize()
  relChart?.resize()
}

onMounted(async () => {
  await fetchStats()
  await nextTick()
  initDonutChart()
  initRelationChart()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  donutChart?.dispose()
  relChart?.dispose()
})
</script>

<style scoped>
.dashboard {
  min-height: 100%;
}

/* ===== 顶部横幅（静态星点 + 渐变，无动画，紧凑） ===== */
.hero {
  position: relative;
  overflow: hidden;
  border-radius: 16px;
  margin-bottom: 12px;
  color: #fff;
  background:
    radial-gradient(rgba(255, 255, 255, .5) 1px, transparent 1.6px),
    radial-gradient(rgba(255, 255, 255, .26) 1px, transparent 1.6px),
    linear-gradient(125deg, #171b3a 0%, #232a63 40%, #3b2d8f 75%, #5b2ea6 100%);
  background-size: 120px 120px, 70px 70px, 100% 100%;
  background-position: 0 0, 36px 42px, 0 0;
  box-shadow: 0 14px 30px -14px rgba(35, 42, 99, .55);
}
.hero::before {
  content: '';
  position: absolute;
  width: 300px;
  height: 300px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(99, 140, 255, .35), transparent 65%);
  top: -150px;
  right: 8%;
  pointer-events: none;
}
.hero::after {
  content: '';
  position: absolute;
  width: 240px;
  height: 240px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(194, 122, 255, .28), transparent 65%);
  bottom: -140px;
  left: 28%;
  pointer-events: none;
}
.hero-content {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 18px 26px;
  min-height: 116px;
}
.hero-left { min-width: 0; }
.hero-eyebrow {
  font-size: 11px;
  letter-spacing: 3px;
  text-transform: uppercase;
  color: rgba(255, 255, 255, .62);
  margin-bottom: 5px;
  font-weight: 600;
}
.hero-title {
  margin: 0 0 6px;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: .5px;
}
.hero-sub {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: rgba(255, 255, 255, .8);
  max-width: 560px;
}
.hero-metrics {
  display: flex;
  gap: 12px;
  flex-shrink: 0;
}
.hero-metric {
  min-width: 100px;
  padding: 10px 16px;
  border-radius: 12px;
  background: rgba(255, 255, 255, .1);
  border: 1px solid rgba(255, 255, 255, .22);
  backdrop-filter: blur(8px);
  text-align: center;
}
.hero-metric-value {
  font-size: 22px;
  font-weight: 700;
  line-height: 1.2;
  font-family: var(--font-family-number, inherit);
}
.hero-metric-label {
  font-size: 12px;
  color: rgba(255, 255, 255, .75);
  margin-top: 2px;
}

/* ===== KPI 卡 ===== */
.stats-row {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 12px;
  margin-bottom: 12px;
}
.stat-card {
  position: relative;
  overflow: hidden;
  background: var(--color-bg-surface, #fff);
  border-radius: var(--radius-lg, 12px);
  padding: 11px 12px;
  display: flex;
  align-items: center;
  gap: 10px;
  border: 1px solid var(--border-light, #ebeef5);
  box-shadow: var(--shadow-card, 0 2px 10px rgba(31, 45, 90, .05));
  transition: transform .25s ease, box-shadow .25s ease;
}
.stat-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: var(--card-bg);
  transform: scaleX(0);
  transform-origin: left;
  transition: transform .3s ease;
}
.stat-card:hover::before { transform: scaleX(1); }
.stat-card:hover {
  transform: translateY(-3px);
  box-shadow: var(--shadow-hover, 0 12px 28px rgba(31, 45, 90, .12));
}
.stat-icon-box {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  background: var(--card-bg);
  box-shadow: 0 6px 14px -6px var(--card-color);
}
.stat-info { min-width: 0; }
.stat-value {
  font-size: 21px;
  font-weight: 700;
  line-height: 1.15;
  color: var(--color-text-primary, #1c2438);
  font-family: var(--font-family-number, inherit);
}
.stat-label {
  font-size: 12px;
  color: var(--color-text-secondary, #8590a8);
  margin-top: 2px;
  white-space: nowrap;
}

/* ===== 图表卡（紧凑高度，一屏放得下） ===== */
.chart-row { margin-bottom: 0; }
.chart-card {
  background: var(--color-bg-surface, #fff);
  border-radius: var(--radius-lg, 12px);
  padding: 12px 16px;
  margin-bottom: 12px;
  border: 1px solid var(--border-light, #ebeef5);
  box-shadow: var(--shadow-card, 0 2px 10px rgba(31, 45, 90, .05));
  height: 292px;
  display: flex;
  flex-direction: column;
}
.chart-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-primary, #1c2438);
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.title-chip {
  width: 24px;
  height: 24px;
  border-radius: 7px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  flex-shrink: 0;
  box-shadow: 0 4px 10px -4px rgba(31, 45, 90, .4);
}
.chip-blue { background: linear-gradient(135deg, #4f8df7, #6a5cf6); }
.chip-violet { background: linear-gradient(135deg, #8b5cf6, #c084fc); }
.chip-pink { background: linear-gradient(135deg, #f4587a, #ff8fa3); }
.chip-green { background: linear-gradient(135deg, #22c08a, #14b8d6); }
.chart-extra {
  margin-left: auto;
  font-size: 11.5px;
  font-weight: 500;
  color: #4f6ef7;
  background: rgba(79, 110, 247, .08);
  border: 1px solid rgba(79, 110, 247, .18);
  padding: 1px 9px;
  border-radius: 999px;
  white-space: nowrap;
}
.chart-body { flex: 1; min-height: 0; }

/* ===== 课程图谱建设一览列表 ===== */
.course-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding-top: 6px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.course-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  border-radius: 10px;
  border: 1px solid var(--border-light, #ebeef5);
  background: #fafbfe;
  cursor: pointer;
  transition: transform .2s ease, box-shadow .2s ease, border-color .2s ease;
}
.course-row:hover {
  border-color: rgba(79, 110, 247, .35);
  box-shadow: 0 6px 16px -8px rgba(31, 45, 90, .18);
  transform: translateY(-1px);
}
.course-rank {
  width: 24px;
  height: 24px;
  border-radius: 7px;
  background: linear-gradient(135deg, #4f8df7, #6a5cf6);
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.course-main { flex: 1; min-width: 0; }
.course-name {
  font-size: 13.5px;
  font-weight: 600;
  color: #1c2438;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 6px;
}
.course-bar {
  height: 5px;
  border-radius: 3px;
  background: #eef1f7;
  overflow: hidden;
}
.course-bar i {
  display: block;
  height: 100%;
  border-radius: 3px;
  background: linear-gradient(90deg, #4f8df7, #6a5cf6);
}
.course-metrics {
  display: flex;
  gap: 16px;
  flex-shrink: 0;
}
.cm {
  font-size: 11.5px;
  color: #8590a8;
  white-space: nowrap;
  text-align: right;
}
.cm b {
  display: block;
  font-size: 15px;
  font-weight: 700;
  color: #1c2438;
  font-family: var(--font-family-number, inherit);
}
.course-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #b6bfd2;
  font-size: 14px;
}

/* ===== 快速入口瓷砖 ===== */
.quick-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  flex: 1;
  min-height: 0;
  padding-top: 4px;
}
.quick-tile {
  position: relative;
  overflow: hidden;
  border-radius: 12px;
  padding: 10px 14px;
  color: #fff;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 2px;
  box-shadow: 0 8px 18px -10px rgba(31, 45, 90, .45);
  transition: transform .22s ease, box-shadow .22s ease;
}
.quick-tile::before {
  content: '';
  position: absolute;
  width: 110px;
  height: 110px;
  border-radius: 50%;
  background: rgba(255, 255, 255, .12);
  right: -34px;
  top: -40px;
  pointer-events: none;
}
.quick-tile:hover {
  transform: translateY(-2px);
  box-shadow: 0 14px 26px -12px rgba(31, 45, 90, .55);
}
.tile-icon {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  background: rgba(255, 255, 255, .2);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 2px;
}
.tile-label { font-size: 14px; font-weight: 700; }
.tile-desc {
  font-size: 11.5px;
  color: rgba(255, 255, 255, .85);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 86%;
}
.tile-arrow {
  position: absolute;
  right: 12px;
  bottom: 12px;
  opacity: .85;
  transition: transform .2s ease;
}
.quick-tile:hover .tile-arrow { transform: translateX(3px); }

@media (max-width: 1400px) {
  .stats-row { grid-template-columns: repeat(4, 1fr); }
}
@media (max-width: 1100px) {
  .hero-content { flex-direction: column; align-items: flex-start; }
  .hero-metrics { width: 100%; }
  .hero-metric { flex: 1; }
}
@media (max-width: 768px) {
  .stats-row { grid-template-columns: repeat(2, 1fr); }
  .hero-metrics { display: none; }
  .quick-grid { grid-template-columns: 1fr; }
}
</style>
