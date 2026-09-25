<template>
  <!--
    AI 助教悬浮窗（参考智慧树"AI 助教小智"交互）
    - 右下角悬浮球，任意 Tab 下随时唤起/收起
    - 复用现有问答接口 api.ask(question, courseId, documentId)
    - 消息结构与主问答页一致：{ role, content, sources, error }
  -->
  <div
    class="ai-widget-fab"
    :class="{ 'is-open': open, 'is-dragging': dragging }"
    :style="fabStyle"
    :title="open ? '收起' : 'AI 助教（可拖动）'"
    @click="toggle"
    @pointerdown="onFabPointerDown"
  >
    <el-icon v-if="open" :size="20"><Close /></el-icon>
    <svg
      v-else
      class="ai-fab-icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="1.8"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      <path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z" />
      <path d="M20 3v4" />
      <path d="M22 5h-4" />
      <path d="M4 17v2" />
      <path d="M5 18H3" />
    </svg>
  </div>

  <transition name="ai-panel">
    <div
      v-show="open"
      class="ai-widget-panel"
      :class="{ 'is-resizing': resizing }"
      :style="panelStyle"
    >
      <!-- 三边缩放手柄：左边缘调宽度 / 上边缘调高度 / 左上角同时调整 -->
      <div
        class="ai-resize-edge ai-resize-left"
        title="拖动调整宽度"
        @pointerdown="onResizePointerDown($event, 'x')"
      ></div>
      <div
        class="ai-resize-edge ai-resize-top"
        title="拖动调整高度"
        @pointerdown="onResizePointerDown($event, 'y')"
      ></div>
      <div
        class="ai-resize-corner"
        title="拖动调整大小"
        @pointerdown="onResizePointerDown($event, 'xy')"
      ></div>
      <!-- 头部：助教身份 + 问候 + 课程上下文 -->
      <div class="ai-panel-header">
        <div class="ai-header-row">
          <div class="ai-avatar">
            <svg class="ai-avatar-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z" />
              <path d="M20 3v4" />
              <path d="M22 5h-4" />
              <path d="M4 17v2" />
              <path d="M5 18H3" />
            </svg>
          </div>
          <div class="ai-header-text">
            <div class="ai-title">Hi～我是你的 AI 助教小智</div>
            <div class="ai-subtitle">课程学习中欢迎随时提问，小智将全力为你答疑解惑，共同进步哦！</div>
          </div>
          <el-icon class="ai-close" :size="18" title="收起" aria-label="收起" @click="toggle"><Close /></el-icon>
        </div>
        <div class="ai-header-bottom">
          <div class="ai-context-chip" :class="{ empty: !hasContext }">
            <template v-if="hasContext">当前课程：{{ effCourseName || effCourseId }}</template>
            <template v-else>未选择课程 · 可在页面上方选择学习资料</template>
          </div>
          <span v-if="messages.length > 1" class="ai-clear" @click="clearChat">清空</span>
        </div>
      </div>

      <!-- 消息区 -->
      <div ref="chatBoxRef" class="ai-messages">
        <div
          v-for="(m, i) in messages"
          :key="i"
          class="ai-msg-row"
          :class="m.role === 'user' ? 'is-user' : 'is-ai'"
        >
          <div class="ai-msg-main">
            <div v-if="m.role === 'ai' && m.greeting" class="ai-tag">AI 助教</div>
            <div class="ai-bubble">
              <div v-if="m.error" class="ai-error">⚠️ AI 服务暂时不可用，请稍后重试</div>
              <template v-else>
                <div v-if="m.role === 'ai'" class="ai-msg-text ai-md" v-html="renderMarkdown(m.content)"></div>
                <div v-else class="ai-msg-text">{{ m.content }}</div>
                <div v-if="m.role === 'ai' && m.sources && m.sources.length" class="ai-sources">
                  <div class="ai-sources-title">参考来源 · 课程知识库</div>
                  <div v-for="(s, j) in m.sources" :key="s.kp_id || s.name || j" class="ai-source-item">
                    <span class="ai-source-cat">{{ s.category || '知识点' }}</span>
                    <span class="ai-source-name">{{ s.name }}</span>
                  </div>
                </div>
              </template>
            </div>
          </div>
        </div>

        <div v-if="asking" class="ai-msg-row is-ai">
          <div class="ai-bubble ai-typing">
            <span class="ai-dot"></span><span class="ai-dot"></span><span class="ai-dot"></span>
          </div>
        </div>
      </div>

      <!-- 输入区 -->
      <div class="ai-input-row">
        <input
          v-model="question"
          class="ai-input"
          type="text"
          placeholder="请输入您的问题"
          :disabled="asking"
          @keydown.enter="onEnter"
        />
        <button class="ai-send" :disabled="asking || !question.trim()" title="发送" @click="send">
          <el-icon :size="16"><Top /></el-icon>
        </button>
      </div>
    </div>
  </transition>
