import { reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../../api'
import { useLatestRequest } from '../useLatestRequest'

/** 教师端主观题批改：汇总、列表、单题/批量给分、学生筛选累积 */
export function useTeacherGrading({ courseId, active, onSummary }) {
  const gradingSummary = ref(null)
  const gradingList = ref([])
  const gradingLoading = ref(false)
  const gradingTotal = ref(0)
  const gradingPage = ref(1)
  const gradingPageSize = ref(20)
  const gradingDocFilter = ref('')
  const gradingStatus = ref('PENDING') // PENDING=待批改 / GRADED=已批改（复查与改判）
  const gradingStudentFilter = ref('')
  const gradingStudentOptions = ref([])
  let gradingOptCourse = null
  const gradingSelection = ref([])
  const gradingScore = reactive({}) // record_id -> 打分输入值
  const gradingBatchScore = ref(100)
  const gradingBatchComment = ref('')
  const request = useLatestRequest()

  function resetData() {
    gradingSummary.value = null
    gradingList.value = []
    gradingTotal.value = 0
    gradingSelection.value = []
    gradingLoading.value = false
    onSummary?.(0)
  }

  /** 待批改列表 + 进度汇总（进入批改 Tab / 刷新 / 批改后统一调用） */
  async function loadGrading() {
    const cid = String(courseId.value || '')
    if (!cid) {
      request.invalidate()
      resetData()
      return
    }
    const { token, signal } = request.beginRead()
    gradingLoading.value = true
    if (gradingOptCourse !== cid) {
      // 切换课程后重建学生候选与筛选
      gradingOptCourse = cid
      gradingStudentOptions.value = []
      gradingStudentFilter.value = ''
    }
    try {
      const [summary, data] = await Promise.all([
        api.getGradingSummary(cid, { signal }),
        api.getPendingGrades({
          course_id: cid,
          document_id: gradingDocFilter.value || undefined,
          student_id: gradingStudentFilter.value || undefined,
          status: gradingStatus.value,
          page: gradingPage.value,
          page_size: gradingPageSize.value,
        }, { signal }),
      ])
      if (!request.isLatest(token) || String(courseId.value) !== cid) return
      gradingSummary.value = summary
      gradingList.value = data.items || []
      gradingTotal.value = data.total || 0
      onSummary?.(summary?.pending ?? 0)
      gradingList.value.forEach((it) => {
        // 待批改默认满分；已批改预填当前分数（便于微调后改判）
        if (gradingStatus.value === 'GRADED') gradingScore[it.record_id] = Number(it.score ?? 0)
        else if (gradingScore[it.record_id] === undefined) gradingScore[it.record_id] = 100
        // 学生筛选下拉：累积本课程出现过的学生
        const sid = String(it.student_id ?? '')
        if (sid && !gradingStudentOptions.value.some((o) => o.value === sid)) {
          gradingStudentOptions.value.push({ value: sid, label: it.student_name || sid })
        }
      })
      gradingSelection.value = []
    } catch (e) {
      if (request.isLatest(token) && String(courseId.value) === cid) {
        resetData()
        ElMessage.warning(`批改数据加载失败：${e.message}`)
      }
    } finally {
      if (request.isLatest(token)) gradingLoading.value = false
    }
  }

  function onGradingSelection(rows) {
    gradingSelection.value = (rows || []).map((r) => r.record_id)
  }

  /** 切换待批改/已批改视图：回到第一页并清空选择（避免跨视图误批量） */
  function onGradingStatusChange() {
    gradingPage.value = 1
    gradingSelection.value = []
    loadGrading()
  }

  // 与原实现一致：只换筛选，不强制回到第一页（页码保留由用户自行控制）
  function changeDocumentFilter() {
    loadGrading()
  }

  function changeStudentFilter() {
    loadGrading()
  }

  /** 单题批改（就地更新分数与评语；后端按 >=60 记为答对，允许重批覆盖） */
  async function submitGrade(row) {
    const score = Number(gradingScore[row.record_id])
    if (Number.isNaN(score) || score < 0 || score > 100) {
      ElMessage.warning('请填写 0-100 的分数')
      return
    }
    let comment
    try {
      const { value } = await ElMessageBox.prompt(
        '可填写评语（可选，学生可见）',
        `批改 · ${row.student_name} · ${score} 分`,
        {
          confirmButtonText: '确认给分',
          cancelButtonText: '取消',
          inputPlaceholder: '评语（可选）',
          inputValue: '',
        },
      )
      comment = value || ''
    } catch {
      return // 教师取消：不写库
    }
    try {
      await api.gradeAnswer(row.record_id, score, comment || undefined)
      ElMessage.success(`已批改：${row.student_name} ${score} 分`)
      await loadGrading()
    } catch (e) {
      ElMessage.error(`批改失败：${e.message}`)
    }
  }

  /** 批量批改（同一分数与评语；客观题/越权记录由后端跳过并返回原因） */
  async function batchGrade() {
    if (!gradingSelection.value.length) return
    const score = Number(gradingBatchScore.value)
    if (Number.isNaN(score) || score < 0 || score > 100) {
      ElMessage.warning('请填写 0-100 的分数')
      return
    }
    try {
      const data = await api.gradeAnswersBatch(
        gradingSelection.value, score, gradingBatchComment.value || undefined,
      )
      ElMessage.success(`批量批改完成：更新 ${data.updated} 条，跳过 ${(data.skipped || []).length} 条`)
      await loadGrading()
    } catch (e) {
      ElMessage.error(`批量批改失败：${e.message}`)
    }
  }

  /** 从学生解答对象里取某个空位的作答（填空题形如 {"1": "浮点数"}） */
  function blankAnswerOf(row, key) {
    const ans = row?.user_answer
    if (!ans) return ''
    if (Array.isArray(ans)) return ans[key - 1] ?? ''
    if (typeof ans === 'object') return ans[String(key)] ?? ans[key] ?? ''
    return String(ans)
  }

  // 切课：作废旧响应，重置课程级筛选；在批改 Tab 内才立即重载
  watch(courseId, (cid) => {
    request.invalidate()
    resetData()
    gradingOptCourse = null
    gradingStudentOptions.value = []
    gradingStudentFilter.value = ''
    gradingPage.value = 1
    if (cid && active.value) loadGrading()
  }, { immediate: true })

  // 进入批改 Tab 刷新（原页在 activeTab watcher 中遗漏了 grading，这里补上；
  // 否则从侧栏直达批改页会一直停在初始空表，必须手动点「刷新」）
  watch(active, (isActive) => {
    if (isActive && courseId.value) loadGrading()
  })

  return {
    gradingSummary,
    gradingList,
    gradingLoading,
    gradingTotal,
    gradingPage,
    gradingPageSize,
    gradingDocFilter,
    gradingStatus,
    gradingStudentFilter,
    gradingStudentOptions,
    gradingSelection,
    gradingScore,
    gradingBatchScore,
    gradingBatchComment,
    loadGrading,
    onGradingSelection,
    onGradingStatusChange,
    changeDocumentFilter,
    changeStudentFilter,
    submitGrade,
    batchGrade,
    blankAnswerOf,
  }
}
