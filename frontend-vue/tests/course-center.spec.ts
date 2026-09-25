import { test, expect, Page } from '@playwright/test'
import {
  BASE, RUN, teacherName, studentName, registerAndLogin, api, clickRadio,
} from './helpers'

/**
 * 课程中心 / 课程管理全流程（对应验收标准）
 *
 * 教师：登录 → 课程管理 → 创建课程 → 加课码 → 学生管理 → 邀请学生 → 审核申请
 * 学生：注册 → 加课码加入 → 我的课程 → 进入课程工作区 → 退出课程
 *
 * 【2026-09 修订】本 spec 原先整套跑在「课程中心」页，但界面已改版：
 *   - 教师的「我的课程」标签页被移除，课程管理整体搬到 /teacher（侧栏「课程管理」）；
 *   - 课程中心只保留学生的「我的课程」，教师侧只剩「发现课程 / 加入课程」；
 *   - 课程卡片上的按钮由「课程设置」改名为「设置」。
 * 因此这里把教师链路改到 /teacher 的 #pane-courses，#pane-* 断言代替已不存在的标签页。
 * 这是**测试断言过时**，不是产品功能损坏——学生链路的选择器未变，仍然原样可用。
 *
 * 会创建真实的课程与账号（端到端验证），最后一个用例删除自己创建的课程。
 * serial 模式：用例间共享 process.env 里传递的加课码。
 */

const COURSE_APPROVAL = `E2E审核课_${RUN}`
const COURSE_AUTO = `E2E自动课_${RUN}`

test.describe.configure({ mode: 'serial' })

/** 教师登录并落到「课程管理」页的课程列表 */
async function loginTeacherAtCourses(page: Page) {
  await registerAndLogin(page, teacherName, 'teacher')
  await page.goto(`${BASE}/teacher?tab=courses`)
  await expect(page.locator('#pane-courses')).toBeVisible({ timeout: 20000 })
}

test('教师：创建课程（审核制 / 直接加入）→ 卡片显示 8 位加课码', async ({ page }) => {
  await loginTeacherAtCourses(page)

  // ---- 课程一：审核后加入 ----
  await page.getByRole('button', { name: '新建课程' }).first().click()
  await page.getByPlaceholder('例如：数据结构').fill(COURSE_APPROVAL)
  await page.getByPlaceholder(/一句话说明这门课讲什么/).fill('端到端测试课程（审核后加入）')
  await page.getByPlaceholder(/计算机 \/ 数学/).fill('计算机')
  await page.getByPlaceholder(/软件学院/).fill('软件学院')
  await clickRadio(page, '审核后加入')
  await page.getByRole('dialog').getByRole('button', { name: '创建', exact: true }).click()

  // 创建成功后会弹出带加课码的提示框
  const box = page.locator('.el-message-box')
  await expect(box).toBeVisible({ timeout: 20000 })
  await expect(box).toContainText(/加课码：[A-Z0-9]{8}/)
  await box.getByRole('button', { name: '知道了' }).click()
  await expect(box).toBeHidden({ timeout: 10000 })

  const card1 = page.locator('.course-card', { hasText: COURSE_APPROVAL })
  await expect(card1).toBeVisible({ timeout: 10000 })
  await expect(card1.locator('.joincode-value')).toHaveText(/^[A-Z0-9]{8}$/)
  const codeApproval = (await card1.locator('.joincode-value').innerText()).trim()
  process.env.E2E_CODE_APPROVAL = codeApproval

  // 卡片展示了分类与统计
  await expect(card1.getByText('计算机')).toBeVisible()
  await expect(card1.getByText('文档')).toBeVisible()

  // ---- 课程二：直接加入 ----
  await page.getByRole('button', { name: '新建课程' }).first().click()
  await expect(page.getByPlaceholder('例如：数据结构')).toBeVisible({ timeout: 10000 })
  await page.getByPlaceholder('例如：数据结构').fill(COURSE_AUTO)
  await clickRadio(page, '直接加入')
  await page.getByRole('dialog').getByRole('button', { name: '创建', exact: true }).click()

  // 成功→弹加课码；失败→弹错误提示。把真实原因暴露出来，避免「卡片没出现」这种模糊失败
  const box2 = page.locator('.el-message-box')
  const err2 = page.locator('.el-message--error')
  await expect(box2.or(err2).first()).toBeVisible({ timeout: 20000 })
  if (await err2.count()) {
    throw new Error(`创建第二门课程失败：${await err2.first().innerText()}`)
  }
  await box2.getByRole('button', { name: '知道了' }).click()
  await expect(box2).toBeHidden({ timeout: 10000 })

  const card2 = page.locator('.course-card', { hasText: COURSE_AUTO })
  await expect(card2).toBeVisible({ timeout: 10000 })
  process.env.E2E_CODE_AUTO = (await card2.locator('.joincode-value').innerText()).trim()

  console.log(`加课码：审核制=${codeApproval} 直接加入=${process.env.E2E_CODE_AUTO}`)
})