</template>

<script setup>
import { ref, nextTick, computed } from 'vue'
import { Close, Top } from '@element-plus/icons-vue'
import { api } from '../api'
import { useAppStore } from '../stores/app'
import { renderMarkdown } from '../utils/markdown'

const store = useAppStore()

const props = defineProps({
  courseId: { type: [String, Number], default: '' },
  documentId: { type: [String, Number], default: '' },
  courseName: { type: String, default: '' },
})

// 课程上下文优先取 props（页面内局部挂载场景），否则回退全局学习上下文（App.vue 全局挂载场景），
// 保证全局实例在页面切换时不会因缺少上下文而闪变
const effCourseId = computed(() => props.courseId || store.learningContext.currentCourseId || '')
const effDocumentId = computed(() => props.documentId || store.learningContext.currentDocumentId || '')
const effCourseName = computed(() => {
  if (props.courseName) return props.courseName
  const cid = effCourseId.value
  const c = store.courseById(cid)
  return c ? c.course_name : ''
})

const open = ref(false)
const question = ref('')
const asking = ref(false)
const chatBoxRef = ref(null)

/* ===== 悬浮球拖动 =====
 * fabPos 为 null 时使用样式表默认的右下角位置；拖动后切换为内联 left/top。
 * 点击与拖动通过位移阈值（6px）区分；位置持久化到 localStorage，刷新后保持。 */
const FAB_SIZE = 58
const FAB_MARGIN = 8
const DRAG_KEY = 'ai-fab-pos'
const fabPos = ref(loadFabPos())
const dragging = ref(false)
const dragMoved = ref(false)
let dragStart = null

function loadFabPos() {
  try {
    const p = JSON.parse(localStorage.getItem(DRAG_KEY) || 'null')
    if (p && Number.isFinite(p.x) && Number.isFinite(p.y)) return clampFab(p.x, p.y)
  } catch (e) { /* 忽略损坏的缓存 */ }
  return null
}
function clampFab(x, y) {
  const w = window.innerWidth
  const h = window.innerHeight
  return {
    x: Math.min(Math.max(x, FAB_MARGIN), w - FAB_SIZE - FAB_MARGIN),
    y: Math.min(Math.max(y, FAB_MARGIN), h - FAB_SIZE - FAB_MARGIN),
  }
}
const fabStyle = computed(() =>
  fabPos.value
    ? { left: fabPos.value.x + 'px', top: fabPos.value.y + 'px', right: 'auto', bottom: 'auto' }
    : {}
)
/* 面板跟随悬浮球：优先在悬浮球上方展开，横向按悬浮球所在半屏对齐，并钳制在视口内 */
const panelStyle = computed(() => {
  // 拖拽调整尺寸期间：使用临时矩形（保持右下角固定）
  if (resizeStyle.value) return resizeStyle.value
  // 用户调整过尺寸则覆盖样式表默认宽高（380 x min(600px, 72vh)）
  const sizeStyle = panelSize.value ? { width: panelSize.value.w + 'px', height: panelSize.value.h + 'px' } : {}
  if (!fabPos.value) return sizeStyle
  const w = window.innerWidth
  const h = window.innerHeight
  const pw = panelSize.value ? panelSize.value.w : Math.min(380, w - 32)
  const ph = panelSize.value ? panelSize.value.h : Math.min(600, Math.round(h * 0.72))
  const f = fabPos.value
  const left = f.x + FAB_SIZE / 2 < w / 2 ? f.x : f.x + FAB_SIZE - pw
  const top = f.y >= ph + FAB_MARGIN * 2 ? f.y - 10 - ph : f.y + FAB_SIZE + 10
  return {
    left: Math.min(Math.max(left, FAB_MARGIN), Math.max(w - pw - FAB_MARGIN, FAB_MARGIN)) + 'px',
    top: Math.min(Math.max(top, FAB_MARGIN), Math.max(h - ph - FAB_MARGIN, FAB_MARGIN)) + 'px',
    right: 'auto',
    bottom: 'auto',
    ...sizeStyle,
  }
})

