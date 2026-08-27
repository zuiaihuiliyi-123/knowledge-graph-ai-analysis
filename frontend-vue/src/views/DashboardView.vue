<template>
  <div class="dashboard">
    <!-- 页头 -->
    <div class="page-header">
      <h2 class="page-title">数据总览（演示占位）</h2>
      <p class="page-desc">课程知识图谱数据概览</p>
    </div>

    <!-- 开发中提示 Banner -->
    <el-alert
      type="warning"
      :closable="false"
      show-icon
      title="数据总览功能开发中，当前展示数据为演示占位，正式数据将在后续版本接入。"
      class="placeholder-banner"
    />

    <!-- 顶部统计卡片（模仿视频风格） -->
    <div class="stats-row">
      <div class="stat-card" v-for="(item, idx) in statCards" :key="idx" :style="{ borderTopColor: item.color }">
        <div class="stat-value" :style="{ color: item.color }">{{ item.value }}</div>
        <div class="stat-label">{{ item.label }}</div>
      </div>
    </div>

    <!-- 图表区域 -->
    <el-row :gutter="16" class="chart-row">
      <!-- 柱状图 + 折线图（知识点分布趋势） -->
      <el-col :span="16">
        <div class="chart-card">
          <div class="chart-title"><el-icon><DataAnalysis /></el-icon> 各课程知识图谱数据概览</div>
          <div ref="barChartRef" class="chart-body"></div>
        </div>
      </el-col>

      <!-- 雷达图（学习维度分析） -->
      <el-col :span="8">
        <div class="chart-card">
          <div class="chart-title"><el-icon><Aim /></el-icon> 知识覆盖雷达</div>
          <div ref="radarChartRef" class="chart-body"></div>
        </div>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="chart-row">
      <!-- 饼图（知识点分类分布） -->
      <el-col :span="12">
        <div class="chart-card">
          <div class="chart-title"><el-icon><PieChart /></el-icon> 知识点分类占比</div>
          <div ref="pieChartRef" class="chart-body"></div>
        </div>
      </el-col>

      <!-- 关系类型分布 -->
      <el-col :span="12">
        <div class="chart-card">
          <div class="chart-title"><el-icon><Connection /></el-icon> 关系类型统计</div>
          <div ref="relationChartRef" class="chart-body"></div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import * as echarts from 'echarts'
import { DataAnalysis, Aim, PieChart, Connection } from '@element-plus/icons-vue'

const barChartRef = ref(null)
const radarChartRef = ref(null)
const pieChartRef = ref(null)
const relationChartRef = ref(null)

let barChart = null
let radarChart = null
let pieChart = null
let relationChart = null

// 统计卡片数据（与你的知识图谱系统相关）
const statCards = ref([
  { label: '课程总数', value: '3', color: '#409eff' },
  { label: '专业方向', value: '2', color: '#67c23a' },
  { label: '知识点数', value: '60', color: '#e6a23c' },
  { label: '关系数量', value: '45', color: '#f56c6c' },
  { label: '概念节点', value: '60', color: '#909399' },
  { label: '学生人数', value: '58', color: '#b37feb' },
  { label: '问答次数', value: '226', color: '#ff85c0' },
])

function initBarChart() {
  if (!barChartRef.value) return
  barChart = echarts.init(barChartRef.value)
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
      data: ['数据结构', '算法设计', '操作系统', '计算机网络', '数据库原理', '编译原理', '软件工程'],
      axisLabel: { color: '#606266', fontSize: 11, rotate: 20 },
      axisLine: { lineStyle: { color: '#dcdfe6' } },
    },
    yAxis: [
      { type: 'value', name: '数量', nameTextStyle: { color: '#909399', fontSize: 11 },
        axisLabel: { color: '#909399' }, splitLine: { lineStyle: { color: '#f0f0f0', type: 'dashed' } } },
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
        data: [12, 15, 8, 10, 9, 6, 8],
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
        data: [10, 12, 7, 8, 7, 5, 6],
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
        data: [3.2, 3.8, 2.9, 3.1, 2.8, 2.5, 3.0],
      },
    ],
  }
  barChart.setOption(option)
}

