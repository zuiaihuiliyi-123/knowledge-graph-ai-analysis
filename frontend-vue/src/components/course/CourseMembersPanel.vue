<template>
  <div class="members-panel">
    <!-- 概览：总人数 / 已通过 / 待审核 / 已移除 / 平均进度 -->
    <div class="stat-row">
      <div class="stat-box">
        <div class="stat-num">{{ stats.total ?? '—' }}</div>
        <div class="stat-label">总人数</div>
      </div>
      <div class="stat-box">
        <div class="stat-num">{{ stats.approved ?? '—' }}</div>
        <div class="stat-label">已通过</div>
      </div>
      <div class="stat-box" :class="{ highlight: stats.pending > 0 }">
        <div class="stat-num">{{ stats.pending ?? '—' }}</div>
        <div class="stat-label">待审核</div>
      </div>
      <div class="stat-box">
        <div class="stat-num">{{ stats.removed ?? '—' }}</div>
        <div class="stat-label">已移除</div>
      </div>
      <div class="stat-box">
        <div class="stat-num">{{ stats.avg_progress ?? 0 }}%</div>
        <div class="stat-label">平均进度</div>
      </div>
    </div>

    <el-tabs v-model="activeTab" @tab-change="reload">
<!-- 全部成员 -->
      <el-tab-pane label="全部成员" name="approved">
        <div class="toolbar">
          <el-input
            v-model="keyword"
            placeholder="搜索用户名 / 姓名 / 学号"
            clearable
            style="width: 240px"
            @keyup.enter="reload"
            @clear="reload"
          >
            <template #append>
              <el-button :icon="Search" @click="reload" />
            </template>
          </el-input>
          <el-select v-model="roleFilter" placeholder="角色" clearable style="width: 120px" @change="reload">
            <el-option label="学生" value="student" />
            <el-option label="教师" value="teacher" />
          </el-select>
          <el-button :icon="Refresh" @click="reload">刷新</el-button>
        </div>

        <div v-if="selectedMembers.length" class="batch-bar">
          <span class="batch-count">已选 <b>{{ selectedMembers.length }}</b> 名成员</span>
          <el-button size="small" type="danger" plain @click="runBatchRemoveMembers">批量移除</el-button>
          <el-button size="small" link @click="clearMemberSelection">取消选择</el-button>
        </div>

        <el-table
          ref="memberTableRef"
          :data="memberRows"
          v-loading="loading"
          size="small"
          empty-text="暂无成员"
          row-key="user_id"
          @selection-change="onMemberSelectionChange"
        >
          <el-table-column type="selection" width="46" :selectable="(row) => row.role !== 'owner'" />
          <el-table-column label="成员" min-width="150">
            <template #default="{ row }">
              <div class="member-cell">
                <el-avatar :size="30" :src="row.avatar_url || undefined">
                  {{ (row.name || '?').slice(0, 1) }}
                </el-avatar>
                <div class="member-name">
                  <div>{{ row.name || row.username }}</div>
                  <div class="member-sub">@{{ row.username }}</div>
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="角色" width="92">
            <template #default="{ row }">
              <el-tag
                size="small"
                :type="row.user_id === ownerId ? 'danger' : row.role === 'teacher' ? 'warning' : 'success'"
                effect="plain"
              >
                {{ row.user_id === ownerId ? '主讲' : row.role === 'teacher' ? '协作教师' : '学生' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="学号 / 工号" min-width="110">
            <template #default="{ row }">{{ row.student_no || row.teacher_no || '—' }}</template>
          </el-table-column>
          <el-table-column label="学校 / 学院" min-width="140" show-overflow-tooltip>
            <template #default="{ row }">
              {{ [row.school, row.college].filter(Boolean).join(' / ') || '—' }}
            </template>
          </el-table-column>
          <el-table-column label="加入时间" width="140">
            <template #default="{ row }">{{ fmtTime(row.joined_at || row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="学习进度" min-width="150">
            <template #default="{ row }">
              <span v-if="row.role === 'teacher'" class="co-teacher-na">教学协作，不参与学习统计</span>
              <el-progress
                v-else
                :percentage="Number(row.progress) || 0"
                :stroke-width="10"
                :color="progressColor"
              />
            </template>
          </el-table-column>
          <el-table-column label="掌握情况" width="110">
            <template #default="{ row }">
              <template v-if="row.role === 'teacher'">—</template>
              <template v-else>{{ row.mastered_count ?? 0 }} / {{ row.total_knowledge ?? 0 }}</template>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="160" fixed="right">
            <template #default="{ row }">
              <el-button v-if="row.role !== 'teacher' && row.user_id !== ownerId" size="small" text type="primary" @click="openDetail(row)">查看学习情况</el-button>
              <el-button v-if="row.user_id !== ownerId && row.user_id !== selfId" size="small" text type="danger" @click="remove(row)">移除</el-button>
            </template>
          </el-table-column>
        </el-table>

        <el-pagination
          v-if="total > pageSize"
          class="pager"
          layout="prev, pager, next, total"
          :total="total"
          :page-size="pageSize"
          :current-page="page"
          @current-change="onPageChange"
        />
      </el-tab-pane>
    <!-- 待审核申请 -->
      <el-tab-pane name="pending">
        <template #label>
          <span class="tab-label">
            待审核申请
            <el-badge v-if="stats.pending > 0" :value="stats.pending" type="danger" />
          </span>
        </template>

        <el-table :data="pendingRows" v-loading="loading" size="small" empty-text="暂无待审核申请">
          <el-table-column label="学生" min-width="150">
            <template #default="{ row }">
              <div class="member-cell">
                <el-avatar :size="30" :src="row.avatar_url || undefined">
                  {{ (row.name || '?').slice(0, 1) }}
                </el-avatar>
                <div class="member-name">
                  <div>{{ row.name || row.username }}</div>
                  <div class="member-sub">@{{ row.username }}</div>
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="学号 / 工号" min-width="110">
            <template #default="{ row }">{{ row.student_no || row.teacher_no || '—' }}</template>
          </el-table-column>
          <el-table-column label="学校" min-width="120">
            <template #default="{ row }">{{ row.school || '—' }}</template>
          </el-table-column>
          <el-table-column label="申请时间" width="140">
            <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="申请理由" min-width="150" show-overflow-tooltip>
            <template #default="{ row }">{{ row.applied_reason || '—' }}</template>
          </el-table-column>
          <el-table-column label="来源" width="92">
            <template #default="{ row }">
              <el-tag size="small" effect="plain">{{ sourceText(row.join_source) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="150" fixed="right">
            <template #default="{ row }">
              <el-button size="small" type="primary" @click="approve(row)">同意</el-button>
              <el-button size="small" type="danger" plain @click="openReject(row)">拒绝</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      </el-tabs>

    <!-- 拒绝理由 -->
    <el-dialog v-model="rejectVisible" title="拒绝申请" width="420px">
      <div class="reject-target">学生：{{ current?.name || current?.username }}</div>
      <el-input
        v-model="rejectComment"
        type="textarea"
        :rows="3"
        maxlength="200"
        show-word-limit
        placeholder="选填：说明拒绝原因，学生会在「申请中」看到"
      />
      <template #footer>
        <el-button @click="rejectVisible = false">取消</el-button>
        <el-button type="danger" :loading="acting" @click="doReject">确认拒绝</el-button>
      </template>
    </el-dialog>

    <!-- 学生学习情况 -->
    <el-drawer v-model="detailVisible" :title="`学习情况 · ${current?.name || ''}`" size="420px">
      <div v-if="current" class="detail-body">
        <div class="detail-row">
          <span class="detail-label">学号 / 工号</span>
          <span>{{ current.student_no || current.teacher_no || '—' }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">学校 / 学院</span>
          <span>{{ [current.school, current.college].filter(Boolean).join(' / ') || '—' }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">专业 / 班级</span>
          <span>{{ [current.major, current.class_name].filter(Boolean).join(' / ') || '—' }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">加入时间</span>
          <span>{{ fmtTime(current.joined_at || current.created_at) }}</span>
        </div>

        <el-divider content-position="left">学习数据</el-divider>
        <div class="detail-row">
          <span class="detail-label">已掌握</span>
          <span>{{ current.mastered_count ?? 0 }} / {{ current.total_knowledge ?? 0 }} 个知识点</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">学习进度</span>
          <span>{{ current.progress ?? 0 }}%</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">收藏知识点</span>
          <span>{{ current.favorite_count ?? 0 }} 个</span>
        </div>
        <el-progress
          :percentage="Number(current.progress) || 0"
          :stroke-width="12"
          :color="progressColor"
        />

        <el-divider content-position="left">档案信息</el-divider>
        <div class="detail-row">
          <span class="detail-label">昵称</span>
          <span>{{ current.nickname || '—' }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">姓名</span>
          <span>{{ current.real_name || '—' }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">职称 / 研究方向</span>
          <span>{{ [current.title, current.research_area].filter(Boolean).join(' / ') || '—' }}</span>
        </div>
        <div class="hint">
          更详细的「当前学习 / 推荐学习」请在「教学监测」中查看。
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Search } from '@element-plus/icons-vue'
import { api } from '../../api'
import { formatDateTime } from '../../utils/format'
import { useAppStore } from '../../stores/app'
import { useDeferredAction } from '../../composables/useDeferredAction'

const props = defineProps({
  courseId: { type: [String, Number], required: true },
  // 课程创建者 id（后端下发的 teacher_id）。「主讲」判定必须用它；
  // 不传时回退为当前登录用户，仅兼容旧调用方。
  teacherId: { type: [String, Number], default: null },
})
const emit = defineEmits(['refresh'])

const store = useAppStore()
// 移除成员先走「撤销窗口」，5 秒后才真正提交
const { schedule: scheduleRemove } = useDeferredAction()

const activeTab = ref('approved')
const pendingRows = ref([])
const memberRows = ref([])
const stats = ref({})
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const keyword = ref('')
const roleFilter = ref('')
const loading = ref(false)

// ---------------- 批量选择 ----------------
const memberTableRef = ref(null)
const selectedMembers = ref([])

function onMemberSelectionChange(rows) {
  selectedMembers.value = rows
}

function clearMemberSelection() {
  memberTableRef.value?.clearSelection()
  selectedMembers.value = []
}

async function runBatchRemoveMembers() {
  await batchRemoveMembers()
  clearMemberSelection()
}

const rejectVisible = ref(false)
const rejectComment = ref('')
const detailVisible = ref(false)
const current = ref(null)
const acting = ref(false)

// 「主讲」必须按课程创建者（teacher_id）判定，而不是当前登录用户：
// 否则协作教师（如 admin 之于数据结构课程）打开面板时会把自己显示成主讲、
// 把真正的创建者显示成协作教师——角色恰好搞反。
const ownerId = computed(() => props.teacherId ?? store.user?.user_id)
// 当前登录用户 id：任何人（含协作教师）都不能移除自己——自己的行不显示「移除」按钮；
// 主讲移除协作教师（撤销误邀请）的场景不受影响，因为那一行不是「自己」。
const selfId = computed(() => store.user?.user_id)

const progressColor = [
  { color: '#f56c6c', percentage: 30 },
  { color: '#e6a23c', percentage: 70 },
  { color: '#67c23a', percentage: 100 },
]

function fmtTime(t) {
  return formatDateTime(t)
}

const SOURCE_TEXT = { create: '创建', code: '加课码', invite: '邀请', apply: '申请', import: '导入' }
const sourceText = (s) => SOURCE_TEXT[s] || s || '—'

async function loadStats() {
  try {
    stats.value = await api.getMemberStats(props.courseId)
  } catch {
    stats.value = {}
  }
}

async function loadPending() {
  const data = await api.getCourseMembers(props.courseId, { status: 'pending', page_size: 100 })
  pendingRows.value = data.items || []
}

async function loadMembers() {
  const data = await api.getCourseMembers(props.courseId, {
    status: 'approved',
    role: roleFilter.value || undefined,
    keyword: keyword.value || undefined,
    page: page.value,
    page_size: pageSize.value,
  })
  memberRows.value = data.items || []
  total.value = data.total || 0
}

async function reload() {
  if (!props.courseId) return
  loading.value = true
  try {
    await loadStats()
    if (activeTab.value === 'pending') await loadPending()
    else await loadMembers()
  } catch (e) {
    ElMessage.error(e?.message || '加载成员数据失败')
  } finally {
    loading.value = false
  }
}

function onPageChange(p) {
  page.value = p
  loadMembers().catch(() => {})
}

function openReject(row) {
  current.value = row
  rejectComment.value = ''
  rejectVisible.value = true
}

async function doReject() {
  acting.value = true
  try {
    await api.rejectMember(props.courseId, current.value.user_id, rejectComment.value || null)
    ElMessage.success('已拒绝该申请')
    rejectVisible.value = false
    await reload()
    emit('refresh')
  } catch (e) {
    ElMessage.error(e?.message || '操作失败')
  } finally {
    acting.value = false
  }
}

async function approve(row) {
  acting.value = true
  try {
    await api.approveMember(props.courseId, row.user_id)
    ElMessage.success(`已同意 ${row.name || row.username} 加入课程`)
    await reload()
    emit('refresh')
  } catch (e) {
    ElMessage.error(e?.message || '操作失败')
  } finally {
    acting.value = false
  }
}

// 移除不再弹确认框：先从列表移除、5 秒内可撤销；撤销只刷新列表（请求尚未发出）
async function remove(row) {
  const index = memberRows.value.findIndex((m) => m.user_id === row.user_id)
  if (index >= 0) memberRows.value.splice(index, 1)
  scheduleRemove({
    text: `已将「${row.name || row.username}」移出课程`,
    run: async () => {
      await api.removeMember(props.courseId, row.user_id, true)
      await reload()
      emit('refresh')
    },
    onUndo: () => reload(),
  })
}

/** 批量移除：先确认一次，再进入 5 秒撤销窗口；课程所有者与自己不参与批量移除 */
async function batchRemoveMembers() {
  const rows = selectedMembers.value.filter((m) => m.role !== 'owner' && m.user_id !== store.user?.user_id)
  const skipped = selectedMembers.value.length - rows.length
  if (!rows.length) {
    ElMessage.info('选中的成员里没有可移除的对象（课程所有者不能移除）')
    return
  }
  const ok = await ElMessageBox.confirm(
    `确定把选中的 ${rows.length} 名成员移出本课程吗？他们的学习记录与收藏会保留，但将无法再访问本课程内容。`,
    '批量移除成员',
    { type: 'warning', confirmButtonText: '确认移除', cancelButtonText: '取消' },
  ).catch(() => false)
  if (!ok) return
  const ids = rows.map((m) => m.user_id)
  memberRows.value = memberRows.value.filter((m) => !ids.includes(m.user_id))
  if (skipped > 0) ElMessage.info(`已跳过 ${skipped} 名不可移除的成员（课程所有者）`)
  scheduleRemove({
    text: `已移除 ${ids.length} 名成员`,
    run: async () => {
      for (const id of ids) {
        try {
          await api.removeMember(props.courseId, id, true)
        } catch {
          /* 单条失败不阻断其余，最后统一刷新 */
        }
      }
      await reload()
      emit('refresh')
    },
    onUndo: () => reload(),
  })
}

function openDetail(row) {
  current.value = row
  detailVisible.value = true
}

watch(() => props.courseId, () => {
  page.value = 1
  keyword.value = ''
  roleFilter.value = ''
  reload()
}, { immediate: true })
</script>

<style scoped>
.stat-row {
  display: flex;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}
.stat-box {
  flex: 1;
  background: var(--color-bg-soft);
  border-radius: var(--radius-md);
  padding: 12px 8px;
  text-align: center;
}
.stat-box.highlight {
  background: #fef0f0;
}
.stat-num {
  font-size: 20px;
  font-family: var(--font-family-number);
  font-weight: var(--font-weight-bold);
  color: var(--color-text-primary);
}
.stat-box.highlight .stat-num {
  color: var(--color-danger);
}
.stat-label {
  font-size: 12px;
  color: var(--color-text-secondary);
  margin-top: 2px;
}

.toolbar {
  display: flex;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
  flex-wrap: wrap;
}

.member-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 批量操作条：勾选后出现在表格上方 */
.batch-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  padding: 8px 12px;
  border-radius: 8px;
  background: #f2f6ff;
  border: 1px solid #d8e4ff;
}

.batch-count {
  font-size: 13px;
  color: #3d4d66;
  margin-right: 4px;
}

.batch-count b {
  color: #2f6fd0;
}
.member-name {
  min-width: 0;
}
.member-sub {
  font-size: 11px;
  color: var(--color-text-muted);
}
.co-teacher-na {
  font-size: 12px;
  color: var(--color-text-muted);
  font-style: italic;
}

.tab-label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.pager {
  margin-top: var(--space-3);
  justify-content: flex-end;
}

.reject-target {
  font-size: 13px;
  color: var(--color-text-regular);
  margin-bottom: 8px;
}

.detail-body {
  font-size: 13px;
}
.detail-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 6px 0;
  color: var(--color-text-regular);
}
.detail-label {
  color: var(--color-text-muted);
  flex-shrink: 0;
}
.hint {
  margin-top: var(--space-4);
  font-size: 12px;
  color: var(--color-text-muted);
  line-height: 1.6;
}

/* ===== v2 视觉增强 ===== */
.stat-box {
  background: #fff;
  border: 1px solid var(--border-light);
  box-shadow: var(--shadow-card);
  transition: transform .22s ease, box-shadow .22s ease;
}
.stat-box:hover { transform: translateY(-2px); box-shadow: var(--shadow-hover); }
.stat-num { color: var(--brand-600); }
.stat-box.highlight {
  background: linear-gradient(135deg, rgba(245,71,93,.08), rgba(245,71,93,.03));
  border-color: rgba(245,71,93,.3);
}
.member-cell :deep(.el-avatar) {
  background: var(--gradient-brand);
  font-weight: 600;
}
</style>
