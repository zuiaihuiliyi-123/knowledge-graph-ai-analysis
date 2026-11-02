/**
 * SSE（text/event-stream）帧解析。
 *
 * 单独成文件的原因：这里没有任何浏览器/环境依赖，可以直接在 Node（Playwright 测试）
 * 里对一个真实 `ReadableStream` 跑，验证「跨 chunk 切断中文」「CRLF」「注释心跳」这些
 * 只有真机上才会暴露的边界，而不必为了测解析去起一个 SSE 服务。
 */

/** 把一段「一帧」文本解析为 `{ event, data }`；无有效内容时返回 null */
export function parseSseFrame(frame) {
  const lines = String(frame).split('\n')
  let event = 'message'
  const dataLines = []
  for (const raw of lines) {
    const line = raw.replace(/\r$/, '')
    if (!line || line.startsWith(':')) continue // 空行 / 注释（心跳）跳过
    const sep = line.indexOf(':')
    const field = sep === -1 ? line : line.slice(0, sep)
    // SSE 规定字段值前的一个空格要去掉
    let value = sep === -1 ? '' : line.slice(sep + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') event = value || 'message'
    else if (field === 'data') dataLines.push(value)
  }
  if (!dataLines.length) return null
  const text = dataLines.join('\n')
  let data = text
  try {
    data = JSON.parse(text)
  } catch {
    // 非 JSON 的 data 原样返回，避免一帧异常吃掉整条流
  }
  return { event, data }
}

/**
 * 逐帧读取 fetch 响应体，产出 `{ event, data }`。
 * `TextDecoder({ stream: true })` 用于处理跨 chunk 的多字节字符（中文常被切断）。
 */
export async function* readSseEvents(response) {
  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  const drain = function* () {
    let idx = buffer.indexOf('\n\n')
    while (idx !== -1) {
      const frame = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      const parsed = parseSseFrame(frame)
      if (parsed) yield parsed
      idx = buffer.indexOf('\n\n')
    }
  }

  const normalize = (text) => text.replace(/\r\n/g, '\n')

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer = normalize(buffer + decoder.decode(value, { stream: true }))
    yield* drain()
  }
  buffer = normalize(buffer + decoder.decode())
  yield* drain()
  const tail = parseSseFrame(buffer)
  if (tail) yield tail
}
