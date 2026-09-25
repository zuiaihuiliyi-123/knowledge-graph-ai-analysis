<template>
  <div class="admin-governance">
    <PageHeader title="课程治理" desc="自动挑出需要管理员介入的课程；每一项都由真实字段判定，不做评分或推测">
      <template #extra>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </template>
    </PageHeader>

    <!-- 治理状态分布 -->
    <div class="metric-row">
      <MetricTile label="课程总数" :value="courseCount" :icon="Notebook" tone="blue" />
      <MetricTile
        label="正常"
        :value="governance.normal"
        :icon="CircleCheck"
        tone="green"
        hint="未被治理处置的课程"
      />
      <MetricTile label="已下架" :value="governance.hidden" :icon="Hide" tone="amber" hint="学生无法发现或加入" />
      <MetricTile label="已归档" :value="governance.archived" :icon="Box" tone="slate" hint="冻结保留，供追溯" />
      <MetricTile
        label="需介入分组"
        :value="activeBuckets.length"
        :icon="WarningFilled"
        tone="red"
        hint="下面列出的问题分组"
      />
    </div>

    <!-- 分组 -->
    <el-card v-for="b in buckets" :key="b.key" class="page-card bucket-card" shadow="never">
      <template #header>
        <div class="bk-header">
          <div class="bk-text">
            <span class="bk-label">{{ b.label }}</span>
            <span class="bk-count" :class="{ zero: !b.total }">{{ b.total }}</span>
            <span class="bk-desc">{{ b.description }}</span>
          </div>
          <el-button v-if="b.total > b.items.length" link type="primary" @click="goCourses(b)">
            查看全部 {{ b.total }} 门
          </el-button>
        </div>
      </template>

      <el-table v-if="b.items.length" :data="b.items" size="small" row-key="course_id">
        <el-table-column label="课程" min-width="200">
          <template #default="{ row }">
            <div class="cname">{{ row.course_name }}</div>
            <div class="csub">ID {{ row.course_id }}<span v-if="row.category"> · {{ row.category }}</span></div>
          </template>
        </el-table-column>
        <el-table-column label="负责人" min-width="140">
          <template #default="{ row }">
            <template v-if="row.teacher_id">
              {{ row.teacher_name || '未知用户' }}
              <el-tag v-if="row.teacher_active === 0" size="small" type="danger" effect="plain" class="ml4">已禁用</el-tag>
            </template>
            <el-tag v-else size="small" type="warning" effect="plain">无负责人</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="成员 / 文档" width="120">
          <template #default="{ row }">
            <span class="num">{{ row.member_count || 0 }}</span> /
            <span class="num">{{ row.document_count || 0 }}</span>
          </template>
        </el-table-column>
        <!-- 业务状态与治理状态分列：一个反映教师/管理员对课程自身的开关，
             一个反映平台处置结论，两者正交 -->
        <el-table-column label="业务状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="bizTagType(row.status)" effect="plain">
              {{ bizLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="治理状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="govTagType(row.governance_status)" effect="light">
              {{ govLabel(row.governance_status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最近更新" width="120">
          <template #default="{ row }">
            <span class="dim">{{ fmtDate(row.updated_at || row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row)">详情</el-button>
            <el-button link type="primary" @click="openGovernance(row)">治理</el-button>
            <el-button
              v-if="!row.teacher_id || row.teacher_active === 0"
              link
              type="primary"
              @click="openTransfer(row)"
            >指派负责人</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-else class="empty-line">该分组当前为空 —— 没有需要处理的课程</div>
    </el-card>

    <!-- 课程详情（与课程管理页同源：/admin/courses/{id}） -->
    <el-drawer v-model="detailVisible" title="课程详情" size="620px" :destroy-on-close="true">
      <div v-if="detail" v-loading="detailLoading" class="detail-body">
        <div class="dh-name">{{ detail.course.course_name }}</div>
        <div class="dh-tags">
          <el-tag size="small" :type="govTagType(detail.course.governance_status)">
            {{ govLabel(detail.course.governance_status) }}
          </el-tag>
          <el-tag size="small" :type="detail.course.is_public ? 'success' : 'info'" effect="plain">
            {{ detail.course.is_public ? '公开' : '非公开' }}
          </el-tag>
          <span class="dim">ID {{ detail.course.course_id }}</span>
        </div>

        <el-divider content-position="left">概览</el-divider>
        <div class="kv-grid">
          <div class="kv"><span class="kv-k">负责人</span><span class="kv-v">{{ detail.course.teacher_name || '（无）' }}</span></div>
          <div class="kv"><span class="kv-k">课程码</span><span class="kv-v">{{ detail.course.course_code || '—' }}</span></div>
          <div class="kv"><span class="kv-k">成员 / 待审</span><span class="kv-v">{{ detail.course.member_count || 0 }} / {{ detail.course.pending_member_count || 0 }}</span></div>
          <div class="kv"><span class="kv-k">文档</span><span class="kv-v">{{ detail.course.document_count || 0 }}</span></div>
          <div class="kv"><span class="kv-k">知识节点</span><span class="kv-v">{{ graphAvailable ? detail.graph.node_count : '未检测' }}</span></div>
          <div class="kv"><span class="kv-k">知识关系</span><span class="kv-v">{{ graphAvailable ? detail.graph.edge_count : '未检测' }}</span></div>
          <div class="kv"><span class="kv-k">创建时间</span><span class="kv-v">{{ fmtTime(detail.course.created_at) }}</span></div>
          <div class="kv"><span class="kv-k">最近更新</span><span class="kv-v">{{ fmtTime(detail.course.updated_at) }}</span></div>
          <div v-if="detail.course.governance_note" class="kv wide">
            <span class="kv-k">治理备注</span><span class="kv-v">{{ detail.course.governance_note }}</span>
          </div>
        </div>

        <el-divider content-position="left">成员（{{ (detail.members || []).length }}）</el-divider>
        <el-table :data="detail.members || []" size="small" max-height="240">
          <el-table-column label="成员" min-width="130">
            <template #default="{ row }">{{ row.name || row.username }}</template>
          </el-table-column>
          <el-table-column label="角色" width="100">
            <template #default="{ row }">
              <el-tag size="small" effect="plain">{{ memberRoleLabel(row.role) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="row.status === 'approved' ? 'success' : 'warning'" effect="plain">
                {{ memberStatusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <template #empty><div class="empty-line">暂无成员</div></template>
        </el-table>
      </div>
    </el-drawer>

    <!-- 治理动作（与课程管理页共用同一后端接口，弹窗形态保持一致） -->
    <el-dialog v-model="govVisible" title="课程治理" width="540px" align-center>
      <p class="dlg-tip">课程：<b>{{ acting?.course_name }}</b></p>

      <!-- 与课程管理页一致：把「业务状态」和「治理状态」分开呈现，并标明各自来源 -->
      <div class="state-panel">
        <div class="sp-row">
          <span class="sp-label">业务状态</span>
          <el-tag size="small" :type="bizTagType(acting?.status)" effect="plain">
            {{ bizLabel(acting?.status) }}
          </el-tag>
          <span class="sp-src">来源：教师或管理员的「关闭 / 重新开放」设置</span>
        </div>
        <div class="sp-row">
          <span class="sp-label">治理状态</span>
          <el-tag size="small" :type="govTagType(acting?.governance_status)" effect="light">
            {{ govLabel(acting?.governance_status) }}
          </el-tag>
          <span class="sp-src">来源：平台治理（下架 / 归档）</span>
        </div>
        <div class="sp-row">
          <span class="sp-label">学生可见</span>
          <span :class="['vis-cell', visibleToStudents(acting) ? 'on' : 'off']">
            {{ visibleToStudents(acting) ? '可见（可发现、可加入）' : '不可见（无法发现、无法加入）' }}
          </span>
        </div>
      </div>

      <div v-for="g in govGroups" :key="g.key" class="gov-group">
        <div class="gg-title">{{ g.title }}</div>
        <div class="gg-hint">{{ g.hint }}</div>
        <div class="gov-actions">
          <div v-for="a in g.items" :key="a.action" class="gov-item" :class="{ disabled: a.disabled }">
            <div class="gi-text">
              <div class="gi-title">{{ a.label }}</div>
              <div class="gi-desc">{{ a.desc }}</div>
            </div>
            <el-button size="small" :type="a.type" :disabled="a.disabled" :loading="submitting" @click="submitGovernance(a)">
              {{ a.label }}
            </el-button>
          </div>
        </div>
      </div>
      <el-form label-width="70px" class="gov-note">
        <el-form-item label="备注">
          <el-input
            v-model="govForm.note"
            type="textarea"
            :rows="2"
            maxlength="200"
            show-word-limit
            placeholder="选填，仅对上方「平台治理」动作生效（写入课程治理备注）"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="govVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 指派 / 转移负责人 -->
    <el-dialog v-model="transferVisible" title="指派课程负责人" width="460px" align-center>
      <p class="dlg-tip">为课程 <b>{{ acting?.course_name }}</b> 指派负责人：</p>
      <el-select v-model="transferForm.teacher_id" filterable placeholder="选择接收的教师" style="width: 100%">
        <el-option
          v-for="t in transferCandidates"
          :key="t.user_id"
          :label="`${t.name}（${t.username}）`"
          :value="t.user_id"
        />
      </el-select>
      <el-alert
        class="dlg-alert"
        type="info"
        :closable="false"
        show-icon
        title="只能指派给状态正常的教师账号。若课程原本有负责人，原负责人会保留为协作教师。"
      />
      <template #footer>
        <el-button @click="transferVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" :disabled="!transferForm.teacher_id" @click="submitTransfer">
          确认指派
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { Notebook, CircleCheck, Hide, Box, WarningFilled } from '@element-plus/icons-vue'
import PageHeader from '../../components/PageHeader.vue'
import MetricTile from '../../components/admin/MetricTile.vue'
import { api } from '../../api'
import {
  bizLabel, bizTagType, govLabel, govTagType, governanceActionDisabled,
  visibleToStudents,
} from '../../utils/courseGovernance'

const router = useRouter()

const loading = ref(false)
const submitting = ref(false)
const buckets = ref([])
const governance = ref({})
const courseCount = ref(null)
const teachers = ref([])

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)
const acting = ref(null)

const govVisible = ref(false)
const transferVisible = ref(false)
const govForm = reactive({ note: '' })
const transferForm = reactive({ teacher_id: null })

const fmtTime = (t) => (t ? String(t).slice(0, 16) : '—')
const fmtDate = (t) => (t ? String(t).slice(0, 10) : '—')
const memberRoleLabel = (r) =>
  ({ owner: '创建者', teacher: '协作教师', student: '学生', assistant: '助教' }[r] || r || '—')
const memberStatusLabel = (s) =>
  ({ pending: '待审核', approved: '已通过', rejected: '已拒绝', removed: '已移除' }[s] || s || '—')

const graphAvailable = computed(() => !!detail.value?.graph?.available)

// 只把「确实有内容」的分组计为需介入；空分组不制造虚假的告警数字
const activeBuckets = computed(() => buckets.value.filter((b) => b.total > 0))

// 治理动作与课程管理页完全一致（同一个后端接口、同一套启用条件），
// 启用条件由 utils/courseGovernance.governanceActionDisabled 统一给出，避免两页漂移。
const govActions = computed(() => {
  const c = acting.value
  return [
    // 治理维度：只改治理状态，课程的业务状态原样保留
    { group: 'governance', action: 'hide', label: '下架课程', type: 'warning', desc: '学生无法再发现 / 申请 / 用加课码或邀请加入；业务状态与数据原样保留', disabled: governanceActionDisabled('hide', c) },
    { group: 'governance', action: 'restore', label: '撤销下架', type: 'primary', desc: '治理状态回到「正常」；业务状态恢复成下架前的样子，不会被强行打开', disabled: governanceActionDisabled('restore', c) },
    { group: 'governance', action: 'archive', label: '归档课程', type: 'info', desc: '冻结归档（可见性一并收回），保留数据供追溯', disabled: governanceActionDisabled('archive', c) },
    { group: 'governance', action: 'unarchive', label: '取消归档', type: 'primary', desc: '从归档状态回到「正常」，业务状态同样原样保留', disabled: governanceActionDisabled('unarchive', c) },
    // 业务维度：只改课程自身的开放 / 关闭
    { group: 'business', action: 'close', label: '关闭课程', type: 'danger', desc: '把课程的业务状态置为「已关闭」——相当于管理员代教师停课', disabled: governanceActionDisabled('close', c) },
    { group: 'business', action: 'reopen', label: '重新开放', type: 'success', desc: '把课程的业务状态置回「开放」', disabled: governanceActionDisabled('reopen', c) },
  ]
})

const govGroups = computed(() => [
  {
    key: 'governance',
    title: '平台治理',
    hint: '只改变治理状态，课程的业务状态由教师（或下面的业务动作）决定，不会被覆盖',
    items: govActions.value.filter((a) => a.group === 'governance'),
  },
  {
    key: 'business',
    title: '课程业务状态',
    hint: '只改变课程自身的开放 / 关闭，不影响平台治理状态',
    items: govActions.value.filter((a) => a.group === 'business'),
  },
])

const transferCandidates = computed(() =>
  teachers.value.filter((t) => t.is_active && t.user_id !== acting.value?.teacher_id))

async function load() {
  loading.value = true
  try {
    const data = await api.adminGovernance()
    buckets.value = data.buckets || []
    governance.value = data.governance || {}
  } catch (e) {
    ElMessage.error(e?.message || '加载治理数据失败')
  } finally {
    loading.value = false
  }
}

async function loadCounts() {
  try {
    const data = await api.adminDashboard()
    courseCount.value = data.counts?.course_count ?? null
    // dashboard 的 governance 与 /admin/governance 同源，这里仅作为兜底
    if (!Object.keys(governance.value).length) governance.value = data.governance || {}
  } catch {
    // 概览失败不影响治理分组展示
  }
}

async function loadOptions() {
  try {
    const data = await api.adminOptions()
    teachers.value = data.teachers || []
  } catch {
    // 教师下拉失败时转移弹窗会显示为无可选项，不影响其他功能
  }
}

// 分组数量超过一次展示上限时，跳到课程管理页并用治理状态过滤出完整列表
function goCourses(bucket) {
  const query = { page: 1 }
  if (bucket.key === 'hidden' || bucket.key === 'archived') query.governance_status = bucket.key
  router.push({ path: '/admin/courses', query })
}

async function openDetail(course) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await api.adminGetCourse(course.course_id)
  } catch (e) {
    ElMessage.error(e?.message || '加载课程详情失败')
    detailVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

function openGovernance(course) {
  acting.value = course
  govForm.note = ''
  govVisible.value = true
}

async function submitGovernance(a) {
  submitting.value = true
  try {
    // 只有治理动作写治理备注；业务动作（关闭 / 重新开放）后端会忽略备注
    const note = a.group === 'governance' ? (govForm.note || null) : null
    await api.adminCourseGovernance(acting.value.course_id, a.action, note)
    ElMessage.success(`${a.label}成功`)
    govVisible.value = false
    await Promise.all([load(), loadCounts()])
  } catch (e) {
    ElMessage.error(e?.message || '治理操作失败')
  } finally {
    submitting.value = false
  }
}

function openTransfer(course) {
  acting.value = course
  transferForm.teacher_id = null
  transferVisible.value = true
}

async function submitTransfer() {
  submitting.value = true
  try {
    await api.adminTransferCourse(acting.value.course_id, transferForm.teacher_id)
    ElMessage.success('课程负责人已指派')
    transferVisible.value = false
    await Promise.all([load(), loadCounts()])
    if (detailVisible.value && detail.value) {
      await openDetail(detail.value.course)
    }
  } catch (e) {
    ElMessage.error(e?.message || '指派失败')
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  await Promise.all([load(), loadCounts(), loadOptions()])
})
</script>

<style scoped>
.metric-row {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
  margin-bottom: 14px;
}

.bucket-card { margin-bottom: 14px; }
.bk-header { display: flex; align-items: center; gap: 12px; }
.bk-text { flex: 1; min-width: 0; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.bk-label { font-size: 14px; font-weight: 700; color: var(--text-primary); }
.bk-count {
  font-family: var(--font-family-number);
  font-size: 13px;
  font-weight: 700;
  color: #fff;
  background: var(--gradient-brand);
  border-radius: 20px;
  padding: 1px 9px;
}
.bk-count.zero { background: var(--color-text-muted); }
.bk-desc { font-size: 12px; color: var(--color-text-secondary); }

.cname {
  font-weight: 600;
  color: var(--text-primary);
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.csub { font-size: 11.5px; color: var(--color-text-muted); margin-top: 1px; }
.dim { color: var(--color-text-muted); font-size: 12.5px; }
.num { font-family: var(--font-family-number); font-weight: 600; color: var(--text-primary); }
.ml4 { margin-left: 4px; }
.empty-line { padding: 18px 0; text-align: center; color: var(--color-text-muted); font-size: 13px; }

/* ---- 详情 ---- */
.detail-body { padding: 0 4px 12px; }
.dh-name { font-size: 16px; font-weight: 700; color: var(--text-primary); }
.dh-tags { display: flex; align-items: center; gap: 6px; margin-top: 6px; flex-wrap: wrap; }

.kv-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; }
.kv {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 7px 10px;
  border-radius: var(--radius-sm);
  background: var(--bg-soft);
  font-size: 13px;
  min-width: 0;
}
.kv.wide { grid-column: 1 / -1; }
.kv-k { color: var(--color-text-secondary); font-size: 12.5px; flex-shrink: 0; }
.kv-v {
  color: var(--text-primary);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ---- 弹窗 ---- */
.dlg-tip { margin: 0 0 12px; font-size: 13px; color: var(--text-regular); }

/* 状态来源面板（与课程管理页的治理弹窗保持一致） */
.state-panel {
  padding: 10px 12px;
  margin-bottom: 14px;
  border-radius: var(--radius-sm);
  background: var(--bg-soft);
  border: 1px solid var(--border-light);
}
.sp-row { display: flex; align-items: center; gap: 8px; }
.sp-row + .sp-row { margin-top: 7px; }
.sp-label { flex: 0 0 62px; font-size: 12.5px; color: var(--color-text-secondary); }
.sp-src { font-size: 11.5px; color: var(--color-text-secondary); }
.vis-cell { font-size: 12.5px; font-weight: 600; }
.vis-cell.on { color: var(--brand-600, #2f6fed); }
.vis-cell.off { color: var(--color-danger, #d9534f); }

.gov-group + .gov-group { margin-top: 16px; }
.gg-title { font-size: 13px; font-weight: 600; color: var(--text-primary); }
.gg-hint { font-size: 11.5px; color: var(--color-text-secondary); margin: 2px 0 8px; }
.gov-actions { display: flex; flex-direction: column; gap: 8px; }
.gov-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  background: var(--bg-soft);
  border: 1px solid var(--border-light);
}
.gov-item.disabled { opacity: .55; }
.gi-text { flex: 1; min-width: 0; }
.gi-title { font-size: 13.5px; font-weight: 600; color: var(--text-primary); }
.gi-desc { font-size: 11.5px; color: var(--color-text-secondary); margin-top: 2px; }
.gov-note { margin-top: 14px; }
.dlg-alert { margin-top: 12px; border-radius: var(--radius-sm); }

@media (max-width: 1440px) {
  .metric-row { grid-template-columns: repeat(3, 1fr); }
}
@media (max-width: 1024px) {
  .metric-row { grid-template-columns: repeat(2, 1fr); }
}
</style>
