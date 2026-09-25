import { test, expect, Page } from '@playwright/test'
import {
  BASE, RUN, registerAndLogin, login, loginAdmin, api, dismissBackendStatus,
} from './helpers'

/**
 * 管理员端验收 + 三角色权限回归。
 *
 * 本 spec 全部只读：不创建课程、不上传文档、不改任何账号状态，
 * 因此跑完不需要清理数据。唯一的例外是 e2e 前缀的教师 / 学生账号，
 * 由 registerAndLogin 按需创建（与其它 spec 共用同一套命名约定）。
 *
 * 管理员使用系统内置账号（sysadmin / admin123，见 sql_db.ensure_default_admin），
 * 刻意不注册新的管理员账号：能自己注册管理员本身就是权限漏洞。
 */

/** 导航到管理端某页并等待主内容渲染完成 */
async function gotoAdminPage(page: Page, path: string) {
  await page.goto(`${BASE}${path}`)
  await expect(page.locator('.page-header').first()).toBeVisible({ timeout: 20000 })
}

/** 收集页面加载期间对管理端接口的失败请求（>=400），用于断言页面没有静默报错 */
function collectAdminFailures(page: Page) {
  const failures: string[] = []
  page.on('response', (resp) => {
    const url = resp.url()
    if (url.includes('/api/v1/admin/') && resp.status() >= 400) {
      failures.push(`${resp.status()} ${url}`)
    }
  })
  return failures
}

test('未登录：访问 /admin 被送到登录页', async ({ page }) => {
  await page.goto(`${BASE}/admin`)
  await page.waitForURL(/\/login/, { timeout: 20000 })
  await expect(page.getByPlaceholder('请输入用户名')).toBeVisible()
})

test('未登录：管理端接口一律 401', async ({ page }) => {
  for (const path of ['/api/v1/admin/dashboard', '/api/v1/admin/users',
    '/api/v1/admin/courses', '/api/v1/admin/system', '/api/v1/admin/audit-logs']) {
    const r = await api(page, 'get', path)
    expect(r.status(), `${path} 未登录应为 401`).toBe(401)
  }
})

test('管理员：登录后进入工作台，侧栏是管理端菜单', async ({ page }) => {
  await loginAdmin(page)

  // 侧栏菜单内容切换为管理端，而不是教师/学生菜单
  const menu = page.locator('.sidebar-menu')
  for (const title of ['工作台', '用户管理', '课程管理', '资源管理', '课程治理', '系统监控', '审计日志']) {
    await expect(menu.getByText(title, { exact: true }).first()).toBeVisible()
  }
  // 管理员不参与教学，侧栏不应出现课程中心
  await expect(menu.getByText('课程中心')).toHaveCount(0)

  // 工作台的核心统计卡渲染出来
  await expect(page.getByText('用户总数').first()).toBeVisible({ timeout: 20000 })
  await expect(page.getByText('课程总数').first()).toBeVisible()
  await expect(page.getByText('文档总数').first()).toBeVisible()
})

test('管理员：7 个管理页面均可打开且无失败请求', async ({ page }) => {
  test.setTimeout(180000)
  await loginAdmin(page)

  // 第二项是页面自身 PageHeader 的标题（与侧栏菜单名可能不同，如工作台 → 管理工作台）
  const pages: Array<[string, string]> = [
    ['/admin', '管理工作台'],
    ['/admin/users', '用户管理'],
    ['/admin/courses', '课程管理'],
    ['/admin/resources', '资源管理'],
    ['/admin/governance', '课程治理'],
    ['/admin/system', '系统监控'],
    ['/admin/audit-logs', '审计日志'],
  ]

  for (const [path, title] of pages) {
    const failures = collectAdminFailures(page)
    await gotoAdminPage(page, path)
    await expect(page.locator('.page-header').getByText(title, { exact: true })).toBeVisible()
    // 给页面内的异步请求留出时间（统计、列表等）
    await page.waitForTimeout(1200)
    expect(failures, `${path} 存在失败的接口请求：${failures.join('; ')}`).toEqual([])
  }
})

test('管理员：用户管理与搜索能读到真实数据', async ({ page }) => {
  await loginAdmin(page)
  await gotoAdminPage(page, '/admin/users')

  // 不依赖默认分页/排序：直接按用户名搜索（顺带验证搜索链路）
  await page.getByPlaceholder(/搜索用户名/).fill('sysadmin')
  await page.getByRole('button', { name: '查询' }).click()

  const body = page.locator('.el-table__body')
  await expect(body.getByText('sysadmin').first()).toBeVisible({ timeout: 20000 })
  await expect(body.getByText('管理员').first()).toBeVisible()
  await expect(page.getByText(/共 \d+ 个账号/)).toBeVisible()
})

