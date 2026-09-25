<template>
  <div class="admin-system">
    <PageHeader title="系统监控" desc="各组件状态为真实探测结果；指标为当前进程内存计数，不做估算与虚构">
      <template #extra>
        <span class="stats-text" v-if="metrics.uptime_seconds !== undefined">
          已运行 {{ fmtUptime(metrics.uptime_seconds) }}
        </span>
        <el-button :loading="loading" @click="load">重新检测</el-button>
      </template>
    </PageHeader>

    <!-- 组件状态 -->
    <el-card class="page-card" shadow="never">
      <template #header>
        <div class="card-header">
          <span class="ch-title">组件状态</span>
          <span class="ch-sub">
            共 {{ components.length }} 项，正常 {{ okCount }} 项
            <template v-if="downComponents.length">，异常 {{ downComponents.length }} 项</template>
          </span>
        </div>
      </template>

      <div class="comp-grid">
        <div
          v-for="c in components"
          :key="c.key"
          class="comp-item"
          :class="c.available ? 'ok' : 'bad'"
        >
          <div class="ci-head">
            <span class="ci-dot"></span>
            <span class="ci-label">{{ c.label }}</span>
            <el-tag size="small" :type="c.available ? 'success' : 'danger'" effect="light">
              {{ c.available ? '正常' : '不可用' }}
            </el-tag>
          </div>
          <div class="ci-detail">{{ c.detail }}</div>
        </div>
      </div>

      <div v-if="!components.length" class="empty-line">尚未获取到组件状态</div>
    </el-card>

    <!-- 运行指标 -->
    <el-card class="page-card" shadow="never">
      <template #header>
        <div class="card-header">
          <span class="ch-title">运行指标</span>
          <span class="ch-sub">{{ metrics.scope_note }}</span>
        </div>
      </template>

      <div class="metric-row">
        <MetricTile
          label="API 请求数"
          :value="req.total"
          :icon="Connection"
          tone="blue"
          :hint="`平均 ${req.avg_ms} ms`"
        />
        <MetricTile
          label="请求错误数"
          :value="req.errors"
          :icon="WarningFilled"
          tone="amber"
          :hint="`其中 5xx ${req.server_errors}`"
        />
        <MetricTile
          label="LLM 调用次数"
          :value="llm.total_calls"
          :icon="MagicStick"
          tone="violet"
          :hint="`平均 ${llm.avg_ms} ms`"
        />
        <MetricTile
          label="LLM 调用成功"
          :value="llm.success"
          :icon="CircleCheck"
          tone="green"
        />
        <MetricTile
          label="LLM 调用失败"
          :value="llm.failed"
          :icon="CircleClose"
          tone="red"
        />
        <MetricTile
          label="抽取任务数"
          :value="extraction.total"
          :icon="Files"
          tone="slate"
          :hint="`失败 ${extraction.failed ?? 0}`"
        />
      </div>

      <div class="scope-note" v-if="llm.scopes?.length">
        <div class="sn-title">LLM 调用分场景统计</div>
        <el-table :data="llm.scopes" size="small">
          <el-table-column prop="label" label="场景" min-width="160" />
          <el-table-column label="调用" width="90">
            <template #default="{ row }"><span class="num">{{ row.calls }}</span></template>
          </el-table-column>
          <el-table-column label="成功" width="90">
            <template #default="{ row }"><span class="num">{{ row.success }}</span></template>
          </el-table-column>
          <el-table-column label="失败" width="90">
            <template #default="{ row }">
              <span class="num" :class="{ bad: row.failed > 0 }">{{ row.failed }}</span>
            </template>
          </el-table-column>
          <el-table-column label="平均耗时" width="110">
            <template #default="{ row }"><span class="num">{{ row.avg_ms }} ms</span></template>
          </el-table-column>
          <template #empty><div class="empty-line">本次启动以来还没有 LLM 调用</div></template>
        </el-table>
        <div class="untracked" v-if="llm.untracked?.length">
          以下 LLM 调用点尚未接入计数（不计入上面的数字）：{{ llm.untracked.join('、') }}
        </div>
      </div>

      <div class="scope-note" v-if="topPaths.length">
        <div class="sn-title">请求量最高的接口</div>
        <el-table :data="topPaths" size="small">
          <el-table-column prop="path" label="接口" min-width="260" show-overflow-tooltip />
          <el-table-column label="请求" width="100">
            <template #default="{ row }"><span class="num">{{ row.count }}</span></template>
          </el-table-column>
          <el-table-column label="错误" width="90">
            <template #default="{ row }">
              <span class="num" :class="{ bad: row.errors > 0 }">{{ row.errors }}</span>
            </template>
          </el-table-column>
          <el-table-column label="平均耗时" width="120">
            <template #default="{ row }"><span class="num">{{ row.avg_ms }} ms</span></template>
          </el-table-column>
        </el-table>
      </div>

      <div v-if="!req.total" class="empty-line">
        本次服务启动以来还没有记录到 HTTP 请求（指标从进程启动时开始计数）
      </div>
    </el-card>

    <!-- 资源统计 -->
    <div class="two-col">
      <el-card class="page-card" shadow="never">
        <template #header>
          <div class="card-header"><span class="ch-title">文档解析 / 抽取状态</span></div>
        </template>
        <div class="status-block">
          <div class="sb-title">解析状态</div>
          <div class="sb-list">
            <div v-for="(v, k) in docStatus.parse" :key="k" class="sb-item">
              <el-tag size="small" :type="statusTag(k)" effect="plain">{{ k }}</el-tag>
              <span class="num">{{ v }}</span>
            </div>
            <div v-if="!Object.keys(docStatus.parse || {}).length" class="empty-line">暂无文档</div>
          </div>
        </div>
        <div class="status-block">
          <div class="sb-title">抽取状态</div>
          <div class="sb-list">
            <div v-for="(v, k) in docStatus.extract" :key="k" class="sb-item">
              <el-tag size="small" :type="statusTag(k)" effect="plain">{{ k }}</el-tag>
              <span class="num">{{ v }}</span>
            </div>
            <div v-if="!Object.keys(docStatus.extract || {}).length" class="empty-line">暂无文档</div>
          </div>
        </div>
      </el-card>

      <el-card class="page-card" shadow="never">
        <template #header>
          <div class="card-header"><span class="ch-title">数据表行数</span></div>
        </template>
        <el-table :data="tableRows" size="small" max-height="380">
          <el-table-column prop="table" label="数据表" min-width="170" />
          <el-table-column label="行数" width="110" align="right">
            <template #default="{ row }">
              <!-- count 为 null 表示该表查询失败（可能不存在），不是 0 -->
              <span class="num" :class="{ bad: row.count === null }">
                {{ row.count === null ? '读取失败' : row.count }}
              </span>
            </template>
          </el-table-column>
          <template #empty><div class="empty-line">暂无数据</div></template>
        </el-table>
      </el-card>
    </div>

    <!-- 平台计数 -->
    <el-card class="page-card" shadow="never">
      <template #header>
        <div class="card-header"><span class="ch-title">平台数据规模</span></div>
      </template>
      <div class="count-grid">
        <div v-for="c in countCells" :key="c.label" class="count-cell">
          <div class="cc-value">{{ c.value ?? '—' }}</div>
          <div class="cc-label">{{ c.label }}</div>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Connection, WarningFilled, CircleCheck, CircleClose, Files, MagicStick,
} from '@element-plus/icons-vue'
import PageHeader from '../../components/PageHeader.vue'
import MetricTile from '../../components/admin/MetricTile.vue'
import { api } from '../../api'

