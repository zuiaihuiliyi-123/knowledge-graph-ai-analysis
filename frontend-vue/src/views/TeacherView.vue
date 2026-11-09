<template>
  <div>
    <PageHeader title="课程管理" desc="管理课程、课程文档、知识图谱、题库与教学监测" />

    <el-tabs v-model="activeTab" class="main-view-tabs" @tab-change="onTabChange">
      <!-- ===================== Tab：课程管理 ===================== -->
      <el-tab-pane name="courses">
        <template #label><span class="tab-label"><el-icon><Notebook /></el-icon>课程管理</span></template>
        <TeacherCoursesTab
          :courses="store.courses"
          :loading="store.isLoading"
          @create="openCreateCourse"
          @invite="openInviteForCurrent"
          @enter="manageDocuments"
          @members="goMembers"
          @leave="leaveCourse"
          @settings="openSettings"
          @delete="deleteCourse"
        />
      </el-tab-pane>

      <!-- ===================== Tab：课程文档 ===================== -->
      <el-tab-pane name="documents">
        <template #label><span class="tab-label"><el-icon><FolderOpened /></el-icon>课程文档</span></template>
        <TeacherDocumentsTab
          :active="activeTab === 'documents'"
          :courses="store.courses"
          :course-id="currentCourseId"
          :course-name="currentCourseName"
          :course-code="currentCourseCode"
          :documents="documents"
          :documents-loading="documentsLoading"
          :reload-documents="loadDocuments"
          :refresh-documents="refreshDocuments"
          @update:course-id="selectCourseForTab"
          @back="backToCourses"
          @read="readDocument"
          @monitor="monitorDocument"
          @create-course="openCreateCourse"
          @go-courses="goCourses"
        />
      </el-tab-pane>

      <!-- ===================== Tab：学生管理 ===================== -->
      <el-tab-pane name="members">
        <template #label><span class="tab-label"><el-icon><UserFilled /></el-icon>学生管理</span></template>
        <TeacherMembersTab
          :courses="store.courses"
          :course-id="currentCourseId"
          :course-name="currentCourseName"
          :teacher-id="currentCourse?.teacher_id"
          @update:course-id="selectCourseForTab"
          @back="backToCourses"
          @invite="openInviteForCurrent"
          @go-courses="goCourses"
          @refresh-courses="reloadCoursesSilently"
        />
      </el-tab-pane>

      <!-- ===================== Tab：图谱管理（查看 + 就地编辑） ===================== -->
      <el-tab-pane name="preview">
        <template #label><span class="tab-label"><el-icon><View /></el-icon>图谱管理</span></template>
        <TeacherGraphTab
          v-model:edit-mode="graphEditMode"
          :active="activeTab === 'preview'"
          :courses="store.courses"
          :course-id="currentCourseId"
          :document-id="currentDocumentId"
          :course-name="currentCourseName"
          :document-name="currentDocumentName"
          :current-document="currentDocument"
          :documents="documents"
          @update:course-id="selectCourseForTab"
          @update:document-id="selectDocument"
          @switch-document="switchDocument"
          @read-document="readCurrentDocument"
          @back="backToDocuments"
          @go-courses="goCourses"
        />
      </el-tab-pane>

      <!-- ===================== Tab：教学监测 ===================== -->
      <el-tab-pane name="monitor">
        <template #label><span class="tab-label"><el-icon><UserFilled /></el-icon>教学监测</span></template>
        <TeacherMonitorTab
          :active="activeTab === 'monitor'"
          :courses="store.courses"
          :course-id="currentCourseId"
          :course-name="currentCourseName"
          :document-name="currentDocumentName"
          :current-document-id="currentDocumentId"
          @update:course-id="selectCourseForTab"
          @back="backToDocuments"
          @go-courses="goCourses"
          @view-graph="goMonitorGraph"
        />
      </el-tab-pane>

      <!-- ===================== Tab：题库管理 ===================== -->
      <el-tab-pane name="questions">
        <template #label><span class="tab-label"><el-icon><Collection /></el-icon>题库管理</span></template>
        <TeacherQuestionsTab
          :active="activeTab === 'questions'"
          :courses="store.courses"
          :course-id="currentCourseId"
          :course-name="currentCourseName"
          :documents="documents"
          :ensure-documents="ensureDocuments"
          @update:course-id="selectCourseForTab"
          @go-courses="goCourses"
          @go-grading="gotoGrading"
        />
      </el-tab-pane>

      <!-- ===================== Tab：主观题批改 ===================== -->
      <el-tab-pane name="grading">
        <template #label>
          <span class="tab-label">
            <el-icon><EditPen /></el-icon>主观题批改
            <el-badge v-if="gradingPending" :value="gradingPending" class="tab-badge" />
          </span>
        </template>
        <TeacherGradingTab
          ref="gradingTabRef"
          :active="activeTab === 'grading'"
          :courses="store.courses"
          :course-id="currentCourseId"
          :course-name="currentCourseName"
          :documents="documents"
          @update:course-id="selectCourseForTab"
          @pending-change="gradingPending = $event"
          @go-courses="goCourses"
        />
      </el-tab-pane>
    </el-tabs>

    <!-- 新建课程（文档/图谱/监测/题库空态也可触发） -->
    <CreateCourseDialog v-model="createCourseVisible" @created="onCourseCreated" />

    <!-- 课程设置（含加课码刷新与删除课程） -->
    <CourseSettingsDialog
      v-model="settingsVisible"
      :course="settingsCourse"
      @saved="reloadCoursesSilently"
      @deleted="onCourseDeleted"
    />

    <!-- 邀请学生 / 协作教师 -->
    <InviteDialog v-model="inviteVisible" :course-id="inviteCourseId" />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Notebook, FolderOpened, UserFilled, View, Collection, EditPen,
} from '@element-plus/icons-vue'
import { useAppStore } from '../stores/app'
import PageHeader from '../components/PageHeader.vue'
import CreateCourseDialog from '../components/course/CreateCourseDialog.vue'
import CourseSettingsDialog from '../components/course/CourseSettingsDialog.vue'
import InviteDialog from '../components/course/InviteDialog.vue'
import TeacherCoursesTab from '../components/teacher/TeacherCoursesTab.vue'
import TeacherDocumentsTab from '../components/teacher/TeacherDocumentsTab.vue'
import TeacherMembersTab from '../components/teacher/TeacherMembersTab.vue'
import TeacherGraphTab from '../components/teacher/TeacherGraphTab.vue'
import TeacherMonitorTab from '../components/teacher/TeacherMonitorTab.vue'
import TeacherQuestionsTab from '../components/teacher/TeacherQuestionsTab.vue'
import TeacherGradingTab from '../components/teacher/TeacherGradingTab.vue'
import { useDocumentList } from '../composables/teacher/useDocumentList'