function initRadarChart() {
  if (!radarChartRef.value) return
  radarChart = echarts.init(radarChartRef.value)
  const option = {
    tooltip: {},
    radar: {
      indicator: [
        { name: '概念掌握', max: 100 },
        { name: '定理理解', max: 100 },
        { name: '公式应用', max: 100 },
        { name: '方法实践', max: 100 },
        { name: '综合能力', max: 100 },
      ],
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
          value: [78, 65, 72, 85, 70],
          name: '当前水平',
          areaStyle: { color: 'rgba(64,158,255,0.2)' },
          lineStyle: { color: '#409eff', width: 2 },
          itemStyle: { color: '#409eff' },
        },
        {
          value: [92, 88, 85, 90, 87],
          name: '目标水平',
          areaStyle: { color: 'rgba(103,194,58,0.15)' },
          lineStyle: { color: '#67c23a', width: 2, type: 'dashed' },
          itemStyle: { color: '#67c23a' },
        },
      ],
    }],
  }
  radarChart.setOption(option)
}

function initPieChart() {
  if (!pieChartRef.value) return
  pieChart = echarts.init(pieChartRef.value)
  const option = {
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: {
      orient: 'vertical',
      right: '5%',
      top: 'center',
      textStyle: { color: '#606266', fontSize: 12 },
    },
    series: [{
      type: 'pie',
      radius: ['42%', '68%'],
      center: ['38%', '50%'],
      avoidLabelOverlap: true,
      itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
      label: { show: false, position: 'center' },
      emphasis: {
        label: { show: true, fontSize: 16, fontWeight: 'bold', formatter: '{b}\n{d}%' },
      },
      labelLine: { show: false },
      data: [
        { value: 22, name: '概念', itemStyle: { color: '#409eff' } },
        { value: 15, name: '定理', itemStyle: { color: '#67c23a' } },
        { value: 12, name: '公式', itemStyle: { color: '#e6a23c' } },
        { value: 18, name: '方法', itemStyle: { color: '#f56c6c' } },
        { value: 8, name: '其他', itemStyle: { color: '#909399' } },
      ],
    }],
  }
  pieChart.setOption(option)
}

function initRelationChart() {
  if (!relationChartRef.value) return
  relationChart = echarts.init(relationChartRef.value)
  const option = {
    tooltip: { trigger: 'item' },
    grid: { left: '3%', right: '4%', bottom: '10%', top: '12%', containLabel: true },
    xAxis: {
      type: 'category',
      data: ['前置关系', '包含关系', '相似关系', '引用关系', '扩展关系'],
      axisLabel: { color: '#606266', fontSize: 11 },
      axisLine: { lineStyle: { color: '#dcdfe6' } },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#909399' },
      splitLine: { lineStyle: { color: '#f0f0f0', type: 'dashed' } },
    },
    series: [{
      type: 'bar',
      barWidth: '45%',
      itemStyle: {
        borderRadius: [6, 6, 0, 0],
        color: function(params) {
          const colors = ['#409eff', '#67c23a', '#e6a23c', '#f56c6c', '#b37feb']
          return new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: colors[params.dataIndex] },
            { offset: 1, color: colors[params.dataIndex] + '99' },
          ])
        },
      },
      data: [18, 12, 8, 5, 4],
    }],
  }
  relationChart.setOption(option)
}

function handleResize() {
  barChart?.resize()
  radarChart?.resize()
  pieChart?.resize()
  relationChart?.resize()
}

onMounted(async () => {
  await nextTick()
  initBarChart()
  initRadarChart()
  initPieChart()
  initRelationChart()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  barChart?.dispose()
  radarChart?.dispose()
  pieChart?.dispose()
  relationChart?.dispose()
})
</script>

<style scoped>
.dashboard {
  min-height: 100%;
}
.placeholder-banner {
  margin-bottom: 16px;
}

/* ===== 统计卡片行 ===== */
.stats-row {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 14px;
  margin-bottom: 18px;
}
.stat-card {
  background: #fff;
  border-radius: 10px;
  padding: 18px 14px;
  text-align: center;
  border-top: 3px solid #409eff;
  box-shadow: 0 2px 8px rgba(0,0,0,0.04);
  transition: transform 0.2s, box-shadow 0.2s;
}
.stat-card:hover {
  transform: translateY(-3px);
  box-shadow: 0 6px 20px rgba(0,0,0,0.08);
}
.stat-value {
  font-size: 28px;
  font-weight: 700;
  line-height: 1.2;
  font-family: 'DIN Alternate', 'Helvetica Neue', sans-serif;
}
.stat-label {
  font-size: 12px;
  color: #909399;
  margin-top: 6px;
}

/* ===== 图表卡片 ===== */
.chart-row {
  margin-bottom: 16px;
}
.chart-card {
  background: #fff;
  border-radius: 10px;
  padding: 18px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.04);
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

/* 响应式：小屏幕时统计卡片换行 */
@media (max-width: 1200px) {
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
