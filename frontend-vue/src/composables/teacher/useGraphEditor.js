import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../../api'
import { useLatestRequest } from '../useLatestRequest'

/**
 * 教师端图谱编辑（三栏审核区）的业务状态：
 * 可编辑知识点列表、节点表单、前置知识查询、新增节点/关系。
 *
 * 图谱画布实例由 GraphCanvas 自己管理；这里只处理数据与写操作。
 * 读取列表用请求序号防止快速换文档时把旧节点写进来。
 */
export function useGraphEditor({ courseId, documentId, editMode }) {
  const editNodes = ref([])
  const nodeListSearch = ref('')
  const selectedNode = ref(null)
  const editForm = ref({ name: '', category: '概念', description: '' })
  const savingNode = ref(false)
  const deletingNode = ref(false)
  const prereqs = ref([])
  const prereqLoading = ref(false)
  const prereqLoaded = ref(false)

  const addNodeVisible = ref(false)
  const addNodeForm = ref({ name: '', category: '概念', description: '' })
  const addNodeLoading = ref(false)
  const addEdgeVisible = ref(false)
  const addEdgeForm = ref({ source: '', type: 'PRECEDES', target: '' })
  const addEdgeLoading = ref(false)
  const edgeDialogVisible = ref(false)
  const clickedEdge = ref(null)
  const deleteEdgeLoading = ref(false)

  const nodesRequest = useLatestRequest()
  const prereqRequest = useLatestRequest()

  const filteredEditNodes = computed(() => {
    const kw = nodeListSearch.value.trim().toLowerCase()
    if (!kw) return editNodes.value
    return editNodes.value.filter(
      (n) => (n.label || '').toLowerCase().includes(kw)
        || (n.description || '').toLowerCase().includes(kw),
    )
  })

  async function loadEditNodes() {
    const cid = String(courseId.value || '')
    const did = String(documentId.value || '')
    if (!cid || !did) {
      nodesRequest.invalidate()
      editNodes.value = []
      return
    }
    const { token, signal } = nodesRequest.beginRead()
    try {
      const data = await api.getGraphV1(cid, did, { limit: 800 }, { signal })
      if (!nodesRequest.isLatest(token)) return
      if (String(courseId.value) !== cid || String(documentId.value) !== did) return
      editNodes.value = data.nodes || []
    } catch {
      if (nodesRequest.isLatest(token)) editNodes.value = []
    }
  }

  // 选中节点后同步编辑表单；换节点即清空前置知识查询结果
  watch(selectedNode, (n) => {
    prereqRequest.invalidate()
    prereqs.value = []
    prereqLoaded.value = false
    if (n) {
      editForm.value = {
        name: n.label || '',
        category: n.properties?.category || '概念',
        description: n.description || '',
      }
    }
  })

  // (课程, 文档, 编辑模式) 变化 → 清空编辑选中态；只有编辑模式才拉可编辑列表。
  // immediate：深链 tab=edit 进来时，setup 阶段这些值已经是最终值，不会再有“变化”触发。
  watch([courseId, documentId, editMode], ([cid, did, editing]) => {
    selectedNode.value = null
    edgeDialogVisible.value = false
    clickedEdge.value = null
    if (editing && cid && did) loadEditNodes()
    else editNodes.value = []
  }, { immediate: true })

  async function saveNode() {
    if (!selectedNode.value) return
    if (!editForm.value.name.trim()) {
      ElMessage.warning('名称不能为空')
      return
    }
    savingNode.value = true
    try {
      await api.updateNode(courseId.value, documentId.value, selectedNode.value.id, {
        name: editForm.value.name.trim(),
        category: editForm.value.category,
        description: editForm.value.description,
      })
      ElMessage.success('保存成功')
      selectedNode.value = null
      await loadEditNodes()
    } catch (e) {
      ElMessage.error(`保存失败：${e.message}`)
    } finally {
      savingNode.value = false
    }
  }

  async function deleteNode() {
    const node = selectedNode.value
    if (!node) return
    try {
      await ElMessageBox.confirm(
        `确定删除知识点「${node.label}」及其全部关系吗？`,
        '删除确认',
        {
          type: 'warning',
          confirmButtonText: '删除',
          cancelButtonText: '取消',
          confirmButtonClass: 'el-button--danger',
        },
      )
    } catch {
      return
    }
    deletingNode.value = true
    try {
      await api.deleteNode(courseId.value, documentId.value, node.id)
      ElMessage.success('已删除')
      selectedNode.value = null
      await loadEditNodes()
    } catch (e) {
      ElMessage.error(`删除失败：${e.message}`)
    } finally {
      deletingNode.value = false
    }
  }

  async function loadPrereqs() {
    if (!selectedNode.value) return
    const { token, signal } = prereqRequest.beginRead()
    const label = selectedNode.value.label
    prereqLoading.value = true
    try {
      const res = await api.getPrerequisites(
        label, courseId.value, documentId.value, { signal },
      )
      if (!prereqRequest.isLatest(token)) return
      // 查询期间用户可能已经换了节点：只保留当前节点的结果
      if (selectedNode.value?.label !== label) return
      prereqs.value = res.prerequisites || []
    } catch (e) {
      if (prereqRequest.isLatest(token)) ElMessage.warning(`查询失败：${e.message}`)
    } finally {
      if (prereqRequest.isLatest(token)) {
        prereqLoading.value = false
        prereqLoaded.value = true
      }
    }
  }

  function openAddNode() {
    if (!courseId.value) {
      ElMessage.warning('请先选择文档')
      return
    }
    addNodeForm.value = { name: '', category: '概念', description: '' }
    addNodeVisible.value = true
  }

  async function submitAddNode() {
    if (!addNodeForm.value.name.trim()) {
      ElMessage.warning('名称不能为空')
      return
    }
    addNodeLoading.value = true
    try {
      await api.createNode(courseId.value, documentId.value, {
        name: addNodeForm.value.name.trim(),
        category: addNodeForm.value.category,
        description: addNodeForm.value.description,
      })
      ElMessage.success('新增成功')
      addNodeVisible.value = false
      await loadEditNodes()
    } catch (e) {
      ElMessage.error(`新增失败：${e.message}`)
    } finally {
      addNodeLoading.value = false
    }
  }

  function openAddEdge() {
    if (!courseId.value) {
      ElMessage.warning('请先选择文档')
      return
    }
    if (!editNodes.value.length) loadEditNodes()
    addEdgeForm.value = { source: '', type: 'PRECEDES', target: '' }
    addEdgeVisible.value = true
  }

  async function submitAddEdge() {
    const { source, type, target } = addEdgeForm.value
    if (!source || !target) {
      ElMessage.warning('请选择源和目标知识点')
      return
    }
    if (source === target) {
      ElMessage.warning('源和目标不能相同')
      return
    }
    addEdgeLoading.value = true
    try {
      await api.createEdge(courseId.value, documentId.value, { source, type, target })
      ElMessage.success('关系创建成功')
      addEdgeVisible.value = false
      await loadEditNodes()
    } catch (e) {
      ElMessage.error(`创建失败：${e.message}`)
    } finally {
      addEdgeLoading.value = false
    }
  }

  function buildClickedEdge(edge) {
    if (!edge) return null
    return {
      ...edge,
      sourceLabel: editNodes.value.find((n) => n.id === edge.source)?.label || edge.source,
      targetLabel: editNodes.value.find((n) => n.id === edge.target)?.label || edge.target,
    }
  }

  async function removeEdge() {
    if (!clickedEdge.value) return
    try {
      await ElMessageBox.confirm('确定删除该关系吗？', '删除确认', {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger',
      })
    } catch {
      return
    }
    deleteEdgeLoading.value = true
    try {
      await api.deleteEdge(courseId.value, documentId.value, clickedEdge.value.id)
      ElMessage.success('关系已删除')
      edgeDialogVisible.value = false
      await loadEditNodes()
    } catch (e) {
      ElMessage.error(`删除失败：${e.message}`)
    } finally {
      deleteEdgeLoading.value = false
    }
  }

  return {
    editNodes,
    nodeListSearch,
    selectedNode,
    editForm,
    savingNode,
    deletingNode,
    prereqs,
    prereqLoading,
    prereqLoaded,
    addNodeVisible,
    addNodeForm,
    addNodeLoading,
    addEdgeVisible,
    addEdgeForm,
    addEdgeLoading,
    edgeDialogVisible,
    clickedEdge,
    deleteEdgeLoading,
    filteredEditNodes,
    loadEditNodes,
    saveNode,
    deleteNode,
    loadPrereqs,
    openAddNode,
    submitAddNode,
    openAddEdge,
    submitAddEdge,
    buildClickedEdge,
    removeEdge,
  }
}