const loading = ref(false)
const components = ref([])
const metrics = ref({ requests: {}, llm: {} })
const topPaths = ref([])
const extraction = ref({})
const docStatus = ref({})
const counts = ref({})
const tableRows = ref([])

const req = computed(() => ({
  total: metrics.value.requests?.total ?? 0,
  errors: metrics.value.requests?.errors ?? 0,
  server_errors: metrics.value.requests?.server_errors ?? 0,
  avg_ms: metrics.value.requests?.avg_ms ?? 0,
}))

const llm = computed(() => ({
  total_calls: metrics.value.llm?.total_calls ?? 0,
  success: metrics.value.llm?.success ?? 0,
  failed: metrics.value.llm?.failed ?? 0,
  avg_ms: metrics.value.llm?.avg_ms ?? 0,
  scopes: metrics.value.llm?.scopes || [],
  untracked: metrics.value.llm?.untracked || [],
}))

const okCount = computed(() => components.value.filter((c) => c.available).length)
const downComponents = computed(() => components.value.filter((c) => !c.available))

const countCells = computed(() => {
  const c = counts.value || {}
  return [
    { label: '用户总数', value: c.user_count },
    { label: '教师', value: c.teacher_count },
    { label: '学生', value: c.student_count },
    { label: '管理员', value: c.admin_count },
    { label: '启用账号', value: c.active_user_count },
    { label: '禁用账号', value: c.disabled_user_count },
    { label: '课程总数', value: c.course_count },
    { label: '公开课程', value: c.public_course_count },
    { label: '文档总数', value: c.document_count },
    { label: '已通过成员', value: c.member_count },
    { label: '题目总数', value: c.question_count },
  ]
})