test('管理员：系统监控展示各组件真实状态', async ({ page }) => {
  await loginAdmin(page)
  await gotoAdminPage(page, '/admin/system')
  await expect(page.getByText('API 服务 (FastAPI)')).toBeVisible({ timeout: 20000 })
  await expect(page.getByText('关系库 (SQLite)')).toBeVisible()
  await expect(page.getByText('图数据库 (Neo4j)')).toBeVisible()
  await expect(page.getByText('大模型 (LLM)')).toBeVisible()
  await expect(page.getByText('文件存储')).toBeVisible()
})

test('管理员：审计日志能看到自己的登录记录', async ({ page }) => {
  await loginAdmin(page)
  await gotoAdminPage(page, '/admin/audit-logs')
  // 登录动作是刚发生的，必然在列表里（证明审计真的在写）
  await expect(page.locator('.el-table__body').getByText('管理员登录').first())
    .toBeVisible({ timeout: 20000 })
})

test('管理员：侧栏折叠按钮可用（既有缺陷回归）', async ({ page }) => {
  await loginAdmin(page)
  const sidebar = page.locator('.app-sidebar')
  await expect(sidebar).not.toHaveClass(/collapsed/)

  await page.locator('.collapse-btn').click()
  await expect(sidebar).toHaveClass(/collapsed/)

  await page.locator('.collapse-btn').click()
  await expect(sidebar).not.toHaveClass(/collapsed/)
})

test('管理员：个人中心复用同一页面', async ({ page }) => {
  await loginAdmin(page)
  await dismissBackendStatus(page)
  await page.goto(`${BASE}/profile`)
  // 不应被守卫弹回工作台，而是正常渲染个人中心
  await expect(page.locator('.page-header').first()).toBeVisible({ timeout: 20000 })
  await expect(page).toHaveURL(/\/profile/)
})

test('管理员：访问教师/学生页面被引导回工作台', async ({ page }) => {
  await loginAdmin(page)
  await page.goto(`${BASE}/course-center`)
  await page.waitForURL(/\/admin$/, { timeout: 20000 })
})

test('学生：访问 /admin 被拒（403 页），且管理端接口 403', async ({ page }) => {
  const token = await registerAndLogin(page, `e2e_admin_s_${RUN}`, 'student')

  await page.goto(`${BASE}/admin`)
  await page.waitForURL(/\/403/, { timeout: 20000 })

  for (const path of ['/api/v1/admin/dashboard', '/api/v1/admin/users',
    '/api/v1/admin/system', '/api/v1/admin/audit-logs']) {
    const r = await api(page, 'get', path, token)
    expect(r.status(), `学生访问 ${path} 应为 403`).toBe(403)
  }
})

test('教师：访问 /admin 被拒（403 页），且管理端接口 403', async ({ page }) => {
  const token = await registerAndLogin(page, `e2e_admin_t_${RUN}`, 'teacher')

  await page.goto(`${BASE}/admin`)
  await page.waitForURL(/\/403/, { timeout: 20000 })

  for (const path of ['/api/v1/admin/dashboard', '/api/v1/admin/courses',
    '/api/v1/admin/governance', '/api/v1/admin/resources/documents']) {
    const r = await api(page, 'get', path, token)
    expect(r.status(), `教师访问 ${path} 应为 403`).toBe(403)
  }
})

test('管理员登录不影响教师登录：教师仍进课程中心', async ({ page }) => {
  await login(page, 'admin', 'admin123')
  await expect(page).toHaveURL(/course-center/)
  const menu = page.locator('.sidebar-menu')
  await expect(menu.getByText('课程中心').first()).toBeVisible()
  await expect(menu.getByText('用户管理')).toHaveCount(0)
})

test('管理员登录不影响学生登录：学生仍进课程中心', async ({ page }) => {
  await registerAndLogin(page, `e2e_admin_s2_${RUN}`, 'student')
  await expect(page).toHaveURL(/course-center/)
  const menu = page.locator('.sidebar-menu')
  await expect(menu.getByText('课程中心').first()).toBeVisible()
  await expect(menu.getByText('用户管理')).toHaveCount(0)
})
