import { test, expect } from '@playwright/test'
import { BASE, RUN, login, registerAndLogin, dismissBackendStatus, api } from './helpers'

/**
 * 既有功能回归（课程中心改造不得破坏这些已验收的能力）
 * - 教师文档表：生成进度列 + 6 个操作按钮 + 占位行守卫
 * - 文档在线阅读器：PDF 渲染 / 文本层 / 搜索 / 缩放 / 阅读进度
 * - 阅读器 → 图谱跳转
 * - 知识图谱（G6 画布）渲染
 * - 学生端 AI 助教悬浮窗
 *
 * 使用既有数据：admin / admin123，课程 5（含 69 个真实知识点的真实 PDF 文档）。
 *
 * 【2026-09 修订】两处断言随界面/数据变化更新（属测试过时，非功能损坏）：
 *   1. 文档行操作按钮由 6 个减为 4 个（在线阅读 / 下载 / 监测 / 删除）——
 *      「查看图谱」「编辑」已并入图谱管理与阅读器，不在文档行上；
 *   2. 不再硬编码 doc_id。库经历过重建，doc_id 会变（课程 5 现只有 doc 100，
 *      原先写的 doc 4 已不存在），因此只断言「进入了阅读器」，不假定具体编号。
 */

const COURSE = 5

/** 课程 5 的第一份文档 id（动态取：库重建后 doc_id 会变，硬编码必然失效） */
async function firstDocId(page: any, token: string): Promise<number> {
  const r = await api(page, 'get', `/api/v1/documents?course_id=${COURSE}`, token)
  const body = await r.json()
  const items = Array.isArray(body.data) ? body.data : (body.data?.items || [])
  expect(items.length, `课程 ${COURSE} 应当有文档`).toBeGreaterThan(0)
  return items[0].doc_id
}

test.describe.configure({ mode: 'serial' })

test('教师文档表：生成进度列与 4 个操作按钮都在，且非占位行可点', async ({ page }) => {
  await login(page, 'admin', 'admin123')
  await page.goto(`${BASE}/teacher?tab=documents&course_id=${COURSE}`)
  await dismissBackendStatus(page)

  const table = page.locator('.el-table').first()
  await expect(table).toBeVisible({ timeout: 20000 })

  // 表头（含课程中心改造前就已验收的「生成进度」）
  for (const col of ['文件名', '类型', '大小', '解析状态', '抽取状态', '生成进度', '知识点', '操作']) {
    await expect(table.getByRole('columnheader', { name: col, exact: true })).toBeVisible()
  }

  // 数据行上的 4 个按钮
  const row = table.locator('.el-table__row').first()
  for (const btn of ['在线阅读', '下载', '监测', '删除']) {
    await expect(row.getByRole('button', { name: btn })).toBeVisible()
  }
  // 真实文档行：按钮必须可用（占位行守卫不能误伤）
  await expect(row.getByRole('button', { name: '在线阅读' })).toBeEnabled()
})

test('在线阅读：PDF 画布 / 文本层 / 搜索 / 缩放 / 进度 全部可用', async ({ page }) => {
  await login(page, 'admin', 'admin123')
  await page.goto(`${BASE}/teacher?tab=documents&course_id=${COURSE}`)
  await dismissBackendStatus(page)

  await page.locator('.el-table__row').first().getByRole('button', { name: '在线阅读' }).click()
  await page.waitForURL(/\/reader\/\d+/, { timeout: 25000 })

  // PDF 画布渲染
  const canvas = page.locator('canvas').first()
  await expect(canvas).toBeVisible({ timeout: 30000 })

  // 文本层（供选中文本菜单/知识点定位使用）
  await expect(page.locator('.pdfv-text-layer span').first()).toBeAttached({ timeout: 30000 })

  // 搜索：输入关键词后出现命中提示
  const searchInput = page.locator('.rtb-search input').first()
  await expect(searchInput).toBeVisible({ timeout: 15000 })
  await searchInput.fill('对象')
  await searchInput.press('Enter')
  await expect(page.locator('.rtb-search').getByText(/\d+\s*\/\s*\d+/).first())
    .toBeVisible({ timeout: 25000 })

  // 缩放：点「放大」后比例文字变化
  const zoomLabel = page.locator('.rtb-zoom-label').first()
  const before = (await zoomLabel.innerText()).trim()
  await page.locator('button[title^="放大"]').first().click()
  await expect(zoomLabel).not.toHaveText(before, { timeout: 15000 })

  // 阅读进度：顶栏进度条存在（localStorage 记录阅读位置）
  await expect(page.locator('.rtb-progress').first()).toBeAttached({ timeout: 15000 })
})