const store = useAppStore()
const route = useRoute()
const router = useRouter()

const TEACHER_TABS = ['courses', 'documents', 'members', 'preview', 'edit', 'monitor', 'questions', 'grading']

const activeTab = ref(TEACHER_TABS.includes(route.query.tab) ? route.query.tab : 'courses')

// 图谱管理：「查看图谱」与「编辑图谱」整合为同一 Tab，graphEditMode 控制就地切换编辑界面
const graphEditMode = ref(false)

// ===================== 当前上下文：课程 + 文档 =====================
const currentCourseId = ref('')
const currentDocumentId = ref('')

const currentCourse = computed(() => store.courseById(currentCourseId.value) || null)
const currentCourseName = computed(() => {
  const c = currentCourse.value
  return c ? c.course_name : (currentCourseId.value ? `课程 #${currentCourseId.value}` : '')
})
const currentCourseCode = computed(() => currentCourse.value?.course_code || '')
const currentDocument = computed(() =>
  documents.value.find((d) => String(d.doc_id) === String(currentDocumentId.value)) || null,
)
const currentDocumentName = computed(() => currentDocument.value?.file_name || '')

// 文档列表是页面级共享数据：课程文档 / 图谱 / 题库 / 批改都要用
const {
  documents,
  documentsLoading,
  loadDocuments,
  ensureDocuments,
  refreshDocuments,
} = useDocumentList(currentCourseId)

