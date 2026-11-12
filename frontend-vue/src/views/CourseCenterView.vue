<template>
  <div class="course-center">
    <PageHeader
      title="课程中心"
      :desc="isTeacher
        ? '发现公开课程、通过加课码加入其他课程（管理自己的课程请前往「课程管理」）'
        : '加入课程、继续学习、查看知识图谱与学习路径'"
    />

    <el-tabs v-model="activeTab" class="cc-tabs" @tab-change="onTabChange">
      <!-- ============ 我的课程（仅学生；教师的课程管理在「课程管理」页，避免两处内容重复） ============ -->
      <el-tab-pane v-if="!isTeacher" name="mine">
        <template #label><span class="tab-label">我的课程</span></template>

        <div class="toolbar">
          <el-input
            v-model="mineKeyword"
            placeholder="搜索我的课程"
            clearable
            style="width: 240px"
            @keyup.enter="loadMine"
            @clear="loadMine"
          >
            <template #append>
              <el-button :icon="Search" @click="loadMine" />
            </template>
          </el-input>
          <el-button :icon="Refresh" @click="loadMine">刷新</el-button>
          <div class="toolbar-right">
            <el-button v-if="isTeacher" type="primary" :icon="Plus" @click="createVisible = true">
              创建课程
            </el-button>
          </div>
        </div>

        <MyCourseGrid
          :courses="mineCourses"
          :role="store.role"
          :loading="loadingMine"
          :show-create="isTeacher"
          :empty-text="isTeacher ? '还没有课程，先创建一门吧' : '还没有加入任何课程'"
          @create="createVisible = true"
          @enter="enterCourse"
          @members="goMembers"
          @settings="openSettings"
          @delete="removeCourse"
          @leave="leaveCourse"
        >
          <template #empty-action>
            <el-button v-if="!isTeacher" type="primary" @click="activeTab = 'join'">
              去加入课程
            </el-button>
          </template>
        </MyCourseGrid>
      </el-tab-pane>

      <!-- ============ 发现课程（全角色：浏览公开课程并申请加入） ============ -->
      <el-tab-pane name="discover">
        <template #label><span class="tab-label">发现课程</span></template>

        <div class="toolbar">
          <el-input
            v-model="discoverQuery.keyword"
            placeholder="搜索课程名称或简介"
            clearable
            style="width: 260px"
            @keyup.enter="searchDiscover"
            @clear="searchDiscover"
          >
            <template #append>
              <el-button :icon="Search" @click="searchDiscover" />
            </template>
          </el-input>
          <el-input
            v-model="discoverQuery.category"
            placeholder="按分类筛选"
            clearable
            style="width: 170px"
            @keyup.enter="searchDiscover"
            @clear="searchDiscover"
          />
          <el-button type="primary" @click="searchDiscover">搜索</el-button>
        </div>

        <el-alert
          class="discover-hint"
          type="info"
          :closable="false"
          show-icon
          title="已加入或正在审核的课程不会在这里重复显示，可在下方「申请中 / 未通过」或「我的课程」查看"
        />

        <div v-loading="loadingDiscover" class="discover-wrap">
          <el-empty
            v-if="!loadingDiscover && !discoverCourses.length"
            :image-size="90"
            description="没有找到符合条件的课程"
          />
          <div v-else class="course-grid">
            <el-card v-for="c in discoverCourses" :key="c.course_id" shadow="hover" class="discover-card">
              <div class="discover-title">{{ c.course_name }}</div>
              <div class="discover-sub">
                {{ c.teacher_name || '—' }}
                <span v-if="c.organization"> · {{ c.organization }}</span>
              </div>
              <div class="discover-desc">{{ c.description || '暂无课程简介' }}</div>
              <div class="discover-meta">
                <el-tag v-if="c.category" size="small" effect="plain" type="info">{{ c.category }}</el-tag>
                <el-tag size="small" effect="plain" :type="joinModeType(c.join_mode)">
                  {{ joinModeText(c.join_mode) }}
                </el-tag>
              </div>
              <div class="discover-stats">
                <span>{{ c.document_count ?? 0 }} 文档</span>
                <span>{{ c.node_count ?? 0 }} 知识点</span>
                <span>{{ c.member_count ?? 0 }} 成员</span>
              </div>
              <div class="discover-actions">
                <el-button
                  v-if="c.join_mode === 'closed'"
                  size="small"
                  disabled
                >已关闭加入</el-button>
                <el-button v-else size="small" type="primary" @click="openApply(c)">
                  {{ c.join_mode === 'auto' ? '直接加入' : '申请加入' }}
                </el-button>
              </div>
            </el-card>
          </div>

          <el-pagination
            v-if="discoverTotal > discoverQuery.page_size"
            class="pager"
            layout="prev, pager, next, total"
            :total="discoverTotal"
            :page-size="discoverQuery.page_size"
            :current-page="discoverQuery.page"
            @current-change="onDiscoverPage"
          />
        </div>
      </el-tab-pane>

      <!-- ============ 加入课程 ============ -->
      <el-tab-pane name="join">
        <template #label><span class="tab-label">加入课程</span></template>

        <div class="join-wrap">
          <el-card class="page-card join-card" shadow="never">
            <div class="join-title">使用加课码加入</div>
            <div class="join-tip">向任课教师索取 8 位加课码，输入后即可加入课程。</div>
            <div class="join-input-row">
              <el-input
                v-model="joinCode"
                placeholder="请输入加课码，例如 A2C4E6G8"
                maxlength="12"
                size="large"
                class="code-input"
                @keyup.enter="doJoin"
              />
              <el-button type="primary" size="large" :loading="joining" @click="doJoin">加入课程</el-button>
            </div>
            <el-input
              v-model="joinReason"
              type="textarea"
              :rows="2"
              maxlength="200"
              show-word-limit
              placeholder="选填：申请理由（若该课程需要审核，教师会看到）"
              class="reason-input"
            />
          </el-card>

          <el-card class="page-card join-card" shadow="never">
            <div class="join-title">使用邀请链接加入</div>
            <div class="join-tip">
              如果教师直接发给你一条邀请链接（形如 <code>/invite/xxxx</code>），
              在浏览器中打开它即可加入，无需加课码。
            </div>
            <div class="join-input-row">
              <el-input
                v-model="inviteLink"
                placeholder="粘贴邀请链接或令牌"
                size="large"
                @keyup.enter="openInviteLink"
              />
              <el-button size="large" @click="openInviteLink">打开</el-button>
            </div>
          </el-card>

          <el-alert
            v-if="!isTeacher"
            type="info"
            :closable="false"
            show-icon
            title="加入方式说明"
            description="「直接加入」的课程输入加课码后立即可学习；「审核后加入」的课程需要等教师同意，可在「我的课程 → 申请中」查看进度。"
          />
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- 申请中 / 未通过：学生与教师（申请加入其他课程时）查看审核进度与教师意见 -->
    <div v-if="pendingCourses.length" class="pending-section">
      <div class="section-title">申请中 / 未通过</div>
      <el-table :data="pendingCourses" size="small">
        <el-table-column label="课程" min-width="160" prop="course_name" />
        <el-table-column label="教师" width="120" prop="teacher_name" />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="row.my_status === 'rejected' ? 'danger' : 'warning'" effect="plain">
              {{ row.my_status === 'rejected' ? '未通过' : '等待教师审核' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="审核意见" min-width="160">
          <template #default="{ row }">{{ row.review_comment || '—' }}</template>
        </el-table-column>
        <el-table-column label="申请时间" width="140">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="120" align="right">
          <template #default="{ row }">
            <el-button
              v-if="row.my_status === 'rejected'"
              size="small"
              type="primary"
              plain
              :loading="pendingActionId === row.course_id"
              @click="reapply(row)"
            >再次申请</el-button>
            <el-button
              v-else
              size="small"
              plain
              :loading="pendingActionId === row.course_id"
              @click="cancelApplication(row)"
            >撤回申请</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 加入课程（发现课程）：join_mode='auto' 是直接入课，不产生待审核 -->
    <el-dialog
      v-model="applyVisible"
      :title="applying?.join_mode === 'auto' ? '加入课程（无需审核）' : '申请加入课程'"
      width="460px"
    >
      <div class="apply-course">{{ applying?.course_name }}</div>
      <div class="apply-sub">
        {{ applying?.teacher_name || '—' }}
        <span v-if="applying?.organization"> · {{ applying.organization }}</span>
      </div>
      <el-input
        v-model="applyReason"
        type="textarea"
        :rows="3"
        maxlength="200"
        show-word-limit
        :placeholder="applying?.join_mode === 'auto'
          ? '选填：给教师留言（该课程为「直接加入」，提交后立即可学习）'
          : '选填：给教师留言说明你的申请理由'"
      />
      <template #footer>
        <el-button @click="applyVisible = false">取消</el-button>
        <el-button type="primary" :loading="applying2" @click="doApply">
          {{ applying?.join_mode === 'auto' ? '加入课程' : '提交申请' }}
        </el-button>
      </template>
    </el-dialog>

    <CreateCourseDialog v-model="createVisible" @created="onCourseCreated" />
    <CourseSettingsDialog
      v-model="settingsVisible"
      :course="settingsCourse"
      :is-owner="settingsCourse?.is_owner !== false"
      @saved="loadMine"
      @deleted="loadMine"
    />
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Search } from '@element-plus/icons-vue'
import PageHeader from '../components/PageHeader.vue'
import MyCourseGrid from '../components/course/MyCourseGrid.vue'
import CreateCourseDialog from '../components/course/CreateCourseDialog.vue'
import CourseSettingsDialog from '../components/course/CourseSettingsDialog.vue'
import { api } from '../api'
import { formatDateTime } from '../utils/format'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const route = useRoute()
const router = useRouter()

const VALID_TABS = ['mine', 'discover', 'join']
const isTeacher = computed(() => store.role === 'teacher')

// 教师端不展示「我的课程」tab（与课程管理页重复），课程中心定位为「发现 / 加入课程」
const defaultTab = isTeacher.value ? 'discover' : 'mine'
const queryTab = String(route.query.tab || '')
const activeTab = ref(
  queryTab === 'mine' && isTeacher.value
    ? 'discover'
    : VALID_TABS.includes(queryTab) ? queryTab : defaultTab
)

const mineCourses = ref([])
const pendingCourses = ref([])
const loadingMine = ref(false)
const mineKeyword = ref('')

const discoverCourses = ref([])
const discoverTotal = ref(0)
const loadingDiscover = ref(false)
const discoverQuery = reactive({ keyword: '', category: '', page: 1, page_size: 12 })

const joinCode = ref('')
const joinReason = ref('')
const joining = ref(false)
const inviteLink = ref('')

const createVisible = ref(false)
const settingsVisible = ref(false)
const settingsCourse = ref(null)

const applyVisible = ref(false)
const applying = ref(null)
const applyReason = ref('')
const applying2 = ref(false)
// 「申请中 / 未通过」行内操作（撤回 / 再次申请）的 loading，值为 course_id
const pendingActionId = ref(null)

const JOIN_MODE = {
  auto: { text: '直接加入', type: 'success' },
  approval: { text: '审核后加入', type: 'warning' },
  closed: { text: '关闭加入', type: 'info' },
}
const joinModeText = (m) => (JOIN_MODE[m] || JOIN_MODE.approval).text
const joinModeType = (m) => (JOIN_MODE[m] || JOIN_MODE.approval).type
const fmtTime = (t) => formatDateTime(t)

/** 拉取「申请中 / 未通过」的课程（my_status 由后端在 /courses/my 中返回，pending 状态同时含被拒绝） */
async function loadPending() {
  try {
    const pd = await api.getMyCourses({ status: 'pending', page_size: 100 })
    pendingCourses.value = pd.items || []
  } catch (e) {
    pendingCourses.value = []
  }
}

async function loadMine() {
  loadingMine.value = true
  try {
    // 卡片的加课码 / 待审核数来自列表接口；这里直接用 /courses/my 保证 my_role 等字段齐全
    const data = await api.getMyCourses({
      status: 'approved',
      keyword: mineKeyword.value || undefined,
      page_size: 100,
    })
    mineCourses.value = data.items || []
    // 同步到全局课程列表，供教师/学生端既有的课程下拉框使用。
    // 这是次要同步：失败不影响本页列表，但必须提示，否则下拉框会停在旧数据且毫无迹象。
    store.fetchCourses(true).catch(() => {
      ElMessage.warning('课程下拉数据同步失败，页面顶部课程选择可能不是最新，请稍后刷新')
    })
    await loadPending()
  } catch (e) {
    ElMessage.error(e?.message || '加载我的课程失败')
  } finally {
    loadingMine.value = false
  }
}

async function loadDiscover() {
  loadingDiscover.value = true
  try {
    const data = await api.discoverCourses({
      keyword: discoverQuery.keyword || undefined,
      category: discoverQuery.category || undefined,
      page: discoverQuery.page,
      page_size: discoverQuery.page_size,
    })
    discoverCourses.value = data.items || []
    discoverTotal.value = data.total || 0
  } catch (e) {
    ElMessage.error(e?.message || '加载课程失败')
  } finally {
    loadingDiscover.value = false
  }
}

function searchDiscover() {
  discoverQuery.page = 1
  loadDiscover()
}

function onDiscoverPage(p) {
  discoverQuery.page = p
  loadDiscover()
}

function onTabChange(tab) {
  router.replace({ path: '/course-center', query: { tab } })
  if (tab === 'discover') loadDiscover()
  if (tab === 'mine') loadMine()
}

/** 课程卡片 → 进入课程学习空间（复用既有教师端 / 学生端工作区，不另起一套界面） */
function enterCourse(c) {
  store.currentCourseId = String(c.course_id)
  // 同一课程重复进入时保留已选的学习资料（否则回到「学习总览」数据会丢失）；
  // 切换到不同课程才清空文档层，禁止沿用上一课程的文档
  const sameCourse =
    String(store.learningContext.currentCourseId || '') === String(c.course_id)
  if (sameCourse) {
    store.setLearningContext({ courseId: c.course_id })
  } else {
    store.clearLearningDocument()
    store.setLearningContext({ courseId: c.course_id, documentId: null })
  }
  if (isTeacher.value) {
    router.push({ path: '/teacher', query: { tab: 'documents', course_id: String(c.course_id) } })
  } else {
    router.push({ path: '/student', query: { tab: 'documents', course_id: String(c.course_id) } })
  }
}

function goMembers(c) {
  store.currentCourseId = String(c.course_id)
  router.push({ path: '/teacher', query: { tab: 'members', course_id: String(c.course_id) } })
}

function openSettings(c) {
  settingsCourse.value = c
  settingsVisible.value = true
}

function onCourseCreated() {
  loadMine()
}

async function removeCourse(c) {
  // 协作教师没有删除权：提前提示，避免走到后端再吃一个 403
  if (c?.is_owner === false) {
    ElMessage.warning('协作教师不能删除课程，如需删除请联系课程创建者')
    return
  }
  const ok = await ElMessageBox.confirm(
    `确定删除课程「${c.course_name}」吗？\n\n该课程的文档、知识图谱、学习记录、收藏、成员与邀请都会一并删除，且不可恢复。`,
    '删除课程',
    { type: 'error', confirmButtonText: '确认删除', cancelButtonText: '取消' },
  ).catch(() => false)
  if (!ok) return
  try {
    await store.deleteCourse(c.course_id)
    ElMessage.success('课程已删除')
    loadMine()
  } catch (e) {
    ElMessage.error(e?.message || '删除失败')
  }
}

async function leaveCourse(c) {
  const ok = await ElMessageBox.confirm(
    `确定退出课程「${c.course_name}」吗？\n\n退出后你将无法再查看该课程的文档与知识图谱；你的学习记录与收藏会保留。`,
    '退出课程',
    { type: 'warning', confirmButtonText: '确认退出', cancelButtonText: '取消' },
  ).catch(() => false)
  if (!ok) return
  try {
    await store.leaveCourse(c.course_id)
    ElMessage.success('已退出该课程')
    loadMine()
  } catch (e) {
    ElMessage.error(e?.message || '退出失败')
  }
}

async function doJoin() {
  const code = (joinCode.value || '').trim()
  if (!code) {
    ElMessage.warning('请输入加课码')
    return
  }
  joining.value = true
  try {
    const data = await store.joinByCode(code, joinReason.value?.trim() || null)
    if (data.status === 'approved') {
      ElMessage.success(`已加入课程「${data.course_name}」`)
    } else {
      ElMessage.success(`已提交申请，等待教师审核（课程：${data.course_name}）`)
    }
    joinCode.value = ''
    joinReason.value = ''
    await loadMine()
    if (data.status === 'approved') {
      // 教师加入其他教师的课程后，课程会出现在「课程管理」列表里：
      // 刷新全局课程缓存并带他过去，避免加入成功却不知道去哪里找。
      await store.fetchCourses(true).catch(() => {})
      if (isTeacher.value) {
        router.push({ path: '/teacher', query: { tab: 'courses' } })
        return
      }
    }
    if (!isTeacher.value) {
      activeTab.value = 'mine'
      onTabChange('mine')
    }
  } catch (e) {
    // 后端对「码无效 / 已关闭加入 / 已是成员」都给了明确中文提示，直接透出
    ElMessage.error(e?.message || '加入失败')
  } finally {
    joining.value = false
  }
}

function openApply(c) {
  applying.value = c
  applyReason.value = ''
  applyVisible.value = true
}

async function doApply() {
  applying2.value = true
  try {
    const data = await store.applyToCourse(applying.value.course_id, applyReason.value?.trim() || null)
    // 「直接加入」的课程后端会直接 approved，别再说「等待教师审核」
    if (data?.status === 'approved') {
      ElMessage.success(`已加入课程「${data.course_name}」`)
      await loadMine()
      await store.fetchCourses(true).catch(() => {})
      if (isTeacher.value) {
        applyVisible.value = false
        router.push({ path: '/teacher', query: { tab: 'courses' } })
        return
      }
      activeTab.value = 'mine'
      onTabChange('mine')
    } else {
      ElMessage.success('已提交申请，等待教师审核')
    }
    applyVisible.value = false
    await loadDiscover()
    await loadPending()
  } catch (e) {
    ElMessage.error(e?.message || '申请失败')
  } finally {
    applying2.value = false
  }
}

/** 未通过 → 再次申请：复用发现页的申请弹窗（后端对 rejected 记录允许重新申请） */
function reapply(row) {
  openApply(row)
}

/** 审核中 → 撤回申请：删除待审核记录，课程会重新出现在发现页 */
async function cancelApplication(row) {
  const ok = await ElMessageBox.confirm(
    `确定撤回对「${row.course_name}」的加入申请吗？撤回后可随时重新申请。`,
    '撤回申请',
    { type: 'warning', confirmButtonText: '确认撤回', cancelButtonText: '取消' },
  ).catch(() => false)
  if (!ok) return
  pendingActionId.value = row.course_id
  try {
    await api.cancelApplication(row.course_id)
    ElMessage.success('已撤回申请')
    await loadPending()
    if (activeTab.value === 'discover') await loadDiscover()
  } catch (e) {
    ElMessage.error(e?.message || '撤回失败')
  } finally {
    pendingActionId.value = null
  }
}

/** 从「加入课程」页粘贴邀请链接：抽取 token 后跳到落地页 */
function openInviteLink() {
  const raw = (inviteLink.value || '').trim()
  if (!raw) return
  const m = raw.match(/invite\/([A-Za-z0-9_-]+)/)
  const token = m ? m[1] : raw
  router.push({ name: 'invite', params: { token } })
}

onMounted(() => {
  // 学生与教师都加载「申请中 / 未通过」，保证两端状态展示一致
  loadPending()
  if (isTeacher.value) {
    // 教师端课程中心默认落在「发现课程」，必须初始加载（原条件排除了教师，导致进来是空白）
    loadDiscover()
  } else {
    loadMine()
    if (activeTab.value === 'discover') loadDiscover()
  }
})
</script>

<style scoped>
.course-center {
  padding-bottom: var(--space-4);
}

.cc-tabs :deep(.el-tabs__header) {
  margin-bottom: var(--space-4);
}

.toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-4);
  flex-wrap: wrap;
}
.toolbar-right {
  margin-left: auto;
}

