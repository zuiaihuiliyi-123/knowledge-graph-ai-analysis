<template>
  <el-row :gutter="16" class="qa-layout">
    <!-- 左栏：当前课程 + 相关知识点 -->
    <el-col :xs="24" :sm="6">
      <el-card class="page-card qa-side">
        <template #header>
          <div class="qa-side-title"><el-icon><Collection /></el-icon>当前课程</div>
        </template>
        <div class="qa-side-course">{{ courseName }}</div>

        <el-divider content-position="left">相关知识点</el-divider>
        <el-empty
          v-if="!relatedKps.length"
          description="提问后，此处展示答案引用的知识点"
          :image-size="60"
        />
        <div v-else class="kp-list">
          <div v-for="kp in relatedKps" :key="kp.kp_id || kp.name" class="kp-item" @click="emit('jump-kp', kp)">
            <el-tag size="small" effect="plain" :type="categoryTagType(kp.category)">
              {{ kp.category || '知识点' }}
            </el-tag>
            <span class="kp-name">{{ kp.name }}</span>
            <el-icon class="kp-jump"><Right /></el-icon>
          </div>
        </div>
      </el-card>
    </el-col>

    <!-- 右栏：AI 学习助手 -->
    <el-col :xs="24" :sm="18">
      <el-card class="qa-card qa-main">
        <div class="qa-toolbar">
          <span class="qa-title"><el-icon><MagicStick /></el-icon>AI 学习助手</span>
          <el-button size="small" @click="clearChat">清空对话</el-button>
        </div>

        <div ref="chatBoxRef" class="chat-box chat-scroll">
          <div v-if="!messages.length" class="chat-welcome">
            <div class="welcome-icon"><el-icon :size="40" color="var(--color-primary)"><MagicStick /></el-icon></div>
            <p>基于课程知识图谱的 AI 问答助手</p>
            <p class="welcome-sub">例如：什么是线性表？栈和队列有什么区别？</p>
          </div>

          <template v-for="(m, i) in messages" :key="i">
            <!-- 上下文分隔：切换学习资料后历史消息保留，用分隔行标出「属于哪份资料」 -->
            <div v-if="showCtxDivider(i)" class="chat-divider">
              <span>已切换学习资料 · {{ m.ctxLabel }}</span>
            </div>
            <div
              class="msg-row"
              :class="m.role === 'user' ? 'msg-user' : 'msg-ai'"
            >
            <div class="msg-bubble">
              <!-- 错误态：LLM 生成失败（网络/接口异常也归为此类） -->
              <div v-if="m.error" class="msg-error" :title="m.errDetail || ''">
                <el-icon><WarningFilled /></el-icon>
                <span>AI 服务暂时不可用，请稍后重试</span>
              </div>
              <template v-else>
                <div v-if="m.role === 'ai'" class="msg-text msg-md" v-html="renderMarkdown(m.content)"></div>
                <div v-else class="msg-text">{{ m.content }}</div>
                <div v-if="m.streaming" class="msg-streaming"><span class="typing-dot"></span>正在生成…</div>
                <!-- 证据链：回答正文 → 引用来源 → 相关知识点 -->
                <div v-if="m.role === 'ai'" class="msg-sources">
                  <div class="sources-title">
                    <el-icon><Document /></el-icon> 参考来源 · 课程知识库
                  </div>
                  <template v-if="m.sources && m.sources.length">
                    <div
                      v-for="(s, j) in m.sources"
                      :key="s.kp_id || s.name || j"
                      class="source-card"
                      @click="emit('jump-kp', s)"
                    >
                      <div class="source-head">
                        <el-tag size="small" effect="plain" :type="categoryTagType(s.category)">
                          {{ s.category || '知识点' }}
                        </el-tag>
                        <span class="source-name">{{ s.name }}</span>
                        <el-button
                          v-if="s.kp_id && ctxKey"
                          size="small"
                          text
                          class="source-fav"
                          :title="isFav(s.kp_id) ? '取消收藏' : '收藏'"
                          :type="isFav(s.kp_id) ? 'warning' : 'info'"
                          @click.stop="emit('toggle-favorite', s.kp_id)"
                        >
                          <el-icon><StarFilled v-if="isFav(s.kp_id)" /><Star v-else /></el-icon>
                        </el-button>
                        <el-icon class="source-jump"><Right /></el-icon>
                      </div>
                      <div v-if="s.description" class="source-desc">{{ s.description }}</div>
                    </div>
                  </template>
                  <div v-else class="sources-empty">未检索到相关知识点（AI 基于通用知识回答，仅供参考）</div>
                </div>
                </template>
              </div>
            </div>
          </template>

          <div v-if="asking && !streamingText" class="msg-row msg-ai">
            <div class="msg-bubble typing">
              <span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>
            </div>
          </div>
        </div>

        <div class="chat-input">
          <el-input
            v-model="question"
            placeholder="输入你的问题，回车发送"
            :disabled="asking"
            @keyup.enter="sendQuestion"
          />
          <el-button type="primary" :loading="asking" @click="sendQuestion">
            发送
          </el-button>
        </div>
      </el-card>
    </el-col>
  </el-row>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import {
  Collection, Document, MagicStick, Right, Star, StarFilled, WarningFilled,
} from '@element-plus/icons-vue'
import { api } from '../../api'
import { askStream } from '../../api/qaStream'
import { categoryTagType } from '../../utils/graphStyle'
import { renderMarkdown } from '../../utils/markdown'
import { isAbortError, useLatestRequest } from '../../composables/useLatestRequest'

