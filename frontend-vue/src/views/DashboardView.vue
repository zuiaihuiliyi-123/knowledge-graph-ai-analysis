<template>
  <div class="dashboard">
    <!-- 页头 -->
    <div class="page-header">
      <h2 class="page-title">数据总览</h2>
      <p class="page-desc">课程知识图谱与班级学习情况监测</p>
    </div>

    <!-- 第一层：当前课程选择（班级学习情况按此课程统计） -->
    <div class="course-select-row">
      <span class="row-label">当前课程</span>
      <CourseSelector v-model="classCourseId" @change="onClassCourseChange" />
    </div>

    <!-- 第二层：核心统计卡（原有 7 张 KPI，全部保留） -->
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

    <!-- 第三层：班级学习情况（真实学生数据） -->
    <div v-if="classCourseId" class="chart-card class-card">
      <div class="chart-title">
        <el-icon><UserFilled /></el-icon> 班级学习情况
        <span class="class-note">（无选课关系表，班级学生 = 在该课程有学习记录的学生）</span>
      </div>

      <div v-loading="classLoading" class="class-summary">
        <div v-for="k in classKpis" :key="k.label" class="class-kpi">
          <div class="class-kpi-value" :style="{ color: k.color }">{{ k.value }}</div>
          <div class="class-kpi-label">{{ k.label }}</div>
        </div>
      </div>

      <el-empty
        v-if="!classLoading && classData && !classData.student_count"
        description="暂无学生学习记录（尚未有学生开始学习该课程）"
        :image-size="80"
      />
      <div v-else ref="progressDistRef" class="chart-body dist-chart"></div>
    </div>

    <!-- 第四层：课程概览 + 知识点类别覆盖 -->
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

    <!-- 第五层：关系结构 + 快速入口 -->
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

    <!-- 第六层：学生学习情况表格 -->
    <el-card v-if="classCourseId" class="chart-card">
      <div class="chart-title"><el-icon><User /></el-icon> 学生学习情况</div>

      <div class="table-toolbar">
        <el-input
          v-model="studentSearch"
          placeholder="搜索学生姓名 / 用户名"
          clearable
          :prefix-icon="Search"
          style="width: 240px"
        />
        <el-select v-model="progressFilter" placeholder="进度筛选" clearable style="width: 150px">
          <el-option
            v-for="b in progressBins"
            :key="b.value"
            :label="b.label"
            :value="b.value"
          />
        </el-select>
      </div>

      <el-table
        :data="pagedStudents"
        v-loading="classLoading"
        :default-sort="{ prop: 'progress', order: 'ascending' }"
        @sort-change="onSortChange"
      >
        <el-table-column prop="student_name" label="学生" sortable="custom" min-width="140">
          <template #default="{ row }">
            <span class="student-cell">
              {{ row.student_name }}
              <span v-if="row.username && row.username !== row.student_name" class="student-username">
                @{{ row.username }}
              </span>
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="total_knowledge" label="知识点总数" width="110" align="center" />
        <el-table-column prop="mastered_count" label="已掌握" width="90" align="center" />
        <el-table-column prop="progress" label="学习进度" sortable="custom" min-width="150">
          <template #default="{ row }">
            <el-progress
              :percentage="row.progress"
              :color="progressColor(row.progress)"
              :stroke-width="10"
            />
          </template>
        </el-table-column>
        <el-table-column label="当前学习" min-width="160">
          <template #default="{ row }">
            <span v-if="row.current_name" class="current-name">{{ row.current_name }}</span>
            <span v-else class="cell-empty">—</span>
          </template>
        </el-table-column>
        <el-table-column label="推荐学习" min-width="180">
          <template #default="{ row }">
            <template v-if="row.recommended && row.recommended.length">
              <el-tag
                v-for="r in row.recommended.slice(0, 3)"
                :key="r.kp_id || r.name"
                size="small"
                class="rec-tag"
              >{{ r.name }}</el-tag>
            </template>
            <span v-else class="cell-empty">—</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="statusType(row.progress)" size="small" effect="plain">
              {{ statusText(row.progress) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" align="center" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" link @click="openStudentDetail(row)">
              查看
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无学生学习记录" :image-size="80" />
        </template>
      </el-table>

      <el-pagination
        v-if="filteredStudents.length > pageSize"
        class="table-pagination"
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :page-sizes="[10, 20, 50]"
        :total="filteredStudents.length"
        layout="total, sizes, prev, pager, next"
      />
    </el-card>

    <!-- 第七层：学生详情 Drawer -->
    <el-drawer v-model="detailVisible" title="学生学习详情" direction="rtl" size="480px">
      <template v-if="detailStudent">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="学生">
            {{ detailStudent.student_name }}
            <span v-if="detailStudent.username && detailStudent.username !== detailStudent.student_name" class="detail-username">
              @{{ detailStudent.username }}
            </span>
          </el-descriptions-item>
          <el-descriptions-item label="当前课程">{{ currentCourseName }}</el-descriptions-item>
          <el-descriptions-item label="学习进度">
            <el-progress
              :percentage="detailStudent.progress"
              :color="progressColor(detailStudent.progress)"
              :stroke-width="10"
            />
          </el-descriptions-item>
          <el-descriptions-item label="已掌握数量">
            {{ detailStudent.mastered_count }} / {{ detailStudent.total_knowledge }}
          </el-descriptions-item>
          <el-descriptions-item label="未学习数量">{{ detailStudent.unmastered_count }}</el-descriptions-item>
          <el-descriptions-item label="当前学习知识点">
            {{ detailStudent.current_name || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="收藏数量">{{ detailStudent.favorite_count }}</el-descriptions-item>
        </el-descriptions>

        <el-divider content-position="left">推荐学习知识点</el-divider>
        <ul v-if="detailStudent.recommended && detailStudent.recommended.length" class="rec-list">
          <li v-for="r in detailStudent.recommended" :key="r.kp_id || r.name">
            <el-tag size="small" effect="plain">{{ r.category || '知识点' }}</el-tag>
            <span class="rec-name">{{ r.name }}</span>
            <div class="rec-reason">{{ r.reason }}</div>
          </li>
        </ul>
        <el-empty v-else description="暂无推荐" :image-size="60" />

        <div class="drawer-actions">
          <el-button type="primary" @click="goGraph">查看知识图谱</el-button>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import {
  DataAnalysis, Aim, Connection, Compass, ArrowRight, Search,
  Collection, Document, User, UserFilled, Upload, EditPen, ChatDotRound, Guide,
} from '@element-plus/icons-vue'
import { api } from '../api'
import { useAppStore } from '../stores/app'
import CourseSelector from '../components/CourseSelector.vue'

const router = useRouter()
const store = useAppStore()

const barChartRef = ref(null)
const radarChartRef = ref(null)
const relationChartRef = ref(null)
const progressDistRef = ref(null)

let barChart = null
let radarChart = null
let relationChart = null
let progressDistChart = null

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

// ===== 班级学习情况（教师教学监测） =====
const classCourseId = ref('')
const classData = ref(null)
const classLoading = ref(false)
const studentSearch = ref('')
const progressFilter = ref('')
const page = ref(1)
const pageSize = ref(10)
const sortState = ref({ prop: 'progress', order: 'ascending' })
const detailVisible = ref(false)
const detailStudent = ref(null)

const progressBins = [
  { value: '0', label: '0–20%' },
  { value: '20', label: '20–40%' },
  { value: '40', label: '40–60%' },
  { value: '60', label: '60–80%' },
  { value: '80', label: '80–100%' },
]

const currentCourseName = computed(() => {
  const c = store.courseById(classCourseId.value)
  return c ? c.course_name : `课程 #${classCourseId.value}`
})

const classKpis = computed(() => {
  const d = classData.value
  const total = d?.total_knowledge ?? 0
  const count = d?.student_count ?? 0
  const avg = d?.avg_progress ?? 0
  return [
    { label: '已开始学习人数', value: count, color: '#409eff' },
    { label: '平均学习进度', value: avg + '%', color: '#e6a23c' },
    { label: '课程知识点总数', value: total, color: '#67c23a' },
  ]
})

function binFor(progress) {
  if (progress >= 80) return 80
  if (progress >= 60) return 60
  if (progress >= 40) return 40
  if (progress >= 20) return 20
  return 0
}

const filteredStudents = computed(() => {
  let list = classData.value?.students || []
  const kw = studentSearch.value.trim().toLowerCase()
  if (kw) {
    list = list.filter(
      (s) =>
        (s.student_name || '').toLowerCase().includes(kw) ||
        (s.username || '').toLowerCase().includes(kw)
    )
  }
  if (progressFilter.value !== '') {
    const min = Number(progressFilter.value)
    list = list.filter((s) =>
      min === 80 ? s.progress >= 80 : s.progress >= min && s.progress < min + 20
    )
  }
  const { prop, order } = sortState.value
  const dir = order === 'descending' ? -1 : 1
  return [...list].sort((a, b) => {
    const av = prop === 'student_name' ? a.student_name : a[prop]
    const bv = prop === 'student_name' ? b.student_name : b[prop]
    if (av === bv) return 0
    return (av > bv ? 1 : -1) * dir
  })
})

const pagedStudents = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return filteredStudents.value.slice(start, start + pageSize.value)
})

function onSortChange({ prop, order }) {
  sortState.value = { prop: prop || 'progress', order: order || 'ascending' }
}

// 统计卡（7 张，真实数据 + 图标）
const statCards = computed(() => [
  { label: '课程总数', value: stats.value.course_count, color: '#409eff', icon: Collection },
  { label: '知识点数', value: stats.value.node_count, color: '#e6a23c', icon: DataAnalysis },
  { label: '关系数量', value: stats.value.edge_count, color: '#f56c6c', icon: Connection },
  { label: '概念节点', value: stats.value.concept_node_count, color: '#67c23a', icon: Aim },
  { label: '学生人数', value: stats.value.student_count, color: '#b37feb', icon: User },
  { label: '教师人数', value: stats.value.teacher_count, color: '#909399', icon: UserFilled },
  { label: '文档数', value: stats.value.document_count, color: '#ff85c0', icon: Document },
])

// 快速入口（角色区分，全部指向真实页面）
const quickLinks = computed(() =>
  store.role === 'teacher'
    ? [
        { label: '文档上传', desc: '上传课程资料，自动构建图谱', icon: Upload, path: '/teacher?tab=upload' },
        { label: '图谱预览', desc: '浏览已生成的课程知识图谱', icon: Search, path: '/teacher?tab=preview' },
        { label: '编辑图谱', desc: '手动增删知识点与关系', icon: EditPen, path: '/teacher?tab=edit' },
      ]
    : [
        { label: '图谱浏览', desc: '浏览课程知识图谱', icon: Compass, path: '/student?tab=browse' },
        { label: '智能问答', desc: '基于课程知识库提问', icon: ChatDotRound, path: '/student?tab=qa' },
        { label: '学习路径推荐', desc: '获取个性化学习建议', icon: Guide, path: '/student?tab=path' },
      ]
)

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

// ===== 班级学习数据 =====
async function loadClassData() {
  if (!classCourseId.value) {
    classData.value = null
    return
  }
  classLoading.value = true
  try {
    classData.value = await api.getTeacherStudentsProgress(classCourseId.value)
    await nextTick()
    renderProgressDist()
  } catch (e) {
    ElMessage.warning(`班级学习情况加载失败：${e.message}`)
    classData.value = null
  } finally {
    classLoading.value = false
  }
}

function onClassCourseChange() {
  classData.value = null
  studentSearch.value = ''
  progressFilter.value = ''
  page.value = 1
  if (classCourseId.value) loadClassData()
}

function openStudentDetail(row) {
  detailStudent.value = row
  detailVisible.value = true
}

function goGraph() {
  if (!classCourseId.value) return
  router.push({ path: '/teacher', query: { tab: 'preview', course_id: classCourseId.value } })
}

function progressColor(p) {
  if (p >= 80) return '#67c23a'
  if (p >= 40) return '#e6a23c'
  return '#f56c6c'
}

function statusText(p) {
  if (p >= 100) return '已完成'
  if (p > 0) return '进行中'
  return '未开始'
}

function statusType(p) {
  if (p >= 100) return 'success'
  if (p > 0) return 'primary'
  return 'info'
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

function renderProgressDist() {
  if (!progressDistRef.value) return
  if (!progressDistChart) progressDistChart = echarts.init(progressDistRef.value)
  const students = classData.value?.students || []
  const labels = progressBins.map((b) => b.label)
  const counts = progressBins.map((b) => {
    const min = Number(b.value)
    return students.filter((s) =>
      min === 80 ? s.progress >= 80 : s.progress >= min && s.progress < min + 20
    ).length
  })
  const option = {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: '3%', right: '4%', bottom: '8%', top: '10%', containLabel: true },
    xAxis: {
      type: 'category',
      data: labels,
      name: '学习进度',
      nameTextStyle: { color: '#909399', fontSize: 11 },
      axisLabel: { color: '#606266', fontSize: 11 },
      axisLine: { lineStyle: { color: '#dcdfe6' } },
    },
    yAxis: {
      type: 'value',
      name: '学生数',
      nameTextStyle: { color: '#909399', fontSize: 11 },
      minInterval: 1,
      axisLabel: { color: '#909399' },
      splitLine: { lineStyle: { color: '#f0f0f0', type: 'dashed' } },
    },
    series: [{
      type: 'bar',
      barWidth: '50%',
      itemStyle: {
        borderRadius: [6, 6, 0, 0],
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: '#409eff' },
          { offset: 1, color: '#79bbff' },
        ]),
      },
      data: counts,
    }],
  }
  if (counts.every((v) => v === 0)) option.graphic = emptyGraphic()
  progressDistChart.setOption(option)
}

