<template>
  <div class="admin-dashboard" v-loading="loading">
    <PageHeader title="管理工作台" desc="平台用户、课程、资源与系统运行的总体情况">
      <template #extra>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </template>
    </PageHeader>

    <!-- 用户概览 + 治理概览 -->
    <el-row :gutter="14" class="block-row">
      <el-col :xs="24" :sm="24" :md="12">
        <el-card class="page-card" shadow="never">
          <template #header>
            <div class="card-head">
              <span>用户概览</span><span class="head-actions">
              <span class="stats-text">
                共 {{ fmt(counts.user_count) }} 人 · 启用 {{ fmt(counts.active_user_count) }} ·
                禁用 {{ fmt(counts.disabled_user_count) }}
              </span>
            <el-button class="head-jump" text type="primary" @click="router.push('/admin/users')">用户管理<el-icon><ArrowRight /></el-icon></el-button></span></div>
          </template>
          <div class="role-list">
            <div v-for="r in roles" :key="r.role" class="role-item">
              <span class="role-dot" :class="r.role" />
              <span class="role-name">{{ r.label }}</span>
              <div class="role-bar"><i :style="{ width: roleWidth(r.count) }" /></div>
              <span class="role-count">{{ fmt(r.count) }}</span>
            </div>
          </div>
          <el-divider content-position="left">最近注册</el-divider>
          <div v-if="recentUsers.length" class="mini-list">
            <div v-for="u in recentUsers" :key="u.user_id" class="mini-row">
              <span class="mini-main">{{ u.display_name || u.username }}</span>
              <el-tag size="small" :type="roleTagType(u.role)">{{ roleLabel(u.role) }}</el-tag>
              <el-tag v-if="!u.is_active" size="small" type="danger">已禁用</el-tag>
              <span class="mini-time">{{ fmtTime(u.created_at) }}</span>
            </div>
          </div>
          <div v-else class="empty-line">暂无数据</div>
        </el-card>
      </el-col>

      <el-col :xs="24" :sm="24" :md="12">
        <el-card class="page-card" shadow="never">
          <template #header>
            <div class="card-head">
              <span>课程与治理概览</span><span class="head-actions">
              <span class="stats-text">共 {{ fmt(counts.course_count) }} 门 · 在读成员 {{ fmt(counts.member_count) }} 人</span>
            <el-button class="head-jump" text type="primary" @click="router.push('/admin/courses')">课程管理<el-icon><ArrowRight /></el-icon></el-button><el-button class="head-jump" text type="primary" @click="router.push('/admin/governance')">课程治理<el-icon><ArrowRight /></el-icon></el-button></span></div>
          </template>
          <div class="gov-grid">
            <div class="gov-item">
              <div class="gov-value">{{ fmt(governance.normal) }}</div>
              <div class="gov-label">正常</div>
            </div>
            <div class="gov-item">
              <div class="gov-value warn">{{ fmt(governance.hidden) }}</div>
              <div class="gov-label">已下架</div>
            </div>
            <div class="gov-item">
              <div class="gov-value muted">{{ fmt(governance.archived) }}</div>
              <div class="gov-label">已归档</div>
            </div>
            <div class="gov-item">
              <div class="gov-value">{{ fmt(courseStatus.public) }}</div>
              <div class="gov-label">公开课程</div>
            </div>
            <div class="gov-item">
              <!-- 业务状态：课程自身的开放/关闭，与治理状态无关 -->
              <div class="gov-value">{{ fmt(courseStatus.disabled) }}</div>
              <div class="gov-label">业务已关闭</div>
            </div>
            <div class="gov-item">
              <!-- 组合结果：业务开放 且 治理正常，学生当前可发现/可加入 -->
              <div class="gov-value">{{ fmt(courseStatus.visible) }}</div>
              <div class="gov-label">学生可见</div>
            </div>
          </div>
          <el-divider content-position="left">最近创建课程</el-divider>
          <div v-if="recentCourses.length" class="mini-list">
            <div v-for="c in recentCourses" :key="c.course_id" class="mini-row">
              <span class="mini-main">{{ c.course_name }}</span>
              <span class="mini-sub">{{ c.teacher_name || '无负责人' }}</span>
              <el-tag v-if="c.governance_status !== 'normal'" size="small" type="warning">
                {{ c.governance_status === 'hidden' ? '已下架' : '已归档' }}
              </el-tag>
              <span class="mini-time">{{ fmtTime(c.created_at) }}</span>
            </div>
          </div>
          <div v-else class="empty-line">暂无数据</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 平台趋势（真实 created_at 聚合）+ 资源状态 -->
    <el-row :gutter="14" class="block-row">
      <el-col :xs="24" :sm="24" :md="12">
        <el-card class="page-card trend-card" shadow="never">
          <template #header>
            <div class="card-head">
              <span>平台趋势</span><span class="head-actions">
              <span class="stats-text">近 14 天</span>
              <el-radio-group v-model="trendView" size="small">
                <el-radio-button value="chart">图表</el-radio-button>
                <el-radio-button value="list">列表</el-radio-button>
              </el-radio-group>
              <el-button class="head-jump" text type="primary" @click="router.push('/admin/audit-logs')">审计日志<el-icon><ArrowRight /></el-icon></el-button></span></div>
          </template>
          <div class="trend-legend">
            <span class="lg-dot users" />新增用户
            <span class="lg-dot courses" />新增课程
            <span class="lg-dot documents" />新增文档
          </div>
          <div v-if="trendView === 'chart'" class="trend-chart">
            <div v-for="d in trend" :key="d.date" class="trend-col" :title="trendTip(d)">
              <div class="trend-bars">
                <i class="bar users" :style="{ height: barHeight(d.users) }" />
                <i class="bar courses" :style="{ height: barHeight(d.courses) }" />
                <i class="bar documents" :style="{ height: barHeight(d.documents) }" />
              </div>
              <div class="trend-day">{{ d.date.slice(5) }}</div>
            </div>
          </div>
          <el-table
            v-else
            :data="trend"
            size="small"
            max-height="360"
            class="trend-table"
            :default-sort="{ prop: 'date', order: 'descending' }"
          >
            <el-table-column prop="date" label="日期" width="130" sortable />
            <el-table-column prop="users" label="新增用户" align="center" sortable />
            <el-table-column prop="courses" label="新增课程" align="center" sortable />
            <el-table-column prop="documents" label="新增文档" align="center" sortable />
          </el-table>
          <div v-if="trendTotal === 0" class="empty-line">
            近 14 天没有新增用户 / 课程 / 文档（这 14 天确实是 0，不是统计失败）
          </div>
        </el-card>
      </el-col>

      <el-col :xs="24" :sm="24" :md="12">
        <el-card class="page-card" shadow="never">
          <template #header>
            <div class="card-head">
              <span>资源 · 图谱 · 题库</span><span class="head-actions">
              <span class="stats-text">文档 {{ fmt(counts.document_count) }} 份</span>
            <el-button class="head-jump" text type="primary" @click="router.push('/admin/resources')">资源管理<el-icon><ArrowRight /></el-icon></el-button></span></div>
          </template>
          <div class="extract-grid">
            <div class="extract-item">
              <div class="gov-value green">{{ fmt(extraction.success) }}</div>
              <div class="gov-label">抽取成功</div>
            </div>
            <div class="extract-item">
              <div class="gov-value warn">{{ fmt(extraction.processing) }}</div>
              <div class="gov-label">处理中</div>
            </div>
            <div class="extract-item">
              <div class="gov-value red">{{ fmt(extraction.failed) }}</div>
              <div class="gov-label">抽取失败</div>
            </div>
          </div>
          <el-divider content-position="left">知识图谱 · 题库</el-divider>
          <div class="extract-grid">
            <div class="extract-item">
              <div class="gov-value green">{{ graphAvailable ? fmt(counts.node_count) : '—' }}</div>
              <div class="gov-label">知识节点</div>
            </div>
            <div class="extract-item">
              <div class="gov-value green">{{ graphAvailable ? fmt(counts.edge_count) : '—' }}</div>
              <div class="gov-label">知识关系</div>
            </div>
            <div class="extract-item">
              <div class="gov-value">{{ fmt(counts.question_count) }}</div>
              <div class="gov-label">题目总数</div>
            </div>
          </div>
          <el-divider content-position="left">最近上传文档</el-divider>
          <div v-if="recentDocuments.length" class="mini-list">
            <div v-for="d in recentDocuments" :key="d.doc_id" class="mini-row">
              <span class="mini-main" :title="d.file_name">{{ d.file_name }}</span>
              <span class="mini-sub">{{ d.course_name || '—' }}</span>
              <el-tag size="small" :type="extractTagType(d)">{{ extractLabel(d) }}</el-tag>
              <span class="mini-time">{{ fmtTime(d.created_at) }}</span>
            </div>
          </div>
          <div v-else class="empty-line">暂无数据</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 系统状态摘要 -->
    <el-card class="page-card" shadow="never">
      <template #header>
        <div class="card-head">
          <span>系统状态</span>
          <el-button text type="primary" @click="router.push('/admin/system')">
            进入系统监控
          </el-button>
        </div>
      </template>
      <div class="comp-row">
        <div v-for="c in components" :key="c.key" class="comp-item" :class="{ down: !c.available }">
          <span class="comp-dot" :class="c.available ? 'ok' : 'bad'" />
          <div class="comp-text">
            <div class="comp-label">{{ c.label }}</div>
            <div class="comp-detail" :title="c.detail">{{ c.detail }}</div>
          </div>
        </div>
      </div>
      <div v-if="!components.length" class="empty-line">系统状态未检测</div>
      <el-divider content-position="left">最近管理操作</el-divider>
      <div v-if="recentAudits.length" class="mini-list">
        <div v-for="a in recentAudits" :key="a.log_id" class="mini-row">
          <span class="mini-main">{{ actionLabel(a.action) }}</span>
          <span class="mini-sub">{{ a.detail || '—' }}</span>
          <el-tag size="small" :type="a.result === 'success' ? 'info' : 'danger'">
            {{ a.result === 'success' ? '成功' : '失败' }}
          </el-tag>
          <span class="mini-time">{{ fmtTime(a.created_at) }}</span>
        </div>
      </div>
      <div v-else class="empty-line">
        暂无审计记录（管理员的关键操作都会记录在这里）
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ArrowRight,
} from '@element-plus/icons-vue'
import PageHeader from '../../components/PageHeader.vue'
import { api } from '../../api'

