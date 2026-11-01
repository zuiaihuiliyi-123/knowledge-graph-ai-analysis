import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../../api'
import { useLatestRequest } from '../useLatestRequest'
import { questionTypeLabel } from '../../utils/question'

/**
 * 教师端题库：列表 / 总览 / 知识点覆盖率 / 知识点名称缓存 / 表单 CRUD。
 *
 * 扫描、自动标注、导入向导、收藏情况由各自弹窗组件维护，
 * 这里只负责「弹窗保存后刷新列表」这类页面级联动。
 */
export function useTeacherQuestions({ courseId, active, documents, ensureDocuments }) {
  const questionList = ref([])
  const questionsLoading = ref(false)
  const questionTotal = ref(0)
  const questionPage = ref(1)
  const questionPageSize = ref(10)
  const questionStats = ref(null)
  const questionDocFilter = ref('')
  const questionFilters = reactive({ q_type: '', keyword: '', is_active: '' })
  const questionCoverage = ref(null)
  const questionCoverageLoading = ref(false)
  const questionKpOptions = ref([])

  const formVisible = ref(false)
  const formSaving = ref(false)
  const editingQuestion = ref(null)
  const formPreset = ref(null)
  const autoLabelVisible = ref(false)
  const importVisible = ref(false)
  const favoritesVisible = ref(false)

  const listRequest = useLatestRequest()
  const statsRequest = useLatestRequest()
  const coverageRequest = useLatestRequest()
  const kpRequest = useLatestRequest()
  let kpInflight = null

  const danglingKpText = computed(() => {
    const list = questionCoverage.value?.dangling || []
    const names = list.slice(0, 5).map((d) => d.kp_id)
    return names.join('、') + (list.length > 5 ? ` 等 ${list.length} 个` : '')
  })

  function kpNameById(id) {
    return questionKpOptions.value.find((n) => n.id === id)?.label
  }

  /**
   * 关联知识点下拉：题库可能跨文档展示题目，因此聚合课程下全部文档的图谱节点。
   *
   * 刻意保留「每次进入题库都重取」的语义（图谱可能在别处被编辑/重新抽取，缓存会让
   * 知识点列和下拉长期显示旧名称）；只把同一课程+文档集合的并发请求合并成一次。
   */
  async function loadKpOptions() {
    const cid = String(courseId.value || '')
    const docs = documents.value || []
    if (!cid || !docs.length) {
      questionKpOptions.value = []
      return
    }
    const key = `${cid}|${docs.map((d) => d.doc_id).join(',')}`
    if (kpInflight && kpInflight.key === key) return kpInflight.promise

    const { token, signal } = kpRequest.beginRead()
    const promise = (async () => {
      try {
        const graphs = await Promise.all(
          docs.map((d) =>
            api.getGraphV1(cid, d.doc_id, { limit: 500 }, { signal })
              .catch(() => ({ nodes: [] })),
          ),
        )
        if (!kpRequest.isLatest(token) || String(courseId.value) !== cid) return
        const map = new Map()
        graphs.forEach((g) => {
          ;(g.nodes || []).forEach((n) => {
            if (n.id && !map.has(n.id)) map.set(n.id, { id: n.id, label: n.label })
          })
        })
        questionKpOptions.value = Array.from(map.values())
      } catch {
        if (kpRequest.isLatest(token)) questionKpOptions.value = []
      }
    })()
    kpInflight = { key, promise }
    try {
      return await promise
    } finally {
      if (kpInflight && kpInflight.promise === promise) kpInflight = null
    }
  }

  async function loadQuestions() {
    const cid = String(courseId.value || '')
    if (!cid) {
      listRequest.invalidate()
      questionList.value = []
      questionTotal.value = 0
      return
    }
    const { token, signal } = listRequest.beginRead()
    questionsLoading.value = true
    try {
      const data = await api.listQuestions({
        course_id: cid,
        document_id: questionDocFilter.value || undefined,
        q_type: questionFilters.q_type || undefined,
        keyword: questionFilters.keyword || undefined,
        is_active: questionFilters.is_active === '' ? undefined : questionFilters.is_active === '1',
        page: questionPage.value,
        page_size: questionPageSize.value,
      }, { signal })
      if (!listRequest.isLatest(token) || String(courseId.value) !== cid) return
      questionList.value = data.items || []
      questionTotal.value = data.total || 0
    } catch (e) {
      if (listRequest.isLatest(token) && String(courseId.value) === cid) {
        questionList.value = []
        questionTotal.value = 0
        ElMessage.warning(`题库加载失败：${e.message}`)
      }
    } finally {
      if (listRequest.isLatest(token)) questionsLoading.value = false
    }
  }

  async function loadQuestionStats() {
    const cid = String(courseId.value || '')
    if (!cid) {
      statsRequest.invalidate()
      questionStats.value = null
      return
    }
    const { token, signal } = statsRequest.beginRead()
    try {
      const data = await api.getQuestionStats(cid, { signal })
      if (!statsRequest.isLatest(token) || String(courseId.value) !== cid) return
      questionStats.value = data
    } catch {
      if (statsRequest.isLatest(token)) questionStats.value = null
    }
  }

  async function loadQuestionCoverage() {
    const cid = String(courseId.value || '')
    if (!cid) {
      coverageRequest.invalidate()
      questionCoverage.value = null
      return
    }
    const { token, signal } = coverageRequest.beginRead()
    questionCoverageLoading.value = true
    try {
      const data = await api.getQuestionCoverage(
        cid, questionDocFilter.value || undefined, { signal },
      )
      if (!coverageRequest.isLatest(token) || String(courseId.value) !== cid) return
      questionCoverage.value = data
    } catch (e) {
      if (coverageRequest.isLatest(token) && String(courseId.value) === cid) {
        questionCoverage.value = null
        ElMessage.warning(`覆盖率加载失败：${e.message}`)
      }
    } finally {
      if (coverageRequest.isLatest(token)) questionCoverageLoading.value = false
    }
  }

  /** 进入题库 / 切换筛选时统一刷新（列表 + 总览 + 覆盖率 + 知识点下拉） */
  async function refreshAll({ resetPage = true } = {}) {
    if (resetPage) questionPage.value = 1
    // 原版会在文档列表为空时兜底拉一次；页面级列表失败后，进入题库也能自愈
    if (String(courseId.value || '') && !(documents.value || []).length) ensureDocuments?.()
    loadKpOptions()
    await Promise.all([loadQuestions(), loadQuestionStats(), loadQuestionCoverage()])
  }

  function onPageChange() {
    loadQuestions()
  }

  function onPageSizeChange() {
    questionPage.value = 1
    refreshAll({ resetPage: false })
  }

  // ---------------- 表单 / CRUD ----------------
  function openQuestionForm(row = null) {
    editingQuestion.value = row
    formPreset.value = null
    formVisible.value = true
    if (!questionKpOptions.value.length) loadKpOptions()
  }

  /** 覆盖率卡片点击「无题知识点」→ 打开新增表单并预填该知识点 */
  function openQuestionForKp(kp) {
    editingQuestion.value = null
    formPreset.value = kp
      ? { kp_id: kp.kp_id || '', document_id: kp.document_id ? String(kp.document_id) : '' }
      : null
    formVisible.value = true
    // 目标知识点不在下拉里时也重取（图谱可能刚更新过）
    const known = (questionKpOptions.value || [])
      .some((n) => String(n.id) === String(kp?.kp_id))
    if (!questionKpOptions.value.length || !known) loadKpOptions()
  }

  async function saveQuestion(payload) {
    const cid = String(courseId.value || '')
    if (!cid) return
    formSaving.value = true
    try {
      if (editingQuestion.value) {
        await api.updateQuestion(editingQuestion.value.question_id, payload)
        ElMessage.success('题目已更新')
      } else {
        await api.createQuestion({ course_id: cid, ...payload })
        ElMessage.success('题目已新增')
      }
      formVisible.value = false
      await refreshAll()
    } catch (e) {
      ElMessage.error(`保存失败：${e.message}`)
    } finally {
      formSaving.value = false
    }
  }

  async function toggleQuestionActive(row) {
    try {
      await api.setQuestionActive(row.question_id, !row.is_active)
      ElMessage.success(row.is_active ? '题目已停用（移出出题池）' : '题目已启用')
      await refreshAll()
    } catch (e) {
      ElMessage.error(`操作失败：${e.message}`)
    }
  }

  async function removeQuestion(row) {
    try {
      await ElMessageBox.confirm(
        '删除后学生将无法再做该题；若已有作答记录，系统会自动改为「停用」以保护答题数据。是否继续？',
        '删除题目',
        { type: 'warning' },
      )
    } catch {
      return
    }
    try {
      const data = await api.deleteQuestion(row.question_id)
      ElMessage.success(data?.soft_deleted ? '该题已有作答记录，已停用（未物理删除）' : '题目已删除')
      await refreshAll()
    } catch (e) {
      ElMessage.error(`删除失败：${e.message}`)
    }
  }

  // 切课：失效所有在途读请求并清空课程级数据；题库筛选条件保留（与原页一致）
  watch(courseId, (cid) => {
    listRequest.invalidate()
    statsRequest.invalidate()
    coverageRequest.invalidate()
    kpRequest.invalidate()
    questionList.value = []
    questionTotal.value = 0
    questionStats.value = null
    questionCoverage.value = null
    questionKpOptions.value = []
    questionsLoading.value = false
    questionCoverageLoading.value = false
    kpInflight = null
    questionDocFilter.value = ''
    questionPage.value = 1
    if (cid && active.value) refreshAll({ resetPage: false })
  }, { immediate: true })

  // 进入题库 Tab 时刷新（与原页 activeTab watcher 一致）
  watch(active, (isActive) => {
    if (isActive && courseId.value) refreshAll()
  })

  // 文档列表晚于课程加载完成：补齐知识点下拉（不重复请求整页数据）
  watch(
    () => (documents.value || []).map((d) => d.doc_id).join(','),
    () => {
      if (active.value && courseId.value) loadKpOptions()
    },
  )

  return {
    questionList,
    questionsLoading,
    questionTotal,
    questionPage,
    questionPageSize,
    questionStats,
    questionDocFilter,
    questionFilters,
    questionCoverage,
    questionCoverageLoading,
    questionKpOptions,
    formVisible,
    formSaving,
    editingQuestion,
    formPreset,
    autoLabelVisible,
    importVisible,
    favoritesVisible,
    danglingKpText,
    kpNameById,
    loadQuestions,
    loadQuestionStats,
    loadQuestionCoverage,
    loadKpOptions,
    refreshAll,
    onPageChange,
    onPageSizeChange,
    openQuestionForm,
    openQuestionForKp,
    saveQuestion,
    toggleQuestionActive,
    removeQuestion,
    questionTypeLabel,
  }
}
