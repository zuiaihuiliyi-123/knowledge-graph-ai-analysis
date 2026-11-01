import { test, expect } from '@playwright/test'
import {
  BASE, RUN, loginTeacher, api, dismissBackendStatus, dismissJoinCodeAlert, clickRadio,
} from './helpers'

/**
 * 写操作 E2E：新建/改名/删除课程 + 新增/编辑/删除题目。
 *
 * ⚠️ 必须在**副本库**上跑（spec 会真实删除数据）：
 *   1) 停掉正在跑的后端，复制一份数据库：
 *        Copy-Item $env:USERPROFILE\..\kg-data\app.db ..\kg-data\app-e2e.db
 *   2) 用副本库启动后端（Windows PowerShell）：
 *        cd backend
 *        $env:SQLITE_DB_PATH='../../kg-data/app-e2e.db'; $env:RECONCILE_ON_STARTUP='0'
 *        python -m uvicorn app.main:app --port 8000
 *   3) 前端照常 `npm run dev`，再跑本 spec：
 *        npx playwright test tests/write-ops.spec.ts --project=chromium
 *   4) 跑完删掉副本库 `kg-data/app-e2e.db`，真实数据未被触碰。
 *
 * 为什么不用主库：删除课程是级联删除（文档/图谱/记录/成员/邀请），一旦断言写错就会
 * 把演示数据真删掉；副本库让写操作测试变成「可随意重跑」的。
 *
 * 覆盖的坑：
 * - 新建课程后会弹加课码提示框，必须先关掉再点下一张卡片，否则点击被遮罩拦截；
 * - 课程卡片/列表两种视图并存（localStorage `kg_course_view_mode`），这里显式切到卡片视图；
 * - Element Plus 的 radio 内层 span 拦截指针事件，统一走 clickRadio 点 label。
 */

const COURSE_NAME = `E2E 写操作 ${RUN}`
const COURSE_RENAMED = `E2E 写操作改名 ${RUN}`
const STEM = `E2E 判断题 ${RUN}：链表属于线性结构。`
const STEM_EDITED = `E2E 判断题 ${RUN}：链表属于线性结构（已编辑）。`

test('写操作：新建 → 改名 → 加题 → 改题 → 删题 → 删课程', async ({ page }) => {
  // 多步骤真实写入（含 6 次接口往返 + 前端重渲染），30s 默认超时不够用
  test.setTimeout(180000)
  page.setDefaultTimeout(20000)
  const errors: string[] = []
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })

  const token = await loginTeacher(page)
  // 固定卡片视图，避免本机遗留的「列表视图」导致选择器差异
  await page.evaluate(() => localStorage.setItem('kg_course_view_mode', 'card'))

  // ---------- 1) 新建课程 ----------
  await page.goto(`${BASE}/teacher?tab=courses`)
  await dismissBackendStatus(page)
  await page.locator('.course-toolbar').getByRole('button', { name: '新建课程' }).click()

  const createDialog = page.getByRole('dialog').filter({ hasText: '新建课程' })
  await createDialog.getByPlaceholder('例如：数据结构').fill(COURSE_NAME)
  await createDialog.getByRole('button', { name: '创建' }).click()
  await dismissJoinCodeAlert(page) // 加课码提示框：不关掉会以遮罩拦截后续点击

  const card = page.locator('.course-card').filter({ hasText: COURSE_NAME })
  await expect(card, '新建的课程应出现在课程列表').toHaveCount(1, { timeout: 20000 })

  // 新课程必须能出现在后端列表（前端乐观更新的反证）
  const listResp = await api(page, 'get', '/api/v1/courses', token)
  const listBody = await listResp.json()
  const created = (listBody?.data?.items || []).find((c) => c.course_name === COURSE_NAME)
  expect(created, '新建课程应已落库').toBeTruthy()
  const courseId = created.course_id

  // ---------- 2) 改名（课程设置） ----------
  await card.getByRole('button', { name: '设置' }).click()
  const settingsDialog = page.getByRole('dialog').filter({ hasText: '课程设置' })
  const nameInput = settingsDialog
    .locator('.el-form-item')
    .filter({ hasText: '课程名称' })
    .locator('input')
  await nameInput.fill(COURSE_RENAMED)
  await settingsDialog.getByRole('button', { name: '保存' }).click()

  const renamedCard = page.locator('.course-card').filter({ hasText: COURSE_RENAMED })
  await expect(renamedCard, '改名后卡片应显示新名称').toHaveCount(1, { timeout: 20000 })
  await expect(page.locator('.course-card').filter({ hasText: COURSE_NAME })
    .filter({ hasNotText: '改名' }), '旧名称不应残留').toHaveCount(0)

  // ---------- 3) 新增题目（判断题，无需上传文档，不走 LLM） ----------
  await page.goto(`${BASE}/teacher?tab=questions&course_id=${courseId}`)
  await dismissBackendStatus(page)
  await expect(page.getByText('题库总览')).toBeVisible({ timeout: 20000 })

  await page.getByRole('button', { name: '新增题目' }).click()
  const qDialog = page.getByRole('dialog').filter({ hasText: '新增题目' })
  await clickRadio(qDialog, '判断题')
  await qDialog.getByPlaceholder('请输入题干').fill(STEM)
  await clickRadio(qDialog, '正确')
  await qDialog.getByRole('button', { name: '保存' }).click()

  const qRow = page.locator('.el-table__row').filter({ hasText: STEM })
  await expect(qRow, '新题目应出现在题库列表').toHaveCount(1, { timeout: 20000 })

  // ---------- 4) 编辑题目 ----------
  await qRow.getByRole('button', { name: '编辑' }).click()
  const editDialog = page.getByRole('dialog').filter({ hasText: '编辑题目' })
  await editDialog.getByPlaceholder('请输入题干').fill(STEM_EDITED)
  await editDialog.getByRole('button', { name: '保存' }).click()

  const editedRow = page.locator('.el-table__row').filter({ hasText: STEM_EDITED })
  await expect(editedRow, '编辑后列表应显示新题干').toHaveCount(1, { timeout: 20000 })

  // ---------- 5) 删除题目（有确认框） ----------
  await editedRow.getByRole('button', { name: '删除' }).click()
  await page.locator('.el-message-box').getByRole('button', { name: '确定' }).click()
  await expect(page.locator('.el-table__row').filter({ hasText: STEM_EDITED }),
    '删除后题目应从列表消失').toHaveCount(0, { timeout: 20000 })

  // ---------- 6) 删除课程（级联删除，故必须用副本库） ----------
  await page.goto(`${BASE}/teacher?tab=courses`)
  await dismissBackendStatus(page)
  const targetCard = page.locator('.course-card').filter({ hasText: COURSE_RENAMED })
  await expect(targetCard).toHaveCount(1, { timeout: 20000 })
  await targetCard.getByRole('button', { name: '删除' }).click()
  await page.locator('.el-message-box').getByRole('button', { name: '删除' }).click()
  await expect(page.locator('.course-card').filter({ hasText: COURSE_RENAMED }),
    '删除后课程卡片应消失').toHaveCount(0, { timeout: 20000 })

  const afterResp = await api(page, 'get', '/api/v1/courses', token)
  const afterBody = await afterResp.json()
  const stillThere = (afterBody?.data?.items || []).some((c) => c.course_name === COURSE_RENAMED)
  expect(stillThere, '删除后课程不应再出现在后端列表').toBe(false)

  expect(errors, `控制台不应报错：${errors.join(' | ')}`).toEqual([])
})
