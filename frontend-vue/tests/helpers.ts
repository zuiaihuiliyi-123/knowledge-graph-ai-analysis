import { expect, Page } from '@playwright/test'

/** 被测站点地址：优先用环境变量，默认指向 vite dev server（端口见 vite.config.js） */
export const BASE = process.env.KGU_BASE || 'http://localhost:5173'

/** 本轮测试创建的数据统一带该前缀，便于识别与清理 */
export const RUN = process.env.KGU_RUN || String(Date.now()).slice(-6)

export const teacherName = `e2e_t_${RUN}`
export const studentName = `e2e_s_${RUN}`
export const student2Name = `e2e_s2_${RUN}`
export const PASSWORD = 'pw123456'

/** 等待进入课程中心；成功返回 true（不抛错，供分支判断） */
async function waitForCenter(page: Page, timeout: number) {
  return page.waitForURL(/course-center/, { timeout }).then(() => true).catch(() => false)
}

/** 取当前登录 token 并断言已登录 */
async function tokenOrFail(page: Page) {
  const token = await page.evaluate(() => localStorage.getItem('kg_token'))
  expect(token, '登录后应拿到 token').toBeTruthy()
  return token as string
}

/**
 * 幂等的「确保已登录」：先按已有账号登录，失败再注册。
 * 同一 spec 里多个用例共用同一账号，因此必须可重复调用。
 *
 * 注意：登录与注册现在是两个独立路由（/login、/register），不再是同一页的 el-tabs，
 * 因此各自 goto 到对应页面即可，无需再用 pane 容器限定作用域。
 */
export async function registerAndLogin(page: Page, username: string, role: 'teacher' | 'student') {
  await page.goto(`${BASE}/login`)

  // 1) 先试登录（账号通常已存在）
  await page.getByPlaceholder('请输入用户名').fill(username)
  await page.getByPlaceholder('请输入密码').fill(PASSWORD)
  await page.getByRole('button', { name: '登 录' }).click()
  if (await waitForCenter(page, 10000)) return tokenOrFail(page)

  // 2) 登录失败 → 去注册页注册（注册成功会自动登录并进入课程中心）
  await page.goto(`${BASE}/register`)
  await page.getByPlaceholder('请输入用户名').fill(username)
  await page.getByPlaceholder('至少 6 位').fill(PASSWORD)
  await page.getByPlaceholder('再次输入密码').fill(PASSWORD)
  await clickRadio(page, role === 'teacher' ? '教师' : '学生')
  await page.getByRole('button', { name: '注 册' }).click()
  if (await waitForCenter(page, 25000)) return tokenOrFail(page)

  // 3) 注册也失败（例如并发下已被创建）→ 回落再登录一次
  await page.goto(`${BASE}/login`)
  await page.getByPlaceholder('请输入用户名').fill(username)
  await page.getByPlaceholder('请输入密码').fill(PASSWORD)
  await page.getByRole('button', { name: '登 录' }).click()
  await page.waitForURL(/course-center/, { timeout: 25000 })
  return tokenOrFail(page)
}

/** 用已存在的账号登录，返回 access_token */
export async function login(page: Page, username: string, password: string) {
  await page.goto(`${BASE}/login`)
  await page.getByPlaceholder('请输入用户名').fill(username)
  await page.getByPlaceholder('请输入密码').fill(password)
  await page.getByRole('button', { name: '登 录' }).click()
  await page.waitForURL(/course-center/, { timeout: 25000 })
  return tokenOrFail(page)
}

/**
 * 管理员登录：与 login() 的唯一差别是落地页。
 * 管理员的首页是 /admin（不是课程中心），因此不能用 login() 那样等 /course-center。
 */
export async function loginAdmin(page: Page, username = 'sysadmin', password = 'admin123') {
  await page.goto(`${BASE}/login`)
  await page.getByPlaceholder('请输入用户名').fill(username)
  await page.getByPlaceholder('请输入密码').fill(password)
  await page.getByRole('button', { name: '登 录' }).click()
  await page.waitForURL(/\/admin$/, { timeout: 25000 })
  return tokenOrFail(page)
}

/** 带 token 直接调后端（走 vite 代理），用于权限断言 */
export async function api(page: Page, method: 'get' | 'post' | 'put' | 'delete', path: string, token?: string, data?: any) {
  const headers: Record<string, string> = {}
  if (token) headers.Authorization = `Bearer ${token}`
  const resp = await page.request[method](`${BASE}${path}`, { headers, data })
  return resp
}

/** 关闭可能出现的 Element Plus 提示框（创建课程后的加课码弹窗等） */
export async function dismissAlert(page: Page) {
  const btn = page.getByRole('button', { name: /知道了|确定|关闭/ })
  if (await btn.count()) {
    await btn.first().click().catch(() => {})
  }
}

/**
 * 点击 el-radio 选项。
 * Element Plus 的 radio 内层 span 会拦截指针事件，直接点 role=radio 会一直重试，
 * 因此这里点 label（文本即选项名）。
 */
export async function clickRadio(scope: any, text: string) {
  await scope.locator('label.el-radio').filter({ hasText: text }).first().click()
}

/**
 * 关闭右下角「后端服务在线」浮窗。
 * 它固定在右下角，会盖住页面底部的按钮（保存资料等），导致点击被拦截。
 */
export async function dismissBackendStatus(page: Page) {
  const card = page.locator('.backend-status')
  if (await card.count()) {
    const btn = card.getByRole('button', { name: '关闭' })
    if (await btn.count()) await btn.first().click({ force: true }).catch(() => {})
  }
}
