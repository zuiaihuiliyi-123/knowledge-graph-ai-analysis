import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api } from '../api'
import { askStream } from '../api/qaStream'
import { isAbortError } from '../composables/useLatestRequest'

/**
 * AI 问答共享会话（多会话版）。
 *
 * 学生端「智能问答」Tab 与全局 AI 悬浮球读写的是同一份状态：同一段对话、同一个等待态、
 * 同一个「停止生成」。布局对齐主流 AI 产品 —— 左侧历史会话列表，右侧一条对话流。
 *
 * - 对话按课程持久化到 localStorage：一段对话 = 一条记录，刷新、切 Tab、换入口都不丢
 * - 提问时序（AbortController + 序号作废）集中在这里，两个入口共用一套语义
 * - 回答走 SSE 流式（api/qaStream.js）逐字渲染；不支持流式时回落到同步 api.ask
 */

// v2：每个课程存 { activeId, sessions: [{ id, title, updatedAt, messages }] }
const CHAT_KEY = 'kg_ai_chat_v2'
// 旧版：v1 悬浮窗 / 问答页各存一份「每课程一个消息数组」，首次加载时迁移过来
const LEGACY_KEYS = ['kg_ai_chat_v1', 'kg_qa_chat_v1']
const MAX_SESSIONS = 30
const MAX_MESSAGES = 80
const MAX_SOURCE_PER_MSG = 10
const MAX_CONTENT_LEN = 20000
const TITLE_LEN = 24