/* ===== 面板尺寸调整（三边：左 / 上 / 左上角） =====
 * 左边缘拖宽度、上边缘拖高度、左上角同时拖宽高；向左/上拖放大，向右/下拖缩小，
 * 过程中保持面板右下角固定；尺寸钳制在 [最小值, 视口-32] 之间并写入 localStorage，刷新后保持。 */
const SIZE_KEY = 'ai-panel-size'
const PANEL_MIN_W = 300
const PANEL_MIN_H = 360
const panelSize = ref(loadPanelSize())
const resizing = ref(false)
const resizeStyle = ref(null)
let resizeStart = null
let resizeLastSize = null

function loadPanelSize() {
  try {
    const s = JSON.parse(localStorage.getItem(SIZE_KEY) || 'null')
    if (s && Number.isFinite(s.w) && Number.isFinite(s.h)) return clampPanelSize(s.w, s.h)
  } catch (e) { /* 忽略损坏的缓存 */ }
  return null
}
function clampPanelSize(w, h) {
  const vw = window.innerWidth
  const vh = window.innerHeight
  return {
    w: Math.round(Math.min(Math.max(w, PANEL_MIN_W), Math.max(vw - 32, PANEL_MIN_W))),
    h: Math.round(Math.min(Math.max(h, PANEL_MIN_H), Math.max(vh - 32, PANEL_MIN_H))),
  }
}
function onResizePointerDown(e, dir) {
  if (e.button !== undefined && e.button !== 0) return
  e.preventDefault()
  e.stopPropagation()
  const panel = e.currentTarget.closest('.ai-widget-panel')
  if (!panel) return
  const rect = panel.getBoundingClientRect()
  resizing.value = true
  resizeStart = { px: e.clientX, py: e.clientY, w: rect.width, h: rect.height, left: rect.left, top: rect.top, dir: dir || 'xy' }
  resizeLastSize = null
  window.addEventListener('pointermove', onResizePointerMove)
  window.addEventListener('pointerup', onResizePointerUp, { once: true })
}
function onResizePointerMove(e) {
  if (!resizing.value || !resizeStart) return
  const dir = resizeStart.dir || 'xy'
  const dw = dir.indexOf('x') !== -1 ? resizeStart.px - e.clientX : 0
  const dh = dir.indexOf('y') !== -1 ? resizeStart.py - e.clientY : 0
  const next = clampPanelSize(resizeStart.w + dw, resizeStart.h + dh)
  resizeLastSize = next
  resizeStyle.value = {
    left: resizeStart.left + (resizeStart.w - next.w) + 'px',
    top: resizeStart.top + (resizeStart.h - next.h) + 'px',
    width: next.w + 'px',
    height: next.h + 'px',
    right: 'auto',
    bottom: 'auto',
  }
}
function onResizePointerUp() {
  window.removeEventListener('pointermove', onResizePointerMove)
  resizing.value = false
  if (resizeLastSize) {
    panelSize.value = resizeLastSize
    try { localStorage.setItem(SIZE_KEY, JSON.stringify(resizeLastSize)) } catch (e) { /* 忽略 */ }
  }
  resizeStyle.value = null
  resizeStart = null
  resizeLastSize = null
}