const router = useRouter()
const loading = ref(false)

const EMPTY = {
  counts: {}, role_distribution: [], governance: {}, course_status: {}, extraction: {},
  document_status: {}, trend: [], recent_users: [], recent_courses: [],
  recent_documents: [], recent_audits: [], components: [],
}
const data = ref({ ...EMPTY })
const components = ref([])
const trendView = ref(localStorage.getItem('admin-trend-view') || 'chart')
watch(trendView, (v) => localStorage.setItem('admin-trend-view', v))

const counts = computed(() => data.value.counts || {})
const roles = computed(() => data.value.role_distribution || [])
const governance = computed(() => data.value.governance || {})
const courseStatus = computed(() => data.value.course_status || {})
const extraction = computed(() => data.value.extraction || {})
const trend = computed(() => data.value.trend || [])
const recentUsers = computed(() => data.value.recent_users || [])
const recentCourses = computed(() => data.value.recent_courses || [])
const recentDocuments = computed(() => data.value.recent_documents || [])
const recentAudits = computed(() => data.value.recent_audits || [])

const fmt = (v) => (v === null || v === undefined ? '—' : Number(v).toLocaleString())
const fmtTime = (t) => (t ? String(t).slice(5, 16) : '—')
function roleLabel(r) { return { admin: '管理员', teacher: '教师', student: '学生' }[r] || r }
function roleTagType(r) {
  return { admin: 'primary', teacher: 'warning', student: 'success' }[r] || 'info'
}