const props = defineProps({
  courseId: { type: [String, Number], default: '' },
  documentId: { type: [String, Number], default: '' },
  courseName: { type: String, default: '' },
  documentName: { type: String, default: '' },
  ctxKey: { type: String, default: '' },
  favoriteIds: { type: Array, default: () => [] },
})

const emit = defineEmits(['jump-kp', 'toggle-favorite'])

const question = ref('')
const asking = ref(false)
// 已收到的正文字数（非空即代表首字已到：用于收起「正在输入」三点动画）
const streamingText = ref('')
const messages = ref([])
const chatBoxRef = ref(null)
const sendRequest = useLatestRequest()

/** 当前问答上下文的展示名（用于区分历史消息属于哪份学习资料） */
function currentCtxLabel() {
  if (props.documentName) return props.documentName
  if (props.documentId) return `文档 #${props.documentId}`
  return '未选择学习资料'
}

/** 给消息打上「属于哪份资料」的标记：切换资料后历史仍可回看，但能看出归属 */
function withCtx(msg) {
  return { ...msg, ctxKey: props.ctxKey, ctxLabel: currentCtxLabel() }
}

/** 相邻两条消息来自不同学习资料时，插入分隔行 */
function showCtxDivider(index) {
  const cur = messages.value[index]
  const prev = messages.value[index - 1]
  if (!cur || !prev) return false
  return !!cur.ctxKey && !!prev.ctxKey && cur.ctxKey !== prev.ctxKey
}

function isFav(kpId) {
  return props.favoriteIds.includes(String(kpId))
}