function handleResize() {
  barChart?.resize()
  radarChart?.resize()
  relationChart?.resize()
  progressDistChart?.resize()
}

async function ensureCourse() {
  if (!store.courses.length) {
    try {
      await store.fetchCourses()
    } catch {
      /* 课程列表加载失败时保持空，选择器会显示加载状态 */
    }
  }
  if (!classCourseId.value && store.courses.length) {
    const cur = store.currentCourseId
    classCourseId.value =
      cur && store.courses.some((c) => String(c.course_id) === String(cur))
        ? String(cur)
        : String(store.courses[0].course_id)
  }
}

onMounted(async () => {
  await ensureCourse()
  await fetchStats()
  await nextTick()
  initBarChart()
  initRadarChart()
  initRelationChart()
  if (classCourseId.value) await loadClassData()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  barChart?.dispose()
  radarChart?.dispose()
  relationChart?.dispose()
  progressDistChart?.dispose()
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

/* ===== 第一层：课程选择 ===== */
.course-select-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
  padding: 12px 16px;
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}
.row-label {
  color: #606266;
  font-size: 14px;
  font-weight: 600;
}

/* ===== 第二层：统计卡 ===== */
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

/* ===== 第三层：班级学习情况 ===== */
.class-card {
  margin-bottom: 16px;
}
.class-note {
  margin-left: auto;
  font-size: 12px;
  font-weight: 400;
  color: #c0c4cc;
}
.class-summary {
  display: flex;
  gap: 16px;
  margin-bottom: 8px;
}
.class-kpi {
  flex: 1;
  padding: 14px 16px;
  border-radius: 10px;
  background: #fafbfc;
  border: 1px solid #f0f2f5;
}
.class-kpi-value {
  font-size: 24px;
  font-weight: 700;
  font-family: 'DIN Alternate', 'Helvetica Neue', sans-serif;
}
.class-kpi-label {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}
.dist-chart {
  height: 220px;
}

/* ===== 图表卡片 ===== */
.chart-row {
  margin-bottom: 16px;
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

/* ===== 学生学习情况表格 ===== */
.table-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.student-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}
.student-username {
  font-size: 12px;
  color: #909399;
  font-weight: 400;
}
.current-name {
  color: #409eff;
}
.cell-empty {
  color: #c0c4cc;
}
.rec-tag {
  margin: 2px 4px 2px 0;
}
.table-pagination {
  margin-top: 12px;
  justify-content: flex-end;
}

/* ===== 学生详情 Drawer ===== */
.detail-username {
  font-size: 12px;
  color: #909399;
  margin-left: 6px;
}
.rec-list {
  list-style: none;
  padding: 0;
  margin: 12px 0 0;
}
.rec-list li {
  padding: 8px 0;
  border-bottom: 1px dashed #e4e7ed;
}
.rec-name {
  font-weight: 600;
  margin-left: 8px;
}
.rec-reason {
  color: #909399;
  font-size: 12px;
  margin-top: 4px;
}
.drawer-actions {
  margin-top: 16px;
}

/* ===== 快速入口 ===== */
.quick-card {
  height: 100%;
}
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
  .class-summary {
    flex-direction: column;
  }
}
</style>