// 批改 Tab 角标（由批改页在加载汇总后回传，避免页面为了一个数字再请求一次）
const gradingPending = ref(0)
const gradingTabRef = ref(null)

// ===================== 课程 CRUD / 弹窗 =====================
const createCourseVisible = ref(false)
const settingsVisible = ref(false)
const settingsCourse = ref(null)
const inviteVisible = ref(false)
const inviteCourseId = ref('')

function openCreateCourse() {
  createCourseVisible.value = true
}

/** 新建课程成功：同步列表；若当前在文档页空状态创建，则直接进入该课程的文档管理 */
function onCourseCreated(created) {
  store.fetchCourses(true).catch(() => {})
  if (activeTab.value === 'documents' && created?.course_id != null) {
    router.replace({
      path: '/teacher',
      query: { tab: 'documents', course_id: String(created.course_id) },
    })
  }
}

/** 课程被删除：若当前上下文正是它，清空后回到课程列表 */
function onCourseDeleted(course) {
  store.fetchCourses(true).catch(() => {})
  if (String(course?.course_id) === String(currentCourseId.value)) {
    currentCourseId.value = ''
    currentDocumentId.value = ''
    router.replace({ path: '/teacher', query: { tab: 'courses' } })
  }
}

function reloadCoursesSilently() {
  store.fetchCourses(true).catch(() => {})
}

function openSettings(course) {
  settingsCourse.value = course
  settingsVisible.value = true
}

/** 「邀请学生」：优先当前课程，否则退回第一门课程 */
function openInviteForCurrent() {
  const cid = currentCourseId.value || store.courses[0]?.course_id
  if (!cid) {
    ElMessage.warning('请先创建或选择一门课程')
    return
  }
  inviteCourseId.value = String(cid)
  inviteVisible.value = true
}

async function deleteCourse(course) {
  const id = String(course.course_id)
  try {
    await ElMessageBox.confirm(
      `确定删除课程「${course.course_name}」吗？将同时删除该课程下的文档、图谱数据及学习记录，此操作不可恢复。`,
      '删除课程',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger',
      },
    )
  } catch {
    return
  }
  try {
    await store.deleteCourse(id)
    ElMessage.success('课程已删除')
    if (String(currentCourseId.value) === id) {
      currentCourseId.value = ''
      currentDocumentId.value = ''
    }
  } catch (e) {
    ElMessage.error(`删除失败：${e.message}`)
  }
}

