<template>
  <div ref="rootEl" class="drv">
    <ReaderTopBar
      ref="topBarRef"
      :title="doc.fileName"
      :subtitle="courseName"
      :file-type="doc.fileType"
      :file-size="doc.fileSizeText"
      :doc-id="docId"
      :documents="courseDocs"
      :back-text="backText"
      :percent="percent"
      :page="page"
      :total-pages="totalPages"
      :show-paging="kind === 'pdf'"
      :zoom-label="Math.round(scale * 100) + '%'"
      :zoom-fit="zoomFit"
      :show-zoom="kind === 'pdf'"
      :searchable="kind !== 'docx' && kind !== 'none'"
      :search-placeholder="searchPlaceholder"
      :search-status="searchStatus"
      v-model:search-value="searchQuery"
      :sidebar-open="sidebarOpen"
      :panel-open="panelOpen"
      @back="goBack"
      @switch-document="switchDocument"
      @download="download"
      @fullscreen="toggleFullscreen"
      @toggle-sidebar="toggleSidebar"
      @toggle-panel="togglePanel"
      @prev-page="viewerCall('prevPage')"
      @next-page="viewerCall('nextPage')"
      @jump-page="(p) => viewerCall('goToPage', p)"
      @zoom-in="viewerCall('zoomIn')"
      @zoom-out="viewerCall('zoomOut')"
      @zoom-reset="viewerCall('zoomReset')"
      @zoom-fit="viewerCall('fitWidth')"
      @search="onSearch"
      @search-next="viewerCall('searchNext')"
      @search-prev="viewerCall('searchPrev')"
    />

    <!-- 续读提示：只在确实有上次进度时出现，且可一键从头开始 -->
    <div v-if="resumeNotice" class="drv-resume">
      <el-icon><Clock /></el-icon>
      <span>{{ resumeNotice }}</span>
      <button class="drv-resume-btn" type="button" @click="restartReading">从头开始</button>
      <button class="drv-resume-close" type="button" aria-label="关闭提示" @click="resumeNotice = ''">
        <el-icon><Close /></el-icon>
      </button>
    </div>

    <div class="drv-body">
      <DocumentSidebar
        v-if="sidebarOpen"
        :doc-key="docId"
        :mode="kind"
        :outline="outlineItems"
        :doc="doc"
        :course-name="courseName"
        :progress="{ percent, page, totalPages }"
        :prefs="prefs"
        :current-page="page"
        :viewer-api="viewerApi"
        :can-search="kind === 'pdf' || kind === 'text'"
        :outline-ready="outlineReady"
        @select="onOutlineSelect"
        @restart="restartReading"
        @update:prefs="onPrefsChange"
        @close="sidebarOpen = false"
      />

      <main ref="stageEl" class="drv-stage">
        <PdfViewer
          v-if="kind === 'pdf'"
          :key="'pdf-' + docId"
          ref="pdfRef"
          :url="contentUrl"
          :headers="authHeaders()"
          :restore="restoreState"
          @ready="onViewerReady"
          @error="onPdfError"
          @total-pages="(n) => (totalPages = n)"
          @page-change="(p) => (page = p)"
          @progress="(v) => (percent = v)"
          @zoom="onZoom"
          @outline="onOutline"
          @page-text="(t) => (visibleText = t)"
          @search-progress="onSearchProgress"
          @search-result="onSearchResult"
          @search-index="onSearchIndex"
        />

        <TextViewer
          v-else-if="kind === 'text'"
          :key="'text-' + docId"
          ref="textRef"
          :text="textContent"
          :font-size="prefs.fontSize"
          :line-height="prefs.lineHeight"
          :max-width="prefs.maxWidth"
          :restore="restoreState"
          @ready="onViewerReady"
          @progress="(v) => (percent = v)"
          @outline="onOutline"
          @page-text="(t) => (visibleText = t)"
          @search-result="onSearchResult"
          @search-index="onSearchIndex"
        />

        <DocxViewer
          v-else-if="kind === 'docx'"
          :key="'docx-' + docId"
          ref="docxRef"
          :buffer="docxBuffer"
          :restore="restoreState"
          @ready="onViewerReady"
          @progress="(v) => (percent = v)"
          @outline="onOutline"
          @page-text="(t) => (visibleText = t)"
        />

        <!-- 未确定类型 / 加载失败 / 不支持预览 -->
        <div v-else class="drv-state">
          <template v-if="loading">
            <el-icon class="drv-spin" :size="26"><Loading /></el-icon>
            <p class="drv-state-title">正在准备文档…</p>
          </template>
          <template v-else-if="error">
            <el-icon :size="30" color="#f56c6c"><WarningFilled /></el-icon>
            <p class="drv-state-title">无法打开文档</p>
            <p class="drv-state-sub">{{ error }}</p>
            <div class="drv-state-actions">
              <el-button size="small" plain @click="bootstrap">重试</el-button>
              <el-button size="small" text @click="goBack">返回课程</el-button>
            </div>
          </template>
          <template v-else>
            <el-icon :size="30" color="#e6a23c"><WarningFilled /></el-icon>
            <p class="drv-state-title">该格式暂不支持在线预览</p>
            <p class="drv-state-sub">
              当前支持 PDF、TXT、MD 与 DOCX。可以下载原文件后用本地应用打开。
            </p>
            <el-button size="small" type="primary" plain @click="download">下载原文件</el-button>
          </template>
        </div>
      </main>

      <DocumentKnowledgePanel
        v-if="panelOpen && kind !== 'none'"
        ref="panelRef"
        :course-id="courseId"
        :document-id="docId"
        :doc-name="doc.fileName"
        :visible-text="visibleText"
        :can-locate="kind === 'pdf' || kind === 'text'"
        :selection-text="selection ? selection.text : ''"
        :kind="kind"
        @close="panelOpen = false"
        @open-graph="openGraph"
        @locate="locateInDocument"
      />
    </div>

    <!-- 选中正文后的浮动学习菜单（fixed 定位，随选区出现在视口里） -->
    <SelectionMenu
      v-if="selection"
      :x="selection.x"
      :y="selection.y"
      :actions="selectionActions"
      @action="onSelectionAction"
      @dismiss="clearSelection"
    />
  </div>