const statusTag = (s) =>
  ({ COMPLETED: 'success', PARSED: 'success', PARSING: 'warning', EXTRACTING: 'warning', FAILED: 'danger' }[s] || 'info')

function fmtUptime(sec) {
  const s = Number(sec || 0)
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (d) return `${d} 天 ${h} 小时`
  if (h) return `${h} 小时 ${m} 分`
  return `${m} 分 ${s % 60} 秒`
}

async function load() {
  loading.value = true
  try {
    const data = await api.adminSystem()
    components.value = data.components || []
    metrics.value = data.metrics || { requests: {}, llm: {} }
    topPaths.value = data.metrics?.top_paths || []
    extraction.value = data.extraction || {}
    docStatus.value = data.document_status || {}
    counts.value = data.counts || {}
    tableRows.value = data.table_rows || []
  } catch (e) {
    ElMessage.error(e?.message || '系统状态检测失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.card-header { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.ch-title { font-size: 14px; font-weight: 700; color: var(--text-primary); }
.ch-sub { font-size: 12px; color: var(--color-text-secondary); }
.num { font-family: var(--font-family-number); font-weight: 600; color: var(--text-primary); }
.num.bad { color: #f5475d; }
.empty-line { padding: 16px 0; text-align: center; color: var(--color-text-muted); font-size: 13px; }

/* ---- 组件状态 ---- */
.comp-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.comp-item {
  padding: 12px 14px;
  border-radius: var(--radius-md);
  background: var(--bg-soft);
  border: 1px solid var(--border-light);
  border-left: 3px solid var(--color-text-muted);
}
.comp-item.ok { border-left-color: #18b87a; }
.comp-item.bad { border-left-color: #f5475d; }
.ci-head { display: flex; align-items: center; gap: 8px; }
.ci-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--color-text-muted); flex-shrink: 0; }
.comp-item.ok .ci-dot { background: #18b87a; box-shadow: 0 0 0 3px rgba(24,184,122,.16); }
.comp-item.bad .ci-dot { background: #f5475d; box-shadow: 0 0 0 3px rgba(245,71,93,.16); }
.ci-label { flex: 1; min-width: 0; font-size: 13.5px; font-weight: 600; color: var(--text-primary); }
.ci-detail {
  margin-top: 6px;
  font-size: 11.5px;
  color: var(--color-text-secondary);
  line-height: 1.5;
  word-break: break-all;
}

/* ---- 指标 ---- */
.metric-row {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 12px;
}
.scope-note { margin-top: 18px; }
.sn-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 8px;
}
.untracked {
  margin-top: 8px;
  font-size: 12px;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

/* ---- 两栏 ---- */
.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}
.status-block + .status-block { margin-top: 14px; }
.sb-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--color-text-secondary);
  margin-bottom: 8px;
}
.sb-list { display: flex; flex-wrap: wrap; gap: 10px; }
.sb-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: var(--radius-sm);
  background: var(--bg-soft);
}

/* ---- 计数 ---- */
.count-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; }
.count-cell {
  padding: 10px 8px;
  border-radius: var(--radius-sm);
  background: var(--bg-soft);
  text-align: center;
}
.cc-value {
  font-family: var(--font-family-number);
  font-size: 19px;
  font-weight: 700;
  color: var(--text-primary);
}
.cc-label { margin-top: 2px; font-size: 11.5px; color: var(--color-text-secondary); }

@media (max-width: 1600px) {
  .metric-row { grid-template-columns: repeat(3, 1fr); }
  .count-grid { grid-template-columns: repeat(4, 1fr); }
}
@media (max-width: 1280px) {
  .comp-grid { grid-template-columns: repeat(2, 1fr); }
  .two-col { grid-template-columns: 1fr; }
}
@media (max-width: 1024px) {
  .metric-row { grid-template-columns: repeat(2, 1fr); }
  .count-grid { grid-template-columns: repeat(3, 1fr); }
}
</style>
