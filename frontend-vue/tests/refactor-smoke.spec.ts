import { test, expect } from '@playwright/test'
import { BASE, login, dismissBackendStatus, api, loginTeacher } from './helpers'

/**
 * 教师端 / 学生端模块化重构后的只读冒烟回归。
 *
 * 覆盖：
 * - 教师 7 个 Tab 全部可打开，题库深链不落空态，批改页从侧栏直达能加载汇总；
 * - 学生 7 个 Tab 全部可打开，G6 画布正常渲染；
 * - 全程无 console error / pageerror。
 *
 * 只读：不创建课程、账号或作答记录。依赖既有演示数据（admin / demo_student）。
 */

async function firstCourseAndDoc(page: any, token: string, mine: boolean) {
  const path = mine ? '/api/v1/courses/my' : '/api/v1/courses'
  const resp = await api(page, 'get', path, token)
  const body = await resp.json()
  const courses = ((body.data || {}).items || []).filter((c: any) => (c.node_count || 0) > 0)
  expect(courses.length, '需要至少一门已有知识点的课程').toBeGreaterThan(0)
  const course = courses[0]
  const docsResp = await api(page, 'get', `/api/v1/documents?course_id=${course.course_id}`, token)
  const docData = (await docsResp.json()).data
  const docs = (Array.isArray(docData) ? docData : docData?.items) || []
  const doc = docs.find((d: any) => d.extract_status === 'COMPLETED') || docs[0]
  expect(doc, '该课程下应有文档').toBeTruthy()
  return { course, doc }
}

test('教师端：7 个 Tab 与深链加载正常，控制台无错误', async ({ page }) => {
  test.setTimeout(120000)
  const errors: string[] = []
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
  page.on('pageerror', (e) => errors.push(String(e)))

  const token = await loginTeacher(page)
  const { course, doc } = await firstCourseAndDoc(page, token, false)
  const c = course.course_id
  const d = doc.doc_id

  await page.goto(`${BASE}/teacher?tab=courses`)
  await dismissBackendStatus(page)
  await expect(page.locator('.course-toolbar')).toBeVisible({ timeout: 20000 })

  await page.goto(`${BASE}/teacher?tab=documents&course_id=${c}`)
  await expect(page.locator('.el-table').first()).toBeVisible({ timeout: 20000 })

  // 回归：open=1 深链需自动选中首个可用文档（页面级文档请求与课程 watch 的竞态）
  await page.goto(`${BASE}/teacher?tab=preview&course_id=${c}&open=1`)
  await page.waitForURL(/document_id=\d+/, { timeout: 30000 })
  await expect(page.locator('canvas').first()).toBeVisible({ timeout: 40000 })

  await page.goto(`${BASE}/teacher?tab=preview&course_id=${c}&document_id=${d}`)
  await expect(page.locator('canvas').first()).toBeVisible({ timeout: 40000 })

  // 图谱编辑模式：三栏工作区可进入/退出
  await page.getByRole('button', { name: '编辑图谱' }).click()
  await expect(page.getByText(/知识点列表（\d+）/)).toBeVisible({ timeout: 20000 })
  await expect(page.locator('canvas').first()).toBeVisible({ timeout: 40000 })
  await page.getByRole('button', { name: '返回查看' }).click()
  await expect(page.getByRole('button', { name: '编辑图谱' })).toBeVisible({ timeout: 20000 })

  await page.goto(`${BASE}/teacher?tab=members&course_id=${c}`)
  await expect(page.getByText('总人数')).toBeVisible({ timeout: 20000 })

  await page.goto(`${BASE}/teacher?tab=monitor&course_id=${c}`)
  await expect(page.getByText('班级学习情况')).toBeVisible({ timeout: 20000 })

  await page.goto(`${BASE}/teacher?tab=questions&course_id=${c}`)
  await expect(page.getByText('题库总览')).toBeVisible({ timeout: 20000 })
  await expect(page.getByText('请先选择要管理题库的课程')).toHaveCount(0)

  // 题目表单 / 导入向导组件可打开与关闭（只读，不提交）
  await page.getByRole('button', { name: '新增题目' }).click()
  await expect(page.getByPlaceholder('请输入题干')).toBeVisible({ timeout: 20000 })
  await page.getByRole('dialog').getByRole('button', { name: '取消' }).click()
  await page.getByRole('button', { name: '从文档导入' }).click()
  await expect(page.getByText('解析预览')).toBeVisible({ timeout: 20000 })
  await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click()

  // 回归：原实现遗漏了 grading 的 activeTab 加载，直达时只有空表；现在应加载汇总
  await page.goto(`${BASE}/teacher?tab=grading&course_id=${c}`)
  await expect(page.getByText('批改进度')).toBeVisible({ timeout: 20000 })
  await expect(page.getByText('待批改作答')).toBeVisible({ timeout: 20000 })
  await expect(page.getByText('请先选择要批改的课程')).toHaveCount(0)

  await expect(page.locator('#pane-courses')).toHaveCount(1)
  await expect(page.locator('#pane-documents')).toHaveCount(1)
  await expect(page.locator('#pane-members')).toHaveCount(1)
  await expect(page.locator('#pane-preview')).toHaveCount(1)
  await expect(page.locator('#pane-monitor')).toHaveCount(1)
  await expect(page.locator('#pane-questions')).toHaveCount(1)
  await expect(page.locator('#pane-grading')).toHaveCount(1)

  expect(errors, `控制台不应报错：${errors.join(' | ')}`).toEqual([])
})

test('学生端：7 个 Tab 正常打开，图谱可渲染，控制台无错误', async ({ page }) => {
  test.setTimeout(120000)
  const errors: string[] = []
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
  page.on('pageerror', (e) => errors.push(String(e)))

  const token = await login(page, 'demo_student', 'demo123456')
  const { course, doc } = await firstCourseAndDoc(page, token, true)
  const q = `course_id=${course.course_id}&document_id=${doc.doc_id}`

  await page.goto(`${BASE}/student?tab=overview&${q}`)
  await dismissBackendStatus(page)
  await expect(page.locator('#pane-overview')).toBeVisible({ timeout: 20000 })
  await expect(page.getByText('学习总览').first()).toBeVisible()

  await page.goto(`${BASE}/student?tab=documents&${q}`)
  await expect(page.locator('#pane-documents')).toBeVisible({ timeout: 20000 })

  await page.goto(`${BASE}/student?tab=browse&${q}`)
  await expect(page.locator('canvas').first()).toBeVisible({ timeout: 40000 })

  await page.goto(`${BASE}/student?tab=qa&${q}`)
  await expect(page.getByText('AI 学习助手')).toBeVisible({ timeout: 20000 })

  await page.goto(`${BASE}/student?tab=path&${q}`)
  await expect(page.getByText('学习路径').first()).toBeVisible({ timeout: 20000 })

  await page.goto(`${BASE}/student?tab=favorites&${q}`)
  await expect(page.locator('#pane-favorites')).toBeVisible({ timeout: 20000 })

  await page.goto(`${BASE}/student?tab=practice&${q}`)
  await expect(page.getByText('练习设置')).toBeVisible({ timeout: 20000 })
  // 出题是只读操作：有题则进入答题卡，无题则显示空态
  await page.getByRole('button', { name: '开始练习' }).click()
  await expect(
    page.locator('.practice-card').or(page.getByText('还没有题目。请在上方设置出题条件后点击「开始练习」')),
  ).toBeVisible({ timeout: 20000 })

  expect(errors, `控制台不应报错：${errors.join(' | ')}`).toEqual([])
})