</template>

<script setup>
/**
 * 课程文档在线阅读器（独立整页，不套主框架布局）。
 *
 * 职责划分：
 * - 本页负责「文档元信息 / 内容获取 / 进度存取 / 工具栏状态」，并把它转发给具体 Viewer
 * - 三类 Viewer 各自负责渲染与定位能力，通过 ready 事件交出统一的能力对象
 * - 不修改任何既有业务接口：内容只读新接口 /api/v1/documents/{id}/content，
 *   知识点与问答复用既有的图谱 / 学习 / 收藏 / QA 接口
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  Clock, Close, CollectionTag, Document as DocumentIcon, EditPen, Loading, MagicStick, WarningFilled,
} from '@element-plus/icons-vue'

import ReaderTopBar from '../components/reader/ReaderTopBar.vue'
import DocumentSidebar from '../components/reader/DocumentSidebar.vue'
import DocumentKnowledgePanel from '../components/reader/DocumentKnowledgePanel.vue'
import SelectionMenu from '../components/reader/SelectionMenu.vue'
import PdfViewer from '../components/reader/PdfViewer.vue'
import TextViewer from '../components/reader/TextViewer.vue'
import DocxViewer from '../components/reader/DocxViewer.vue'

import { api } from '../api'
import { useAppStore } from '../stores/app'
import { isStudentUser } from '../utils/userRole'
import {
  authHeaders, decodeTextBuffer, describeContentStatus, documentContentUrl, fetchDocumentBuffer,
  fetchDocumentMeta, probeContentStatus, resolveViewerKind,
} from '../utils/documentContent'
import {
  clearReadingProgress, getReaderPrefs, getReadingProgress, saveReaderPrefs, saveReadingProgress,
} from '../utils/readingProgress'

const route = useRoute()
const router = useRouter()
const store = useAppStore()

// 文档 id 取自路由：阅读器内切换文档走 router.replace，本页跟着重新引导一次。
// 这样「当前文档」只有路由一个来源，不需要第二套状态，也天然保留了分享/刷新/后退语义。
const docId = computed(() => String(route.params.docId || ''))
const courseId = String(route.query.course_id || '')
const from = String(route.query.from || '')

/** 当前课程的文档列表（与课程文档列表同源），供顶部下拉切换 */
const courseDocs = ref([])