function onFabPointerDown(e) {
  if (e.button !== undefined && e.button !== 0) return
  dragging.value = true
  dragMoved.value = false
  const rect = e.currentTarget.getBoundingClientRect()
  dragStart = { px: e.clientX, py: e.clientY, x: rect.left, y: rect.top }
  window.addEventListener('pointermove', onFabPointerMove)
  window.addEventListener('pointerup', onFabPointerUp, { once: true })
}
function onFabPointerMove(e) {
  if (!dragging.value || !dragStart) return
  const dx = e.clientX - dragStart.px
  const dy = e.clientY - dragStart.py
  if (!dragMoved.value && Math.hypot(dx, dy) < 6) return
  dragMoved.value = true
  fabPos.value = clampFab(dragStart.x + dx, dragStart.y + dy)
}
function onFabPointerUp() {
  window.removeEventListener('pointermove', onFabPointerMove)
  dragging.value = false
  if (dragMoved.value && fabPos.value) {
    try { localStorage.setItem(DRAG_KEY, JSON.stringify(fabPos.value)) } catch (e) { /* 忽略 */ }
  }
}

// 首条问候消息（清空对话后也会回到这条）
const GREETING = '你好！我是你的 AI 助教小智～\n课程学习中遇到任何疑问随时问我，我会结合课程知识图谱为你解答。'
const messages = ref([{ role: 'ai', content: GREETING, sources: [], greeting: true }])

const hasContext = computed(() => Boolean(effCourseId.value && effDocumentId.value))

function toggle() {
  // 拖动结束后松手会触发 click，此处抑制，避免误开/误关面板
  if (dragMoved.value) { dragMoved.value = false; return }
  open.value = !open.value
  if (open.value) scrollToBottom()
}

// 中文输入法组合状态下 Enter 用于选字，不应触发发送
function onEnter(e) {
  if (e.isComposing) return
  send()
}

async function send() {
  const q = question.value.trim()
  if (!q || asking.value) return
  question.value = ''
  messages.value.push({ role: 'user', content: q })
  asking.value = true
  scrollToBottom()
  try {
    const res = await api.ask(q, effCourseId.value || null, effDocumentId.value || null)
    const sources = (res.sources || []).map(normalizeSource)
    // 与主问答页一致：区分「检索成功但 LLM 生成失败」（降级文案）与正常回答
    messages.value.push({
      role: 'ai',
      content: res.answer || '（无回答）',
      sources,
      error: isLlmError(res.answer),
    })
  } catch (e) {
    // 网络 / 接口整体异常
    messages.value.push({ role: 'ai', content: '', error: true, sources: [] })
  } finally {
    asking.value = false
    scrollToBottom()
  }
}

function clearChat() {
  messages.value = [{ role: 'ai', content: GREETING, sources: [], greeting: true }]
}

// LLM 生成失败判定：后端生成异常时返回固定前缀的降级文案（与主问答页同一约定）
function isLlmError(answer) {
  return typeof answer === 'string' && answer.indexOf('问答服务暂时不可用') !== -1
}

// 引用来源归一化：后端返回结构化对象；兼容旧字符串格式（[类别] 名称: 描述）
function normalizeSource(s) {
  if (typeof s !== 'string') return s || {}
  const m = s.match(/^\[(.+?)\]\s*(.+?)(?::\s*([\s\S]*))?$/)
  if (m) return { category: m[1], name: m[2].trim(), description: (m[3] || '').trim() }
  return { category: '', name: s, description: '' }
}