.pending-section {
  margin-top: var(--space-5);
}
.section-title {
  font-size: var(--font-size-section);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
  margin-bottom: var(--space-3);
}

.discover-hint {
  margin-bottom: var(--space-3);
}
.discover-wrap {
  min-height: 200px;
}
.course-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(268px, 1fr));
  gap: var(--space-4);
}
.discover-card {
  border-radius: var(--radius-lg);
}
.discover-title {
  font-size: var(--font-size-body);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.discover-sub {
  font-size: 12px;
  color: var(--color-text-secondary);
  margin-top: 2px;
}
.discover-desc {
  font-size: 12px;
  color: var(--color-text-muted);
  line-height: 1.5;
  height: 36px;
  margin: 8px 0;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.discover-meta {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.discover-stats {
  display: flex;
  gap: var(--space-3);
  font-size: 12px;
  color: var(--color-text-secondary);
  margin: 10px 0;
  padding-top: 8px;
  border-top: 1px solid var(--color-border-light);
}
.discover-actions {
  text-align: right;
}

.pager {
  margin-top: var(--space-4);
  justify-content: flex-end;
}

.join-wrap {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  max-width: 720px;
}
.join-card :deep(.el-card__body) {
  padding: var(--space-5);
}
.join-title {
  font-size: var(--font-size-section);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
}
.join-tip {
  font-size: 12px;
  color: var(--color-text-muted);
  margin: 6px 0 var(--space-3);
  line-height: 1.6;
}
.join-input-row {
  display: flex;
  gap: var(--space-2);
}
.code-input :deep(input) {
  font-family: var(--font-family-number);
  letter-spacing: 2px;
  text-transform: uppercase;
}
.reason-input {
  margin-top: var(--space-3);
}

.apply-course {
  font-size: var(--font-size-body);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
}
.apply-sub {
  font-size: 12px;
  color: var(--color-text-secondary);
  margin: 4px 0 var(--space-3);
}

/* ===== v2 视觉增强 ===== */
.discover-card {
  border: 1px solid var(--border-light);
  box-shadow: var(--shadow-card);
  position: relative;
  overflow: hidden;
  transition: transform .25s ease, box-shadow .25s ease, border-color .25s ease;
}
.discover-card::before {
  content: '';
  position: absolute;
  inset: 0 0 auto 0;
  height: 3px;
  background: var(--gradient-brand);
  opacity: 0;
  transition: opacity .25s;
}
.discover-card:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-hover);
  border-color: var(--brand-200);
}
.discover-card:hover::before { opacity: 1; }
.discover-title { font-size: 15px; }
.discover-stats { border-top-color: var(--border-light); }
.join-card {
  border-radius: var(--radius-lg);
  position: relative;
  overflow: hidden;
}
.join-card::before {
  content: '';
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 4px;
  background: var(--gradient-brand);
}
.join-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
}
.join-title::before {
  content: '';
  width: 6px; height: 6px;
  border-radius: 50%;
  background: var(--brand-500);
  box-shadow: 0 0 0 4px var(--brand-50);
}
</style>
