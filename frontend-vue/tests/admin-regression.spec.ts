import { test, expect } from '@playwright/test'
import { BASE, RUN, login, registerAndLogin, dismissBackendStatus } from './helpers'

/**
 * 管理员端改造的教师端 / 学生端回归（只读：不创建课程、不上传文档、不改账号）。
 *
 * 为什么不用 getByRole('tab')：
 *   改造前就已经存在一个刻意的设计——教师 / 学生页的顶部标签栏被 CSS 隐藏
 *   （见 TeacherView.vue / StudentView.vue 的 `.main-view-tabs > .el-tabs__header
 *   { display: none }`，注释写明「顶部标签栏已由左侧菜单接管」）。
 *   因此 `.el-tabs__item` 仍然存在但 `display:none`，Playwright 的
 *   getByRole('tab') 默认过滤隐藏元素，永远匹配不到。
 *   既有 spec 中所有基于 getByRole('tab') 的断言因此都是失败状态（改造前即如此），
 *   详见报告的「未覆盖项」。这里按真实交互走：**用侧边栏导航 + 断言面板内容**。
 *
 * 使用既有真实数据：admin / admin123（教师），课程 5 与其真实文档。
 */

const COURSE = 5

test('教师：侧边栏菜单与应用外壳完整', async ({ page }) => {
  await login(page, 'admin', 'admin123')
  await dismissBackendStatus(page)

  // 品牌区（应用外壳未被管理员端改造影响）
  await expect(page.locator('.brand-name')).toHaveText('智育数据')

  const menu = page.locator('.sidebar-menu')
  for (const title of ['课程中心', '数据总览', '课程管理', '图谱管理', '题库管理', '个人中心']) {
    await expect(menu.getByText(title, { exact: true }).first()).toBeVisible()
  }
  // 教师不应看到管理员菜单
  await expect(menu.getByText('用户管理')).toHaveCount(0)
  await expect(menu.getByText('审计日志')).toHaveCount(0)
})

test('教师：侧边栏「课程管理」可进入课程列表并渲染出真实课程', async ({ page }) => {
  await login(page, 'admin', 'admin123')
  await dismissBackendStatus(page)

  await page.locator('.sidebar-menu').getByText('课程管理', { exact: true }).first().click()
  await expect(page).toHaveURL(/\/teacher/, { timeout: 20000 })

  await expect(page.locator('#pane-courses')).toBeVisible({ timeout: 20000 })
  // 真实课程规模（admin 名下有课程，文案形如「共 9 门课程」）
  await expect(page.getByText(/共 \d+ 门课程/)).toBeVisible({ timeout: 20000 })
  await expect(page.getByRole('button', { name: '新建课程' }).first()).toBeVisible()
})

test('教师：侧边栏「图谱管理」切到图谱面板', async ({ page }) => {
  await login(page, 'admin', 'admin123')
  await dismissBackendStatus(page)

  await page.locator('.sidebar-menu').getByText('图谱管理', { exact: true }).first().click()
  await expect(page).toHaveURL(/tab=preview/, { timeout: 20000 })
  await expect(page.locator('#pane-preview')).toBeVisible({ timeout: 20000 })
})

test('教师：课程文档面板渲染真实文档行，操作按钮可用', async ({ page }) => {
  await login(page, 'admin', 'admin123')
  await page.goto(`${BASE}/teacher?tab=documents&course_id=${COURSE}`)
  await dismissBackendStatus(page)

  const table = page.locator('.el-table').first()
  await expect(table).toBeVisible({ timeout: 20000 })

  // 既有验收过的列必须还在（含「生成进度」）
  for (const col of ['文件名', '类型', '大小', '解析状态', '抽取状态', '生成进度', '知识点']) {
    await expect(table.getByRole('columnheader', { name: col, exact: true })).toBeVisible()
  }

  // 真实文档行 + 当前 UI 的 4 个操作按钮
  const row = table.locator('.el-table__row').first()
  await expect(row).toBeVisible({ timeout: 20000 })
  for (const btn of ['在线阅读', '下载', '监测', '删除']) {
    await expect(row.getByRole('button', { name: btn })).toBeVisible()
  }
  await expect(row.getByRole('button', { name: '在线阅读' })).toBeEnabled()
})

test('学生：侧边栏菜单与应用外壳完整（含学习空间分组）', async ({ page }) => {
  await registerAndLogin(page, `e2e_reg_s_${RUN}`, 'student')
  await expect(page.locator('.brand-name')).toHaveText('智育数据')

  const menu = page.locator('.sidebar-menu')
  await expect(menu.getByText('课程中心', { exact: true }).first()).toBeVisible()
  await expect(menu.getByText('学习总览', { exact: true }).first()).toBeVisible()

  // 展开「学习空间」子菜单，三个学习 Tab 入口都在
  await menu.getByText('学习空间', { exact: true }).first().click()
  for (const child of ['课程文档', '图谱浏览', '做题练习']) {
    await expect(menu.getByText(child, { exact: true }).first()).toBeVisible({ timeout: 10000 })
  }
  // 学生不应看到管理员菜单
  await expect(menu.getByText('用户管理')).toHaveCount(0)
})

test('学生：课程中心渲染「我的课程」与「发现课程」', async ({ page }) => {
  await registerAndLogin(page, `e2e_reg_s2_${RUN}`, 'student')
  await dismissBackendStatus(page)

  await expect(page.locator('.page-header').getByText('课程中心')).toBeVisible({ timeout: 20000 })
  await expect(page.locator('#pane-mine')).toBeVisible({ timeout: 20000 })
  await expect(page.getByPlaceholder('搜索我的课程')).toBeVisible()

  // 课程中心自身保留了可见的标签栏，可切换到「发现课程」
  await page.locator('.cc-tabs .el-tabs__item').filter({ hasText: '发现课程' }).first().click()
  await expect(page.locator('#pane-discover')).toBeVisible({ timeout: 15000 })
  await expect(page.locator('#pane-mine')).toBeHidden()
})

test('学生：个人中心可用且仍支持上传头像/保存资料区块', async ({ page }) => {
  await registerAndLogin(page, `e2e_reg_s3_${RUN}`, 'student')
  await dismissBackendStatus(page)
  await page.goto(`${BASE}/profile`)
  await expect(page.locator('.page-header').first()).toBeVisible({ timeout: 20000 })
  await expect(page).toHaveURL(/\/profile/)
})