function scrollToBottom() {
  nextTick(() => {
    if (chatBoxRef.value) {
      chatBoxRef.value.scrollTop = chatBoxRef.value.scrollHeight
    }
  })
}
</script>

<style scoped>
/* ===== 悬浮入口球 ===== */
.ai-widget-fab {
  position: fixed;
  right: 26px;
  bottom: 26px;
  z-index: 1999;
  width: 58px;
  height: 58px;
  border-radius: 50%;
  background: linear-gradient(135deg, #5b8def 0%, #6a5cf6 100%);
  box-shadow: 0 8px 24px rgba(91, 108, 246, 0.5), inset 0 1px 0 rgba(255,255,255,.35);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: grab;
  color: #fff;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
  user-select: none;
  touch-action: none;
}
/* 未展开时的呼吸光环，吸引注意但不喧宾夺主 */
.ai-widget-fab:not(.is-open)::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 50%;
  border: 2px solid rgba(91, 108, 246, .55);
  animation: ai-fab-pulse 2.4s ease-out infinite;
}
@keyframes ai-fab-pulse {
  0% { transform: scale(1); opacity: .8; }
  70% { transform: scale(1.45); opacity: 0; }
  100% { transform: scale(1.45); opacity: 0; }
}
.ai-widget-fab:hover {
  transform: scale(1.08);
  box-shadow: 0 10px 30px rgba(91, 108, 246, 0.65), inset 0 1px 0 rgba(255,255,255,.35);
}
.ai-fab-icon {
  width: 26px;
  height: 26px;
}
.ai-avatar-icon {
  width: 22px;
  height: 22px;
}
/* 拖动中：停掉过渡与脉冲，跟随更跟手 */
.ai-widget-fab.is-dragging {
  cursor: grabbing;
  transition: none;
  transform: scale(1.06);
  box-shadow: 0 12px 32px rgba(91, 108, 246, 0.7), inset 0 1px 0 rgba(255,255,255,.35);
}
.ai-widget-fab.is-dragging::before {
  animation: none;
  opacity: 0;
}

