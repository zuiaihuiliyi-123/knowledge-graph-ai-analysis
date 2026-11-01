import { test, expect } from '@playwright/test'
import { parseSseFrame, readSseEvents } from '../src/api/sse'
import { BASE, login, dismissBackendStatus } from './helpers'

/**
 * 5-4 前端 SSE 接入验证（不需要 LLM：解析用本地 ReadableStream，界面用路由拦截喂帧）。
 *
 * 为什么这么测：
 * - 本机 SiliconFlow/DeepSeek 账号欠费，真正跑流式会立刻返回 error 事件；
 *   但「流的解析与渲染」与模型无关，用 `page.route` 伪造一份合法 SSE 响应即可完整覆盖；
 * - 解析器最容易被踩的两个边界是「中文被 chunk 切断」和「CRLF 换行」，
 *   这两点在真实网络上偶发、单靠手点是查不出来的，所以逐字节切开跑一遍。
 */

/** 用任意 chunk 序列造一个 `text/event-stream` Response */
function sseResponse(chunks) {
  const encoder = new TextEncoder()
  const stream = new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(typeof c === 'string' ? encoder.encode(c) : c)
      controller.close()
    },
  })
  return new Response(stream, { headers: { 'content-type': 'text/event-stream' } })
}

async function collect(response) {
  const events = []
  for await (const ev of readSseEvents(response)) events.push(ev)
  return events
}

test.describe('SSE 解析（纯函数，无浏览器）', () => {
  test('中文跨 chunk 被切断时仍能还原（逐个切分点遍历）', async () => {
    const frame = 'event: delta\ndata: {"text":"直接内存访问方式"}\n\n'
    const bytes = new TextEncoder().encode(frame)

    // 在每一个字节位置切开：任何一处切在 UTF-8 多字节字符中间都必须自愈
    for (let cut = 1; cut < bytes.length; cut += 1) {
      const events = await collect(sseResponse([bytes.slice(0, cut), bytes.slice(cut)]))
      expect(events, `在第 ${cut} 字节切开后应正好解析出 1 帧`).toHaveLength(1)
      expect(events[0].event).toBe('delta')
      expect(events[0].data.text).toBe('直接内存访问方式')
    }
  })

  test('兼容 CRLF 换行、注释心跳与多行 data', async () => {
    const body = [
      ': keep-alive\r\n\r\n',
      'event: sources\r\n',
      'data: {"sources":[{"name":"DMA方式"}]}\r\n\r\n',
      'event: delta\r\n',
      'data: {"text":"第一行"}\r\n',
      'data: {"text":"第二行"}\r\n\r\n',
    ]
    const events = await collect(sseResponse(body))
    expect(events.map((e) => e.event)).toEqual(['sources', 'delta'])
    expect(events[0].data.sources[0].name).toBe('DMA方式')
    // 多行 data 按规范用 \n 拼接，不是合法 JSON → 原样返回字符串
    expect(events[1].data).toBe('{"text":"第一行"}\n{"text":"第二行"}')
  })

  test('无 data 的帧被忽略；非 JSON 的 data 原样透传', async () => {
    expect(parseSseFrame('event: ping\n')).toBeNull()
    expect(parseSseFrame(': 只是注释')).toBeNull()
    expect(parseSseFrame('data: 纯文本')).toEqual({ event: 'message', data: '纯文本' })
    // 末尾没有空行的残帧也要能被处理（后端写完立即断开的情况）
    const events = await collect(sseResponse(['event: done\ndata: {"answer":"ok"}']))
    expect(events).toEqual([{ event: 'done', data: { answer: 'ok' } }])
  })
})


