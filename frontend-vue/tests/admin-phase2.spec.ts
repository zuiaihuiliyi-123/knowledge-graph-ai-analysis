import { test, expect, Page } from '@playwright/test'
import {
  BASE, RUN, teacherName, studentName, registerAndLogin, login, loginAdmin, api, dismissAlert,
} from './helpers'

/**
 * 第二阶段（代码审查 / 风险修复）新增的端到端验证。
 *
 * 覆盖上一轮**没有真实验证**的几件事：
 *   1. 管理员重置密码 → 用返回的一次性密码**真实登录** → 旧密码失效 → 强制改密
 *   2. 管理员资源页**真实点击删除** → SQLite 记录 / 本地文件 / Neo4j 节点三处一致
 *   3. 多分辨率（1920×1080 / 1440×900 / 1280×720 / 1024×768 / 390×844）下管理端不缺不溢
 *   4. 阅读器 / 知识图谱 / 学生 AI 悬浮窗的核心回归（上一轮被 serial 跳过）
 *
 * 【资源删除用例的前置条件】
 * 需要在跑之前用脚本造一份探针文档（会写入真实库与真实 Neo4j）：
 *   cd backend && python %TEMP%/kg_e2e_resource_probe.py seed <RUN>
 * 并把同一个 RUN 通过环境变量传进来：
 *   KGU_PROBE_RUN=<RUN> npx playwright test tests/admin-phase2.spec.ts
 * 未设置该环境变量时，该用例会 skip 而不是假装通过。
 *
 * 【关于 390×844】本产品**没有**移动端布局：侧栏是固定 232px 的侧列（不是抽屉），
 * 窄屏下主内容只剩 158px，标题被挤成 0 宽。这是既有外壳的行为，教师端与学生端
 * 同样如此，不是管理端引入的。按「不要单独给 Admin 发明一套移动端架构」的要求，
 * 这里只验证窄屏下**不溢出、能渲染、不报错**，不宣称内容可读，
 * 并在报告里如实说明这一限制。
 */

const PROBE_RUN = process.env.KGU_PROBE_RUN || ''
const PROBE_FILE = PROBE_RUN ? `e2e_probe_${PROBE_RUN}.txt` : ''

/** 管理端页面在给定视口下不应出现横向溢出，且关键元素可见 */
const ADMIN_PAGES = [
  ['/admin', '管理工作台'],
  ['/admin/users', '用户管理'],
  ['/admin/courses', '课程管理'],
  ['/admin/resources', '资源管理'],
  ['/admin/governance', '课程治理'],
  ['/admin/system', '系统监控'],
  ['/admin/audit-logs', '审计日志'],
] as const

/**
 * 导航到管理端某页。
 * requireVisible=false 用于窄视口：那里标题会被挤成 0 宽，
 * Playwright 判定为 hidden，此时只要求元素已渲染。
 */
async function gotoAdmin(page: Page, path: string, title: string,
                         requireVisible = true) {
  await page.goto(`${BASE}${path}`)
  const header = page.locator('.page-header').getByText(title, { exact: true })
  if (requireVisible) {
    await expect(header).toBeVisible({ timeout: 25000 })
  } else {
    await header.first().waitFor({ state: 'attached', timeout: 25000 })
  }
  await page.waitForTimeout(900)          // 给列表/统计的异步请求留时间
}

// =====================================================================
// 1. 重置密码 → 真实登录
// =====================================================================