/**
 * 「正在阅读的文档」标识，阅读进度按它落盘。
 * 不能直接用路由参数：离开阅读器时路由已经先切走了，卸载回调里读到的是空 id，
 * 会写出一条无主的进度记录（localStorage 里会看到 key 为空串的脏数据）。
 */
const activeDocId = ref(docId.value)

const rootEl = ref(null)
const stageEl = ref(null)
const topBarRef = ref(null)
const panelRef = ref(null)
const pdfRef = ref(null)
const textRef = ref(null)
const docxRef = ref(null)

const kind = ref('none') // pdf | text | docx | none
const loading = ref(true)
const error = ref('')
const EMPTY_DOC = { fileName: '文档', fileType: '', fileSizeText: '', entityCount: 0, relationCount: 0, createdAt: '' }
const doc = ref({ ...EMPTY_DOC })
const textContent = ref('')
const docxBuffer = ref(null)

const percent = ref(0)
const page = ref(1)
const totalPages = ref(0)
const scale = ref(1)
const zoomFit = ref(true)
const visibleText = ref('')
const outline = ref([])
const outlineItems = ref([])
const outlineReady = ref(false)
const viewerApi = ref(null)
const prefs = ref(getReaderPrefs())

// 窄屏（平板竖屏 / 手机）下左右两栏都会占满整宽，同时展开会把正文挤没，因此互斥
const NARROW_QUERY = '(max-width: 900px)'
const isNarrow = ref(window.matchMedia?.(NARROW_QUERY)?.matches === true)

// 侧栏与面板的开合是「阅读器状态」，存在偏好里，下次打开保持同样的布局；
// 但窄屏下不沿用，否则一进阅读器正文就被两栏盖住，还得先手动收起来
const sidebarOpen = ref(!isNarrow.value && prefs.value.sidebarOpen !== false)
const panelOpen = ref(!isNarrow.value && prefs.value.panelOpen !== false)
const resumeNotice = ref('')

/** 转到窄屏时收掉两栏；这里不写回偏好，免得把宽屏下的布局习惯覆盖掉 */
function onNarrowChange(e) {
  isNarrow.value = e.matches
  if (e.matches) {
    sidebarOpen.value = false
    panelOpen.value = false
  }
}

function openExclusive(target) {
  if (!isNarrow.value) return
  if (target === 'sidebar') panelOpen.value = false
  else sidebarOpen.value = false
}

function toggleSidebar() {
  sidebarOpen.value = !sidebarOpen.value
  if (sidebarOpen.value) openExclusive('sidebar')
  onPrefsChange({ sidebarOpen: sidebarOpen.value })
}

function togglePanel() {
  panelOpen.value = !panelOpen.value
  if (panelOpen.value) openExclusive('panel')
  onPrefsChange({ panelOpen: panelOpen.value })
}

const searchQuery = ref('')
const searchState = ref({ scanning: '', total: null, index: 0 })

const contentUrl = computed(() => documentContentUrl(docId.value))

const courseName = computed(() => {
  const c = store.courses.find((c) => String(c.course_id) === String(courseId))
  return c ? c.course_name : ''
})

const backText = computed(() => {
  if (from === 'teacher-graph') return '返回图谱管理'
  return from === 'teacher' ? '返回课程管理' : '返回课程'
})

const searchPlaceholder = computed(() =>
  kind.value === 'docx' ? 'Word 文档暂不支持文内搜索' : '在文档中搜索（Ctrl+F）',
)

const searchStatus = computed(() => {
  if (kind.value === 'docx') return ''
  if (searchState.value.scanning) return searchState.value.scanning
  if (!searchQuery.value) return ''
  if (searchState.value.total === 0) return '未找到'
  if (searchState.value.total) return `${searchState.value.index}/${searchState.value.total}`
  return ''
})

// ---------------- 初始化 ----------------

