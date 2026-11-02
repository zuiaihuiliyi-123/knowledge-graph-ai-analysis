import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { CircleCheckFilled, Clock, WarningFilled } from '@element-plus/icons-vue'
import { api } from '../../api'
import { emptyAnswerFor, formatAnswer } from '../../utils/question'
import { useLatestRequest } from '../useLatestRequest'

export const PRACTICE_MODES = [
  { value: 'mixed', label: '分层组卷' },
  { value: 'weak', label: '薄弱强化' },
  { value: 'review', label: '复习巩固' },
  { value: 'new', label: '路径新知识' },
  { value: 'advanced', label: '进阶提升' },
  { value: 'random', label: '随机练习' },
]

/** 「按推荐知识点出题」最多顺延尝试几个推荐项 */
const RECO_MAX_TRIES = 5

/**
 * 学生端做题练习：出题 / 智能推荐 / 作答提交 / 错题本 / 题目收藏。
 *
 * 写操作（提交答案、收藏）不因新请求或上下文切换而取消；
 * 展示态只在「上下文未变 + 请求仍是最新」时更新，避免串题。
 */
export function useStudentPractice({ courseId, documentId, ctxKey, active, graphNodes }) {
  const practiceSubTab = ref('doing')
  const practiceScope = ref('document')
  const practiceQType = ref('')
  const practiceCount = ref(10)
  const practiceKpId = ref('')
  const practiceKpOptions = ref([])
  const practiceList = ref([])
  const practiceIndex = ref(0)
  const practiceLoading = ref(false)
  const practiceSubmitting = ref(false)
  const practiceMode = ref('mixed')
  const practiceRecommendMeta = ref(null)
  const practiceStats = ref(null)
  const practiceStatsLoading = ref(false)
  const practiceAnswers = reactive({})
  const practiceResults = reactive({})
  const practiceFavoriteIds = ref([])
  const wrongList = ref([])
  const wrongLoading = ref(false)
  const myQuestionFavs = ref([])
  const myQuestionFavLoading = ref(false)

  const actionRequest = useLatestRequest()
  const statsRequest = useLatestRequest()
  const wrongRequest = useLatestRequest()
  const favoriteListRequest = useLatestRequest()
  const optionsRequest = useLatestRequest()
  const favoritePending = new Set()

  const currentPracticeQuestion = computed(() => practiceList.value[practiceIndex.value] || null)

  const practiceRecommendHint = computed(() => {
    const meta = practiceRecommendMeta.value
    if (!meta) return ''
    const label = PRACTICE_MODES.find((m) => m.value === meta.mode)?.label || meta.mode
    const buckets = Object.entries(meta.buckets || {})
      .filter(([k, v]) => v > 0 && k !== 'quota_relaxed')
      .map(([k, v]) => `${({ weak: '薄弱', review: '复习', new: '新知识', advanced: '进阶', filled: '回填', random: '随机' })[k] || k} ${v}`)
      .join(' / ')
    const parts = [`智能推荐（${label}）`, buckets && `构成：${buckets}`,
      meta.mastery_available ? '依据：掌握度+作答历史' : '依据：证据不足（按摸底推荐）']
    if (meta.bucket_empty) parts.push('该类型当前无题，已用其他类型回填')
    if (!meta.graph_available) parts.push('图谱不可用（未纳入知识点重要性）')
    return parts.filter(Boolean).join(' · ')
  })

  function isPracticeFavorited(q) {
    return !!q && practiceFavoriteIds.value.includes(q.question_id)
  }

  /** 主观题提交后进入待批改：结果区文案 / 图标 / 配色（与客观题共用同一容器） */
  function resultText(qid) {
    const r = practiceResults[qid] || {}
    if (r.pending) return '已提交，等待教师批改'
    return r.is_correct ? '回答正确' : '回答错误'
  }

  function resultIcon(qid) {
    const r = practiceResults[qid] || {}
    if (r.pending) return Clock
    return r.is_correct ? CircleCheckFilled : WarningFilled
  }

  function resultClass(qid) {
    const r = practiceResults[qid]
    if (!r) return ''
    if (r.pending) return 'pending'
    return r.is_correct ? 'ok' : 'bad'
  }

  /** 知识点下拉：优先复用页面已加载的文档图谱，避免重复请求 */
  async function ensurePracticeKpOptions() {
    const cid = String(courseId.value || '')
    const did = String(documentId.value || '')
    const key = ctxKey.value
    if (!cid || !did) {
      practiceKpOptions.value = []
      return practiceKpOptions.value
    }
    if (practiceKpOptions.value.length) return practiceKpOptions.value
    if ((graphNodes.value || []).length) {
      practiceKpOptions.value = graphNodes.value
      return practiceKpOptions.value
    }
    const { token, signal } = optionsRequest.beginRead()
    try {
      const g = await api.getGraphV1(cid, did, { limit: 500 }, { signal })
      if (!optionsRequest.isLatest(token) || ctxKey.value !== key) return practiceKpOptions.value
      practiceKpOptions.value = g.nodes || []
    } catch {
      if (optionsRequest.isLatest(token) && ctxKey.value === key) practiceKpOptions.value = []
    }
    return practiceKpOptions.value
  }

  async function loadPracticeStats() {
    const cid = String(courseId.value || '')
    const key = ctxKey.value
    if (!cid) {
      statsRequest.invalidate()
      practiceStats.value = null
      return
    }
    const { token, signal } = statsRequest.beginRead()
    practiceStatsLoading.value = true
    try {
      const data = await api.getPracticeStats({
        course_id: cid,
        document_id: practiceScope.value === 'document' ? documentId.value : undefined,
      }, { signal })
      if (!statsRequest.isLatest(token) || ctxKey.value !== key) return
      practiceStats.value = data
    } catch {
      if (statsRequest.isLatest(token) && ctxKey.value === key) practiceStats.value = null
    } finally {
      if (statsRequest.isLatest(token)) practiceStatsLoading.value = false
    }
  }

  /** 按当前出题条件取一批题目（只取数、不改状态；「按推荐知识点出题」复用） */
  async function fetchPracticeItems({ kpId } = {}) {
    return api.getPracticeQuestions({
      course_id: courseId.value,
      document_id: practiceScope.value === 'document' ? documentId.value : undefined,
      kp_id: kpId || undefined,
      q_type: practiceQType.value || undefined,
      count: practiceCount.value,
    })
  }

  /** 把一批题目装载为当前练习（清空上一组作答与判定，避免串题） */
  function applyPracticeQuestions(items) {
    practiceList.value = items
    practiceIndex.value = 0
    Object.keys(practiceAnswers).forEach((k) => delete practiceAnswers[k])
    Object.keys(practiceResults).forEach((k) => delete practiceResults[k])
    practiceList.value.forEach((q) => {
      practiceAnswers[q.question_id] = emptyAnswerFor(q)
    })
    practiceFavoriteIds.value = practiceList.value
      .filter((q) => q.is_favorited).map((q) => q.question_id)
    practiceSubTab.value = 'doing'
  }

  async function startPractice() {
    if (!courseId.value) {
      ElMessage.warning('请先选择课程和学习资料')
      return
    }
    const key = ctxKey.value
    const token = actionRequest.begin()
    practiceLoading.value = true
    try {
      const data = await fetchPracticeItems({ kpId: practiceKpId.value })
      if (!actionRequest.isLatest(token) || ctxKey.value !== key) return
      applyPracticeQuestions(data.items || [])
      practiceRecommendMeta.value = null
      if (!practiceList.value.length) {
        ElMessage.info('该范围内暂无题目，请更换出题范围或联系教师添加题目')
      }
    } catch (e) {
      if (actionRequest.isLatest(token)) ElMessage.error(`出题失败：${e.message}`)
    } finally {
      if (actionRequest.isLatest(token)) practiceLoading.value = false
    }
  }

  /** 智能推荐出题（按学情选卷：掌握度/遗忘/难度适配/错题/图谱重要性…） */
  async function startSmartPractice() {
    if (!courseId.value) {
      ElMessage.warning('请先选择课程和学习资料')
      return
    }
    const key = ctxKey.value
    const token = actionRequest.begin()
    practiceLoading.value = true
    try {
      const data = await api.recommendQuestions({
        course_id: courseId.value,
        document_id: practiceScope.value === 'document' ? documentId.value : undefined,
        kp_id: practiceKpId.value || undefined,
        q_type: practiceQType.value || undefined,
        count: practiceCount.value,
        mode: practiceMode.value,
      })
      if (!actionRequest.isLatest(token) || ctxKey.value !== key) return
      applyPracticeQuestions(data.items || [])
      practiceRecommendMeta.value = data.meta || null
      const label = PRACTICE_MODES.find((m) => m.value === practiceMode.value)?.label
      if (data.count) ElMessage.success(`已按「${label}」推荐 ${data.count} 道题`)
      else ElMessage.info('该范围内暂无可用题目')
    } catch (e) {
      if (actionRequest.isLatest(token)) ElMessage.error(`智能推荐失败：${e.message}`)
    } finally {
      if (actionRequest.isLatest(token)) practiceLoading.value = false
    }
  }

  /** 按学习路径推荐的知识点出题（推荐 → 练题闭环） */
  async function practiceByRecommendation() {
    if (!courseId.value || !documentId.value) {
      ElMessage.warning('请先选择课程和学习资料')
      return
    }
    const key = ctxKey.value
    const cid = courseId.value
    const did = documentId.value
    const token = actionRequest.begin()
    practiceLoading.value = true
    try {
      const nodes = await ensurePracticeKpOptions()
      const progress = await api.getProgress(cid, did)
      const nameOf = (kpId) => nodes.find((n) => String(n.id) === String(kpId))?.label
      const mastered = (progress.mastered_kp_ids || []).map(nameOf).filter(Boolean)
      // /learning-path/recommend 的 data 是 { mastered, recommendations } 对象而非数组
      const res = await api.recommendNext(mastered, cid, did)
      if (!actionRequest.isLatest(token) || ctxKey.value !== key) return
      const recs = (res?.recommendations || []).filter((r) => r && r.name)
      if (!recs.length) {
        ElMessage.info('暂无可推荐的知识点，请先标记已掌握的知识点或查看学习路径')
        return
      }

      // 推荐给的是「下一步该学」，不保证已经出过题：按顺序逐个试，取第一个真能抽到题的
      for (const r of recs.slice(0, RECO_MAX_TRIES)) {
        const target = nodes.find((n) => n.label === r.name)
        if (!target) continue
        const data = await fetchPracticeItems({ kpId: target.id })
        if (!actionRequest.isLatest(token) || ctxKey.value !== key) return
        const items = data.items || []
        if (!items.length) continue
        practiceKpId.value = target.id
        applyPracticeQuestions(items)
        practiceRecommendMeta.value = null
        ElMessage.success(`已按推荐知识点「${r.name}」出题（${items.length} 题）`)
        return
      }

      // 全部落空才退化为「不带知识点条件」出题——只放宽知识点这一维
      practiceKpId.value = ''
      const data = await fetchPracticeItems({})
      if (!actionRequest.isLatest(token) || ctxKey.value !== key) return
      const items = data.items || []
      applyPracticeQuestions(items)
      practiceRecommendMeta.value = null
      if (items.length) {
        ElMessage.warning(`推荐的知识点暂未出题，已改为按其他条件出题（${items.length} 题）`)
      } else {
        ElMessage.info('该范围内暂无题目，请更换出题范围或联系教师添加题目')
      }
    } catch (e) {
      if (actionRequest.isLatest(token)) ElMessage.error(`推荐出题失败：${e.message}`)
    } finally {
      if (actionRequest.isLatest(token)) practiceLoading.value = false
    }
  }

  /** 提交当前题：服务端判分 + 落答题记录，返回正确答案与解析后展示 */
  async function submitCurrent() {
    const q = currentPracticeQuestion.value
    if (!q) return
    const answer = practiceAnswers[q.question_id]
    // 填空题答案是「各空对象」，需所有空位都空才算未作答
    const isBlankObject = answer && typeof answer === 'object' && !Array.isArray(answer)
    const empty = answer === undefined || answer === null || answer === ''
      || (Array.isArray(answer) && !answer.length)
      || (isBlankObject && !Object.values(answer).some((v) => String(v ?? '').trim()))
    if (empty) {
      ElMessage.warning(q.q_type === 'FILL' ? '请至少填写一个空位' : '请先作答再提交')
      return
    }
    const key = ctxKey.value
    practiceSubmitting.value = true
    try {
      const data = await api.submitAnswer(q.question_id, answer)
      // 写操作已完成；只有上下文没变时才把结果挂回当前题
      if (ctxKey.value === key) {
        practiceResults[q.question_id] = data
        if (data.pending) ElMessage.success('已提交，等待教师批改')
        else if (data.is_correct) ElMessage.success('回答正确 🎉')
        else ElMessage.warning('回答错误，看看解析再试')
        loadPracticeStats()
      }
    } catch (e) {
      ElMessage.error(`提交失败：${e.message}`)
    } finally {
      practiceSubmitting.value = false
    }
  }

  /** 收藏 / 取消收藏题目（幂等；同一题在途时忽略重复点击） */
  async function togglePracticeFavorite(q) {
    if (!q || !courseId.value) return
    const qid = q.question_id
    const key = ctxKey.value
    const cid = courseId.value
    if (favoritePending.has(qid)) return
    favoritePending.add(qid)
    try {
      if (isPracticeFavorited(q)) {
        await api.unfavoriteQuestion(cid, qid)
        // 写操作已完成；上下文已切走时不再改当前页面的收藏列表
        if (ctxKey.value === key) {
          practiceFavoriteIds.value = practiceFavoriteIds.value.filter((id) => id !== qid)
          myQuestionFavs.value = myQuestionFavs.value.filter((x) => x.question_id !== qid)
        }
        ElMessage.success('已取消收藏')
      } else {
        await api.favoriteQuestion(cid, qid)
        if (ctxKey.value === key) {
          practiceFavoriteIds.value = [...practiceFavoriteIds.value, qid]
        }
        ElMessage.success('已收藏题目')
      }
      if (ctxKey.value === key) loadPracticeStats()
    } catch (e) {
      ElMessage.error(`操作失败：${e.message}`)
    } finally {
      favoritePending.delete(qid)
    }
  }

  /** 错题本：按题取最近一次答错（含正确答案/解析/知识点名称） */
  async function loadWrongBook() {
    const cid = String(courseId.value || '')
    const key = ctxKey.value
    if (!cid) {
      wrongRequest.invalidate()
      wrongList.value = []
      return
    }
    const { token, signal } = wrongRequest.beginRead()
    wrongLoading.value = true
    try {
      const data = await api.getWrongBook({
        course_id: cid,
        document_id: practiceScope.value === 'document' ? documentId.value : undefined,
      }, { signal })
      if (!wrongRequest.isLatest(token) || ctxKey.value !== key) return
      wrongList.value = data.items || []
    } catch (e) {
      if (!wrongRequest.isLatest(token) || ctxKey.value !== key) return
      wrongList.value = []
      ElMessage.warning(`错题本加载失败：${e.message}`)
    } finally {
      if (wrongRequest.isLatest(token)) wrongLoading.value = false
    }
  }

  /** 我收藏的题目（本课程） */
  async function loadMyQuestionFavorites() {
    const cid = String(courseId.value || '')
    const key = ctxKey.value
    if (!cid) {
      favoriteListRequest.invalidate()
      myQuestionFavs.value = []
      return
    }
    const { token, signal } = favoriteListRequest.beginRead()
    myQuestionFavLoading.value = true
    try {
      const data = await api.getQuestionFavList(cid, { signal })
      if (!favoriteListRequest.isLatest(token) || ctxKey.value !== key) return
      myQuestionFavs.value = data.items || []
      // 同一课程内「我的收藏」是全量集合，可安全用于覆盖收藏标记
      practiceFavoriteIds.value = myQuestionFavs.value.map((q) => q.question_id)
    } catch (e) {
      if (!favoriteListRequest.isLatest(token) || ctxKey.value !== key) return
      myQuestionFavs.value = []
      ElMessage.warning(`收藏题目加载失败：${e.message}`)
    } finally {
      if (favoriteListRequest.isLatest(token)) myQuestionFavLoading.value = false
    }
  }

  /** 进入练习 Tab / 切换上下文时统一刷新（统计 + 错题本 + 收藏题目 + 知识点下拉） */
  async function loadPracticeTab() {
    ensurePracticeKpOptions()
    await Promise.all([loadPracticeStats(), loadWrongBook(), loadMyQuestionFavorites()])
  }

  /** 从错题本 / 收藏题目直接重做：载入单题并切回答题子页 */
  function redoQuestion(q) {
    if (!q) return
    practiceList.value = [{ ...q }]
    practiceIndex.value = 0
    practiceAnswers[q.question_id] = emptyAnswerFor(q)
    delete practiceResults[q.question_id]
    practiceSubTab.value = 'doing'
  }

  // 子页切换：按需刷新错题本 / 收藏题目
  watch(practiceSubTab, (sub) => {
    if (sub === 'wrong') loadWrongBook()
    else if (sub === 'fav') loadMyQuestionFavorites()
  })

  // 学习上下文切换：清空作答/判定/题目与知识点下拉（缓存按作用域隔离，不跨文档残留）
  watch(ctxKey, (key) => {
    // 所有「按文档」的读请求一起作废，避免切换资料后旧响应写进新上下文
    actionRequest.invalidate()
    statsRequest.invalidate()
    wrongRequest.invalidate()
    favoriteListRequest.invalidate()
    optionsRequest.invalidate()
    practiceList.value = []
    practiceIndex.value = 0
    practiceKpId.value = ''
    practiceKpOptions.value = []
    practiceRecommendMeta.value = null
    practiceLoading.value = false
    practiceStatsLoading.value = false
    wrongLoading.value = false
    myQuestionFavLoading.value = false
    Object.keys(practiceAnswers).forEach((k) => delete practiceAnswers[k])
    Object.keys(practiceResults).forEach((k) => delete practiceResults[k])
    if (!key) {
      // 题目收藏是课程级数据：换文档时先保留旧值（下一轮 loadPracticeTab 会覆盖），
      // 只有完全清空上下文时才一并清掉
      practiceFavoriteIds.value = []
      practiceStats.value = null
      wrongList.value = []
      myQuestionFavs.value = []
    } else if (active.value) {
      loadPracticeTab()
    }
  }, { immediate: true })

  // 进入练习 Tab 刷新（与原页 watch(activeTab) 的练习分支一致）
  watch(active, (isActive) => {
    if (isActive && ctxKey.value) loadPracticeTab()
  })

  // 页面级图谱晚于练习 Tab 加载完成时，复用它的节点作为知识点下拉
  watch(graphNodes, (nodes) => {
    if (nodes?.length && !practiceKpOptions.value.length) practiceKpOptions.value = nodes
  })

  return {
    PRACTICE_MODES,
    practiceSubTab,
    practiceScope,
    practiceQType,
    practiceCount,
    practiceKpId,
    practiceKpOptions,
    practiceList,
    practiceIndex,
    practiceLoading,
    practiceSubmitting,
    practiceMode,
    practiceRecommendMeta,
    practiceRecommendHint,
    practiceStats,
    practiceStatsLoading,
    practiceAnswers,
    practiceResults,
    practiceFavoriteIds,
    wrongList,
    wrongLoading,
    myQuestionFavs,
    myQuestionFavLoading,
    currentPracticeQuestion,
    formatAnswer,
    resultText,
    resultIcon,
    resultClass,
    isPracticeFavorited,
    ensurePracticeKpOptions,
    loadPracticeStats,
    startPractice,
    startSmartPractice,
    practiceByRecommendation,
    submitCurrent,
    togglePracticeFavorite,
    loadWrongBook,
    loadMyQuestionFavorites,
    loadPracticeTab,
    redoQuestion,
  }
}