async function leaveCourse(course) {
  const id = String(course.course_id)
  try {
    await ElMessageBox.confirm(
      `确定退出课程「${course.course_name}」吗？退出后将无法查看该课程内容。`,
      '退出课程',
      { type: 'warning', confirmButtonText: '退出', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await store.leaveCourse(id)
    ElMessage.success('已退出课程')
    if (String(currentCourseId.value) === id) {
      currentCourseId.value = ''
      currentDocumentId.value = ''
    }
  } catch (e) {
    ElMessage.error(`退出失败：${e.message}`)
  }
}

// ===================== 导航与路由同步 =====================
function goCourses() {
  router.push({ path: '/teacher', query: { tab: 'courses' } })
}

function manageDocuments(course) {
  // 入参是课程卡片对象（不是 id）：必须取 course.course_id
  router.push({ path: '/teacher', query: { tab: 'documents', course_id: String(course.course_id) } })
}

function goMembers(course) {
  router.push({ path: '/teacher', query: { tab: 'members', course_id: String(course.course_id) } })
}

function backToCourses() {
  goCourses()
}

function backToDocuments() {
  if (currentCourseId.value) {
    router.push({ path: '/teacher', query: { tab: 'documents', course_id: currentCourseId.value } })
  } else {
    goCourses()
  }
}

/**
 * 各 Tab 内选择课程：只改路由，上下文与数据加载全部交给 route watcher / 子组件 watch。
 * 统一切回当前 Tab（原「学生管理」误用了课程文档的同步函数，会把用户带离当前页）。
 */
function selectCourseForTab(id) {
  currentDocumentId.value = ''
  router
    .replace({
      path: '/teacher',
      query: { tab: activeTab.value, course_id: id ? String(id) : undefined },
    })
    .catch(() => {})
}

/** 图谱管理页内选择文档 */
function selectDocument(id) {
  router
    .replace({
      path: '/teacher',
      query: {
        tab: 'preview',
        course_id: currentCourseId.value || undefined,
        document_id: id ? String(id) : undefined,
      },
    })
    .catch(() => {})
}

/** 「切换文档」选择器确认：换 (课程, 文档) 作用域 */
function switchDocument({ courseId: cid, documentId: did }) {
  if (!cid || !did) return
  router
    .replace({
      path: '/teacher',
      query: { tab: 'preview', course_id: String(cid), document_id: String(did) },
    })
    .catch(() => {})
}

function onTabChange(name) {
  if (name === 'courses') {
    router.replace({ path: '/teacher', query: { tab: 'courses' } })
  } else if (name === 'documents') {
    router.replace({
      path: '/teacher',
      query: { tab: 'documents', course_id: currentCourseId.value || undefined },
    })
  } else {
    router.replace({
      path: '/teacher',
      query: {
        tab: name,
        course_id: currentCourseId.value || undefined,
        document_id: currentDocumentId.value || undefined,
      },
    })
  }
}

/** 题库「待批改」卡片直达批改页（回到第一页，避免沿用上次翻到的页码） */
function gotoGrading() {
  gradingTabRef.value?.resetForDirectEntry?.()
  router
    .replace({
      path: '/teacher',
      query: { tab: 'grading', course_id: currentCourseId.value || undefined },
    })
    .catch(() => {})
}

/** 监测抽屉「查看知识图谱」 */
function goMonitorGraph() {
  if (!currentCourseId.value || !currentDocumentId.value) return
  graphEditMode.value = false
  router.push({
    path: '/teacher',
    query: { tab: 'preview', course_id: currentCourseId.value, document_id: currentDocumentId.value },
  })
}

/** 在线阅读：进入独立的文档阅读器整页（from=teacher 决定返回时回到课程管理） */
function readDocument(doc) {
  router.push({
    name: 'reader',
    params: { docId: String(doc.doc_id) },
    query: { course_id: String(doc.course_id), from: 'teacher' },
  })
}

/** 图谱管理页「在线阅读文档」：返回时回到图谱管理 */
function readCurrentDocument() {
  const doc = currentDocument.value
  if (!doc) return
  router.push({
    name: 'reader',
    params: { docId: String(doc.doc_id) },
    query: {
      course_id: String(doc.course_id ?? currentCourseId.value),
      from: 'teacher-graph',
    },
  })
}

function monitorDocument(doc) {
  router.push({
    path: '/teacher',
    query: { tab: 'monitor', course_id: String(doc.course_id), document_id: String(doc.doc_id) },
  })
}

// ===================== 路由同步 + 守卫 =====================
// 深链 /teacher?tab=xxx&course_id=yyy&document_id=zzz：同步上下文并守卫缺失参数
watch(
  () => [route.query.tab, route.query.course_id, route.query.document_id],
  ([tab, cid, did]) => {
    if (!tab) {
      activeTab.value = 'courses'
      currentCourseId.value = ''
      currentDocumentId.value = ''
      return
    }
    // 图谱管理整合：旧链接 tab=edit 并入 preview 并直接进入编辑模式
    const targetTab = tab === 'edit' ? 'preview' : tab
    if (tab === 'edit') graphEditMode.value = true
    if (!TEACHER_TABS.includes(targetTab)) {
      activeTab.value = 'courses'
      return
    }
    if (['preview', 'monitor'].includes(targetTab)) {
      // 缺少课程/文档时停留在当前页，由页内级联选择器引导
      currentCourseId.value = cid ? String(cid) : ''
      currentDocumentId.value = did ? String(did) : ''
    } else if (tab === 'documents' || tab === 'members' || tab === 'questions' || tab === 'grading') {
      // 这几个 Tab 只需要课程（不需要文档）
      currentCourseId.value = cid ? String(cid) : ''
      currentDocumentId.value = ''
    } else {
      currentCourseId.value = ''
      currentDocumentId.value = ''
    }
    activeTab.value = targetTab
  },
  { immediate: true },
)

// 课程 Tab 每次进入刷新课程列表；图谱页需要文档列表（上下文条/在线阅读），
// 若之前加载失败则重试。其他 Tab 的数据由各自组件按需加载。
watch(activeTab, (tab) => {
  if (tab === 'courses') store.fetchCourses(true).catch(() => {})
  if (tab === 'preview' && currentCourseId.value && !documents.value.length) loadDocuments()
})

// ===== 未保存保护：编辑模式下离开页面，未提交的节点/关系表单会丢失 =====
// 页内切 Tab 不拦：el-tab-pane 是 v-show 保活，表单内容还在（拦了反而烦）
onBeforeRouteLeave(async (to) => {
  if (to.path === '/teacher' || !graphEditMode.value) return true
  try {
    await ElMessageBox.confirm(
      '图谱处于编辑模式，离开后未保存的节点/关系编辑内容会丢失。确定离开吗？',
      '离开编辑模式',
      { type: 'warning', confirmButtonText: '离开', cancelButtonText: '留在本页' },
    )
    return true
  } catch {
    return false
  }
})

function handleBeforeUnload(e) {
  if (!graphEditMode.value) return
  e.preventDefault()
  e.returnValue = ''
}
window.addEventListener('beforeunload', handleBeforeUnload)
onBeforeUnmount(() => window.removeEventListener('beforeunload', handleBeforeUnload))

// 教师端也复用全局 AI 悬浮窗：把当前课程/文档同步到 store.learningContext。
// immediate 必须加：深链进来时 route watcher 已在 setup 阶段写好 currentCourseId。
watch([currentCourseId, currentDocumentId], ([cid, did]) => {
  store.setLearningContext({ courseId: cid || null, documentId: did || null })
}, { immediate: true })

// 深链「直接打开图谱」：仅当 URL 带 open=1 时自动选中该课程首个可用文档。
watch(
  () => route.query.open,
  async (open) => {
    if (open !== '1') return
    const cid = currentCourseId.value
    if (!cid) return
    // 与课程 watch 共用同一个在途请求，避免后发请求把先发请求作废后读到空列表
    if (!documents.value.length) await ensureDocuments()
    const usable = documents.value.filter((d) => !d.is_placeholder && !d.local_stage)
    const first = usable.find((d) => d.extract_status === 'COMPLETED') || usable[0]
    if (first) {
      currentDocumentId.value = String(first.doc_id)
      router.replace({
        query: { tab: 'preview', course_id: String(cid), document_id: String(first.doc_id) },
      })
    }
  },
  { immediate: true },
)

store.fetchCourses(true).catch(() => {})
</script>

<style scoped>
/* 顶部标签栏已由左侧菜单接管：隐藏标签头，仅保留面板切换机制。 */
.main-view-tabs > :deep(.el-tabs__header) {
  display: none;
}
.tab-badge {
  margin-left: 6px;
}
</style>