test.describe('问答界面流式渲染（路由拦截伪造 SSE）', () => {
  const COURSE = process.env.KGU_STUDENT_COURSE || '65'
  const DOC = process.env.KGU_STUDENT_DOC || '101'
  const STUDENT_USER = process.env.KGU_STUDENT_USER || 'demo_student'
  const STUDENT_PASS = process.env.KGU_STUDENT_PASSWORD || 'demo123456'

  test('sources 先到即渲染证据链，delta 增量拼接，done 收尾', async ({ page }) => {
    test.setTimeout(120000)
    const errors = []
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })

    const token = await login(page, STUDENT_USER, STUDENT_PASS)
    expect(token, '学生登录后应拿到 token').toBeTruthy()

    // 记录请求体：断言作用域与鉴权头确实带上了
    const seen = { body: null, auth: '' }
    await page.route('**/api/v1/qa/ask/stream', async (route) => {
      seen.body = route.request().postDataJSON()
      seen.auth = route.request().headers().authorization || ''
      const frames = [
        'event: sources\ndata: {"sources":[{"kp_id":"kp-1","name":"DMA方式","category":"概念","description":"直接内存访问"}]}\n\n',
        'event: delta\ndata: {"text":"DMA 是一种"}\n\n',
        'event: delta\ndata: {"text":"直接内存访问方式。"}\n\n',
        'event: done\ndata: {"answer":"DMA 是一种直接内存访问方式。","sources":[{"kp_id":"kp-1","name":"DMA方式","category":"概念","description":"直接内存访问"}]}\n\n',
      ].join('')
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'text/event-stream; charset=utf-8', 'cache-control': 'no-cache' },
        body: frames,
      })
    })

    await page.goto(`${BASE}/student?tab=qa&course_id=${COURSE}&document_id=${DOC}`)
    await dismissBackendStatus(page)

    // 页面先挂载、后由 initFromRoute() 异步落上下文（要先等课程列表）：
    // 不等这一步就提问，会以 course_id=null 变成「全课程范围检索」，断言必然飘。
    await page.waitForFunction(
      ([cid, did]) => {
        const raw = localStorage.getItem('kg_learning_context')
        if (!raw) return false
        const ctx = JSON.parse(raw)
        return String(ctx.currentCourseId) === cid && String(ctx.currentDocumentId) === did
      },
      [String(COURSE), String(DOC)],
      { timeout: 20000 },
    )

    const input = page.getByPlaceholder('输入你的问题，回车发送')
    await expect(input).toBeVisible({ timeout: 20000 })
    await input.fill('什么是 DMA 方式？')
    await input.press('Enter')

    // 最终答案（delta 拼接 + done 收尾）
    await expect(page.getByText('DMA 是一种直接内存访问方式。')).toBeVisible({ timeout: 20000 })
    // 证据链：来源卡片来自 sources 事件
    await expect(page.locator('.source-card').filter({ hasText: 'DMA方式' })).toHaveCount(1)
    // 生成中提示在 done 之后必须消失，输入框解锁
    await expect(page.locator('.msg-streaming')).toHaveCount(0)
    await expect(input).toBeEnabled()

    expect(seen.body, '前端必须真的调用 /qa/ask/stream').toBeTruthy()
    expect(String(seen.body.course_id)).toBe(String(COURSE))
    expect(String(seen.body.document_id)).toBe(String(DOC))
    expect(seen.body.question).toBe('什么是 DMA 方式？')
    expect(seen.auth.startsWith('Bearer ')).toBe(true)

    expect(errors, `控制台不应报错：${errors.join(' | ')}`).toEqual([])
  })

  test('后端返回 error 事件时展示失败态，不伪造答案', async ({ page }) => {
    test.setTimeout(120000)
    await login(page, STUDENT_USER, STUDENT_PASS)
    await page.route('**/api/v1/qa/ask/stream', (route) => route.fulfill({
      status: 200,
      headers: { 'content-type': 'text/event-stream; charset=utf-8' },
      body: 'event: sources\ndata: {"sources":[]}\n\nevent: error\ndata: {"message":"问答流中断：402 余额不足"}\n\n',
    }))

    await page.goto(`${BASE}/student?tab=qa&course_id=${COURSE}&document_id=${DOC}`)
    await dismissBackendStatus(page)
    // 与上一个用例同理：等上下文落好再提问（否则会以「全部可访问课程」范围提问）
    await page.waitForFunction(
      ([cid, did]) => {
        const raw = localStorage.getItem('kg_learning_context')
        if (!raw) return false
        const ctx = JSON.parse(raw)
        return String(ctx.currentCourseId) === cid && String(ctx.currentDocumentId) === did
      },
      [String(COURSE), String(DOC)],
      { timeout: 20000 },
    )
    const input = page.getByPlaceholder('输入你的问题，回车发送')
    await expect(input).toBeVisible({ timeout: 20000 })
    await input.fill('余额不足时会怎样？')
    await input.press('Enter')

    const errBox = page.locator('.msg-error')
    await expect(errBox).toHaveCount(1, { timeout: 20000 })
    await expect(errBox).toContainText('AI 服务暂时不可用')
    // 后端给的原因放在 title 里：方便排查，但不打扰用户
    await expect(errBox).toHaveAttribute('title', /402/)
    await expect(page.locator('.msg-streaming')).toHaveCount(0)
  })

  test('流式端点返回非 SSE（代理降级/权限拒绝）时回退同步接口，答案照常显示', async ({ page }) => {
    test.setTimeout(120000)
    await login(page, STUDENT_USER, STUDENT_PASS)

    // 模拟「代理不支持流式」：/ask/stream 回一份普通 JSON（后端权限拒绝就是这种形状）
    await page.route('**/api/v1/qa/ask/stream', (route) => route.fulfill({
      status: 200,
      headers: { 'content-type': 'application/json; charset=utf-8' },
      body: JSON.stringify({ code: 4003, message: '无权访问该课程', data: null }),
    }))
    let syncCalled = 0
    await page.route('**/api/v1/qa/ask', (route) => {
      syncCalled += 1
      return route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json; charset=utf-8' },
        body: JSON.stringify({
          code: 0,
          message: 'success',
          data: { question: '降级测试', answer: '同步接口兜底答案。', sources: [] },
        }),
      })
    })

    await page.goto(`${BASE}/student?tab=qa&course_id=${COURSE}&document_id=${DOC}`)
    await dismissBackendStatus(page)
    await page.waitForFunction(
      ([cid, did]) => {
        const raw = localStorage.getItem('kg_learning_context')
        if (!raw) return false
        const ctx = JSON.parse(raw)
        return String(ctx.currentCourseId) === cid && String(ctx.currentDocumentId) === did
      },
      [String(COURSE), String(DOC)],
      { timeout: 20000 },
    )

    const input = page.getByPlaceholder('输入你的问题，回车发送')
    await input.fill('降级测试')
    await input.press('Enter')

    await expect(page.getByText('同步接口兜底答案。')).toBeVisible({ timeout: 20000 })
    expect(syncCalled, '流式失败后应回退到同步 /qa/ask').toBeGreaterThan(0)
    await expect(page.locator('.msg-error')).toHaveCount(0)
  })
})