async function sendQuestion() {
  const q = question.value.trim()
  if (!q || asking.value) return
  const cid = props.courseId
  const did = props.documentId
  // 流式请求用 beginRead()：切换资料/离开页面时中断流。
  // 与同步问答不同——中断 SSE 连接会让后端生成器随之结束，等于「止损」；同步请求则早已计费。
  const { token, signal } = sendRequest.beginRead()
  question.value = ''
  messages.value.push(withCtx({ role: 'user', content: q }))

  // 先占位一条 AI 消息：sources 一到就渲染证据链，delta 直接往里追加
  messages.value.push(withCtx({
    role: 'ai', content: '', sources: [], error: false, streaming: true,
  }))
  const bubble = messages.value[messages.value.length - 1]
  asking.value = true
  streamingText.value = ''
  scrollChatToBottom()

  let received = ''
  try {
    const events = await askStream({ question: q, courseId: cid, documentId: did, signal })
    for await (const ev of events) {
      if (!sendRequest.isLatest(token)) return
      if (ev.event === 'sources') {
        bubble.sources = (ev.data?.sources || []).map(normalizeSource)
      } else if (ev.event === 'delta') {
        received += ev.data?.text || ''
        streamingText.value = received
        bubble.content = received
        scrollChatToBottom()
      } else if (ev.event === 'done') {
        bubble.content = ev.data?.answer || received || '（无回答）'
        if (ev.data?.sources) bubble.sources = ev.data.sources.map(normalizeSource)
        bubble.error = isLlmError(bubble.content)
        bubble.streaming = false
        return
      } else if (ev.event === 'error') {
        bubble.error = true
        bubble.errDetail = ev.data?.message || ''
        bubble.streaming = false
        return
      }
    }
    // 流正常结束但没有 done（后端被中断等）：把已收到的内容当作结果
    bubble.content = received || '（无回答）'
    bubble.streaming = false
  } catch (e) {
    if (isAbortError(e)) {
      // 切换资料/卸载：保留半成品并标记中断，避免看起来像「AI 没回答」
      bubble.streaming = false
      if (!bubble.content) {
        bubble.error = true
        bubble.errDetail = '已中断（切换了学习资料）'
      }
      return
    }
    // 非流式响应（权限拒绝、代理不支持流）或网络失败：有内容就保留，否则回退同步接口
    if (!received) {
      try {
        const res = await api.ask(q, cid, did, { signal })
        if (!sendRequest.isLatest(token)) return
        bubble.content = res.answer || '（无回答）'
        bubble.sources = (res.sources || []).map(normalizeSource)
        bubble.error = isLlmError(res.answer)
        bubble.streaming = false
        return
      } catch (e2) {
        if (isAbortError(e2)) return
        bubble.error = true
        bubble.errDetail = e2.message || ''
      }
    } else {
      bubble.error = false
    }
    bubble.streaming = false
  } finally {
    streamingText.value = ''
    if (sendRequest.isLatest(token)) {
      asking.value = false
      scrollChatToBottom()
    }
  }
}

// LLM 生成失败判定：后端在生成异常时返回固定前缀的降级文案（不改后端契约，前端识别）
function isLlmError(answer) {
  return typeof answer === 'string' && answer.indexOf('问答服务暂时不可用') !== -1
}

function scrollChatToBottom() {
  nextTick(() => {
    if (chatBoxRef.value) chatBoxRef.value.scrollTop = chatBoxRef.value.scrollHeight
  })
}

function clearChat() {
  // 清空对话同时中断在途生成：否则后端会继续吐 token（有成本），且半成品会写回已清空的列表
  sendRequest.invalidate()
  asking.value = false
  streamingText.value = ''
  messages.value = []
}

// 引用来源归一化：兼容旧字符串格式（[类别] 名称: 描述）
function normalizeSource(s) {
  if (typeof s !== 'string') return s || {}
  const m = s.match(/^\[(.+?)\]\s*(.+?)(?::\s*([\s\S]*))?$/)
  if (m) return { category: m[1], name: m[2].trim(), description: (m[3] || '').trim() }
  return { category: '', name: s, description: '' }
}

// 切换学习资料：作废在途回答并解除输入锁定，避免上一文档的回答落进新文档的对话。
// 历史消息保留（用户可回看），新消息按 ctxKey 打标记，界面在跨资料处插入分隔行。
// 注意只在「已有上下文 → 另一个上下文」时中断：首次从空值落上下文（深链进入时
// initFromRoute 是异步的）不该把刚发出的提问掐掉——那次提问本来就是「全部可访问课程」范围。
watch(() => props.ctxKey, (now, before) => {
  if (!before) return
  sendRequest.invalidate()
  asking.value = false
})