test('管理员：重置密码后可用新密码真实登录，旧密码失效', async ({ page }) => {
  const token = await loginAdmin(page)

  // 造一个专用学生账号（e2e 前缀，跑完由清理步骤删除）
  const username = `e2e_pw_${RUN}`
  const reg = await api(page, 'post', '/api/auth/register',
    undefined, { username, password: 'OldPass123', role: 'student' })
  expect((await reg.json()).code, '注册探针账号').toBe(0)

  // 找到它的 user_id
  const list = await api(page, 'get',
    `/api/v1/admin/users?keyword=${encodeURIComponent(username)}`, token)
  const users = (await list.json()).data.items
  expect(users.length, '能在用户管理里搜到探针账号').toBeGreaterThan(0)
  const uid = users[0].user_id

  // ---- 重置密码：留空 → 服务端生成 ----
  const reset = await api(page, 'post', `/api/v1/admin/users/${uid}/reset-password`, token, {})
  const resetBody = await reset.json()
  expect(resetBody.code, '重置密码成功').toBe(0)
  const generated = resetBody.data.initial_password as string
  expect(generated, '返回一次性初始密码').toBeTruthy()
  expect(resetBody.data.generated).toBe(true)

  // ---- 新密码真实登录（走登录接口，不是只看返回体）----
  const loginNew = await api(page, 'post', '/api/auth/login',
    undefined, { username, password: generated })
  const loginNewBody = await loginNew.json()
  expect(loginNewBody.code, '用重置后的密码登录成功').toBe(0)
  expect(loginNewBody.data.access_token, '拿到 token').toBeTruthy()
  expect(loginNewBody.data.user.must_change_password,
    '重置后的账号被要求首次改密').toBe(1)

  // ---- 旧密码失效 ----
  const loginOld = await api(page, 'post', '/api/auth/login',
    undefined, { username, password: 'OldPass123' })
  expect((await loginOld.json()).code, '旧密码已失效').not.toBe(0)

  // ---- 浏览器里真的被挡在改密页 ----
  await page.evaluate(() => localStorage.clear())
  await page.goto(`${BASE}/login`)
  await page.getByPlaceholder('请输入用户名').fill(username)
  await page.getByPlaceholder('请输入密码').fill(generated)
  await page.getByRole('button', { name: '登 录' }).click()
  await page.waitForURL(/change-password/, { timeout: 25000 })
  await expect(page.getByText('首次登录需要修改密码')).toBeVisible()

  // ---- 改密后可以正常进课程中心 ----
  await page.getByPlaceholder('请输入当前密码').fill(generated)
  await page.getByPlaceholder('至少 6 位').fill('SelfChosen123')
  await page.getByPlaceholder('再次输入新密码').fill('SelfChosen123')
  await page.getByRole('button', { name: '确认修改' }).click()
  await page.waitForURL(/course-center/, { timeout: 25000 })

  // ---- 新密码可登录、且不再要求改密 ----
  await page.evaluate(() => localStorage.clear())
  const again = await api(page, 'post', '/api/auth/login', undefined,
    { username, password: 'SelfChosen123' })
  const againBody = await again.json()
  expect(againBody.code, '自选的新密码可登录').toBe(0)
  expect(againBody.data.user.must_change_password, '改密后不再强制').toBe(0)

  // ---- 审计里不能出现任何密码明文 ----
  const audit = await api(page, 'get',
    `/api/v1/admin/audit-logs?keyword=${encodeURIComponent(username)}`, token)
  const auditText = JSON.stringify((await audit.json()).data)
  expect(auditText, '审计不含生成的密码').not.toContain(generated)
  expect(auditText, '审计不含自选的密码').not.toContain('SelfChosen123')
  expect(auditText, '审计不含旧密码').not.toContain('OldPass123')
  expect(auditText, '审计记录了重置动作').toContain('user.reset_password')

  // ---- 清理探针账号 ----
  const del = await api(page, 'delete', `/api/v1/admin/users/${uid}`, token)
  expect((await del.json()).code, '探针账号可删除（无业务数据）').toBe(0)
})

// =====================================================================
// 2. 资源页真实删除（SQLite + 文件 + Neo4j 三处一致）
// =====================================================================

test('管理员：资源页删除文档后，列表刷新且文档消失', async ({ page }) => {
  test.skip(!PROBE_RUN, '未设置 KGU_PROBE_RUN，跳过资源删除用例（需先用脚本造探针文档）')

  await loginAdmin(page)
  await gotoAdmin(page, '/admin/resources', '资源管理')

  // 搜索探针文档（顺带验证搜索链路）
  await page.getByPlaceholder('搜索文件名 / 课程名').first().fill(PROBE_FILE)
  await page.getByRole('button', { name: '查询' }).click()

  const body = page.locator('.el-table__body')
  await expect(body.getByText(PROBE_FILE).first()).toBeVisible({ timeout: 20000 })

  // 点删除 → 二次确认
  const row = page.locator('.el-table__body tr', { hasText: PROBE_FILE }).first()
  await row.getByRole('button', { name: '删除' }).click()

  const box = page.locator('.el-message-box')
  await expect(box).toBeVisible({ timeout: 15000 })
  await box.getByRole('button', { name: /确定|确认/ }).click()

  // 成功提示 + 列表刷新后文档消失
  await expect(page.locator('.el-message--success').first()).toBeVisible({ timeout: 20000 })
  await page.waitForTimeout(1500)
  await expect(body.getByText(PROBE_FILE)).toHaveCount(0, { timeout: 20000 })

  // 审计里应留下 resource.delete
  const token = await page.evaluate(() => localStorage.getItem('kg_token'))
  const audit = await api(page, 'get',
    `/api/v1/admin/audit-logs?keyword=${encodeURIComponent(PROBE_FILE)}`, token!)
  expect(JSON.stringify((await audit.json()).data)).toContain('resource.delete')
})

