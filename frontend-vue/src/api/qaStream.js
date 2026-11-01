/**
 * SSE 流式问答客户端（对齐后端 `POST /api/v1/qa/ask/stream`）。
 *
 * 为什么不用 axios：axios 的 XHR 适配器拿不到「边到边读」的增量数据（只能等整个响应体），
 * 必须用 `fetch` + `ReadableStream`。这里只做两件事：
 *   1. 拼请求（沿用 request.js 的 baseURL 口径 + JWT 头）；
 *   2. 把非流式响应（权限拒绝等 JSON、5xx）转成可读的 Error，交给调用方决定是否降级。
 * 帧解析在 `./sse.js`（纯函数，可脱离浏览器单测）。
 *
 * 后端事件序列：`sources` → `delta`（多次）→ `done`；失败时给 `error` 且**不发 done**。
 */

import { readSseEvents } from './sse'

const BASE = import.meta.env.VITE_API_BASE || ''

/** 非流式响应（权限拒绝 / 5xx / 代理降级）转成 Error */
async function toError(response) {
  let message = `问答服务异常（HTTP ${response.status}）`
  try {
    const body = await response.json()
    if (body && typeof body === 'object') {
      if (body.code != null && body.code !== 0) {
        message = body.message || message
        const err = new Error(message)
        err.code = body.code
        err.status = response.status
        return err
      }
      message = body.detail || body.message || message
    }
  } catch {
    // 响应体不是 JSON：保留默认文案
  }
  const err = new Error(message)
  err.status = response.status
  return err
}

/**
 * 发起流式问答，返回异步生成器（`for await (const ev of await askStream(...))`）。
 *
 * @param {object} opts
 * @param {string} opts.question   提问内容
 * @param {string|number} [opts.courseId]   课程作用域（可空：后端收敛为「我可访问的课程」）
 * @param {string|number} [opts.documentId] 文档作用域
 * @param {AbortSignal} [opts.signal]      取消信号（切换资料/卸载时中断，顺带让后端停止生成）
 * @throws {Error} 网络失败、权限拒绝、或响应不是 SSE（调用方可降级到 `api.ask`）
 */
export async function askStream({ question, courseId, documentId, signal }) {
  const token = localStorage.getItem('kg_token')
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`

  const resp = await fetch(`${BASE}/api/v1/qa/ask/stream`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      question,
      course_id: courseId || null,
      document_id: documentId || null,
    }),
    signal,
  })

  const contentType = resp.headers.get('content-type') || ''
  // 权限拒绝走统一包装 JSON（HTTP 200 + code!=0），不是流；交给调用方降级/提示
  if (!resp.ok || !contentType.includes('text/event-stream') || !resp.body) {
    throw await toError(resp)
  }
  return readSseEvents(resp)
}