test('教师：学生管理 / 邀请弹窗 / 课程设置 可用', async ({ page }) => {
  await loginTeacherAtCourses(page)

  // 通过课程卡片进入「学生管理」（同时验证课程管理 → 学生管理 Tab 的联动）
  await page.locator('.course-card', { hasText: COURSE_APPROVAL })
    .getByRole('button', { name: '学生管理' }).click()
  await page.waitForURL(/tab=members/, { timeout: 20000 })
  await expect(page.locator('#pane-members')).toBeVisible({ timeout: 20000 })

  await expect(page.getByText('总人数')).toBeVisible({ timeout: 20000 })
  await expect(page.getByText('平均进度')).toBeVisible()
  // exact：否则会同时命中「待审核申请」标签页与表格空状态「暂无待审核申请」
  await expect(page.getByText('待审核申请', { exact: true })).toBeVisible()

  // 邀请弹窗
  await page.getByRole('button', { name: '邀请学生' }).first().click()
  await page.getByRole('button', { name: '生成邀请链接' }).click()
  await expect(page.getByText(/新生成的链接/)).toBeVisible({ timeout: 20000 })
  // 只在 .fresh-link 容器内取输入框：弹窗里还有若干 el-select 的 readonly input，
  // 不加限定会先命中它们（值为空，且与邀请链接无关）
  await expect(page.locator('.fresh-link input').first()).toHaveValue(/\/invite\/.+/)
  await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click()

  // 课程设置（加课码 + 刷新 + 删除入口）—— 按钮名已由「课程设置」改为「设置」
  await page.getByRole('button', { name: '返回课程列表' }).click()
  await page.locator('.course-card', { hasText: COURSE_APPROVAL })
    .getByRole('button', { name: '设置', exact: true }).click()
  await expect(page.getByRole('button', { name: '刷新' })).toBeVisible({ timeout: 15000 })
  await expect(page.getByText('删除课程').first()).toBeVisible()
  await page.getByRole('dialog').getByRole('button', { name: '取消', exact: true }).click()
})

test('学生：加课码加入（审核制→待审核；直接加入→立即通过）', async ({ page }) => {
  await registerAndLogin(page, studentName, 'student')

  await page.getByRole('tab', { name: '加入课程' }).click()
  await page.getByPlaceholder(/请输入加课码/).fill(process.env.E2E_CODE_APPROVAL || '')
  await page.getByRole('button', { name: '加入课程' }).click()
  await expect(page.getByText(/已提交申请/)).toBeVisible({ timeout: 20000 })

  // 审核制的课程此时不在「我的课程」里，而是在「申请中」
  await expect(page.locator('.course-card', { hasText: COURSE_APPROVAL })).toHaveCount(0)
  await expect(page.getByText('申请中 / 未通过')).toBeVisible({ timeout: 10000 })
  await expect(page.getByRole('cell', { name: COURSE_APPROVAL })).toBeVisible()
  await expect(page.getByRole('cell', { name: '等待教师审核' })).toBeVisible()

  // 直接加入的课程立即出现在我的课程
  await page.getByRole('tab', { name: '加入课程' }).click()
  await page.getByPlaceholder(/请输入加课码/).fill(process.env.E2E_CODE_AUTO || '')
  await page.getByRole('button', { name: '加入课程' }).click()
  await expect(page.getByText(new RegExp(`已加入课程「${COURSE_AUTO}」`))).toBeVisible({ timeout: 20000 })
  await page.getByRole('tab', { name: '我的课程' }).click()
  await expect(page.locator('.course-card', { hasText: COURSE_AUTO })).toBeVisible()

  // 无效加课码：透出后端中文提示
  await page.getByRole('tab', { name: '加入课程' }).click()
  await page.getByPlaceholder(/请输入加课码/).fill('ZZZZZZZZ')
  await page.getByRole('button', { name: '加入课程' }).click()
  await expect(page.getByText(/加课码无效/)).toBeVisible({ timeout: 15000 })
})

