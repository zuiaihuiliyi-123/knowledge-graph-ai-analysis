import { test, expect } from '@playwright/test'
import { BASE, login, registerAndLogin, api, dismissBackendStatus } from './helpers'

/**
 * PR #3 合并后的集成验证（课程中心 × 题库）
 *
 * 合并时 TeacherView 的 Tab 列表由两侧各自追加（课程中心的 members + 合作者的 questions），
 * 而「深链同步」的 watcher 白名单两边都没包含 questions——只在合并后才暴露：
 * 直达 /teacher?tab=questions&course_id=X 会清空 currentCourseId，
 * 页面永远停在「请先选择要管理题库的课程」空态。
 *
 * 这里锁住该行为，并确认两侧 Tab 并存、题库面板能真正渲染。
 */

test('合并：教师端 7 个 Tab 并存，题库管理深链直达题库总览（不落空态）', async ({ page }) => {
  const errors: string[] = []
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })

  const token = await login(page, 'admin', 'admin123')

  // 取一门当前教师名下的课程，用于深链
  const resp = await api(page, 'get', '/api/v1/courses', token)
  const body = await resp.json()
  const items = body?.data?.items || []
  expect(items.length, 'admin 名下应至少有一门课程').toBeGreaterThan(0)
  const courseId = items[0].course_id

  await page.goto(`${BASE}/teacher?tab=questions&course_id=${courseId}`)
  await dismissBackendStatus(page)

  // 两侧 Tab 必须同时存在（课程中心 6 个 + 题库 1 个）。
  //
  // 注意：**不能用 getByRole('tab')** —— 顶部标签栏已被 CSS 刻意隐藏
  // （TeacherView 的 `.main-view-tabs > :deep(.el-tabs__header) { display: none }`，
  //  注释写明「顶部标签栏已由左侧菜单接管」），隐藏元素不会被 role 选择器命中，
  //  该断言必然失败（属测试过时，不是功能损坏）。
  // 改为断言 7 个面板都存在于 DOM，且侧栏能看到新增的「题库管理」入口。
  for (const pane of ['pane-courses', 'pane-documents', 'pane-members', 'pane-preview',
    'pane-monitor', 'pane-questions', 'pane-grading']) {
    await expect(page.locator(`#${pane}`), `${pane} 应存在`).toHaveCount(1, { timeout: 15000 })
  }
  await expect(page.locator('.sidebar-menu').getByText('题库管理', { exact: true }).first())
    .toBeVisible({ timeout: 15000 })

  // 深链必须带出课程上下文：不出现「请先选择课程」空态，而是渲染题库总览
  await expect(page.getByText('请先选择要管理题库的课程')).toHaveCount(0)
  await expect(page.getByText('题库总览')).toBeVisible({ timeout: 15000 })

  expect(errors, `控制台不应报错：${errors.join(' | ')}`).toEqual([])
})

test('合并：学生端「做题练习」Tab 已并入，且缺上下文时按设计回落到学习总览', async ({ page }) => {
  // 固定账号名：registerAndLogin 先登录后注册，重复运行不会反复新建账号
  await registerAndLogin(page, 'e2e_qb_student', 'student')

  await page.goto(`${BASE}/student?tab=practice`)
  await dismissBackendStatus(page)

  // 「做题练习」Tab 已随 PR #3 并入学生端页签。
  // 同样不能用 getByRole('tab')（顶部标签栏被刻意隐藏），改为断言侧栏入口可见。
  await expect(page.locator('.sidebar-menu').getByText('做题练习', { exact: true }).first())
    .toBeVisible({ timeout: 15000 })

  // 但它是 LEARNING_TABS：没有「课程 + 文档」上下文时不进面板，而是回落到学习总览并提示选择课程/资料。
  // 这是合作者的既有设计（非合并引入），此处锁住该行为，避免日后被误当 bug 改掉。
  await expect(page).toHaveURL(/tab=overview/, { timeout: 15000 })

  // 端到端未因缺上下文而报错
  await expect(page.locator('#pane-overview')).toBeVisible({ timeout: 15000 })
})