/* ===== 悬浮面板 ===== */
.ai-widget-panel {
  position: fixed;
  right: 24px;
  bottom: 92px;
  z-index: 1999;
  width: 380px;
  max-width: calc(100vw - 32px);
  height: min(600px, 72vh);
  border-radius: 16px;
  background: linear-gradient(165deg, #2b2440 0%, #1f1b2e 50%, #17202e 100%);
  box-shadow: 0 14px 44px rgba(10, 8, 24, 0.55);
  border: 1px solid rgba(255, 255, 255, 0.08);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  color: #e8e6f2;
}

/* 面板展开/收起动画 */
.ai-panel-enter-active,
.ai-panel-leave-active {
  transition: opacity 0.22s ease, transform 0.22s ease;
}
.ai-panel-enter-from,
.ai-panel-leave-to {
  opacity: 0;
  transform: translateY(16px) scale(0.96);
}

/* ===== 头部 ===== */
.ai-panel-header {
  padding: 16px 16px 10px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
.ai-header-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.ai-avatar {
  width: 42px;
  height: 42px;
  border-radius: 50%;
  background: linear-gradient(135deg, #8b7cf6, #4f8ef7);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  flex-shrink: 0;
  box-shadow: 0 4px 12px rgba(124, 108, 240, 0.4);
}
.ai-header-text {
  flex: 1;
  min-width: 0;
}
.ai-title {
  font-size: 15px;
  font-weight: 600;
  color: #f2f0fa;
  margin-bottom: 4px;
}
.ai-subtitle {
  font-size: 12px;
  color: #b6b1cf;
  line-height: 1.6;
}
/* 关闭按钮：常驻圆底 + 描边，明显可见，悬停高亮放大 */
.ai-close {
  color: #dcd8ee;
  cursor: pointer;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.14);
  border: 1px solid rgba(255, 255, 255, 0.28);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s ease, color 0.15s ease, transform 0.15s ease, border-color 0.15s ease;
  flex-shrink: 0;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.28);
}
.ai-close:hover {
  color: #fff;
  background: rgba(255, 255, 255, 0.3);
  border-color: rgba(255, 255, 255, 0.5);
  transform: scale(1.1);
}
.ai-header-bottom {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 10px;
}
.ai-context-chip {
  font-size: 11px;
  color: #c9c4e2;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 999px;
  padding: 3px 10px;
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-context-chip.empty {
  color: #8f8aa8;
}
.ai-clear {
  font-size: 11px;
  color: #8f8aa8;
  cursor: pointer;
  flex-shrink: 0;
}
.ai-clear:hover {
  color: #e8e6f2;
}

/* ===== 消息区 ===== */
.ai-messages {
  flex: 1;
  overflow-y: auto;
  padding: 14px;
  scrollbar-width: thin;
  scrollbar-color: rgba(255, 255, 255, 0.18) transparent;
}
.ai-messages::-webkit-scrollbar {
  width: 5px;
}
.ai-messages::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.18);
  border-radius: 3px;
}
.ai-msg-row {
  display: flex;
  margin-bottom: 12px;
}
.ai-msg-row.is-user {
  justify-content: flex-end;
}
.ai-msg-main {
  max-width: 84%;
  display: flex;
  flex-direction: column;
}
.ai-msg-row.is-user .ai-msg-main {
  align-items: flex-end;
}
.ai-tag {
  display: inline-block;
  align-self: flex-start;
  font-size: 10px;
  color: #b6b1cf;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 4px;
  padding: 1px 6px;
  margin-bottom: 4px;
}
.ai-bubble {
  padding: 10px 12px;
  border-radius: 12px;
  font-size: 13px;
  line-height: 1.65;
  word-break: break-word;
}
.ai-msg-row.is-ai .ai-bubble {
  background: rgba(255, 255, 255, 0.08);
  color: #e8e6f2;
  border-bottom-left-radius: 4px;
}
.ai-msg-row.is-user .ai-bubble {
  background: linear-gradient(135deg, #7c6cf0, #5b8bf4);
  color: #fff;
  border-bottom-right-radius: 4px;
}
.ai-msg-text {
  white-space: pre-wrap;
}
/* AI 回答 Markdown 渲染样式（v-html 注入内容，scoped 下需 :deep） */
.ai-md {
  white-space: normal;
}
.ai-md :deep(p) { margin: 0 0 8px; }
.ai-md :deep(p:last-child) { margin-bottom: 0; }
.ai-md :deep(h1),
.ai-md :deep(h2),
.ai-md :deep(h3),
.ai-md :deep(h4) { margin: 12px 0 6px; font-weight: 600; line-height: 1.4; }
.ai-md :deep(h1) { font-size: 17px; }
.ai-md :deep(h2) { font-size: 16px; }
.ai-md :deep(h3) { font-size: 15px; }
.ai-md :deep(h4) { font-size: 14px; }
.ai-md :deep(ul),
.ai-md :deep(ol) { margin: 6px 0 8px; padding-left: 20px; }
.ai-md :deep(li) { margin: 2px 0; }
.ai-md :deep(code) { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.92em; background: rgba(255, 255, 255, 0.12); padding: 1px 5px; border-radius: 4px; }
.ai-md :deep(pre) { margin: 8px 0; padding: 10px 12px; background: rgba(0, 0, 0, 0.25); border-radius: 8px; overflow-x: auto; }
.ai-md :deep(pre code) { background: none; padding: 0; white-space: pre; }
.ai-md :deep(blockquote) { margin: 8px 0; padding: 4px 10px; border-left: 3px solid rgba(255, 255, 255, 0.35); color: rgba(255, 255, 255, 0.85); }
.ai-md :deep(a) { color: #8ab4ff; text-decoration: underline; }
.ai-md :deep(hr) { border: none; border-top: 1px solid rgba(255, 255, 255, 0.2); margin: 10px 0; }
.ai-error {
  color: #f0b8c4;
}

/* 引用来源 */
.ai-sources {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
}
.ai-sources-title {
  font-size: 11px;
  color: #9d97bc;
  margin-bottom: 6px;
}
.ai-source-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #c9c4e2;
  padding: 3px 0;
}
.ai-source-cat {
  flex-shrink: 0;
  font-size: 10px;
  color: #a89cf0;
  background: rgba(124, 108, 240, 0.18);
  border-radius: 4px;
  padding: 1px 6px;
}
.ai-source-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 正在输入动画 */
.ai-typing {
  display: flex;
  gap: 5px;
  align-items: center;
  padding: 12px 14px;
}
.ai-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #b6b1cf;
  animation: ai-bounce 1.2s infinite ease-in-out;
}
.ai-dot:nth-child(2) {
  animation-delay: 0.15s;
}
.ai-dot:nth-child(3) {
  animation-delay: 0.3s;
}
@keyframes ai-bounce {
  0%, 60%, 100% { transform: translateY(0); opacity: 0.5; }
  30% { transform: translateY(-4px); opacity: 1; }
}