test('学生：发现课程页可搜索、且已加入的课程不出现', async ({ page }) => {
  await registerAndLogin(page, studentName, 'student')
  await page.getByRole('tab', { name: '发现课程' }).click()
  await expect(page.getByPlaceholder(/搜索课程名称或简介/)).toBeVisible()

  // 搜索自己的课程名 → 已被排除（因为已加入/已申请）
  await page.getByPlaceholder(/搜索课程名称或简介/).fill(COURSE_AUTO)
  await page.getByRole('button', { name: '搜索' }).click()
  await expect(page.getByText('没有找到符合条件的课程')).toBeVisible({ timeout: 15000 })
})

test('教师：审核申请 → 学生成为正式成员', async ({ page }) => {
  await registerAndLogin(page, teacherName, 'teacher')

  // 从课程卡片进入学生管理（与上一个用例同一条路径，确保带上了课程上下文；
  // 直接 goto ?tab=members 不带 course_id 时会停在「请先选择要管理学生的课程」）
  await page.goto(`${BASE}/teacher?tab=courses`)
  await page.locator('.course-card', { hasText: COURSE_APPROVAL })
    .getByRole('button', { name: '学生管理' }).click()
  await page.waitForURL(/tab=members/, { timeout: 20000 })
  await expect(page.locator('#pane-members')).toBeVisible({ timeout: 20000 })

  // 待审核申请在第二个标签页（默认是「全部成员」），必须先切过去
  await page.locator('#pane-members').getByRole('tab', { name: /待审核申请/ }).click()
  await expect(page.getByRole('cell', { name: studentName })).toBeVisible({ timeout: 20000 })
  await page.getByRole('button', { name: '同意' }).first().click()
  await expect(page.getByText(/已同意/)).toBeVisible({ timeout: 20000 })

  // 切到「全部成员」应能看到该学生
  await page.locator('#pane-members').getByRole('tab', { name: /全部成员/ }).click()
  await expect(page.getByRole('cell', { name: studentName }).first()).toBeVisible({ timeout: 20000 })
  await expect(page.getByText('学习进度').first()).toBeVisible()
})

test('学生：审核通过后进入课程工作区', async ({ page }) => {
  await registerAndLogin(page, studentName, 'student')

  const card = page.locator('.course-card', { hasText: COURSE_APPROVAL })
  await expect(card).toBeVisible({ timeout: 20000 })
  await card.getByRole('button', { name: /继续学习|开始学习/ }).click()

  await page.waitForURL(/\/student\?tab=documents/, { timeout: 20000 })
  expect(page.url()).toContain('course_id=')
  await expect(page.getByText(COURSE_APPROVAL).first()).toBeVisible({ timeout: 15000 })
})

test('学生：退出课程后不再出现在我的课程', async ({ page }) => {
  await registerAndLogin(page, studentName, 'student')
  const card = page.locator('.course-card', { hasText: COURSE_AUTO })
  await expect(card).toBeVisible({ timeout: 15000 })
  await card.getByRole('button', { name: '退出课程' }).click()
  await page.getByRole('button', { name: '确认退出' }).click()
  await expect(page.getByText('已退出该课程')).toBeVisible({ timeout: 20000 })
  await expect(page.locator('.course-card', { hasText: COURSE_AUTO })).toHaveCount(0)
})

test('清理：删除本轮创建的 E2E 课程', async ({ page }) => {
  const token = await registerAndLogin(page, teacherName, 'teacher')
  const resp = await api(page, 'get', '/api/v1/courses/my?page_size=100', token)
  const mine = await resp.json()
  let removed = 0
  for (const c of mine.data.items) {
    if (String(c.course_name).startsWith('E2E')) {
      const r = await api(page, 'delete', `/api/v1/courses/${c.course_id}?confirm=true`, token)
      if (r.ok()) removed += 1
    }
  }
  console.log(`清理：删除测试课程 ${removed} 门`)
  expect(removed).toBeGreaterThan(0)
})
