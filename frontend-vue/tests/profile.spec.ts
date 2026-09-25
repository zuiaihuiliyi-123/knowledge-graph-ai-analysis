import { test, expect } from '@playwright/test'
import { BASE, RUN, registerAndLogin, api, dismissBackendStatus } from './helpers'

/**
 * 个人中心验收
 * 学生：注册/登录 → 完善个人资料（含头像）→ 侧边栏立即生效
 * 同时回归：侧边栏「退出」按钮仍然可用（改造中不能弱化既有功能）
 */

const NIC = `小智_${RUN}`

// 1x1 透明 PNG
const PNG_1PX = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==',
  'base64',
)

test('学生：完善资料 → 保存后侧边栏昵称立即更新（无需刷新）', async ({ page }) => {
  const token = await registerAndLogin(page, `e2e_prof_s_${RUN}`, 'student')

  await page.goto(`${BASE}/profile`)
  await expect(page.getByRole('heading', { name: '个人中心' })).toBeVisible({ timeout: 15000 })
  await dismissBackendStatus(page)

  const pane = page.locator('main')
  await pane.getByPlaceholder(/真实姓名/).fill('测试同学')
  await pane.getByPlaceholder(/展示用昵称/).fill(NIC)
  await pane.getByPlaceholder(/例如：2023 级/).fill('2023')
  await pane.getByPlaceholder(/选填$/).first().fill('示例大学')
  await page.getByRole('button', { name: '保存资料' }).click()
  await expect(page.getByText('资料已保存')).toBeVisible({ timeout: 15000 })

  // 侧边栏立即显示昵称，且没有刷新页面
  await expect(page.locator('.user-name')).toHaveText(NIC)

  // 刷新后依然生效（已落库）
  await page.reload()
  await expect(page.locator('.user-name')).toHaveText(NIC, { timeout: 15000 })

  // 后端确认字段已写入
  const prof = await (await api(page, 'get', '/api/v1/profile', token)).json()
  expect(prof.data.nickname).toBe(NIC)
  expect(prof.data.real_name).toBe('测试同学')
  expect(prof.data.grade).toBe('2023')
})

test('学生：学生专属字段可写、教师专属字段被静默丢弃', async ({ page }) => {
  const token = await registerAndLogin(page, `e2e_prof_s_${RUN}`, 'student')

  const r = await api(page, 'put', '/api/v1/profile', token,
    { teacher_no: 'T999', title: '教授', major: '软件工程' })
  const body = await r.json()
  expect(body.code).toBe(0)
  expect(body.data.major).toBe('软件工程')
  expect(body.data.teacher_no, '学生不应能写教师工号').toBeNull()
  expect(body.data.title, '学生不应能写职称').toBeNull()
})

test('学生：上传头像 → 侧边栏显示头像图片', async ({ page }) => {
  const token = await registerAndLogin(page, `e2e_prof_s_${RUN}`, 'student')

  const resp = await page.request.post(`${BASE}/api/v1/profile/avatar`, {
    headers: { Authorization: `Bearer ${token}` },
    multipart: { file: { name: 'me.png', mimeType: 'image/png', buffer: PNG_1PX } },
  })
  const body = await resp.json()
  expect(body.code).toBe(0)
  expect(body.data.avatar_url).toContain('/api/v1/profile/avatar/')

  // 头像直链可访问（无需鉴权，供 <img src> 使用）
  const img = await page.request.get(`${BASE}${body.data.avatar_url}`)
  expect(img.status()).toBe(200)
  expect(img.headers()['content-type']).toContain('image/png')

  await page.goto(`${BASE}/profile`)
  await dismissBackendStatus(page)
  // 侧栏有两个头像位（展开态 / 折叠态，后者 v-show 隐藏），顶栏还有一个，
  // 因此必须收窄到侧栏再取第一个，否则命中多个元素触发 strict mode 报错
  await expect(page.locator('.app-sidebar .user-avatar-img').first()).toBeVisible({ timeout: 15000 })
})

test('回归：侧边栏「退出」按钮仍然可用', async ({ page }) => {
  await registerAndLogin(page, `e2e_prof_s_${RUN}`, 'student')

  // 退出前会弹 ElMessageBox 确认框（handleLogout），必须点「确定」才会真正登出
  await page.getByRole('button', { name: '退出' }).click()
  await page.locator('.el-message-box').getByRole('button', { name: '确定' }).click()
  await page.waitForURL(/\/login/, { timeout: 20000 })
  const token = await page.evaluate(() => localStorage.getItem('kg_token'))
  expect(token, '退出后应清除 token').toBeFalsy()
})