/* ===== 输入区 ===== */
.ai-input-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}
.ai-input {
  flex: 1;
  height: 38px;
  border: none;
  outline: none;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.1);
  color: #f2f0fa;
  font-size: 13px;
  padding: 0 16px;
}
.ai-input::placeholder {
  color: #8f8aa8;
}
.ai-input:focus {
  background: rgba(255, 255, 255, 0.14);
}
.ai-send {
  width: 38px;
  height: 38px;
  border: none;
  border-radius: 50%;
  background: linear-gradient(135deg, #7c6cf0, #4f8ef7);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
  transition: opacity 0.15s ease, transform 0.15s ease;
}
.ai-send:hover:not(:disabled) {
  transform: scale(1.06);
}
.ai-send:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* ===== 三边缩放手柄（左 / 上 / 左上角） ===== */
.ai-resize-edge {
  position: absolute;
  z-index: 5;
  touch-action: none;
}
.ai-resize-left {
  left: 0;
  top: 0;
  bottom: 0;
  width: 8px;
  cursor: ew-resize;
}
.ai-resize-top {
  top: 0;
  left: 0;
  right: 0;
  height: 8px;
  cursor: ns-resize;
}
/* 边缘悬停给出高亮提示条 */
.ai-resize-left:hover { box-shadow: inset 2px 0 0 rgba(255, 255, 255, 0.45); }
.ai-resize-top:hover { box-shadow: inset 0 2px 0 rgba(255, 255, 255, 0.45); }
.ai-resize-corner {
  position: absolute;
  top: 0;
  left: 0;
  width: 20px;
  height: 20px;
  cursor: nwse-resize;
  z-index: 6;
  touch-action: none;
}
.ai-resize-corner::before {
  content: '';
  position: absolute;
  top: 5px;
  left: 5px;
  width: 9px;
  height: 9px;
  border-top: 2px solid rgba(255, 255, 255, 0.3);
  border-left: 2px solid rgba(255, 255, 255, 0.3);
  border-top-left-radius: 3px;
  transition: border-color 0.15s ease;
}
.ai-resize-corner:hover::before {
  border-color: rgba(255, 255, 255, 0.8);
}
/* 拖拽调整期间禁止选中文字 / 滚动消息，避免干扰跟手性 */
.ai-widget-panel.is-resizing {
  user-select: none;
}
.ai-widget-panel.is-resizing .ai-messages {
  pointer-events: none;
}

/* 小屏适配 */
@media (max-width: 480px) {
  .ai-widget-panel {
    right: 12px;
    bottom: 80px;
    width: calc(100vw - 24px);
  }
  .ai-widget-fab {
    right: 16px;
    bottom: 16px;
  }
}
</style>