// 左栏「相关知识点」= 最近一条 AI 回答的引用来源
const relatedKps = computed(() => {
  for (let i = messages.value.length - 1; i >= 0; i--) {
    const m = messages.value[i]
    if (m.role === 'ai' && m.sources?.length) return m.sources
  }
  return []
})
</script>

<style scoped>
/* 问答 */
.qa-card { max-width: 100%; }
.qa-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--space-3);
}
.chat-box {
  height: 460px;
  padding: var(--space-3);
  background: var(--color-bg-soft);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow-y: auto;
}
.chat-welcome {
  text-align: center;
  color: var(--color-text-secondary);
  margin-top: 120px;
}
.welcome-icon { font-size: 44px; }
.welcome-sub { font-size: var(--font-size-label); }
.msg-row {
  display: flex;
  margin-bottom: var(--space-3);
}
.msg-user { justify-content: flex-end; }
.msg-ai { justify-content: flex-start; }
/* 上下文分隔行：切换学习资料后历史消息保留，用一条细线标出分界 */
.chat-divider {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin: var(--space-3) 0;
  color: var(--color-text-secondary);
  font-size: var(--font-size-caption);
}
.chat-divider::before,
.chat-divider::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--color-border);
}
.msg-bubble {
  max-width: 78%;
  padding: 10px var(--space-4);
  border-radius: var(--radius-lg);
  font-size: var(--font-size-body);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.msg-user .msg-bubble {
  background: var(--color-primary);
  color: var(--text-inverse);
}
.msg-ai .msg-bubble {
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border);
}
.msg-sources {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--color-border);
}
.sources-empty {
  font-size: var(--font-size-caption);
  color: var(--color-text-muted);
}
/* AI 回答 Markdown 渲染样式（v-html 注入内容，scoped 下需 :deep） */
.msg-md { white-space: normal; }
.msg-md :deep(p) { margin: 0 0 8px; }
.msg-md :deep(p:last-child) { margin-bottom: 0; }
.msg-md :deep(h1),
.msg-md :deep(h2),
.msg-md :deep(h3),
.msg-md :deep(h4) { margin: 12px 0 6px; font-weight: 600; line-height: 1.4; }
.msg-md :deep(h1) { font-size: 18px; }
.msg-md :deep(h2) { font-size: 16px; }
.msg-md :deep(h3) { font-size: 15px; }
.msg-md :deep(h4) { font-size: 14px; }
.msg-md :deep(ul),
.msg-md :deep(ol) { margin: 6px 0 8px; padding-left: 22px; }
.msg-md :deep(li) { margin: 2px 0; }
.msg-md :deep(code) { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.9em; background: #f0f1f5; padding: 1px 5px; border-radius: 4px; }
.msg-md :deep(pre) { margin: 8px 0; padding: 10px 12px; background: #f5f6fa; border: 1px solid #eceef3; border-radius: 8px; overflow-x: auto; }
.msg-md :deep(pre code) { background: none; padding: 0; white-space: pre; }
.msg-md :deep(blockquote) { margin: 8px 0; padding: 4px 10px; border-left: 3px solid var(--color-primary, #7c6cf0); color: #555; }
.msg-md :deep(a) { color: var(--color-primary, #5b8bf4); text-decoration: underline; }
.msg-md :deep(hr) { border: none; border-top: 1px solid #e5e5e5; margin: 10px 0; }
.msg-error {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--color-danger);
  font-size: var(--font-size-label);
}
.sources-title {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  margin-bottom: var(--space-2);
}
.source-card {
  padding: var(--space-2) 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  margin-bottom: 6px;
  background: var(--color-bg-soft);
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.source-card:hover {
  background: var(--color-bg-hover);
  border-color: var(--el-color-primary-light-8);
}
.source-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.source-name {
  flex: 1;
  min-width: 0;
  font-size: var(--font-size-label);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.source-jump {
  color: var(--color-text-muted);
  flex-shrink: 0;
}
.source-card:hover .source-jump { color: var(--color-primary); }
.source-desc {
  margin-top: 6px;
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  line-height: 1.5;
}
.typing {
  display: flex;
  gap: 5px;
  align-items: center;
  padding: var(--space-4) var(--space-4);
}
.typing-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-text-muted);
  animation: blink 1.2s infinite;
}
.typing-dot:nth-child(2) { animation-delay: 0.2s; }
.typing-dot:nth-child(3) { animation-delay: 0.4s; }
/* 流式生成中：正文下方的轻提示（首字到达后取代「正在输入」三点动画） */
.msg-streaming {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}
@keyframes blink {
  0%, 100% { opacity: 0.25; }
  50% { opacity: 1; }
}
.chat-input {
  display: flex;
  gap: 10px;
  margin-top: var(--space-3);
}
.qa-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--font-size-section);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
}
.qa-side-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: var(--font-weight-semibold);
  font-size: var(--font-size-body);
  color: var(--color-text-primary);
}
.qa-side :deep(.course-selector) {
  flex-wrap: wrap;
  width: 100%;
}
.qa-side :deep(.course-selector .el-select) {
  width: 100% !important;
}
.kp-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.kp-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) 10px;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.kp-item:hover {
  background: var(--color-bg-hover);
  border-color: var(--el-color-primary-light-8);
}
.kp-name {
  flex: 1;
  min-width: 0;
  font-size: var(--font-size-label);
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.kp-jump {
  color: var(--color-text-muted);
  flex-shrink: 0;
}
.kp-item:hover .kp-jump { color: var(--color-primary); }
.qa-side-course {
  font-size: var(--font-size-label);
  color: var(--color-text-primary);
  font-weight: var(--font-weight-semibold);
  padding: 2px 0;
}

/* v2 视觉增强 */
.qa-side-course {
  background: var(--brand-50);
  color: var(--brand-700);
  border-radius: var(--radius-md);
  padding: 8px 10px !important;
  font-size: 13px;
}
.chat-box {
  background:
    radial-gradient(40% 30% at 100% 0, rgba(91, 141, 239, .05), transparent 70%),
    #f7f9fe;
  border-radius: var(--radius-lg);
  border-color: var(--border-light);
}
.chat-welcome { margin-top: 110px; }
.welcome-icon {
  width: 72px;
  height: 72px;
  margin: 0 auto 14px;
  border-radius: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--gradient-brand);
  color: #fff !important;
  box-shadow: 0 12px 26px -10px rgba(79, 110, 247, .6);
}
.welcome-icon :deep(svg) { color: #fff; }
.msg-bubble {
  padding: 11px 15px;
  box-shadow: 0 2px 10px -4px rgba(22, 34, 66, .12);
  animation: kg-pop-in .25s ease both;
}
.msg-user .msg-bubble {
  background: var(--gradient-brand);
  border-bottom-right-radius: 4px;
}
.msg-ai .msg-bubble {
  background: #fff;
  border-color: var(--border-light);
  border-bottom-left-radius: 4px;
}
.source-card {
  background: #fff;
  border-radius: var(--radius-sm);
  transition: all .18s;
}
.source-card:hover {
  border-color: var(--brand-300);
  box-shadow: 0 4px 12px -6px rgba(79, 110, 247, .35);
  transform: translateX(2px);
}
.kp-item { border-radius: var(--radius-sm); }
.kp-item:hover { border-color: var(--brand-300); background: var(--brand-50); }
.kp-item:hover .kp-jump { color: var(--brand-500); }
.source-fav { flex-shrink: 0; padding: 2px; }
</style>
