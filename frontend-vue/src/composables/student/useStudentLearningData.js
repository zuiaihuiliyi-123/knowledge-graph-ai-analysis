import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Checked, Collection, Odometer, StarFilled } from '@element-plus/icons-vue'
import { api } from '../../api'
import { useLatestRequest } from '../useLatestRequest'

/**
 * 学生端页面级学习数据：
 * - 当前文档图谱 / 学习进度 / 推荐（学习路径与总览的唯一数据源）
 * - 掌握标记、知识点收藏（按 `${courseId}:${documentId}` 作用域缓存）
 * - 节点详情抽屉与图上一阶邻接
 *
 * 所有读请求带「上下文 key + 请求序号」双重校验：快速切换资料时，
 * 旧响应既不会写进新上下文，也不会在组件卸载后继续改状态。
 */
export function useStudentLearningData({ currentCourseId, currentDocumentId, ctxKey }) {
  const graphNodes = ref([])
  const graphEdges = ref([])
  const pathRecs = ref([])
  const currentKnowledgePointId = ref(null)
  const recommendedKnowledgePointIds = ref([])
  const pathDataLoading = ref(false)
  const pathDataChecked = ref(false)
  // 图谱/进度是否已结束（不含随后的推荐）。收藏夹等只需要节点的模块用它，
  // 避免被推荐接口的延迟/失败连带卡住。
  const graphDataChecked = ref(false)

  const masteredCache = reactive({})
  const masteryLoading = ref(false)
  const favoriteCache = reactive({})
  const favoritesLoading = ref(false)
  const favoritesLoaded = ref(false)
  const favoriteLoading = ref(false)

  const drawerVisible = ref(false)
  const drawerNode = ref(null)
  const drawerRelated = ref([])
  const drawerPredecessors = ref([])
  // 后继知识与前置对称展示：此前被并进 related，详情抽屉里看不出来
  const drawerSuccessors = ref([])
  const drawerExpanded = ref(false)

  const pathRequest = useLatestRequest()
  const favoritesRequest = useLatestRequest()

  const masteredKpIds = computed(() => masteredCache[ctxKey.value] || [])
  const pathMasteredIds = computed(() => masteredCache[ctxKey.value] || [])
  const favoriteIds = computed(() => favoriteCache[ctxKey.value] || [])

  async function loadPathData() {
    const key = ctxKey.value
    const cid = currentCourseId.value
    if (!key || !cid) {
      pathRequest.invalidate()
      graphNodes.value = []
      graphEdges.value = []
      pathRecs.value = []
      currentKnowledgePointId.value = null
      recommendedKnowledgePointIds.value = []
      pathDataChecked.value = false
      graphDataChecked.value = false
      pathDataLoading.value = false
      return
    }
    const { token, signal } = pathRequest.beginRead()
    pathDataLoading.value = true
    graphDataChecked.value = false
    try {
      const [graph, progress] = await Promise.all([
        api.getGraphV1(cid, currentDocumentId.value, { limit: 800 }, { signal }),
        api.getProgress(cid, currentDocumentId.value, { signal }),
      ])
      if (!pathRequest.isLatest(token) || ctxKey.value !== key) return
      graphNodes.value = graph.nodes || []
      graphEdges.value = graph.edges || []
      graphDataChecked.value = true

      // 已掌握 kp_id → 名称（recommendNext 入参需名称）
      const byId = new Map(graphNodes.value.map((n) => [String(n.id), n]))
      const nameToId = new Map(graphNodes.value.map((n) => [n.label, String(n.id)]))
      // 重新抽取会整体更换 kp_id：优先按 kp_id 命中，命中不到再按后端存的
      // 名字快照映射回同名新节点。否则「已掌握」会全部变成孤儿，
      // 推荐路径也会把学过的知识点重新推一遍。
      const records = (progress.records || []).filter((r) => r.status === 'MASTERED')
      const learned = records.length
        ? records
        : (progress.mastered_kp_ids || []).map((id) => ({ kp_id: id }))
      const effectiveIds = new Set()
      const masteredNames = []
      for (const r of learned) {
        const id = byId.has(String(r.kp_id))
          ? String(r.kp_id)
          : nameToId.get(r.kp_name)
        if (!id || effectiveIds.has(id)) continue
        effectiveIds.add(id)
        masteredNames.push(byId.get(id)?.label || r.kp_name)
      }
      masteredCache[key] = [...effectiveIds]
      // 推荐失败只影响推荐结果，不能连带清空已成功取回的图谱（否则总览/收藏归零）
      try {
        const res = await api.recommendNext(
          masteredNames, cid, currentDocumentId.value, { signal },
        )
        if (!pathRequest.isLatest(token) || ctxKey.value !== key) return
        pathRecs.value = res.recommendations || []

        // 将 recommendNext 结果映射为 kp_id，建立展示层唯一状态源
        currentKnowledgePointId.value = pathRecs.value[0]
          ? nameToId.get(pathRecs.value[0].name) || null
          : null
        recommendedKnowledgePointIds.value = pathRecs.value
          .slice(1)
          .map((r) => nameToId.get(r.name))
          .filter(Boolean)
      } catch {
        if (!pathRequest.isLatest(token) || ctxKey.value !== key) return
        pathRecs.value = []
        currentKnowledgePointId.value = null
        recommendedKnowledgePointIds.value = []
      }
    } catch {
      if (!pathRequest.isLatest(token) || ctxKey.value !== key) return
      // 图谱/进度都取不回来时不能保留上一个文档的节点，否则整页会显示错乱的数据
      graphNodes.value = []
      graphEdges.value = []
      graphDataChecked.value = true
      pathRecs.value = []
      currentKnowledgePointId.value = null
      recommendedKnowledgePointIds.value = []
    } finally {
      if (pathRequest.isLatest(token)) {
        pathDataLoading.value = false
        pathDataChecked.value = true
      }
    }
  }

  async function loadFavorites() {
    const key = ctxKey.value
    const cid = currentCourseId.value
    if (!key || !cid) {
      favoritesRequest.invalidate()
      favoritesLoading.value = false
      favoritesLoaded.value = false
      return
    }
    const { token, signal } = favoritesRequest.beginRead()
    favoritesLoading.value = true
    favoritesLoaded.value = false
    try {
      const res = await api.getFavorites(cid, currentDocumentId.value, { signal })
      if (!favoritesRequest.isLatest(token) || ctxKey.value !== key) return
      favoriteCache[key] = (res.items || []).map((f) => f.kp_id)
      favoritesLoaded.value = true
    } catch {
      if (favoritesRequest.isLatest(token) && ctxKey.value === key) {
        favoriteCache[key] = []
        favoritesLoaded.value = true
      }
    } finally {
      if (favoritesRequest.isLatest(token)) favoritesLoading.value = false
    }
  }

  function isFavorited(kpId) {
    if (!ctxKey.value || kpId == null) return false
    return (favoriteCache[ctxKey.value] || []).includes(String(kpId))
  }

  /** 收藏 / 取消收藏（详情抽屉 / 学习路径 / QA 来源 / 收藏夹统一入口） */
  async function toggleFavorite(kpId) {
    const key = ctxKey.value
    const cid = currentCourseId.value
    const did = currentDocumentId.value
    if (!key || !cid || kpId == null) return
    const kp = String(kpId)
    if (isFavorited(kp)) {
      await api.removeFavorite(cid, did, kp)
      favoriteCache[key] = (favoriteCache[key] || []).filter((id) => id !== kp)
      ElMessage.success('已取消收藏')
    } else {
      await api.addFavorite(cid, did, kp)
      favoriteCache[key] = [...(favoriteCache[key] || []), kp]
      ElMessage.success('已收藏')
    }
  }

  /** 轻量入口（QA 来源卡 / 学习路径 / 收藏夹）：无独立 loading，仅提示 + 状态同步 */
  async function toggleFavoriteSafe(kpId) {
    try {
      await toggleFavorite(kpId)
    } catch (e) {
      ElMessage.error(`操作失败：${e.message}`)
    }
  }

  /** 详情抽屉的收藏按钮（带 loading 态） */
  async function onToggleFavorite() {
    const node = drawerNode.value
    if (!node || !ctxKey.value || node.id == null) return
    favoriteLoading.value = true
    try {
      await toggleFavorite(node.id)
    } catch (e) {
      ElMessage.error(`操作失败：${e.message}`)
    } finally {
      favoriteLoading.value = false
    }
  }

  /** 详情抽屉的掌握标记：成功后刷新学习路径/推荐 */
  async function onToggleMastery() {
    const node = drawerNode.value
    if (!node || !ctxKey.value) return
    const kpId = String(node.id)
    const key = ctxKey.value
    const cid = currentCourseId.value
    const did = currentDocumentId.value
    const isMastered = (masteredCache[key] || []).includes(kpId)
    masteryLoading.value = true
    try {
      if (isMastered) {
        await api.unmarkMastered(cid, did, kpId)
        masteredCache[key] = (masteredCache[key] || []).filter((id) => id !== kpId)
        ElMessage.success('已取消掌握标记')
      } else {
        await api.markMastered(cid, did, kpId)
        masteredCache[key] = [...(masteredCache[key] || []), kpId]
        ElMessage.success('已标记为掌握')
      }
      if (ctxKey.value === key) loadPathData()
    } catch (e) {
      ElMessage.error(`操作失败：${e.message}`)
    } finally {
      masteryLoading.value = false
    }
  }

  function onNodeClick(node, info) {
    drawerNode.value = node
    drawerSuccessors.value = info?.successors || []
    drawerRelated.value = info?.related || []
    drawerPredecessors.value = info?.predecessors || []
    drawerExpanded.value = !!info?.expanded
    drawerVisible.value = true
  }

  /** 图谱节点 → 下拉候选（学习路径面板用，避免用户手输名称对不上） */
  const knowledgeOptions = computed(() =>
    graphNodes.value.map((n) => ({ id: n.id, label: n.label, category: n.properties?.category || '' })),
  )

  /** 已掌握 kp_id → 名称（学习路径面板默认选中项） */
  const masteredNames = computed(() => {
    const byId = new Map(graphNodes.value.map((n) => [String(n.id), n.label]))
    return (masteredCache[ctxKey.value] || [])
      .map((id) => byId.get(String(id)))
      .filter(Boolean)
  })

  /** 基于当前文档图谱构建客户端一阶邻接（与 GraphCanvas.neighborInfo 同口径） */
  function neighborsOf(nodeId) {
    const id = String(nodeId)
    const byId = new Map(graphNodes.value.map((n) => [String(n.id), n]))
    const predecessorIds = new Set()
    const successorIds = new Set()
    const relatedIds = new Set()
    for (const e of graphEdges.value) {
      const s = String(e.source)
      const t = String(e.target)
      if (e.type === 'PRECEDES') {
        if (t === id) predecessorIds.add(s)
        else if (s === id) successorIds.add(t)
      } else {
        if (s === id) relatedIds.add(t)
        else if (t === id) relatedIds.add(s)
      }
    }
    for (const x of predecessorIds) relatedIds.delete(x)
    for (const x of successorIds) relatedIds.delete(x)
    predecessorIds.delete(id)
    successorIds.delete(id)
    relatedIds.delete(id)
    const map = (set) => [...set].map((nid) => byId.get(nid)).filter(Boolean)
    return {
      predecessors: map(predecessorIds),
      successors: map(successorIds),
      related: map(relatedIds),
    }
  }

  const currentKnowledgePoint = computed(() => {
    const id = currentKnowledgePointId.value
    if (!id) return null
    return graphNodes.value.find((n) => String(n.id) === id) || null
  })

  // 推荐项 kp_id → recommendNext 原始条目（取 reason）
  const recById = computed(() => {
    const nameToId = new Map(graphNodes.value.map((n) => [n.label, String(n.id)]))
    const map = new Map()
    for (const r of pathRecs.value) {
      const id = nameToId.get(r.name)
      if (id) map.set(id, r)
    }
    return map
  })

  // 图谱状态仅在当前文档上下文内生效（避免跨文档残留）
  const browseCurrentKpId = computed(() => (ctxKey.value ? currentKnowledgePointId.value : null))
  const browseRecommendedKpIds = computed(() => (ctxKey.value ? recommendedKnowledgePointIds.value : []))

  const drawerMastered = computed(() =>
    drawerNode.value ? masteredKpIds.value.includes(String(drawerNode.value.id)) : false,
  )
  const drawerFavorited = computed(() =>
    drawerNode.value ? isFavorited(String(drawerNode.value.id)) : false,
  )
  // 已掌握节点不再显示「当前/推荐」状态标签
  const drawerLearningState = computed(() => {
    const id = drawerNode.value ? String(drawerNode.value.id) : ''
    if (!id) return ''
    if (masteredKpIds.value.includes(id)) return ''
    if (currentKnowledgePointId.value === id) return 'current'
    if (recommendedKnowledgePointIds.value.includes(id)) return 'recommended'
    return ''
  })

  const pathProgress = computed(() => {
    const total = graphNodes.value.length
    const mastered = pathMasteredIds.value.length
    const pct = total ? Math.round((mastered / total) * 100) : 0
    return { total, mastered, pct, current: currentKnowledgePoint.value?.label || '' }
  })

  // 基于图谱 PRECEDES 边 + 统一学习状态，构建「已掌握 → 当前 → 推荐 → 未学习」展示链
  const pathChain = computed(() => {
    const nodes = graphNodes.value
    if (!nodes.length) return []

    const byId = new Map(nodes.map((n) => [String(n.id), n]))
    const byName = new Map(nodes.map((n) => [n.label, n]))
    const masteredSet = new Set(pathMasteredIds.value.map(String))

    const succ = new Map() // name -> [name]（PRECEDES：source 是 target 的前置）
    const pred = new Map()
    for (const e of graphEdges.value) {
      if (e.type !== 'PRECEDES') continue
      const s = byId.get(String(e.source))?.label
      const t = byId.get(String(e.target))?.label
      if (!s || !t || s === t) continue
      if (!succ.has(s)) succ.set(s, [])
      succ.get(s).push(t)
      if (!pred.has(t)) pred.set(t, [])
      pred.get(t).push(s)
    }

    const current = currentKnowledgePoint.value
    if (!current) return []

    const segs = []
    const used = new Set()

    // 1) 已掌握：current 的直接前置（且已掌握）
    for (const pname of pred.get(current.label) || []) {
      const node = byName.get(pname)
      if (node && masteredSet.has(String(node.id)) && !used.has(node.id)) {
        used.add(node.id)
        segs.push({ node, state: 'mastered', reason: '' })
      }
    }

    // 2) 当前学习
    if (!used.has(current.id)) {
      used.add(current.id)
      segs.push({ node: current, state: 'current', reason: recById.value.get(current.id)?.reason || '' })
    }

    // 3) 推荐学习：优先取 recommendNext 推荐中 current 的后继，否则取结构后继
    const nexts = succ.get(current.label) || []
    const recIds = recommendedKnowledgePointIds.value
    const rId = recIds.find((id) => nexts.includes(byId.get(id)?.label))
      || recIds[0]
      || (nexts[0] ? byName.get(nexts[0])?.id : null)
    if (rId) {
      const rNode = byId.get(String(rId))
      if (rNode && !used.has(rNode.id)) {
        used.add(rNode.id)
        segs.push({
          node: rNode,
          state: 'recommended',
          reason: recById.value.get(rNode.id)?.reason || `前置知识「${current.label}」掌握后即可学习`,
        })
      }
      // 4) 未学习：推荐节点的后继（再进一步）
      const next2 = succ.get(rNode?.label) || []
      for (const n2name of next2) {
        const n2 = byName.get(n2name)
        if (n2 && !used.has(n2.id)) {
          used.add(n2.id)
          segs.push({ node: n2, state: 'unlearned', reason: '' })
          break
        }
      }
    }

    return segs
  })

  // ===================== 学习总览（复用同一份状态源） =====================
  const overviewTotal = computed(() => graphNodes.value.length)
  const overviewMastered = computed(() => pathMasteredIds.value.length)
  const overviewPct = computed(() => pathProgress.value.pct)
  const overviewFavIds = computed(() => favoriteCache[ctxKey.value] || [])

  const overviewStateCounts = computed(() => {
    const total = graphNodes.value.length
    const masteredSet = new Set(pathMasteredIds.value.map(String))
    const cur = currentKnowledgePointId.value
    const curIsMastered = cur ? masteredSet.has(String(cur)) : false
    const currentCount = cur && !curIsMastered ? 1 : 0
    const recommendedCount = recommendedKnowledgePointIds.value.filter(
      (id) => !masteredSet.has(String(id)),
    ).length
    return {
      total,
      mastered: masteredSet.size,
      current: currentCount,
      recommended: recommendedCount,
      unlearned: Math.max(0, total - masteredSet.size - currentCount - recommendedCount),
    }
  })

  const overviewCurrentNode = computed(() => currentKnowledgePoint.value)

  // 推荐下一步 = recommendNext 结果中排除「当前学习」后的前 4 条
  const overviewRecommended = computed(() => {
    const curName = overviewCurrentNode.value?.label
    return pathRecs.value.filter((r) => r.name !== curName).slice(0, 4)
  })

  // 建议重点学习 = recommendNext 推荐结果（含真实 reason，不伪造原因）
  const overviewFocusList = computed(() => {
    const nameToId = new Map(graphNodes.value.map((n) => [n.label, String(n.id)]))
    return pathRecs.value.slice(0, 5).map((r) => ({
      kpId: nameToId.get(r.name) || null,
      name: r.name,
      category: r.category,
      description: r.description,
      reason: r.reason,
    }))
  })

  const overviewKpis = computed(() => [
    { label: '知识点总数', value: overviewTotal.value, color: '#4f6ef7', icon: Collection },
    { label: '已掌握', value: overviewMastered.value, color: '#67c23a', icon: Checked },
    { label: '学习进度', value: overviewPct.value + '%', color: '#e6a23c', icon: Odometer },
    { label: '我的收藏', value: overviewFavIds.value.length, color: '#f56c6c', icon: StarFilled },
  ])

  const overviewStateList = computed(() => {
    const c = overviewStateCounts.value
    const pct = (n) => (c.total ? Math.round((n / c.total) * 100) : 0)
    return [
      { key: 'mastered', label: '已掌握', glyph: '✓', count: c.mastered, pct: pct(c.mastered), color: '#67c23a' },
      { key: 'current', label: '当前学习', glyph: '●', count: c.current, pct: pct(c.current), color: '#4f6ef7' },
      { key: 'recommended', label: '推荐学习', glyph: '★', count: c.recommended, pct: pct(c.recommended), color: '#e6a23c' },
      { key: 'unlearned', label: '未学习', glyph: '○', count: c.unlearned, pct: pct(c.unlearned), color: '#c0c4cc' },
    ]
  })

  // 学习上下文切换：作废旧请求、收起旧文档的详情抽屉，再按新上下文加载
  watch(ctxKey, (key) => {
    pathRequest.invalidate()
    favoritesRequest.invalidate()
    currentKnowledgePointId.value = null
    recommendedKnowledgePointIds.value = []
    pathRecs.value = []
    graphNodes.value = []
    graphEdges.value = []
    pathDataChecked.value = false
    graphDataChecked.value = false
    // 旧请求的 finally 因序号失效不会再清 loading，这里必须主动复位
    pathDataLoading.value = false
    favoritesLoaded.value = false
    favoritesLoading.value = false
    drawerVisible.value = false
    drawerNode.value = null
    drawerRelated.value = []
    drawerPredecessors.value = []
    drawerSuccessors.value = []
    drawerExpanded.value = false
    if (key) {
      loadPathData()
      loadFavorites()
    }
  }, { immediate: true })

  return {
    graphNodes,
    graphEdges,
    pathRecs,
    currentKnowledgePointId,
    recommendedKnowledgePointIds,
    pathDataLoading,
    pathDataChecked,
    graphDataChecked,
    masteredCache,
    masteryLoading,
    masteredKpIds,
    pathMasteredIds,
    favoriteCache,
    favoritesLoading,
    favoritesLoaded,
    favoriteLoading,
    favoriteIds,
    drawerVisible,
    drawerNode,
    drawerRelated,
    drawerPredecessors,
    drawerSuccessors,
    drawerExpanded,
    knowledgeOptions,
    masteredNames,
    drawerMastered,
    drawerFavorited,
    drawerLearningState,
    currentKnowledgePoint,
    recById,
    browseCurrentKpId,
    browseRecommendedKpIds,
    pathProgress,
    pathChain,
    overviewTotal,
    overviewMastered,
    overviewPct,
    overviewFavIds,
    overviewStateCounts,
    overviewCurrentNode,
    overviewRecommended,
    overviewFocusList,
    overviewKpis,
    overviewStateList,
    loadPathData,
    loadFavorites,
    isFavorited,
    toggleFavorite,
    toggleFavoriteSafe,
    onToggleFavorite,
    onToggleMastery,
    onNodeClick,
    neighborsOf,
  }
}