function fmtSize(bytes) {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/** 文档元信息：优先用课程文档列表（师生均可读），失败再探测内容接口头信息 */
async function resolveDocMeta() {
  if (courseId) {
    try {
      if (!store.courses.length) store.fetchCourses().catch(() => {})
      const list = await api.getDocuments(courseId)
      // 同一份列表既用于当前文档元信息，也用于顶部下拉，不额外发请求
      courseDocs.value = list || []
      const hit = courseDocs.value.find((d) => String(d.doc_id) === docId.value)
      if (hit) {
        doc.value = {
          fileName: hit.file_name,
          fileType: hit.file_type,
          fileSizeText: fmtSize(hit.file_size),
          entityCount: hit.entity_count || 0,
          relationCount: hit.relation_count || 0,
          createdAt: hit.created_at || '',
        }
        return resolveViewerKind(hit.file_type, hit.file_name)
      }
    } catch {
      // 列表读不到时继续走下面的头信息探测
    }
  }
  try {
    const meta = await fetchDocumentMeta(docId.value)
    doc.value = { ...doc.value, fileName: meta.fileName || '文档', fileType: meta.fileType || '' }
    return meta.kind
  } catch (e) {
    error.value = e.message || '无法读取文档信息'
    return null
  }
}

async function bootstrap() {
  loading.value = true
  error.value = ''
  outlineReady.value = false
  outlineItems.value = []
  textContent.value = ''
  docxBuffer.value = null

  const detected = await resolveDocMeta()
  if (detected === null) {
    loading.value = false
    return
  }
  if (!detected) {
    kind.value = 'none'
    loading.value = false
    return
  }

  // 关键顺序：TXT / DOCX 先把内容取回来再挂载 Viewer。
  // 否则 Viewer 会先以空内容挂载，目录解析、滚动位置恢复全部落空，
  // 而且 docx-preview 会因为 buffer 为 null 直接进入错误态且不再重试。
  // PDF 例外：PDF.js 自己按需分段拉取，可以直接挂载。
  if (detected === 'text' || detected === 'docx') {
    try {
      const buffer = await fetchDocumentBuffer(docId.value)
      if (detected === 'docx') docxBuffer.value = buffer
      // 解码回退顺序与后端解析一致（utf-8 → gb18030），保证读到与抽取同源的正文
      else textContent.value = decodeTextBuffer(buffer)
    } catch (e) {
      error.value = e.message
      kind.value = 'none'
      loading.value = false
      return
    }
  }

  kind.value = detected
  loading.value = false
}

const restoreState = ref(null)

function bootstrapRestore() {
  // 每次引导（首次进入 / 换文档）都先把进度归属钉在当前文档上
  activeDocId.value = docId.value
  const saved = getReadingProgress(activeDocId.value)
  restoreState.value = saved
  if (saved && (saved.percent > 1 || saved.page > 1)) {
    const where = saved.totalPages > 1 ? `第 ${saved.page} 页` : ''
    resumeNotice.value = `已恢复到上次阅读位置${where ? '（' + where + '）' : ''}，已读 ${saved.percent}%`
    percent.value = saved.percent || 0
    page.value = saved.page || 1
    window.setTimeout(() => {
      if (resumeNotice.value) resumeNotice.value = ''
    }, 8000)
  }
}

// ---------------- Viewer 能力转发 ----------------

function onViewerReady(apiObject) {
  viewerApi.value = apiObject || null
}

function viewerCall(method, ...args) {
  const fn = viewerApi.value?.[method]
  if (typeof fn === 'function') fn(...args)
}

function onZoom({ scale: s, fit }) {
  scale.value = s
  zoomFit.value = fit
}

function onOutline(items) {
  outline.value = items || []
  outlineItems.value = flattenOutline(outline.value, kind.value)
  // 标记「目录已就绪」：侧栏据此判断「空」是真的没有目录，还是还没解析出来
  outlineReady.value = true
}

/** 把各 Viewer 的大纲统一成「扁平 + 缩进层级 + 可跳转目标」 */
function flattenOutline(items, viewerKind) {
  const out = []
  const walk = (list, depth) => {
    ;(list || []).forEach((it) => {
      if (viewerKind === 'pdf') {
        out.push({ id: `${it.title}-${it.page}-${out.length}`, title: it.title, depth, target: { page: it.page } })
        if (it.children?.length) walk(it.children, Math.min(depth + 1, 4))
      } else if (viewerKind === 'text') {
        out.push({ id: `${it.line}-${out.length}`, title: it.title, depth: it.depth ?? depth, target: { line: it.line } })
      } else if (viewerKind === 'docx') {
        out.push({ id: `docx-${out.length}`, title: it.title, depth: Math.max(0, (it.level || 1) - 1), target: { el: it.el } })
      }
    })
  }
  walk(items, 0)
  return out
}

function onOutlineSelect(item) {
  const target = item?.target || {}
  if (target.page != null) viewerCall('goToPage', target.page)
  else if (target.line != null) viewerCall('goToLine', target.line)
  else if (target.el) docxRef.value?.goToOutlineItem?.({ el: target.el })
}

// ---------------- 搜索 ----------------

function onSearch(q) {
  searchQuery.value = q
  searchState.value = { scanning: '', total: null, index: 0 }
  if (kind.value === 'docx') return
  viewerCall('search', q)
}

function onSearchProgress({ done, total }) {
  searchState.value = { ...searchState.value, scanning: `扫描 ${done}/${total}` }
}

function onSearchResult({ total }) {
  searchState.value = { scanning: '', total, index: total ? 1 : 0 }
  if (searchQuery.value && total === 0) ElMessage.info(`未找到「${searchQuery.value}」`)
}

function onSearchIndex({ index, total }) {
  searchState.value = { scanning: '', total, index }
}

// ---------------- 选中正文 → 浮动学习菜单 ----------------

const selection = ref(null) // { text, x, y }

/** 浮动菜单动作；「加入笔记」是学生个人数据，教师端不出现 */
const selectionActions = computed(() => {
  // key 对应学习助手里「选中内容」系列动作，菜单上只显示短标签，气泡里用完整标签
  const list = [
    { key: 'explain', label: '解释', icon: MagicStick, title: '让 AI 解释选中的内容' },
    { key: 'selectionSummary', label: '总结', icon: DocumentIcon, title: '让 AI 总结选中的内容' },
    { key: 'selectionQuiz', label: '生成题目', icon: EditPen, title: '根据选中的内容出练习题' },
  ]
  if (isStudentUser()) {
    list.push({ key: 'note', label: '加入笔记', icon: CollectionTag, title: '把这段原文存进本文档的笔记' })
  }
  return list
})

/** 选中文字里的连续空格没有信息量，压成一个；换行保留（章节/条目靠它分界） */
function normalizeSelection(raw) {
  return String(raw || '')
    .replace(/\r\n?/g, '\n')
    .replace(/[ \t\u00a0]+/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
    .slice(0, 4000)
}

function selectionRoot(node) {
  return node?.nodeType === 1 ? node : node?.parentElement
}

/** 只认正文里的选区：侧栏、面板、工具条上的选中不该弹出学习菜单 */
function selectionFromStage(sel) {
  const stage = stageEl.value
  if (!stage || !sel || sel.isCollapsed || !sel.rangeCount) return null
  const range = sel.getRangeAt(0)
  if (!stage.contains(selectionRoot(range.commonAncestorContainer))) return null
  const text = normalizeSelection(sel.toString())
  if (text.length < 2) return null // 单个字误触概率太高，不弹菜单
  const rect = range.getBoundingClientRect()
  if (!rect.width && !rect.height) return null
  return { text, x: rect.left + rect.width / 2, y: rect.top }
}

function captureSelection() {
  selection.value = selectionFromStage(window.getSelection())
}

function clearSelection() {
  selection.value = null
  const sel = window.getSelection()
  if (sel && !sel.isCollapsed) sel.removeAllRanges()
}

let pointerTimer = 0
let narrowMedia = null

function onStagePointerUp() {
  // 等到本次事件的选区定下来再读，否则拿到的是上一帧的旧选区
  window.clearTimeout(pointerTimer)
  pointerTimer = window.setTimeout(captureSelection, 0)
}

function onStageTouchEnd() {
  // 移动端拖选结束后才出现选区手柄，晚一点再读
  window.clearTimeout(pointerTimer)
  pointerTimer = window.setTimeout(captureSelection, 320)
}

function onSelectionChange() {
  const sel = window.getSelection()
  if ((!sel || sel.isCollapsed) && selection.value) selection.value = null
}

function onStageScroll() {
  // 正文滚走了，浮动菜单不该继续悬在半空
  if (selection.value) selection.value = null
}

async function onSelectionAction(key) {
  const current = selection.value
  if (!current) return
  const text = current.text
  clearSelection()
  if (key === 'note') {
    panelRef.value?.saveSelectionNote?.(text)
    return
  }
  await askAi(key, text)
}

/**
 * 统一入口：把一段正文交给学习助手发起 AI 阅读动作。
 * 对话状态由面板持有，阅读器只负责给上下文，避免出现第二份会话。
 */
async function askAi(key, context) {
  panelOpen.value = true
  openExclusive('panel')
  await nextTick()
  const panel = panelRef.value
  if (!panel?.runAction) {
    ElMessage.warning('学习助手暂不可用，请稍后重试')
    return
  }
  panel.runAction(key, context)
}

// ---------------- PDF 加载失败：探测真实原因 ----------------

async function onPdfError({ message }) {
  const status = await probeContentStatus(docId.value)
  if (status === 401 || status === 403 || status === 404) {
    error.value = describeContentStatus(status)
    kind.value = 'none'
    return
  }
  error.value = message
  kind.value = 'none'
}

// ---------------- 阅读进度 ----------------

let saveTimer = 0
// 切换文档期间挂起落盘：重置状态会把 percent/page 归零，若不拦住，
// 这些过渡值会以新文档的名义写进本地记录，把真正的阅读位置冲掉
let suppressSave = false

function currentScrollTop() {
  return viewerApi.value?.getScrollTop?.() ?? rootEl.value?.scrollTop ?? 0
}

function persistProgress() {
  if (kind.value === 'none' || !activeDocId.value) return
  saveReadingProgress(activeDocId.value, {
    courseId: courseId || null,
    docName: doc.value.fileName,
    percent: percent.value,
    page: page.value,
    totalPages: totalPages.value,
    scrollTop: currentScrollTop(),
    scale: kind.value === 'pdf' ? scale.value : null,
  })
}

function scheduleSave() {
  if (suppressSave) return
  if (saveTimer) return
  saveTimer = window.setTimeout(() => {
    saveTimer = 0
    persistProgress()
  }, 1200)
}

// 进度变化即节流落盘；页面隐藏/关闭时立即补写一次
function onVisibilityChange() {
  if (document.visibilityState === 'hidden') persistProgress()
}

function restartReading() {
  clearReadingProgress(activeDocId.value)
  resumeNotice.value = ''
  percent.value = 0
  if (kind.value === 'pdf') viewerCall('goToPage', 1)
  else {
    const el = rootEl.value?.querySelector('.txv, .dxv')
    if (el) el.scrollTop = 0
  }
  page.value = 1
  ElMessage.success('已从头开始')
}

function onPrefsChange(patch) {
  prefs.value = saveReaderPrefs(patch)
}

// ---------------- 文档内切换 ----------------

/**
 * 只清空「与具体文档绑定」的状态。
 * 阅读偏好（字号/行距/宽度）与侧栏、面板的开合属于「阅读器状态」，跨文档保留，
 * 换来换去不该把用户调好的阅读环境重置掉。
 */
function resetDocumentState() {
  kind.value = 'none'
  error.value = ''
  textContent.value = ''
  docxBuffer.value = null
  outline.value = []
  outlineItems.value = []
  outlineReady.value = false
  viewerApi.value = null
  visibleText.value = ''
  percent.value = 0
  page.value = 1
  totalPages.value = 0
  scale.value = 1
  zoomFit.value = true
  searchQuery.value = ''
  searchState.value = { scanning: '', total: null, index: 0 }
  resumeNotice.value = ''
  restoreState.value = null
  doc.value = { ...EMPTY_DOC }
}

/**
 * 切换到同课程的另一篇文档。
 * 只改路由，内容加载、权限校验、进度恢复全部复用既有流程；用 replace 而不是 push，
 * 避免「返回」把用户一篇篇文档地倒回去。
 */
function switchDocument(target) {
  const nextId = String(target?.doc_id ?? '')
  if (!nextId || nextId === docId.value) return
  if (saveTimer) {
    window.clearTimeout(saveTimer)
    saveTimer = 0
  }
  persistProgress() // 切走之前先把当前位置写回去，否则这一段阅读会丢
  suppressSave = true
  router.replace({ name: 'reader', params: { docId: nextId }, query: { ...route.query } })
}

async function onDocumentChange() {
  resetDocumentState()
  bootstrapRestore()
  await bootstrap()
  suppressSave = false
  scheduleSave() // 重置期间被拦下的写入在这里补一次（此时已是新文档的真实进度）
}

// ---------------- 导航 / 工具 ----------------

function goBack() {
  // 从图谱管理进来的，回到原文档的图谱——否则读完原文再想改知识点，
  // 得从文档列表重新点一遍课程和文档。docId 用当前值：阅读器内换过文档时
  // 该值已被 replace 更新（switchDocument 保留了 query），与「查看图谱」对称。
  if (from === 'teacher-graph') {
    router.push({
      path: '/teacher',
      query: {
        tab: 'preview',
        ...(courseId ? { course_id: courseId } : {}),
        document_id: docId.value,
      },
    })
    return
  }
  const target = from === 'teacher' || store.role === 'teacher'
    ? { path: '/teacher', query: { tab: 'documents', ...(courseId ? { course_id: courseId } : {}) } }
    : { path: '/student', query: { tab: 'documents', ...(courseId ? { course_id: courseId } : {}) } }
  router.push(target)
}

async function download() {
  try {
    const buffer = await fetchDocumentBuffer(docId.value)
    const blob = new Blob([buffer])
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = doc.value.fileName || `document-${docId.value}`
    document.body.appendChild(a)
    a.click()
    a.remove()
    // 交给浏览器读完再释放
    setTimeout(() => URL.revokeObjectURL(url), 10000)
  } catch (e) {
    ElMessage.error(`下载失败：${e.message}`)
  }
}

function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen()
  else rootEl.value?.requestFullscreen?.()
}

function openGraph() {
  if (!courseId) {
    ElMessage.warning('缺少课程信息，无法跳转图谱')
    return
  }
  const query = { course_id: courseId, document_id: docId.value }
  if (store.role === 'teacher') router.push({ path: '/teacher', query: { tab: 'preview', ...query } })
  else router.push({ path: '/student', query: { tab: 'browse', ...query } })
}

/**
 * 知识点面板点「定位正文」：用阅读器自身的搜索能力跳到首次出现处。
 * 同时把词写回工具栏搜索框，让用户看得见「正在找什么」、并能继续上一个/下一个。
 */
async function locateInDocument(text) {
  if (!text) return
  // 只发起一次全文搜索：词写回搜索框，搜索本身交给 Viewer 的 revealText，
  // 命中结果通过 search-result / search-index 事件回流到状态栏
  searchQuery.value = text
  searchState.value = { scanning: '', total: null, index: 0 }
  const found = await viewerApi.value?.revealText?.(text)
  if (found === false) ElMessage.info(`正文中未找到「${text}」`)
}

/** 焦点在输入框里时，方向键与加减号属于输入行为，阅读器不能抢 */
function isTypingTarget(target) {
  if (!target) return false
  const tag = target.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable === true
}

/** 无分页文档的翻屏：按视口高度的九成滚动，读长文时不必一直滚轮 */
function scrollBodyBy(direction) {
  const el = rootEl.value?.querySelector('.txv, .dxv')
  if (!el) return
  el.scrollBy({ top: direction * el.clientHeight * 0.9, behavior: 'smooth' })
}

function scrollBodyTo(edge) {
  const el = rootEl.value?.querySelector('.txv, .dxv')
  if (!el) return
  el.scrollTo({ top: edge === 'end' ? el.scrollHeight : 0, behavior: 'smooth' })
}

function onKeydown(e) {
  // 查找：任何位置都能唤起搜索框
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
    e.preventDefault()
    topBarRef.value?.focusSearch?.()
    return
  }
  // F3 / Shift+F3：不离开正文也能在命中之间跳，和浏览器的查找习惯一致
  if (e.key === 'F3') {
    e.preventDefault()
    if (searchQuery.value) viewerCall(e.shiftKey ? 'searchPrev' : 'searchNext')
    return
  }
  if (e.key === 'Escape') {
    // 逐层退出：先收浮动菜单，再清搜索，最后退出全屏
    if (selection.value) {
      clearSelection()
      return
    }
    if (searchQuery.value) {
      onSearch('')
      return
    }
    if (document.fullscreenElement) document.exitFullscreen()
    return
  }
  if (isTypingTarget(e.target) || e.ctrlKey || e.metaKey || e.altKey) return
  // 有选区时方向键要留给「调整选区」，不能拿来翻页
  if (selection.value) return

  if (kind.value === 'pdf') {
    if (e.key === 'PageDown' || e.key === 'ArrowRight') {
      e.preventDefault()
      viewerCall('nextPage')
    } else if (e.key === 'PageUp' || e.key === 'ArrowLeft') {
      e.preventDefault()
      viewerCall('prevPage')
    } else if (e.key === 'Home') {
      e.preventDefault()
      viewerCall('goToPage', 1)
    } else if (e.key === 'End') {
      e.preventDefault()
      viewerCall('goToPage', totalPages.value)
    } else if (e.key === '+' || e.key === '=') {
      e.preventDefault()
      viewerCall('zoomIn')
    } else if (e.key === '-' || e.key === '_') {
      e.preventDefault()
      viewerCall('zoomOut')
    } else if (e.key === '0') {
      e.preventDefault()
      viewerCall('zoomReset')
    }
    return
  }

  // TXT / MD / DOCX：翻屏与首尾
  if (e.key === 'PageDown' || e.key === ' ') {
    e.preventDefault()
    scrollBodyBy(1)
  } else if (e.key === 'PageUp' || (e.key === ' ' && e.shiftKey)) {
    e.preventDefault()
    scrollBodyBy(-1)
  } else if (e.key === 'Home') {
    e.preventDefault()
    scrollBodyTo('start')
  } else if (e.key === 'End') {
    e.preventDefault()
    scrollBodyTo('end')
  }
}