const graphAvailable = computed(() => !!data.value.graph_available)

const maxRole = computed(() => Math.max(1, ...roles.value.map((r) => r.count || 0)))
function roleWidth(n) {
  return `${Math.max(((n || 0) / maxRole.value) * 100, n ? 3 : 0)}%`
}

const trendTotal = computed(() =>
  trend.value.reduce((s, d) => s + d.users + d.courses + d.documents, 0))
const maxTrend = computed(() =>
  Math.max(1, ...trend.value.map((d) => Math.max(d.users, d.courses, d.documents))))
function barHeight(n) {
  return n ? `${Math.max((n / maxTrend.value) * 100, 8)}%` : '0'
}
function trendTip(d) {
  return `${d.date}  新增用户 ${d.users} · 课程 ${d.courses} · 文档 ${d.documents}`
}

function extractLabel(d) {
  if (d.parse_status === 'FAILED' || d.extract_status === 'FAILED') return '失败'
  if (d.parse_status === 'PARSING' || d.extract_status === 'EXTRACTING') return '处理中'
  if (d.parse_status === 'PARSED' && d.extract_status === 'COMPLETED') return '已完成'
  return '待处理'
}
function extractTagType(d) {
  const l = extractLabel(d)
  return { 失败: 'danger', 处理中: 'warning', 已完成: 'success', 待处理: 'info' }[l] || 'info'
}

