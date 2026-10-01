<template>
  <div class="admin-fusion">
    <PageHeader title="知识融合" desc="同一文档内重复知识点的识别、消歧、融合与撤销（按文档隔离）">
      <template #extra>
        <el-button :loading="loading" @click="reload">刷新</el-button>
      </template>
    </PageHeader>

    <!-- ============ 作用域：必须先锁定一个文档 ============ -->
    <el-card class="page-card scope-card" shadow="never">
      <div class="scope-row">
        <span class="scope-label">融合范围</span>
        <el-select
          v-model="courseId"
          placeholder="选择课程"
          filterable
          clearable
          style="width: 240px"
          @change="onCourseChange"
        >
          <el-option v-for="c in courses" :key="c.course_id" :label="c.course_name" :value="c.course_id" />
        </el-select>
        <el-select
          v-model="documentId"
          placeholder="选择文档"
          filterable
          clearable
          :disabled="!courseId"
          style="width: 320px"
          @change="onDocumentChange"
        >
          <el-option v-for="d in documents" :key="d.doc_id" :label="d.file_name" :value="d.doc_id">
            <span>{{ d.file_name }}</span>
            <span class="opt-sub">节点 {{ d.entity_count || 0 }}</span>
          </el-option>
        </el-select>
        <el-tag v-if="scope.docId" size="small" effect="plain" type="info">
          文档 ID {{ scope.docId }} · 课程 {{ scope.courseId }}
        </el-tag>
        <span class="dim">融合只在所选文档内部进行，不会跨文档 / 跨课程合并</span>
      </div>
      <el-alert
        v-if="scopeReady && !overview.graph_available"
        class="scope-alert"
        type="error"
        :closable="false"
        show-icon
        :title="overview.graph_error || '知识图谱不可用，无法读取该文档的节点'"
      />
      <el-alert
        v-else-if="scopeReady && !overview.embedding_available"
        class="scope-alert"
        type="info"
        :closable="false"
        show-icon
        title="未配置 Embedding 服务：语义相似度层关闭，融合仍可基于名称、别名、类别与上下文进行。"
      />
    </el-card>

    <!-- ============ 统计卡 ============ -->
    <div v-if="scopeReady" class="metric-row">
      <MetricTile label="知识节点" :value="overview.graph?.raw_node_count" :icon="Share" tone="blue"
                  :hint="`融合后显示 ${overview.graph?.folded_node_count ?? '—'} 个`" />
      <MetricTile label="知识关系" :value="overview.graph?.raw_edge_count" :icon="Connection" tone="violet"
                  :hint="`融合后显示 ${overview.graph?.folded_edge_count ?? '—'} 条`" />
      <MetricTile label="候选对" :value="overview.candidates?.total" :icon="Search" tone="slate" />
      <MetricTile label="已融合" :value="overview.fused_active" :icon="CircleCheck" tone="green"
                  :hint="`已撤销 ${overview.fused_revoked ?? 0} 条`" />
      <MetricTile label="待审核" :value="pendingCount" :icon="Clock" tone="amber" />
      <MetricTile label="待处理冲突" :value="overview.open_conflicts" :icon="WarningFilled" tone="red" />
    </div>

    <el-card v-if="scopeReady" class="page-card" shadow="never">
      <el-tabs v-model="tab">
        <!-- ============ 候选 ============ -->
        <el-tab-pane label="候选对" name="candidates">
          <div class="toolbar">
            <el-button type="primary" :loading="scanning" @click="doScan(false)">预览扫描（不写入）</el-button>
            <el-button :loading="scanning" @click="doScan(true)">扫描并存为候选</el-button>
            <el-button type="success" :disabled="!canApply" :loading="applying" @click="doApplyPreview">
              应用融合
            </el-button>
            <el-select v-model="candQuery.decision" placeholder="决策" clearable style="width: 130px" @change="loadCandidates">
              <el-option v-for="d in ['SAME', 'DIFFERENT', 'UNCERTAIN']" :key="d" :label="d" :value="d" />
            </el-select>
            <el-select v-model="candQuery.status" placeholder="状态" clearable style="width: 140px" @change="loadCandidates">
              <el-option v-for="s in candidateStatuses" :key="s.v" :label="s.t" :value="s.v" />
            </el-select>
            <el-input v-model="candQuery.keyword" placeholder="实体名关键字" clearable style="width: 180px"
                      @keyup.enter="loadCandidates" @clear="loadCandidates" />
            <span class="stats-text">共 {{ candTotal }} 条候选</span>
          </div>

          <el-table :data="candidates" v-loading="candLoading" stripe row-key="candidate_id">
            <el-table-column label="实体 A（拟折叠）" min-width="170">
              <template #default="{ row }">
                <div class="fname">{{ row.source_name }}</div>
                <div class="fsub">{{ row.source_category || '—' }}</div>
              </template>
            </el-table-column>
            <el-table-column label="" width="36">
              <template #default><el-icon class="arrow"><Right /></el-icon></template>
            </el-table-column>
            <el-table-column label="实体 B（拟保留）" min-width="170">
              <template #default="{ row }">
                <div class="fname">{{ row.target_name }}</div>
                <div class="fsub">{{ row.target_category || '—' }}</div>
              </template>
            </el-table-column>
            <el-table-column label="相似度" width="100" sortable :sort-method="(a, b) => a.score - b.score">
              <template #default="{ row }">
                <el-tooltip :content="scoreTip(row)" placement="top">
                  <span class="num">{{ (row.score ?? 0).toFixed(3) }}</span>
                </el-tooltip>
              </template>
            </el-table-column>
            <el-table-column label="决策" width="120">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="decisionTag(row.decision)">
                  {{ decisionLabel(row.decision) }}
                </el-tag>
                <div class="fsub">{{ row.decision_source === 'LLM' ? '模型判断' : '规则' }}</div>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="statusTag(row.status)">
                  {{ statusLabel(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="210" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="openDetail(row)">详情</el-button>
                <el-button link type="success" @click="review(row, 'accept')">通过</el-button>
                <el-button link type="warning" @click="review(row, 'defer')">暂缓</el-button>
                <el-button link type="danger" @click="review(row, 'reject')">驳回</el-button>
              </template>
            </el-table-column>
            <template #empty>
              <div class="empty-line">
                还没有候选。<el-button link type="primary" @click="doScan(false)">先做一次预览扫描</el-button>
              </div>
            </template>
          </el-table>

          <div class="pager">
            <el-pagination
              background
              layout="prev, pager, next, sizes"
              :total="candTotal"
              :current-page="candQuery.page"
              :page-size="candQuery.page_size"
              :page-sizes="[20, 50, 100]"
              @current-change="(p) => { candQuery.page = p; loadCandidates() }"
              @size-change="(s) => { candQuery.page_size = s; candQuery.page = 1; loadCandidates() }"
            />
          </div>
        </el-tab-pane>

        <!-- ============ 已应用 ============ -->
        <el-tab-pane :label="`已应用（${overview.fused_active ?? 0}）`" name="maps">
          <div class="toolbar">
            <el-button type="warning" :disabled="!activeMaps.length" @click="undoRun" :loading="applying">
              整批撤销最近一批
            </el-button>
            <span class="dim">撤销只把融合记录置为「已撤销」，**不删除任何节点**，图谱立即恢复原状。</span>
          </div>
          <el-table :data="maps" v-loading="mapLoading" stripe row-key="fusion_id">
            <el-table-column label="被折叠" min-width="160">
              <template #default="{ row }">
                <div class="fname">{{ row.source_name || row.source_kp_id }}</div>
                <div class="fsub">{{ row.source_kp_id }}</div>
              </template>
            </el-table-column>
            <el-table-column label="" width="36">
              <template #default><el-icon class="arrow"><Right /></el-icon></template>
            </el-table-column>
            <el-table-column label="保留为规范节点" min-width="160">
              <template #default="{ row }">
                <div class="fname">{{ row.target_name || row.target_kp_id }}</div>
                <div class="fsub">{{ row.target_kp_id }}</div>
              </template>
            </el-table-column>
            <el-table-column label="选择理由" min-width="220">
              <template #default="{ row }"><span class="dim">{{ row.reason || '—' }}</span></template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="row.status === 'ACTIVE' ? 'success' : 'info'">
                  {{ row.status === 'ACTIVE' ? '生效中' : '已撤销' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作人" width="110">
              <template #default="{ row }"><span class="dim">{{ row.operator_name || '—' }}</span></template>
            </el-table-column>
            <el-table-column label="操作" width="100" fixed="right">
              <template #default="{ row }">
                <el-button v-if="row.status === 'ACTIVE'" link type="danger" @click="revoke(row)">撤销</el-button>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <template #empty><div class="empty-line">该文档还没有融合记录</div></template>
          </el-table>
        </el-tab-pane>

        <!-- ============ 冲突 ============ -->
        <el-tab-pane :label="`冲突（${overview.open_conflicts ?? 0}）`" name="conflicts">
          <el-table :data="conflicts" v-loading="conflictLoading" stripe row-key="conflict_id">
            <el-table-column label="类型" width="150">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" type="danger">{{ conflictLabel(row.conflict_type) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="涉及实体" min-width="220">
              <template #default="{ row }">
                <span class="fsub">{{ row.source_kp_id }} → {{ row.target_kp_id }}</span>
              </template>
            </el-table-column>
            <el-table-column label="说明" min-width="300">
              <template #default="{ row }"><span class="dim">{{ row.detail || '—' }}</span></template>
            </el-table-column>
            <el-table-column prop="created_at" label="时间" width="160" />
            <template #empty><div class="empty-line">没有待处理的冲突</div></template>
          </el-table>
        </el-tab-pane>

        <!-- ============ 批次 ============ -->
        <el-tab-pane :label="`批次（${overview.run_count ?? 0}）`" name="runs">
          <el-table :data="runs" v-loading="runLoading" stripe row-key="run_id">
            <el-table-column prop="run_id" label="批次" min-width="180" />
            <el-table-column label="模式" width="90">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">{{ row.mode === 'APPLY' ? '实际应用' : '预演' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-tag size="small" effect="plain"
                        :type="row.status === 'COMPLETED' ? 'success' : (row.status === 'REVOKED' ? 'info' : 'danger')">
                  {{ runStatusLabel(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="候选 / 已融合" width="140">
              <template #default="{ row }">
                <span class="num">{{ row.total_candidates }}</span> /
                <span class="num">{{ row.n_applied }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="operator_name" label="操作人" width="110" />
            <el-table-column prop="started_at" label="开始时间" width="160" />
            <template #empty><div class="empty-line">还没有融合批次</div></template>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-card v-else class="page-card">
      <div class="empty-line big">请先在上方选择「课程 → 文档」，再查看或执行该文档的知识融合。</div>
    </el-card>

    <!-- ============ 候选详情 ============ -->
    <el-drawer v-model="detailVisible" title="候选详情" size="640px" :destroy-on-close="true">
      <div v-if="detail" class="detail-body">
        <el-alert
          class="dlg-alert"
          type="info"
          :closable="false"
          show-icon
          title="下表「重建的文本命中上下文」由重新分块后按名称命中重建，不是原始抽取证据（抽取阶段未保留分块溯源）。"
        />

        <div class="cmp-grid">
          <div v-for="side in [detail.source, detail.target]" :key="side.kp_id" class="cmp-card">
            <div class="cmp-title">{{ side.name }}</div>
            <div class="kv"><span class="kv-k">kp_id</span><span class="kv-v mono">{{ side.kp_id }}</span></div>
            <div class="kv"><span class="kv-k">类别</span><span class="kv-v">{{ side.category || '—' }}</span></div>
            <div class="kv"><span class="kv-k">定义</span><span class="kv-v">{{ side.description || '（无）' }}</span></div>
            <div class="kv">
              <span class="kv-k">是否仍在图中</span>
              <span class="kv-v">{{ side.in_graph ? '是' : '否（端点已失效）' }}</span>
            </div>
            <el-divider content-position="left">重建的文本命中上下文</el-divider>
            <template v-if="side.reconstructed_context?.reconstruction_ok">
              <div v-if="side.reconstructed_context.chunk_indices?.length" class="ctx-hits">
                命中分块 {{ side.reconstructed_context.chunk_indices.join('、') }}
                （命中词：{{ (side.reconstructed_context.hit_terms || []).join('、') }}）
              </div>
              <div v-else class="dim">名称未在正文中被命中</div>
              <div v-for="(s, i) in side.reconstructed_context.snippets || []" :key="i" class="ctx-snip">{{ s }}</div>
            </template>
            <div v-else class="dim">
              上下文不可用（{{ side.reconstructed_context?.reason || '重建失败' }}）——
              该项评分权重已置零，不会因"没有上下文"而误判
            </div>
          </div>
        </div>

        <el-divider content-position="left">评分明细</el-divider>
        <div class="score-grid">
          <div v-for="item in scoreRows" :key="item.k" class="score-cell">
            <div class="sc-value">{{ item.v }}</div>
            <div class="sc-label">{{ item.k }}</div>
          </div>
        </div>
        <div v-if="detail.candidate?.reason" class="ctx-snip">判定依据：{{ detail.candidate.reason }}</div>
        <div class="dim">
          决策置信度（模型自评）不是准确率；最终是否融合由人确认。
        </div>
      </div>
      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
        <el-button type="warning" @click="review(detail.candidate, 'defer')">暂缓</el-button>
        <el-button type="danger" @click="review(detail.candidate, 'reject')">驳回</el-button>
        <el-button type="success" @click="review(detail.candidate, 'accept')">通过</el-button>
      </template>
    </el-drawer>

    <!-- ============ 应用确认（Dry Run 预览） ============ -->
    <el-dialog v-model="applyVisible" title="应用融合 · 预演结果" width="820px" align-center>
      <el-alert
        class="dlg-alert"
        type="warning"
        :closable="false"
        show-icon
        title="下面是预演（Dry Run，未写入任何数据）。确认后才会真正应用，且随时可以撤销。"
      />
      <div v-if="applyPlan">
        <p class="dlg-tip">
          选中 <b>{{ applyPlan.selected_count }}</b> 条候选，其中
          <b>{{ applyPlan.planned_count }}</b> 条可以安全融合
          <span v-if="applyPlan.skipped_already_fused">，{{ applyPlan.skipped_already_fused }} 条此前已融合</span>。
        </p>
        <el-table :data="applyPlan.planned_merges" stripe max-height="320">
          <el-table-column label="折叠" min-width="140">
            <template #default="{ row }">{{ row.source.name }}</template>
          </el-table-column>
          <el-table-column label="" width="36">
            <template #default><el-icon class="arrow"><Right /></el-icon></template>
          </el-table-column>
          <el-table-column label="保留为规范节点" min-width="150">
            <template #default="{ row }">
              <div>{{ row.target.name }}</div>
              <div class="fsub">{{ row.canonical_reason }}</div>
            </template>
          </el-table-column>
          <el-table-column label="影响" min-width="150">
            <template #default="{ row }">
              节点 −1 · 关系 {{ row.impact.edges_before }} → {{ row.impact.edges_after }}
              （丢弃 {{ row.impact.edges_dropped }}）
            </template>
          </el-table-column>
          <template #empty><div class="empty-line">没有可安全融合的候选</div></template>
        </el-table>
        <template v-if="applyPlan.conflicts?.length">
          <el-divider content-position="left">被拒绝的候选</el-divider>
          <div v-for="(c, i) in applyPlan.conflicts" :key="i" class="conflict-line">
            <el-tag size="small" type="danger" effect="plain">{{ conflictLabel(c.type) }}</el-tag>
            <span class="dim">{{ c.detail }}</span>
          </div>
        </template>
      </div>
      <template #footer>
        <el-button @click="applyVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!applyPlan?.planned_count" :loading="applying" @click="doApply">
          确认应用 {{ applyPlan?.planned_count || 0 }} 条
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Share, Connection, Search, CircleCheck, Clock, WarningFilled, Right,
} from '@element-plus/icons-vue'
import PageHeader from '../../components/PageHeader.vue'
import MetricTile from '../../components/admin/MetricTile.vue'
import { api } from '../../api'

const loading = ref(false)
const scanning = ref(false)
const applying = ref(false)

const courses = ref([])
const documents = ref([])
const courseId = ref(null)
const documentId = ref(null)
const scope = reactive({ courseId: null, docId: null })

const tab = ref('candidates')
const overview = ref({})
const scopeReady = computed(() => !!scope.docId)

const candidates = ref([])
const candTotal = ref(0)
const candLoading = ref(false)
const candQuery = reactive({ decision: '', status: '', keyword: '', page: 1, page_size: 20 })

const maps = ref([])
const mapLoading = ref(false)
const conflicts = ref([])
const conflictLoading = ref(false)
const runs = ref([])
const runLoading = ref(false)

const detailVisible = ref(false)
const detail = ref(null)
const applyVisible = ref(false)
const applyPlan = ref(null)

const candidateStatuses = [
  { v: 'PENDING', t: '待审核' }, { v: 'ACCEPTED', t: '已通过' },
  { v: 'REJECTED', t: '已驳回' }, { v: 'DEFERRED', t: '已暂缓' },
  { v: 'SUPERSEDED', t: '被顶替' },
]

// 可应用：有人工通过、或规则判为 SAME 的候选
const canApply = computed(() => pendingCount.value > 0)
const pendingCount = computed(() => {
  const agg = overview.value.candidates?.by_status || {}
  const byd = overview.value.candidates?.by_decision || {}
  // 已通过的一律可应用；此外规则判 SAME 且仍待审核的也可自动应用
  return (agg.ACCEPTED || 0) + Math.max(0, (byd.SAME || 0) - (agg.ACCEPTED || 0))
})

const activeMaps = computed(() => maps.value.filter((m) => m.status === 'ACTIVE'))

const scoreRows = computed(() => {
  const d = detail.value?.score_detail || {}
  const fmt = (v) => (v === null || v === undefined ? '不可用' : Number(v).toFixed(3))
  return [
    { k: '词面相似度', v: fmt(d.lexical) },
    { k: '上下文重合', v: fmt(d.context) },
    { k: '类别一致性', v: fmt(d.type) },
    { k: '名称-定义互含', v: fmt(d.desc) },
    { k: '语义相似度', v: fmt(d.semantic) },
    { k: '合计得分', v: fmt(d.score) },
  ]
})

function decisionTag(d) {
  return { SAME: 'success', DIFFERENT: 'danger', UNCERTAIN: 'warning' }[d] || 'info'
}
function decisionLabel(d) {
  return { SAME: '判为同一实体', DIFFERENT: '判为不同实体', UNCERTAIN: '证据不足' }[d] || d
}
function statusTag(s) {
  return { PENDING: 'warning', ACCEPTED: 'success', REJECTED: 'danger', DEFERRED: 'info', SUPERSEDED: 'info' }[s] || 'info'
}
function statusLabel(s) {
  return (candidateStatuses.find((x) => x.v === s) || {}).t || s
}
function conflictLabel(t) {
  return {
    CYCLE: '映射成环', MULTI_TARGET: '多目标冲突',
    ENDPOINT_MISSING: '端点已失效', CROSS_DOC: '跨文档',
  }[t] || t
}
function runStatusLabel(s) {
  return { RUNNING: '进行中', COMPLETED: '已完成', FAILED: '失败', REVOKED: '已撤销' }[s] || s
}
function scoreTip(row) {
  let d = {}
  try { d = JSON.parse(row.score_detail || '{}') } catch { d = {} }
  const notes = (d.notes || []).join('；')
  return `词面 ${(d.lexical ?? 0).toFixed(2)} · 上下文 ${d.context === null || d.context === undefined ? '不可用' : d.context.toFixed(2)} · 定义互含 ${(d.desc ?? 0).toFixed(2)}${notes ? ' ｜ ' + notes : ''}`
}

async function loadCourses() {
  // 注意：管理员端既有列表接口（/admin/courses、/admin/resources/documents）返回的
  // 是 `items`；本页自己的 /admin/fusion/* 接口用的是 `list`。两者不可混用。
  const data = await api.adminListCourses({ page: 1, page_size: 500 })
  courses.value = data?.items || []
}

async function onCourseChange() {
  documentId.value = null
  documents.value = []
  resetScope()
  if (!courseId.value) return
  // 管理员不是课程成员，必须走管理员端文档接口（/api/v1/documents 会 403）。返回键为 `items`。
  const data = await api.adminListDocuments({ course_id: courseId.value, page: 1, page_size: 200 })
  documents.value = data?.items || []
}

function resetScope() {
  scope.courseId = null
  scope.docId = null
  overview.value = {}
  candidates.value = []
  maps.value = []
  conflicts.value = []
  runs.value = []
}

async function onDocumentChange() {
  if (!courseId.value || !documentId.value) {
    resetScope()
    return
  }
  scope.courseId = courseId.value
  scope.docId = documentId.value
  candQuery.page = 1
  await reload()
}

async function reload() {
  if (!scopeReady.value) return
  loading.value = true
  try {
    await Promise.all([loadOverview(), loadCandidates(), loadMaps(), loadConflicts(), loadRuns()])
  } finally {
    loading.value = false
  }
}

async function loadOverview() {
  overview.value = await api.adminFusionOverview(scope.courseId, scope.docId)
}

async function loadCandidates() {
  candLoading.value = true
  try {
    const data = await api.adminFusionCandidates({
      course_id: scope.courseId, document_id: scope.docId,
      decision: candQuery.decision || undefined, status: candQuery.status || undefined,
      keyword: candQuery.keyword || undefined,
      page: candQuery.page, page_size: candQuery.page_size,
    })
    candidates.value = data.list || []
    candTotal.value = data.total || 0
  } finally {
    candLoading.value = false
  }
}

async function loadMaps() {
  mapLoading.value = true
  try {
    const data = await api.adminFusionMaps({
      course_id: scope.courseId, document_id: scope.docId, page: 1, page_size: 200,
    })
    maps.value = data.list || []
  } finally {
    mapLoading.value = false
  }
}

async function loadConflicts() {
  conflictLoading.value = true
  try {
    const data = await api.adminFusionConflicts({
      course_id: scope.courseId, document_id: scope.docId, page: 1, page_size: 100,
    })
    conflicts.value = data.list || []
  } finally {
    conflictLoading.value = false
  }
}

async function loadRuns() {
  runLoading.value = true
  try {
    const data = await api.adminFusionRuns({
      course_id: scope.courseId, document_id: scope.docId, page: 1, page_size: 50,
    })
    runs.value = data.list || []
  } finally {
    runLoading.value = false
  }
}

async function doScan(persist) {
  scanning.value = true
  try {
    const res = await api.adminFusionScan({
      course_id: scope.courseId, document_id: scope.docId, persist, use_llm: false,
    })
    const d = res
    await loadOverview()
    if (persist) await loadCandidates()
    ElMessage.success(
      persist
        ? `已扫描并保存 ${d.candidate_count} 条候选（判为同一实体 ${d.same_count}、不同 ${d.different_count}、证据不足 ${d.uncertain_count}）`
        : `预览完成：${d.candidate_count} 条候选（**未写入任何数据**）${d.context_ok ? '' : '；上下文重建不可用：' + (d.context_error || '')}`,
    )
  } finally {
    scanning.value = false
  }
}

async function openDetail(row) {
  detail.value = await api.adminFusionCandidate(row.candidate_id)
  detailVisible.value = true
}

async function review(row, action) {
  const candidateId = row?.candidate_id || row
  await api.adminFusionReview(candidateId, {
    course_id: scope.courseId, document_id: scope.docId, action,
  })
  ElMessage.success({ accept: '已通过', reject: '已驳回', defer: '已暂缓' }[action])
  detailVisible.value = false
  await Promise.all([loadCandidates(), loadOverview()])
}

async function doApplyPreview() {
  applying.value = true
  try {
    applyPlan.value = await api.adminFusionApply({
      course_id: scope.courseId, document_id: scope.docId, dry_run: true,
    })
    applyVisible.value = true
  } finally {
    applying.value = false
  }
}

async function doApply() {
  applying.value = true
  try {
    const res = await api.adminFusionApply({
      course_id: scope.courseId, document_id: scope.docId, dry_run: false, confirm: true,
    })
    ElMessage.success(`已应用 ${res.applied_count} 条融合，可在「已应用」标签页撤销`)
    applyVisible.value = false
    await reload()
  } finally {
    applying.value = false
  }
}

async function revoke(row) {
  await ElMessageBox.confirm(
    `撤销后「${row.source_name || row.source_kp_id}」会重新成为独立节点，图谱立即恢复。\n` +
    '融合记录与审核历史都会保留，不会丢失。',
    '撤销融合', { type: 'warning', confirmButtonText: '确认撤销', cancelButtonText: '取消' },
  )
  await api.adminFusionRevoke(row.fusion_id, {
    course_id: scope.courseId, document_id: scope.docId, confirm: true,
  })
  ElMessage.success('已撤销，图谱已恢复')
  await reload()
}

async function undoRun() {
  const latest = runs.value.find((r) => r.status === 'COMPLETED')
  if (!latest) {
    ElMessage.info('没有可撤销的批次')
    return
  }
  await ElMessageBox.confirm(
    `将撤销批次 ${latest.run_id} 下已应用的 ${latest.n_applied} 条融合，图谱恢复到该批次之前的状态。`,
    '整批撤销', { type: 'warning', confirmButtonText: '确认撤销', cancelButtonText: '取消' },
  )
  await api.adminFusionUndoRun(latest.run_id, {
    course_id: scope.courseId, document_id: scope.docId, confirm: true,
  })
  ElMessage.success('已整批撤销')
  await reload()
}

onMounted(async () => {
  await loadCourses()
})
</script>

<style scoped>
.scope-card { margin-bottom: 16px; }
.scope-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.scope-label { font-weight: 600; color: var(--text-primary, #1f2937); }
.scope-alert { margin-top: 12px; }
.opt-sub { float: right; color: var(--text-tertiary, #909399); font-size: 12px; margin-left: 12px; }

.metric-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.stats-text { margin-left: auto; color: var(--text-tertiary, #909399); font-size: 13px; }
.fname { font-weight: 600; color: var(--text-primary, #1f2937); }
.fsub { font-size: 12px; color: var(--text-tertiary, #909399); }
.num { font-variant-numeric: tabular-nums; font-weight: 600; }
.dim { color: var(--text-tertiary, #909399); font-size: 13px; }
.mono { font-family: var(--font-family-number, monospace); font-size: 12px; }
.arrow { color: var(--text-tertiary, #909399); }
.empty-line { padding: 20px; text-align: center; color: var(--text-tertiary, #909399); }
.empty-line.big { padding: 48px; font-size: 15px; }
.pager { display: flex; justify-content: flex-end; margin-top: 12px; }

.detail-body { padding-bottom: 8px; }
.dlg-alert { margin-bottom: 12px; }
.dlg-tip { margin: 0 0 12px; color: var(--text-secondary, #4b5563); }

.cmp-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
@media (max-width: 768px) { .cmp-grid { grid-template-columns: 1fr; } }
.cmp-card {
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 10px;
  padding: 12px;
  background: var(--bg-card, #fff);
}
.cmp-title { font-weight: 700; font-size: 15px; margin-bottom: 8px; color: var(--text-primary, #1f2937); }

.kv { display: flex; gap: 8px; margin-bottom: 6px; font-size: 13px; }
.kv-k { flex: 0 0 84px; color: var(--text-tertiary, #909399); }
.kv-v { flex: 1; color: var(--text-secondary, #4b5563); word-break: break-word; }

.ctx-hits { font-size: 12px; color: var(--color-primary, #4f6ef7); margin-bottom: 6px; }
.ctx-snip {
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-secondary, #4b5563);
  background: var(--bg-page, #f3f5fb);
  border-radius: 6px;
  padding: 6px 8px;
  margin-bottom: 6px;
  word-break: break-word;
}
.conflict-line { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 13px; }

.score-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}
.score-cell {
  text-align: center;
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 8px;
  padding: 8px 4px;
}
.sc-value { font-weight: 700; font-size: 16px; color: var(--text-primary, #1f2937); }
.sc-label { font-size: 12px; color: var(--text-tertiary, #909399); margin-top: 2px; }
</style>