onMounted(async () => {
  store.fetchCourses().catch(() => {})
  bootstrapRestore()
  await bootstrap()
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('visibilitychange', onVisibilityChange)
  document.addEventListener('selectionchange', onSelectionChange)
  const stage = stageEl.value
  stage?.addEventListener('mouseup', onStagePointerUp)
  stage?.addEventListener('touchend', onStageTouchEnd, { passive: true })
  // 滚动事件不冒泡，只有捕获阶段才能收到各 Viewer 内部容器的滚动
  stage?.addEventListener('scroll', onStageScroll, true)
  narrowMedia = window.matchMedia?.(NARROW_QUERY)
  narrowMedia?.addEventListener?.('change', onNarrowChange)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('visibilitychange', onVisibilityChange)
  document.removeEventListener('selectionchange', onSelectionChange)
  const stage = stageEl.value
  stage?.removeEventListener('mouseup', onStagePointerUp)
  stage?.removeEventListener('touchend', onStageTouchEnd)
  stage?.removeEventListener('scroll', onStageScroll, true)
  narrowMedia?.removeEventListener?.('change', onNarrowChange)
  if (pointerTimer) window.clearTimeout(pointerTimer)
  if (saveTimer) window.clearTimeout(saveTimer)
  persistProgress() // 离开页面立即落盘，保证下次能续读
})