const ACTION_LABELS = {
  'admin.login': '管理员登录',
  'user.update': '编辑用户资料',
  'user.enable': '启用账号',
  'user.disable': '禁用账号',
  'user.role_change': '修改角色',
  'user.reset_password': '重置密码',
  'user.delete': '删除用户',
  'course.hide': '下架/关闭课程',
  'course.restore': '恢复/重新开放课程',
  'course.archive': '归档课程',
  'course.unarchive': '取消归档',
  'course.transfer': '转移课程负责人',
  'course.delete': '删除课程',
  'resource.delete': '删除文档资源',
  'settings.update': '修改平台设置',
}
const actionLabel = (a) => ACTION_LABELS[a] || a

async function load() {
  loading.value = true
  try {
    const d = await api.adminDashboard()
    data.value = { ...EMPTY, ...d }
    // 系统状态摘要复用系统监控接口（组件探测是实时行为，不适合塞进工作台的主查询里拖慢首屏）
    try {
      const sys = await api.adminSystem()
      components.value = sys.components || []
    } catch {
      components.value = []
    }
  } catch (e) {
    ElMessage.error(e?.message || '加载工作台失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.tile-row { margin-bottom: 0; }
.tile-row .el-col { margin-bottom: 14px; display: flex; }
.tile-row .el-col > * { width: 100%; }
.block-row { margin-bottom: 0; }
.block-row .el-col { margin-bottom: 16px; display: flex; }
.block-row .el-col > .page-card { width: 100%; height: 100%; margin-bottom: 0; box-sizing: border-box; }

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-weight: 600;
  flex-wrap: nowrap;
  min-width: 0;
  min-height: 22px;
}

.card-head > span:first-child {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.head-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: nowrap;
  justify-content: flex-end;
  flex-shrink: 0;
}

.page-card {
  display: flex;
  flex-direction: column;
}

.stats-text {
  white-space: nowrap;
}
.head-jump {
  height: auto;
  padding: 0 !important;
  font-weight: 500;
}
.head-jump .el-icon {
  margin-left: 2px;
}

.ov-section {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin: 2px 0 12px;
}
.ov-section-title {
  position: relative;
  padding-left: 11px;
  font-size: 15px;
  font-weight: 700;
  color: var(--text-primary);
}
.ov-section-title::before {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  width: 3px;
  height: 15px;
  border-radius: 2px;
  transform: translateY(-50%);
  background: var(--gradient-brand);
}
.ov-section-hint {
  font-size: 12px;
  color: var(--color-text-muted);
}

/* ---- 角色分布 ---- */
.role-list { display: flex; flex-direction: column; gap: 12px; }
.role-item { display: flex; align-items: center; gap: 10px; }
.role-dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }
.role-dot.admin { background: #8b5cf6; }
.role-dot.teacher { background: #f5a623; }
.role-dot.student { background: #18b87a; }
.role-name { width: 62px; font-size: 13px; color: var(--text-regular); flex-shrink: 0; }
.role-bar {
  flex: 1;
  height: 8px;
  border-radius: 4px;
  background: var(--bg-soft);
  overflow: hidden;
}
.role-bar i {
  display: block;
  height: 100%;
  border-radius: 4px;
  background: var(--gradient-brand);
  transition: width .4s cubic-bezier(.22,.8,.36,1);
}
.role-count {
  width: 52px;
  text-align: right;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  font-family: var(--font-family-number);
}

/* ---- 治理概览 ---- */
.gov-grid, .extract-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}
.gov-item, .extract-item {
  padding: 12px 10px;
  border-radius: var(--radius-md);
  background: var(--bg-soft);
  text-align: center;
}
.gov-value {
  font-family: var(--font-family-number);
  font-size: 20px;
  font-weight: 700;
  color: var(--text-primary);
}
.gov-value.warn { color: var(--color-warning); }
.gov-value.red { color: var(--color-danger); }
.gov-value.green { color: var(--color-success); }
.gov-value.muted { color: var(--color-text-muted); }
.gov-label { margin-top: 3px; font-size: 12px; color: var(--color-text-secondary); }

/* ---- 迷你列表 ---- */
.mini-list { display: flex; flex-direction: column; gap: 6px; }
.mini-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-radius: var(--radius-sm);
  background: var(--bg-soft);
  font-size: 13px;
}
.mini-main {
  flex: 1;
  min-width: 0;
  color: var(--text-primary);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mini-sub {
  max-width: 40%;
  font-size: 12px;
  color: var(--color-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mini-time {
  font-size: 11.5px;
  color: var(--color-text-muted);
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}
.empty-line {
  padding: 14px 0;
  text-align: center;
  font-size: 12.5px;
  color: var(--color-text-muted);
}

/* ---- 趋势 ---- */
.trend-card { display: flex; flex-direction: column; }
.trend-card :deep(.el-card__body) { flex: 1; display: flex; flex-direction: column; justify-content: center; }
.trend-table { flex: 1; }
.trend-legend {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--color-text-secondary);
  margin-bottom: 8px;
}
.lg-dot { width: 8px; height: 8px; border-radius: 2px; margin-left: 10px; }
.lg-dot:first-child { margin-left: 0; }
.lg-dot.users, .bar.users { background: #5b8def; }
.lg-dot.courses, .bar.courses { background: #8b5cf6; }
.lg-dot.documents, .bar.documents { background: #18b87a; }

.trend-chart {
  display: flex;
  align-items: flex-end;
  gap: 4px;
  flex: none;
  height: 180px;
  padding-top: 6px;
}
.trend-col {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  height: 100%;
}
.trend-bars {
  flex: 1;
  width: 100%;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  gap: 2px;
}
.bar {
  flex: 1 1 0;
  max-width: 14px;
  border-radius: 4px 4px 0 0;
  transition: height .4s cubic-bezier(.22,.8,.36,1);
  min-height: 0;
}
.trend-day {
  margin-top: 6px;
  font-size: 12px;
  font-weight: 500;
  color: var(--color-text-secondary);
  white-space: nowrap;
}

/* ---- 系统状态 ---- */
.comp-row {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 10px;
}
.comp-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  background: var(--bg-soft);
  border: 1px solid var(--border-light);
}
.comp-item.down { background: rgba(245, 71, 93, .05); border-color: rgba(245, 71, 93, .2); }
.comp-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-top: 5px;
  flex-shrink: 0;
}
.comp-dot.ok { background: #3ddc97; box-shadow: 0 0 7px rgba(61,220,151,.8); }
.comp-dot.bad { background: #ff6b81; box-shadow: 0 0 7px rgba(255,107,129,.8); }
.comp-text { min-width: 0; }
.comp-label { font-size: 13px; font-weight: 600; color: var(--text-primary); }
.comp-detail {
  margin-top: 2px;
  font-size: 11.5px;
  color: var(--color-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 768px) {
  .gov-grid, .extract-grid { grid-template-columns: repeat(2, 1fr); }
}
</style>