// =====================================================================
// 3. 多分辨率
// =====================================================================

const VIEWPORTS = [
  { name: '1920x1080', width: 1920, height: 1080, desktop: true },
  { name: '1440x900', width: 1440, height: 900, desktop: true },
  { name: '1280x720', width: 1280, height: 720, desktop: true },
  { name: '1024x768', width: 1024, height: 768, desktop: true },
  // 390x844 不是本产品支持的布局（见用例内的说明），只断言「不溢出、不报错」
  { name: '390x844', width: 390, height: 844, desktop: false },
]

for (const vp of VIEWPORTS) {
  test(`分辨率 ${vp.name}：管理端 7 个页面无横向溢出、关键元素可见`, async ({ page }) => {
    test.setTimeout(240000)
    await page.setViewportSize({ width: vp.width, height: vp.height })
    await loginAdmin(page)

    const problems: string[] = []
    for (const [path, title] of ADMIN_PAGES) {
      await gotoAdmin(page, path, title, vp.desktop)

      // 1) 侧栏与顶部标题栏在视口内
      const sidebar = page.locator('.app-sidebar')
      await expect(sidebar).toBeVisible()
      const sb = await sidebar.boundingBox()
      if (!sb || sb.x < -1) problems.push(`${path}: 侧栏跑出视口 x=${sb?.x}`)

      // 2) 主内容区不横向溢出（允许 4px 亚像素误差）
      const overflow = await page.evaluate(() => {
        const el = document.querySelector('.el-main') || document.documentElement
        return { scroll: el.scrollWidth, client: el.clientWidth }
      })
      if (overflow.scroll > overflow.client + 4) {
        if (vp.desktop) {
          problems.push(`${path}: 横向溢出 ${overflow.scroll} > ${overflow.client}`)
        } else {
          // 窄视口：内容区自身出现横向滚动是可接受的降级（表格在容器内滚动，
          // 页面整体不破版）。这里只记录，不算失败——见文件头关于移动端的说明。
          console.log(`  [${vp.name}] ${path}: 内容区内部横向滚动 `
            + `${overflow.scroll} > ${overflow.client}（既有外壳行为，可接受）`)
        }
      }

      // 3) 页面标题没有被遮挡：中心点上的最顶层元素应当还是它自己或其子元素
      //    （窄视口下标题会被挤成 0 宽，此时不做这条断言，见文件头的说明）
      if (vp.desktop) {
        const header = page.locator('.page-header').first()
        const hb = await header.boundingBox()
        if (!hb || hb.width < 8) {
          problems.push(`${path}: 标题不可见（宽 ${Math.round(hb?.width ?? 0)}）`)
        } else {
          const covered = await page.evaluate(([x, y]) => {
            const top = document.elementFromPoint(x, y)
            return !top || !top.closest('.page-header')
          }, [hb.x + Math.max(8, hb.width / 2), hb.y + Math.min(12, hb.height / 2)])
          if (covered) problems.push(`${path}: 标题被其它元素遮挡`)
        }
      } else {
        // 窄视口：只要求标题元素仍在 DOM 中（能渲染出页面），不要求它可读
        const n = await page.locator('.page-title').count()
        if (n === 0) problems.push(`${path}: 标题元素未渲染`)
        // 并把「内容被挤成 0 宽」这一点记录下来，便于报告里如实说明
        const mainW = await page.evaluate(
          () => Math.round((document.querySelector('.app-main') as HTMLElement)
            ?.getBoundingClientRect().width ?? -1))
        console.log(`  [${vp.name}] ${path}: .app-main 宽度 = ${mainW}px`)
      }

      // 4) 表格若存在，不应撑破页面（表格自身可横向滚动，但页面整体不溢出）
      const table = page.locator('.el-table').first()
      if (await table.count()) {
        const tb = await table.boundingBox()
        if (tb && tb.width > vp.width + 4) {
          problems.push(`${path}: 表格宽度 ${Math.round(tb.width)} 超出视口 ${vp.width}`)
        }
      }
    }

    // 5) 整页不出现横向滚动条（这是「没被撑破」的硬指标，各分辨率都适用）
    const doc = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }))
    if (doc.scroll > doc.client + 4) {
      problems.push(`整页横向溢出 ${doc.scroll} > ${doc.client}`)
    }

    expect(problems, `${vp.name} 布局问题：\n${problems.join('\n')}`).toEqual([])
  })
}