// 阅读进度是本地高频状态，不入 URL；页码 / 缩放变化时节流落盘
watch([percent, page, scale], scheduleSave)

// 路由里的 docId 是「当前文档」的唯一来源，变了就整篇重新引导
watch(docId, () => { void onDocumentChange() })
</script>

<style scoped>
.drv {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: #eef0f5;
  overflow: hidden;
}

.drv-resume {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 16px;
  background: #eef1fe;
  border-bottom: 1px solid #dde4fc;
  color: #3f5ce0;
  font-size: 13px;
  flex-shrink: 0;
}
.drv-resume-btn {
  border: none;
  background: transparent;
  color: #4f6ef7;
  font-size: 13px;
  font-family: inherit;
  text-decoration: underline;
  cursor: pointer;
  padding: 0;
}
.drv-resume-close {
  margin-left: auto;
  border: none;
  background: transparent;
  color: #8aa3f9;
  cursor: pointer;
  display: inline-flex;
  padding: 2px;
}

.drv-body {
  flex: 1;
  display: flex;
  min-height: 0;
}

.drv-stage {
  flex: 1;
  min-width: 0;
  position: relative;
  display: flex;
  flex-direction: column;
}

.drv-state {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: #606266;
}
.drv-state-title {
  margin: 6px 0 0;
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}
.drv-state-sub {
  margin: 0;
  font-size: 13px;
  color: #909399;
  max-width: 440px;
  text-align: center;
  line-height: 1.7;
}
.drv-state-actions {
  margin-top: 10px;
  display: flex;
  gap: 8px;
}
.drv-spin {
  animation: drv-rotate 1.1s linear infinite;
  color: #4f6ef7;
}
@keyframes drv-rotate {
  to { transform: rotate(360deg); }
}

@media (max-width: 900px) {
  .drv-resume {
    font-size: 12px;
    padding: 6px 10px;
  }
}
</style>