test('阅读器 → 跳转知识图谱', async ({ page }) => {
  const token = await login(page, 'admin', 'admin123')
  const docId = await firstDocId(page, token)
  await page.goto(`${BASE}/reader/${docId}?course_id=${COURSE}&from=teacher`)
  await dismissBackendStatus(page)
  await expect(page.locator('canvas').first()).toBeVisible({ timeout: 30000 })

  // 右侧学习助手面板（知识点 / AI 问答）在宽屏下默认就是打开的，不要再去点开关
  // （点一下反而会把它关掉）。直接点面板里的「查看图谱」跳到教师端图谱预览。
  const openGraph = page.locator('.dkp-graph-btn').first()
  await expect(openGraph).toBeVisible({ timeout: 20000 })
  await openGraph.click()

  await page.waitForURL(/tab=preview/, { timeout: 25000 })
  expect(page.url()).toContain('tab=preview')
  expect(page.url()).toContain(`course_id=${COURSE}`)
  // 跳过去后图谱应真的渲染出来
  await expect(page.locator('canvas').first()).toBeVisible({ timeout: 40000 })
})

test('知识图谱：G6 画布渲染出真实图谱', async ({ page }) => {
  const errors: string[] = []
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })

  const token = await login(page, 'admin', 'admin123')
  const docId = await firstDocId(page, token)
  await page.goto(`${BASE}/teacher?tab=preview&course_id=${COURSE}&document_id=${docId}`)
  await dismissBackendStatus(page)

  // G6 在 canvas 上渲染
  await expect(page.locator('canvas').first()).toBeVisible({ timeout: 40000 })
  // 图谱容器有实际尺寸（改造中容易出现容器塌陷导致的空画布）
  const box = await page.locator('canvas').first().boundingBox()
  expect(box, '画布应有尺寸').toBeTruthy()
  expect(box!.width).toBeGreaterThan(100)
  expect(box!.height).toBeGreaterThan(100)

  expect(errors, `控制台不应报错：${errors.join(' | ')}`).toEqual([])
})

test('学生端：AI 助教悬浮窗可开可关', async ({ page }) => {
  await registerAndLogin(page, `e2e_reg_s_${RUN}`, 'student')
  await page.goto(`${BASE}/student?tab=overview`)

  const fab = page.locator('.ai-widget-fab').first()
  await expect(fab).toBeVisible({ timeout: 20000 })
  await fab.click()

  const panel = page.locator('.ai-widget-panel').first()
  await expect(panel).toBeVisible({ timeout: 15000 })
  await expect(panel).toContainText('AI 助教')
  await expect(panel.locator('input.ai-input')).toBeVisible({ timeout: 15000 })
  await expect(panel.locator('.ai-send')).toBeVisible()

  // 面板在右下角有 0.22s 进场动画，等它停稳再点，避免与动画竞争
  await page.waitForTimeout(600)
  await panel.locator('.ai-close').click({ force: true })
  await expect(panel).toBeHidden({ timeout: 15000 })
})

test('回归：课程管理 6 个 Tab 全部存在，控制台无报错', async ({ page }) => {
  const errors: string[] = []
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })

  await login(page, 'admin', 'admin123')
  await page.goto(`${BASE}/teacher?tab=courses`)
  await dismissBackendStatus(page)

  // 顶部标签栏被 CSS 刻意隐藏（「顶部标签栏已由左侧菜单接管」），
  // getByRole('tab') 命中不到隐藏元素，因此改断言各面板存在于 DOM。
  for (const pane of ['pane-courses', 'pane-documents', 'pane-members',
    'pane-preview', 'pane-monitor', 'pane-questions', 'pane-grading']) {
    await expect(page.locator(`#${pane}`), `${pane} 应存在`).toHaveCount(1, { timeout: 15000 })
  }
  expect(errors, `控制台不应报错：${errors.join(' | ')}`).toEqual([])
})
