<template>
  <div class="ai-chat">
    <!-- 左：历史对话列表（对齐主流 AI 产品的侧栏） -->
    <aside class="ai-side">
      <div class="side-top">
        <button class="new-chat" @click="onNewSession">
          <el-icon><Plus /></el-icon>
          <span>新建对话</span>
        </button>
      </div>
      <div class="side-head">历史对话<span v-if="chat.sessionList.length">（{{ chat.sessionList.length }}）</span></div>
      <div class="side-list">
        <div
          v-for="s in chat.sessionList"
          :key="s.id"
          class="side-item"
          :class="{ active: s.id === chat.activeId }"
          @click="onSelectSession(s.id)"
        >
          <el-icon class="side-item-icon"><ChatDotRound /></el-icon>
          <div class="side-item-body">
            <div class="side-item-title">{{ s.title || '新对话' }}</div>
            <div class="side-item-time">{{ relativeTime(s.updatedAt) }}</div>
          </div>
          <el-icon class="side-item-del" title="删除这段对话" @click.stop="onRemoveSession(s)"><Delete /></el-icon>
        </div>
      </div>
      <div class="side-foot">
        <el-icon><Collection /></el-icon>
        <span :title="courseName">{{ courseName || '未选择课程' }}</span>
      </div>
    </aside>

    <!-- 右：一条对话流 + 底部输入区 -->
    <section class="ai-main">
      <header class="ai-top">
        <span class="ai-top-title">AI 学习助手</span>
        <el-tag v-if="documentName" size="small" effect="plain" class="ai-top-doc">{{ documentName }}</el-tag>
        <span class="ai-top-hint" title="学生问答页与右下角 AI 助教悬浮球共用同一份对话">
          <el-icon><InfoFilled /></el-icon>
          与悬浮球共用同一份对话
        </span>
      </header>

      <div ref="chatBoxRef" class="ai-scroll">
        <!-- 空态：居中欢迎 + 可直接点的示例问题（主流 AI 的引导方式） -->
        <div v-if="!messages.length" class="ai-empty">
          <div class="empty-logo"><el-icon :size="30"><MagicStick /></el-icon></div>
          <h3 class="empty-title">今天想学点什么？</h3>
          <p class="empty-sub">回答基于本课程的知识图谱生成，会附上引用的知识点</p>
          <div class="empty-samples">
            <button v-for="s in SAMPLES" :key="s" class="empty-sample" @click="useSample(s)">{{ s }}</button>
          </div>
        </div>

        <div v-else class="ai-thread">
          <template v-for="(m, i) in messages" :key="m.mid">
            <!-- 切换学习资料后插入分隔行，避免两段上下文被误读成同一轮问答 -->
            <div v-if="showCtxDivider(i)" class="chat-divider">
              <span>已切换学习资料 · {{ m.ctxLabel }}</span>
            </div>
            <div
              class="turn"
              :class="m.role === 'user' ? 'is-user' : 'is-ai'"
            >
            <div v-if="m.role === 'user'" class="user-bubble">{{ m.content }}</div>

            <template v-else>
              <div class="turn-avatar"><el-icon><MagicStick /></el-icon></div>
              <div class="turn-body">
                <div v-if="m.error" class="turn-error msg-error" :title="m.errDetail || ''">
                  <el-icon><WarningFilled /></el-icon>
                  <span>{{ m.errorMessage || 'AI 服务暂时不可用，请稍后重试' }}</span>
                  <el-button
                    size="small"
                    text
                    type="primary"
                    class="msg-retry"
                    :disabled="asking"
                    @click="retryMessage(m)"
                  >
                    <el-icon class="icon-gap"><RefreshRight /></el-icon>重试
                  </el-button>
                </div>
                <div v-else class="turn-text md" v-html="renderMarkdown(m.content)"></div>
                <!-- 流式生成中：正文还在增长时的轻提示 -->
                <div v-if="m.streaming" class="msg-streaming">
                  {{ m.content ? '正在生成…' : '正在思考…' }}
                </div>
                <!-- 生成结束但结果不完整（截断 / 中断）：明确说明，不假装完整 -->
                <div v-if="m.note && !m.streaming" class="msg-note">{{ m.note }}</div>

                <!-- 向量检索不可用、已降级为关键词匹配：明确提示回答质量可能下降，避免「变差却不报错」 -->
                <div
                  v-if="!m.streaming && !m.error && m.retrievalMode && m.retrievalMode !== 'vector'"
                  class="msg-degraded"
                >
                  <el-icon><WarningFilled /></el-icon>
                  <span v-if="m.retrievalMode === 'keyword'">
                    当前向量检索不可用，已降级为关键词匹配，回答仅供参考，请结合教材核对。
                  </span>
                  <span v-else>未检索到课程知识点，本回答基于通用知识生成，仅供参考。</span>
                </div>

                <div v-if="!m.error && m.content && !m.streaming" class="turn-ops">
                  <button class="turn-op" @click="copyAnswer(m)">
                    <el-icon><CopyDocument /></el-icon>复制
                  </button>
                  <button class="turn-op" @click="toggleCites(m.mid)">
                    <el-icon><Document /></el-icon>参考来源<span v-if="m.sources && m.sources.length"> {{ m.sources.length }}</span>
                    <el-icon class="op-arrow" :class="{ open: !!openCites[m.mid] }"><ArrowDown /></el-icon>
                  </button>
                </div>

                <!-- 参考来源默认收起，但先用一行 chip 露出来：关键依据不该藏在一次点击后面 -->
                <div v-if="!openCites[m.mid] && m.sources && m.sources.length" class="cite-preview">
                  <span class="cite-preview-label">参考来源</span>
                  <el-tag
                    v-for="(s, j) in m.sources.slice(0, 3)"
                    :key="s.kp_id || s.name || j"
                    size="small"
                    effect="plain"
                    :type="categoryTagType(s.category)"
                    class="cite-preview-tag"
                    @click="emit('jump-kp', s)"
                  >{{ s.name }}</el-tag>
                  <span v-if="m.sources.length > 3" class="cite-preview-more">+{{ m.sources.length - 3 }}</span>
                  <button class="turn-op cite-preview-more-btn" @click="toggleCites(m.mid)">展开全部</button>
                </div>

                <div v-if="openCites[m.mid]" class="cite-list">
                  <div v-if="!m.sources || !m.sources.length" class="cite-empty">
                    未检索到相关知识点（AI 基于通用知识回答，仅供参考）
                  </div>
                  <div
                    v-for="(s, j) in (m.sources || [])"
                    :key="s.kp_id || s.name || j"
                    class="cite-card"
                    @click="emit('jump-kp', s)"
                  >
                    <div class="cite-head">
                      <el-tag size="small" effect="plain" :type="categoryTagType(s.category)">
                        {{ s.category || '知识点' }}
                      </el-tag>
                      <span class="cite-name">{{ s.name }}</span>
                      <el-button
                        v-if="s.kp_id && ctxKey"
                        size="small"
                        text
                        class="cite-fav"
                        :title="isFav(s.kp_id) ? '取消收藏' : '收藏'"
                        :type="isFav(s.kp_id) ? 'warning' : 'info'"
                        @click.stop="emit('toggle-favorite', s.kp_id)"
                      >
                        <el-icon><StarFilled v-if="isFav(s.kp_id)" /><Star v-else /></el-icon>
                      </el-button>
                      <el-icon class="cite-jump"><Right /></el-icon>
                    </div>
                    <div v-if="s.description" class="cite-desc">{{ s.description }}</div>
                  </div>
                </div>
              </div>
            </template>
          </div>
          </template>

          <div v-if="asking && !lastStreaming" class="turn is-ai">
            <div class="turn-avatar"><el-icon><MagicStick /></el-icon></div>
            <div class="turn-body">
              <div class="typing"><span></span><span></span><span></span></div>
            </div>
          </div>
        </div>
      </div>

      <div class="ai-composer">
        <div class="composer-box">
          <textarea
            ref="inputRef"
            v-model="question"
            class="composer-input"
            rows="1"
            :placeholder="asking ? 'AI 正在回答，可以提前输入下一个问题…' : '给 AI 学习助手下达指令，或直接提问'"
            @keydown="onKeydown"
            @input="autoGrow"
          ></textarea>
          <div class="composer-bar">
            <span class="composer-tip">Enter 发送 · Shift + Enter 换行</span>
            <div class="composer-actions">
              <button v-if="asking" class="btn-stop" @click="stopQuestion">
                <el-icon><VideoPause /></el-icon>
                停止生成
              </button>
              <button
                class="btn-send"
                :disabled="asking || !question.trim()"
                title="发送"
                @click="sendQuestion"
              >
                <el-icon><Top /></el-icon>
              </button>
            </div>
          </div>
        </div>
        <p class="composer-note">回答由 AI 基于课程知识图谱生成，请结合教材原文核对</p>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import {
  ArrowDown, ChatDotRound, Collection, CopyDocument, Delete, Document, InfoFilled,
  MagicStick, Plus, RefreshRight, Right, Star, StarFilled, Top, VideoPause, WarningFilled,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { categoryTagType } from '../../utils/graphStyle'
import { renderMarkdown } from '../../utils/markdown'
import { useAiChatStore } from '../../stores/aiChat'

const props = defineProps({
  courseId: { type: [String, Number], default: '' },
  documentId: { type: [String, Number], default: '' },
  courseName: { type: String, default: '' },
  ctxKey: { type: String, default: '' },
  favoriteIds: { type: Array, default: () => [] },
})

const emit = defineEmits(['jump-kp', 'toggle-favorite'])

// 与全局 AI 悬浮球共用同一份会话：这里问的问题会同步出现在悬浮窗，反之亦然
const chat = useAiChatStore()
const messages = computed(() => chat.messages)
const asking = computed(() => chat.asking)

const question = ref('')
const chatBoxRef = ref(null)
const inputRef = ref(null)
// 每条回答的「参考来源」展开状态（按 mid 记，默认收起，不占对话篇幅）
const openCites = ref({})

// 空对话时给的例子，点一下就填进输入框，省得学生不知道能问什么
const SAMPLES = [
  '这门课的核心知识点有哪些？',
  '用一个例子解释课程里的重点概念',
  '帮我梳理这门课的学习顺序',
  '出一道相关练习题并给出答案',
]

// 末尾是否已是一条「正在生成」的占位消息：流式输出时由它显示进度，不再单独转圈
const lastStreaming = computed(() => {
  const last = messages.value[messages.value.length - 1]
  return !!(last && last.streaming)
})

/** 相邻两条消息来自不同的学习资料时，中间插一条分隔说明 */
function showCtxDivider(index) {
  if (index === 0) return false
  const cur = messages.value[index]
  const prev = messages.value[index - 1]
  return !!(cur && prev && cur.ctxKey && cur.ctxKey !== prev.ctxKey)
}

// 课程 / 学习资料上下文变化交给共享会话：切课程载入该课程的历史，换资料只作废在途回答
watch([() => props.courseId, () => props.documentId, () => props.documentName], ([cid, did, dname]) => {
  chat.bindContext(cid, did, dname)
}, { immediate: true })
// 新消息（长度变化）与流式增量（同一条内容变长）都要跟随滚动：悬浮窗提问时这里也要看到
watch(
  () => {
    const list = messages.value
    const last = list[list.length - 1]
    return `${list.length}:${last ? `${last.mid}:${(last.content || '').length}` : ''}`
  },
  () => scrollChatToBottom(),
)
// 切会话后来源展开状态按会话重来，并回到最新一条
watch(() => chat.activeId, () => {
  openCites.value = {}
  resetInputHeight()
  scrollChatToBottom()
})

function isFav(kpId) {
  return props.favoriteIds.includes(String(kpId))
}

async function sendQuestion() {
  const q = question.value.trim()
  if (!q || asking.value) return
  const beforeLen = messages.value.length
  question.value = ''
  resetInputHeight()
  scrollChatToBottom()
  await chat.send(q)
  // 失败时把问题还回输入框，避免用户重打一遍；用户已在等待期间输入新内容则不覆盖
  const bubble = messages.value[messages.value.length - 1]
  if (messages.value.length > beforeLen && bubble?.error && !question.value.trim()) {
    question.value = q
    nextTick(autoGrow)
  }
  scrollChatToBottom()
}

/** 重试失败的问答：复用共享会话的 retry（删除错误气泡，用原提问重新发送） */
async function retryMessage(m) {
  await chat.retry(m.mid)
  scrollChatToBottom()
}

/** 停止当前生成：只中断请求，对话历史保留（共享会话按 CanceledError 标记「已停止生成」） */
function stopQuestion() {
  chat.stop()
}

function scrollChatToBottom() {
  nextTick(() => {
    if (chatBoxRef.value) chatBoxRef.value.scrollTop = chatBoxRef.value.scrollHeight
  })
}

/** 输入框随内容增高（主流 AI 的多行输入），到 150px 后内部滚动 */
function autoGrow() {
  const el = inputRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 150)}px`
}

function resetInputHeight() {
  if (inputRef.value) inputRef.value.style.height = 'auto'
}

/** 回车发送、Shift + Enter 换行；输入法组词中的回车不发送 */
function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    sendQuestion()
  }
}

function useSample(text) {
  question.value = text
  nextTick(() => {
    inputRef.value?.focus()
    autoGrow()
  })
}

function toggleCites(mid) {
  openCites.value = { ...openCites.value, [mid]: !openCites.value[mid] }
}

async function copyAnswer(m) {
  try {
    await navigator.clipboard.writeText(m.content || '')
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('复制失败，请手动选择文本')
  }
}

function onSelectSession(id) {
  chat.selectSession(id)
}

function onNewSession() {
  chat.newSession()
  question.value = ''
  nextTick(() => inputRef.value?.focus())
}

async function onRemoveSession(s) {
  try {
    await ElMessageBox.confirm(
      `删除「${s.title || '新对话'}」后，这段对话记录将不可恢复。`,
      '删除对话',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  chat.removeSession(s.id)
}

/** 历史会话的时间：今天显示时刻，今年显示月-日，更早显示完整日期 */
function relativeTime(t) {
  if (!t) return ''
  const d = new Date(t)
  if (Number.isNaN(d.getTime())) return ''
  const now = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  if (d.toDateString() === now.toDateString()) return `${pad(d.getHours())}:${pad(d.getMinutes())}`
  if (d.getFullYear() === now.getFullYear()) return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}
</script>

<style scoped>
/* ===== 整体：左历史会话 + 右对话流，对齐主流 AI 聊天产品 ===== */
.ai-chat {
  display: flex;
  height: calc(100vh - 232px);
  min-height: 480px;
  max-height: 920px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
  background: var(--color-bg-surface);
}

/* ----- 左侧会话列表 ----- */
.ai-side {
  width: 244px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: var(--color-bg-soft);
  border-right: 1px solid var(--color-border);
}
.side-top { padding: 12px 12px 6px; }
.new-chat {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 36px;
  border-radius: var(--radius-md);
  border: 1px dashed var(--color-border);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
  font-size: var(--font-size-body);
  cursor: pointer;
  transition: all 0.16s;
}
.new-chat:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
  background: var(--brand-50);
}
.side-head {
  padding: 6px 14px 4px;
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}
.side-list { flex: 1; overflow-y: auto; padding: 0 8px 8px; }
.side-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  margin-bottom: 2px;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background 0.15s;
}
.side-item:hover { background: var(--color-bg-hover); }
.side-item.active { background: var(--brand-50); }
.side-item.active .side-item-title {
  color: var(--brand-700);
  font-weight: var(--font-weight-semibold);
}
.side-item-icon { color: var(--color-text-muted); flex-shrink: 0; }
.side-item-body { flex: 1; min-width: 0; }
.side-item-title {
  font-size: var(--font-size-label);
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.side-item-time { margin-top: 2px; font-size: var(--font-size-caption); color: var(--color-text-muted); }
.side-item-del {
  flex-shrink: 0;
  color: var(--color-text-muted);
  opacity: 0;
  transition: opacity 0.15s, color 0.15s;
}
.side-item:hover .side-item-del { opacity: 1; }
.side-item-del:hover { color: var(--color-danger); }
.side-empty { padding: 22px 12px; text-align: center; font-size: var(--font-size-caption); color: var(--color-text-muted); }
.side-foot {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 14px;
  border-top: 1px solid var(--color-border);
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}
.side-foot span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ----- 右侧主区 ----- */
.ai-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.ai-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 20px;
  border-bottom: 1px solid var(--color-border-light);
}
.ai-top-title {
  font-size: var(--font-size-section);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
}
.ai-top-doc {
  max-width: 220px;
  margin-left: 10px;
  margin-right: auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-top-hint {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--font-size-caption);
  color: var(--color-text-muted);
}
.ai-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 18px 20px;
  scroll-behavior: smooth;
}

/* ----- 对话流 ----- */
.ai-thread { max-width: 820px; margin: 0 auto; }
.turn { display: flex; gap: 12px; margin-bottom: 22px; }
.turn.is-user { justify-content: flex-end; }
.user-bubble {
  max-width: 76%;
  padding: 10px 14px;
  border-radius: 14px;
  background: var(--brand-50);
  color: var(--brand-700);
  font-size: var(--font-size-body);
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}
.turn-avatar {
  width: 30px;
  height: 30px;
  border-radius: 9px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--gradient-brand);
  color: #fff;
  box-shadow: 0 6px 14px -8px rgba(79, 110, 247, 0.7);
}
.turn-avatar :deep(svg) { color: #fff; }
.turn-body { flex: 1; min-width: 0; }
.turn-text {
  font-size: var(--font-size-body);
  line-height: 1.75;
  color: var(--color-text-primary);
  word-break: break-word;
}
.turn-error {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--color-danger);
  font-size: var(--font-size-label);
}
.turn-ops { display: flex; align-items: center; gap: 14px; margin-top: 8px; }
.msg-retry { margin-left: 4px; padding: 0 4px; }
/* 参考来源预览：收起状态下也把依据亮出来 */
.cite-preview {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.cite-preview-label {
  font-size: var(--font-size-caption);
  color: var(--color-text-muted);
}
.cite-preview-tag {
  cursor: pointer;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cite-preview-more {
  font-size: var(--font-size-caption);
  color: var(--color-text-muted);
}
.cite-preview-more-btn { padding: 0; }
/* 流式生成中：正文还在增长时的轻提示 */
.msg-streaming {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  font-size: var(--font-size-caption);
  color: var(--color-text-muted);
}
/* 结果不完整（截断 / 中断）的说明：警告色，但不打断阅读 */
.msg-note {
  margin-top: 6px;
  font-size: var(--font-size-caption);
  color: var(--color-warning);
}
/* 检索降级提示：比「结果不完整」更醒目一层，但仍不打断阅读 */
.msg-degraded {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-top: 8px;
  padding: 6px 10px;
  font-size: var(--font-size-caption);
  line-height: 1.6;
  color: var(--color-warning);
  background: rgba(230, 162, 60, 0.1);
  border: 1px solid rgba(230, 162, 60, 0.32);
  border-radius: 8px;
}
/* 切换学习资料的分隔行：把两段上下文分开，避免误读成同一轮问答 */
.chat-divider {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 14px 0 6px;
  font-size: var(--font-size-caption);
  color: var(--color-text-muted);
}
.chat-divider::before,
.chat-divider::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--color-border-light);
}
.cite-empty {
  font-size: var(--font-size-caption);
  color: var(--color-text-muted);
  line-height: 1.6;
}
.turn-op {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 0;
  border: none;
  background: none;
  font-size: var(--font-size-caption);
  color: var(--color-text-muted);
  cursor: pointer;
  transition: color 0.15s;
}
.turn-op:hover { color: var(--color-primary); }
.op-arrow { transition: transform 0.18s; }
.op-arrow.open { transform: rotate(180deg); }

/* 参考来源：收在回答下方，展开才占篇幅（主流 AI 的做法） */
.cite-list {
  margin-top: 10px;
  padding: 10px 12px;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  background: var(--color-bg-soft);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.cite-card {
  padding: 8px 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg-surface);
  cursor: pointer;
  transition: all 0.15s;
}
.cite-card:hover {
  border-color: var(--brand-300);
  box-shadow: 0 4px 12px -6px rgba(79, 110, 247, 0.35);
}
.cite-head { display: flex; align-items: center; gap: 8px; }
.cite-name {
  flex: 1;
  min-width: 0;
  font-size: var(--font-size-label);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cite-fav { flex-shrink: 0; padding: 2px; }
.cite-jump { color: var(--color-text-muted); flex-shrink: 0; }
.cite-card:hover .cite-jump { color: var(--color-primary); }
.cite-desc {
  margin-top: 5px;
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  line-height: 1.55;
}

/* AI 回答 Markdown 渲染（v-html 注入内容，scoped 下需 :deep） */
.md :deep(p) { margin: 0 0 8px; }
.md :deep(p:last-child) { margin-bottom: 0; }
.md :deep(h1),
.md :deep(h2),
.md :deep(h3),
.md :deep(h4) { margin: 12px 0 6px; font-weight: 600; line-height: 1.4; }
.md :deep(h1) { font-size: 18px; }
.md :deep(h2) { font-size: 16px; }
.md :deep(h3) { font-size: 15px; }
.md :deep(h4) { font-size: 14px; }
.md :deep(ul),
.md :deep(ol) { margin: 6px 0 8px; padding-left: 22px; }
.md :deep(li) { margin: 2px 0; }
.md :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.9em;
  background: #f0f1f5;
  padding: 1px 5px;
  border-radius: 4px;
}
.md :deep(pre) {
  margin: 8px 0;
  padding: 10px 12px;
  background: #f5f6fa;
  border: 1px solid #eceef3;
  border-radius: 8px;
  overflow-x: auto;
}
.md :deep(pre code) { background: none; padding: 0; white-space: pre; }
.md :deep(blockquote) {
  margin: 8px 0;
  padding: 4px 10px;
  border-left: 3px solid var(--color-primary, #7c6cf0);
  color: #555;
}
.md :deep(a) { color: var(--color-primary, #5b8bf4); text-decoration: underline; }
.md :deep(hr) { border: none; border-top: 1px solid #e5e5e5; margin: 10px 0; }

/* ----- 空态 ----- */
.ai-empty {
  min-height: 100%;
  max-width: 760px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 20px 0 32px;
}
.empty-logo {
  width: 64px;
  height: 64px;
  margin-bottom: 16px;
  border-radius: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--gradient-brand);
  color: #fff;
  box-shadow: 0 14px 30px -14px rgba(79, 110, 247, 0.7);
}
.empty-logo :deep(svg) { color: #fff; }
.empty-title {
  margin: 0 0 8px;
  font-size: 20px;
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
}
.empty-sub { margin: 0 0 26px; font-size: var(--font-size-label); color: var(--color-text-secondary); }
.empty-samples {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  width: 100%;
  max-width: 560px;
}
.empty-sample {
  text-align: left;
  padding: 12px 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-soft);
  color: var(--color-text-primary);
  font-size: var(--font-size-label);
  cursor: pointer;
  transition: all 0.16s;
}
.empty-sample:hover {
  border-color: var(--brand-300);
  background: var(--color-bg-surface);
  box-shadow: 0 6px 16px -10px rgba(79, 110, 247, 0.45);
  transform: translateY(-1px);
}

/* ----- 输入区 ----- */
.ai-composer {
  padding: 10px 20px 14px;
  border-top: 1px solid var(--color-border-light);
  background: var(--color-bg-surface);
}
.composer-box {
  max-width: 820px;
  margin: 0 auto;
  padding: 8px 10px 8px 14px;
  border: 1px solid var(--color-border);
  border-radius: 14px;
  background: var(--color-bg-soft);
  transition: border-color 0.15s, box-shadow 0.15s;
}
.composer-box:focus-within {
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px rgba(79, 110, 247, 0.12);
}
.composer-input {
  width: 100%;
  max-height: 150px;
  padding: 4px 0;
  border: none;
  outline: none;
  resize: none;
  background: transparent;
  font-family: inherit;
  font-size: var(--font-size-body);
  line-height: 1.6;
  color: var(--color-text-primary);
}
.composer-input::placeholder { color: var(--color-text-muted); }
.composer-bar { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 4px; }
.composer-tip { font-size: var(--font-size-caption); color: var(--color-text-muted); }
.composer-actions { display: flex; align-items: center; gap: 8px; }
.btn-stop {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 28px;
  padding: 0 10px;
  border-radius: 14px;
  border: 1px solid rgba(239, 94, 94, 0.5);
  background: rgba(239, 94, 94, 0.08);
  color: #d24b4b;
  font-size: var(--font-size-caption);
  cursor: pointer;
  transition: background 0.15s;
}
.btn-stop:hover { background: rgba(239, 94, 94, 0.16); }
.btn-send {
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--gradient-brand);
  color: #fff;
  cursor: pointer;
  transition: transform 0.15s, opacity 0.15s;
}
.btn-send:hover:not(:disabled) { transform: scale(1.06); }
.btn-send:disabled { opacity: 0.45; cursor: not-allowed; }
.btn-send :deep(svg) { color: #fff; }
.composer-note {
  max-width: 820px;
  margin: 6px auto 0;
  text-align: center;
  font-size: var(--font-size-caption);
  color: var(--color-text-muted);
}

/* 等待回答时的三点动画 */
.typing { display: flex; align-items: center; gap: 5px; height: 28px; }
.typing span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-text-muted);
  animation: blink 1.2s infinite;
}
.typing span:nth-child(2) { animation-delay: 0.2s; }
.typing span:nth-child(3) { animation-delay: 0.4s; }
@keyframes blink {
  0%, 100% { opacity: 0.25; }
  50% { opacity: 1; }
}

/* 窄屏：会话列表收成顶部一条横向可滚的条，主对话区保持整宽 */
@media (max-width: 900px) {
  .ai-chat { flex-direction: column; }
  .ai-side {
    width: 100%;
    max-height: 172px;
    border-right: none;
    border-bottom: 1px solid var(--color-border);
  }
  .side-list { display: flex; gap: 4px; overflow-x: auto; overflow-y: hidden; }
  .side-item { flex: 0 0 180px; margin-bottom: 0; }
  .empty-samples { grid-template-columns: minmax(0, 1fr); }
}
</style>