export const useAiChatStore = defineStore('aiChat', () => {
  // 当前课程的历史会话（按最近更新排在前面，与主流 AI 的历史列表一致）
  const sessions = ref([])
  const activeId = ref('')
  // 是否正在等待回答（两个入口共用，任一入口提问时另一入口也会显示「正在回答」）
  const asking = ref(false)
  const courseId = ref('')
  const documentId = ref('')
  // 当前学习资料名：写进消息标记，用于「切换资料后历史消息属于哪份资料」的分隔行
  const documentName = ref('')

  let abort = null
  // 上下文切换 / 切会话 / 新提问时自增：迟到的旧回调发现序号变了就丢弃
  let seq = 0
  let midSeq = 0

  const activeSession = computed(() => sessions.value.find((s) => s.id === activeId.value) || null)
  const messages = computed(() => (activeSession.value ? activeSession.value.messages : []))
  const sessionList = computed(() =>
    sessions.value.slice().sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))
  )

  function newId() {
    return `s${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`
  }
  function createSession() {
    return { id: newId(), title: '', updatedAt: Date.now(), messages: [] }
  }
  function storageKey(cid) {
    const id = cid === undefined ? courseId.value : cid
    return `chat_${id || 'none'}`
  }
  function readAll(key) {
    try { return JSON.parse(localStorage.getItem(key) || '{}') } catch { return {} }
  }
  function writeAll(key, all) {
    try { localStorage.setItem(key, JSON.stringify(all)) } catch { /* 隐私模式等：仅内存保留 */ }
  }

  function normalizeMessage(m) {
    return {
      mid: ++midSeq,
      role: m.role === 'user' ? 'user' : 'ai',
      content: typeof m.content === 'string' ? m.content : '',
      sources: Array.isArray(m.sources) ? m.sources.slice(0, MAX_SOURCE_PER_MSG) : [],
      error: !!m.error,
      errDetail: typeof m.errDetail === 'string' ? m.errDetail : '',
      // 存盘的是结果：重新载入时不可能还在流式生成，不保留中间态
      streaming: false,
      ctxKey: typeof m.ctxKey === 'string' ? m.ctxKey : '',
      ctxLabel: typeof m.ctxLabel === 'string' ? m.ctxLabel : '',
      time: Number(m.time) || 0,
    }
  }
  function normalizeMessages(list) {
    return (Array.isArray(list) ? list : [])
      // 旧数据里首条可能是悬浮窗写进去的问候语，那不是用户对话，丢弃
      .filter((m) => m && !m.greeting)
      .map(normalizeMessage)
  }
  function normalizeSession(raw) {
    return {
      id: String(raw.id || newId()),
      title: typeof raw.title === 'string' ? raw.title : '',
      updatedAt: Number(raw.updatedAt) || 0,
      messages: normalizeMessages(raw.messages),
    }
  }
  function titleFromMessages(msgs) {
    const first = msgs.find((m) => m.role === 'user' && m.content)
    return first ? truncateTitle(first.content) : ''
  }
  function truncateTitle(text) {
    const t = (text || '').replace(/\s+/g, ' ').trim()
    return t.length > TITLE_LEN ? `${t.slice(0, TITLE_LEN)}…` : t
  }

  /** 读当前课程的会话；没有历史时给一个空会话，界面直接可用（首次提问才落盘） */
  function loadSessions(cid) {
    const key = storageKey(cid)
    const all = readAll(CHAT_KEY)
    const payload = all[key]
    let list = []
    let nextActive = ''

    if (payload && Array.isArray(payload.sessions)) {
      list = payload.sessions.map(normalizeSession)
      nextActive = list.some((s) => s.id === payload.activeId) ? payload.activeId : ''
    } else {
      // 迁移旧版：每课程一个消息数组（可能来自 kg_ai_chat_v1 / kg_qa_chat_v1）
      let legacy = Array.isArray(payload) ? payload : null
      if (!legacy || !legacy.length) {
        for (const k of LEGACY_KEYS) {
          const v = readAll(k)[key]
          if (Array.isArray(v) && v.length) { legacy = v; break }
        }
      }
      if (legacy && legacy.length) {
        const msgs = normalizeMessages(legacy)
        list = [{
          id: newId(),
          title: titleFromMessages(msgs),
          updatedAt: msgs.length ? (msgs[msgs.length - 1].time || Date.now()) : Date.now(),
          messages: msgs,
        }]
        nextActive = list[0].id
      }
    }

    if (!list.length) list = [createSession()]
    sessions.value = list
    activeId.value = nextActive || list[0].id
    persist()
  }

  function persist() {
    if (!courseId.value && !sessions.value.length) return
    const key = storageKey()
    const all = readAll(CHAT_KEY)
    all[key] = {
      activeId: activeId.value,
      sessions: sessions.value.slice(0, MAX_SESSIONS).map((s) => ({
        id: s.id,
        title: s.title,
        updatedAt: s.updatedAt,
        messages: (s.messages || []).slice(-MAX_MESSAGES).map((m) => ({
          role: m.role,
          content: (m.content || '').slice(0, MAX_CONTENT_LEN),
          sources: (m.sources || []).slice(0, MAX_SOURCE_PER_MSG),
          error: !!m.error,
          errDetail: m.errDetail || '',
          ctxKey: m.ctxKey || '',
          ctxLabel: m.ctxLabel || '',
          time: m.time || 0,
        })),
      })),
    }
    writeAll(CHAT_KEY, all)
  }

  /** 作废在途回答并解除等待态（切课程 / 换学习资料 / 切会话时调用） */
  function abortInFlight() {
    seq++
    if (abort) { abort.abort(); abort = null }
    asking.value = false
    // 正在生成的那条占位消息要收尾：保留半成品，一个字都没有就留一句明确的中断说明，
    // 否则切回来会看到一条永远「正在生成…」的空气泡
    for (const s of sessions.value) {
      for (const m of s.messages) {
        if (!m.streaming) continue
        m.streaming = false
        if (!m.content) m.content = '（已中断）'
      }
    }
  }

  /** 把会话顶到列表最前（主流 AI 的历史列表按最近使用排序） */
  function touch(session) {
    session.updatedAt = Date.now()
    const i = sessions.value.findIndex((s) => s.id === session.id)
    if (i > 0) {
      sessions.value.splice(i, 1)
      sessions.value.unshift(session)
    }
  }

  /** 取当前会话；没有就补一个空的 */
  function ensureSession() {
    const found = sessions.value.find((s) => s.id === activeId.value)
    if (found) return found
    if (!sessions.value.length) sessions.value = [createSession()]
    activeId.value = sessions.value[0].id
    return sessions.value[0]
  }

  /**
   * 绑定当前学习上下文。课程变化时载入该课程的历史会话；仅文档变化时保留历史，
   * 只作废在途回答（旧文档的答案不应落进新文档的对话）。
   */
  function bindContext(cid, did = '', dname = '') {
    const nextCourse = cid ? String(cid) : ''
    const nextDoc = did ? String(did) : ''
    const nextName = dname ? String(dname) : ''
    const docChanged = nextDoc !== documentId.value
    // 资料名只有问答页知道；悬浮球绑定上下文时不带名字，文档没变就别把已有名字抹掉
    if (nextName || docChanged || nextCourse !== courseId.value) documentName.value = nextName
    if (nextCourse === courseId.value) {
      if (docChanged) {
        documentId.value = nextDoc
        abortInFlight()
      }
      return
    }
    abortInFlight()
    courseId.value = nextCourse
    documentId.value = nextDoc
    loadSessions(nextCourse)
  }

  /** 当前上下文标识（跨资料的历史消息靠它分辨归属）；无学习资料时为空串 */
  function ctxKeyOf() {
    return courseId.value && documentId.value ? `${courseId.value}:${documentId.value}` : ''
  }
  function ctxLabelOf() {
    return documentName.value || (documentId.value ? `文档 #${documentId.value}` : '未选择学习资料')
  }

  /** 新建对话：当前已经是空对话就不重复新建，避免点一下多出一个「新对话」 */
  function newSession() {
    const cur = sessions.value.find((s) => s.id === activeId.value)
    if (cur && !cur.messages.length) return cur.id
    abortInFlight()
    const s = createSession()
    sessions.value.unshift(s)
    activeId.value = s.id
    persist()
    return s.id
  }

  function selectSession(id) {
    if (!id || id === activeId.value) return
    if (!sessions.value.some((s) => s.id === id)) return
    // 切走时作废在途回答，否则答案会落到旧会话、而新会话一直转圈
    abortInFlight()
    activeId.value = id
    persist()
  }

  function removeSession(id) {
    const i = sessions.value.findIndex((s) => s.id === id)
    if (i < 0) return
    const wasActive = sessions.value[i].id === activeId.value
    sessions.value.splice(i, 1)
    if (wasActive) { abortInFlight(); activeId.value = '' }
    if (!sessions.value.length) sessions.value = [createSession()]
    if (!activeId.value) activeId.value = sessions.value[0].id
    persist()
  }

  /**
   * 发送提问。两个入口共用：谁先发起都写进同一段对话。
   *
   * 流程对齐后端 SSE 契约：先占位一条空的 AI 消息，`sources` 到了先渲染证据链，
   * `delta` 边到边追加（逐字输出），`done` 收尾。非流式响应（权限拒绝 / 代理不支持 SSE）
   * 或网络异常时回落到同步 api.ask。
   */
  async function send(question) {
    const q = (question || '').trim()
    if (!q || asking.value) return
    const session = ensureSession()
    const mySeq = ++seq
    const ctx = { ctxKey: ctxKeyOf(), ctxLabel: ctxLabelOf() }

    session.messages.push({
      mid: ++midSeq, role: 'user', content: q, sources: [], error: false, errDetail: '',
      streaming: false, ...ctx, time: Date.now(),
    })
    // 会话标题取首个提问，和主流 AI 的历史列表一致
    if (!session.title) session.title = truncateTitle(q)
    const bubble = {
      mid: ++midSeq, role: 'ai', content: '', sources: [], error: false, errDetail: '',
      streaming: true, ...ctx, time: Date.now(),
    }
    session.messages.push(bubble)
    touch(session)
    asking.value = true
    persist()

    const controller = new AbortController()
    abort = controller
    const scope = {
      courseId: courseId.value || null,
      documentId: documentId.value || null,
      signal: controller.signal,
    }
    let received = ''
    try {
      const events = await askStream({ question: q, ...scope })
      for await (const ev of events) {
        if (mySeq !== seq) return // 上下文/会话已切换或新提问已接管，本次回答作废
        if (ev.event === 'sources') {
          bubble.sources = (ev.data?.sources || []).map(normalizeSource).slice(0, MAX_SOURCE_PER_MSG)
        } else if (ev.event === 'delta') {
          received += ev.data?.text || ''
          bubble.content = received
        } else if (ev.event === 'done') {
          bubble.content = ev.data?.answer || received || '（无回答）'
          if (ev.data?.sources) {
            bubble.sources = ev.data.sources.map(normalizeSource).slice(0, MAX_SOURCE_PER_MSG)
          }
          // 区分「检索成功但 LLM 生成失败」（后端固定降级文案）与正常回答
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
      // 流正常结束但没等到 done（后端被中断等）：已收到的内容照样给用户
      bubble.content = received || '（无回答）'
      bubble.streaming = false
    } catch (e) {
      if (mySeq !== seq) return
      if (isAbortError(e)) {
        // 用户点「停止生成」：一个字都没收到就给一句明确提示，否则保留半成品
        bubble.streaming = false
        if (!bubble.content) bubble.content = '（已停止生成）'
        return
      }
      // 有增量就保留（只是最后一段没收干净）；一个 delta 都没有才回落同步接口
      if (received) {
        bubble.streaming = false
        return
      }
      try {
        const res = await api.ask(q, scope.courseId, scope.documentId, { signal: controller.signal })
        if (mySeq !== seq) return
        bubble.content = res.answer || '（无回答）'
        bubble.sources = (res.sources || []).map(normalizeSource).slice(0, MAX_SOURCE_PER_MSG)
        bubble.error = isLlmError(res.answer)
        bubble.streaming = false
      } catch (e2) {
        if (mySeq !== seq) return
        bubble.streaming = false
        if (isAbortError(e2)) {
          if (!bubble.content) bubble.content = '（已停止生成）'
          return
        }
        bubble.error = true
        bubble.errDetail = e2?.message || ''
      }
    } finally {
      if (mySeq === seq) { asking.value = false; abort = null }
      touch(session)
      persist()
    }
  }

  /** 停止当前生成：只中断请求，不改变对话历史 */
  function stop() {
    if (abort) abort.abort()
  }

  /** 清空当前会话的消息（会话本身保留，界面上就是重新开一个话题） */
  function clear() {
    abortInFlight()
    const s = ensureSession()
    s.messages = []
    s.title = ''
    s.updatedAt = Date.now()
    persist()
  }

  // LLM 生成失败判定：后端生成异常时返回固定前缀的降级文案（前端识别，不改后端契约）
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

  return {
    sessions,
    activeId,
    messages,
    asking,
    courseId,
    documentId,
    documentName,
    activeSession,
    sessionList,
    bindContext,
    newSession,
    selectSession,
    removeSession,
    send,
    stop,
    clear,
  }
})