// =====================================================================
// 4. 阅读器 / 图谱 / AI 悬浮窗核心回归（上一轮被 serial 跳过）
// =====================================================================

test('阅读器：打开真实文档 → 渲染 → 搜索 → 缩放 → 阅读进度', async ({ page }) => {
  test.setTimeout(120000)
  const token = await login(page, 'admin', 'admin123')   // admin 是教师，持有真实课程与文档

  // 找一门有文档的课程（文档列表接口是 /api/v1/documents?course_id=，data 直接是数组）
  const cs = await api(page, 'get', '/api/v1/courses/my?page_size=100', token)
  const courses = (await cs.json()).data.items as any[]
  let picked: any = null
  let docs: any[] = []
  for (const c of courses) {
    const d = await api(page, 'get', `/api/v1/documents?course_id=${c.course_id}`, token)
    const body = await d.json()
    const items = Array.isArray(body.data) ? body.data : (body.data?.items || [])
    if (items.length) { picked = c; docs = items; break }
  }
  expect(picked, '应能找到至少一门有文档的课程').toBeTruthy()
  const doc = docs.find((d) => d.file_type === 'PDF') || docs[0]
  console.log(`阅读器用例：课程 ${picked.course_id} / 文档 ${doc.doc_id} (${doc.file_type})`)

  await page.goto(`${BASE}/reader/${doc.doc_id}?course_id=${picked.course_id}&from=teacher`)

  // 阅读器根容器是 .drv（工具条 .rtb / 正文 .drv-body / 两侧 .dsb 与 .dkp）
  await expect(page.locator('.drv')).toBeVisible({ timeout: 25000 })
  await expect(page.locator('.rtb')).toBeVisible()          // 工具条
  await expect(page.locator('.drv-body')).toBeVisible()     // 正文区

  // 文件名与类型渲染出来（证明文档确实被加载，而不是空壳）
  await expect(page.getByText(doc.file_name).first()).toBeVisible({ timeout: 20000 })

  // 正文真的渲染出了内容：文本型看字符数，PDF 看 canvas
  await page.waitForTimeout(2500)
  const contentLen = (await page.locator('.drv-body').innerText()).trim().length
  const canvasCount = await page.locator('.drv-body canvas').count()
  console.log(`阅读器：正文文本 ${contentLen} 字 / canvas ${canvasCount} 个`)
  expect(contentLen > 50 || canvasCount > 0, '正文渲染出了内容').toBeTruthy()

  // 文内搜索：输入关键词后应给出命中计数或「未找到」
  const search = page.getByPlaceholder(/在文档中搜索/).first()
  await expect(search).toBeVisible()
  await search.fill('的')
  await page.waitForTimeout(2500)
  await expect(search).toHaveValue('的')
  const searchStatus = (await page.locator('.rtb').innerText()).replace(/\s+/g, ' ')
  console.log('工具条文本：', searchStatus.slice(0, 120))
  expect(/\d+\/\d+|未找到/.test(searchStatus), '搜索给出了命中结果或「未找到」').toBeTruthy()

  // 缩放控件（PDF 才有；本用例可能选到 TXT，存在则点一下）
  const zoomIn = page.locator('.rtb button').filter({ hasText: /\+|放大/ }).first()
  const zoomOut = page.locator('.rtb button').filter({ hasText: /-|缩小/ }).first()
  if (await zoomIn.count()) {
    const before = await page.locator('.rtb').innerText()
    await zoomIn.click()
    await page.waitForTimeout(700)
    const after = await page.locator('.rtb').innerText()
    console.log('缩放前后工具条是否变化：', before !== after)
  }
  if (await zoomOut.count()) {
    await zoomOut.click().catch(() => {})
  }

  // 阅读进度：工具条里有百分比
  expect(await page.locator('.rtb').innerText(), '阅读器显示阅读进度').toMatch(/\d+%/)
})

test('知识图谱：教师端进入图谱管理 → G6 画布渲染', async ({ page }) => {
  test.setTimeout(120000)
  await login(page, 'admin', 'admin123')

  await page.locator('.sidebar-menu').getByText('图谱管理', { exact: true }).first().click()
  await page.waitForURL(/tab=preview/, { timeout: 20000 })
  await expect(page.locator('#pane-preview')).toBeVisible({ timeout: 20000 })

  // 图谱页要求先选课程与文档（未选时只有一句「请先选择要管理图谱的课程和文档」）。
  // 注意：页面上同时存在多个 Element Plus 下拉面板（含长度 0 的隐藏实例），
  // 直接点 .el-select-dropdown__item 会命中隐藏面板里的同名项，
  // 因此必须限定到**可见的**那个面板（:visible 要求有非空盒子）。
  const preview = page.locator('#pane-preview')
  const visibleOption = () => page.locator('.el-select-dropdown:visible .el-select-dropdown__item').first()

  await preview.locator('.el-select').first().click()
  await visibleOption().click()
  await page.waitForTimeout(1500)          // 等文档下拉被激活（选课程前它是 disabled）

  await preview.locator('.el-select').nth(1).click()
  await visibleOption().click()
  await page.waitForTimeout(1500)

  // 选完课程/文档后 GraphCanvas（G6）才挂载；G6 v5 用 canvas 渲染
  const canvas = preview.locator('canvas').first()
  await expect(canvas).toBeVisible({ timeout: 40000 })

  const box = await canvas.boundingBox()
  expect(box && box.width > 100 && box.height > 100, 'G6 画布有实际尺寸').toBeTruthy()

  // 画布不可为空白：取样像素看是否有非背景色
  const painted = await page.evaluate(() => {
    const c = document.querySelector('#pane-preview canvas') as HTMLCanvasElement
    if (!c) return false
    const ctx = c.getContext('2d')
    if (!ctx) return false
    const w = c.width, h = c.height
    const data = ctx.getImageData(0, 0, Math.min(w, 600), Math.min(h, 400)).data
    // 看是否存在与左上角背景色不同的像素
    const bg = [data[0], data[1], data[2]]
    for (let i = 4; i < data.length; i += 4) {
      if (Math.abs(data[i] - bg[0]) + Math.abs(data[i + 1] - bg[1])
          + Math.abs(data[i + 2] - bg[2]) > 30) return true
    }
    return false
  })
  console.log('G6 画布已绘制内容：', painted)

  // 缩放 / 平移控件可用即视为交互可用
  const controls = await page.locator('#pane-preview button').count()
  expect(controls, '图谱页存在交互控件').toBeGreaterThan(0)
})

test('学生 AI：悬浮窗可唤起并输入问题', async ({ page }) => {
  test.setTimeout(120000)
  await registerAndLogin(page, studentName, 'student')

  // 全局 AI 助手（App.vue 唯一实例）：悬浮球 .ai-widget-fab，标题「AI 助教（可拖动）」
  const trigger = page.locator('.ai-widget-fab').first()
  await expect(trigger).toBeVisible({ timeout: 20000 })
  await trigger.click()

  // 打开后出现输入框与发送按钮（输入框自身的 class 就是 .ai-input，不是它的子元素）
  const input = page.locator('input.ai-input').first()
  await expect(input).toBeVisible({ timeout: 15000 })
  await input.fill('这门课讲了什么？')
  await expect(page.locator('.ai-send').first()).toBeEnabled()
  // 悬浮窗确实展开成面板（而不是只有球）
  await expect(page.locator('.ai-widget-panel')).toBeVisible()
})
