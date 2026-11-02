import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../../api'
import {
  docGraphStatus,
  docIsInFlight,
  isSameDocument,
} from '../../utils/documentStatus'

const MAX_UPLOAD_BYTES = 50 * 1024 * 1024
const BATCH_STORAGE_PREFIX = 'kg_batch_upload_'
const DOC_POLL_INTERVAL = 3000
const DOC_POLL_TIMEOUT = 30 * 60 * 1000

function batchStorageKey(courseId) {
  return `${BATCH_STORAGE_PREFIX}${courseId}`
}

/**
 * 课程文档 Tab 的上传批次、失败重试与进度轮询。
 *
 * 文档列表本身由页面级 useDocumentList 持有（其它 Tab 也要用），
 * 这里只负责「产生变化 → 调用页面刷新」并把结果对回本地批次记录。
 */
export function useCourseDocuments({
  courseId,
  active,
  documents,
  reloadDocuments,
  refreshDocuments,
}) {
  const uploadFileList = ref([])
  const uploading = ref(false)
  // 每条：{ uid, file_name, file_size, document_id, upload_failed, duplicate, error, ... }
  const batchItems = ref([])
  // 表格行重试状态放本地，避免直接改 props.documents 里的对象
  const retryingDocs = reactive({})

  // ---------------- 批次记录持久化（File 对象不可序列化，只存恢复所需字段） ----------------
  function persistBatchItems() {
    if (!courseId.value) return
    try {
      const slim = batchItems.value.slice(0, 50).map((it) => ({
        uid: it.uid,
        file_name: it.file_name,
        file_size: it.file_size,
        document_id: it.document_id ?? null,
        upload_failed: !!it.upload_failed,
        duplicate: !!it.duplicate,
        error: it.error || '',
        created_at: it.created_at || '',
      }))
      localStorage.setItem(batchStorageKey(courseId.value), JSON.stringify(slim))
    } catch {
      /* 存储不可用时不影响上传与处理 */
    }
  }

  watch(batchItems, persistBatchItems, { deep: true })

  function loadBatchItemsForCourse(cid) {
    if (!cid) {
      batchItems.value = []
      return
    }
    try {
      const raw = localStorage.getItem(batchStorageKey(cid))
      const list = raw ? JSON.parse(raw) : []
      batchItems.value = Array.isArray(list) ? list : []
    } catch {
      batchItems.value = []
    }
  }

  /** 页面刷新/关闭后恢复出的批次记录：没有 document_id 说明上传没走完，标为可重新选择的失败项 */
  function markInterruptedBatchItems() {
    let changed = false
    for (const item of batchItems.value) {
      if (item.document_id || item.upload_failed) continue
      item.upload_failed = true
      item.error = '上传过程已中断（页面刷新或关闭），请重新选择文件'
      changed = true
    }
    if (changed) batchItems.value = [...batchItems.value]
  }

  function batchDocument(item) {
    if (!item?.document_id) return null
    return documents.value.find((d) => String(d.doc_id) === String(item.document_id)) || null
  }

  /** 后端文档状态 -> 统一阶段（每阶段文案与百分比都对应真实库状态） */
  function batchStage(item) {
    const base = { text: '待处理', type: 'info', percentage: 0, inFlight: false, completed: false, failed: false, message: '' }
    if (item.uploading) {
      return { ...base, text: '上传中', type: 'warning', percentage: 10, inFlight: true, message: '正在上传文件…' }
    }
    if (item.upload_failed && !item.document_id) {
      return { ...base, text: '失败', type: 'danger', percentage: 100, failed: true, progressStatus: 'exception', message: item.error || '上传失败' }
    }
    const doc = batchDocument(item)
    if (!doc) {
      return { ...base, text: '待处理', percentage: 5, message: '等待后端建档…' }
    }
    const graph = docGraphStatus(doc)
    if (doc.parse_status === 'FAILED' || doc.extract_status === 'FAILED' || graph === 'FAILED') {
      const percentage = doc.parse_status === 'FAILED' ? 35 : doc.extract_status === 'FAILED' ? 75 : 90
      return {
        ...base, text: '失败', type: 'danger', percentage, failed: true,
        progressStatus: 'exception', message: doc.error_message || item.error || '处理失败',
      }
    }
    if (graph === 'COMPLETED') {
      return { ...base, text: '已完成', type: 'success', percentage: 100, completed: true, progressStatus: 'success', message: '' }
    }
    if (graph === 'BUILDING') {
      return { ...base, text: '图谱构建中', type: 'warning', percentage: 90, inFlight: true, message: '正在写入 Neo4j…' }
    }
    if (doc.extract_status === 'EXTRACTING') {
      return { ...base, text: '知识抽取中', type: 'warning', percentage: 70, inFlight: true, message: '正在抽取知识点与关系…' }
    }
    if (doc.parse_status === 'PARSING') {
      return { ...base, text: '解析中', type: 'warning', percentage: 35, inFlight: true, message: '正在解析文档…' }
    }
    if (doc.parse_status === 'PARSED' && doc.extract_status === 'COMPLETED') {
      return { ...base, text: '图谱构建中', type: 'warning', percentage: 85, inFlight: true, message: '准备写入图谱…' }
    }
    if (doc.parse_status === 'PARSED') {
      return { ...base, text: '待抽取', type: 'info', percentage: 50, inFlight: true, message: '排队等待知识抽取…' }
    }
    return { ...base, text: '待处理', percentage: 5, inFlight: true, message: '排队等待处理…' }
  }

  const batchSummary = computed(() => {
    const total = batchItems.value.length
    let success = 0
    let failed = 0
    let processing = 0
    let sum = 0
    for (const item of batchItems.value) {
      const stage = batchStage(item)
      if (stage.completed) success += 1
      else if (stage.failed) failed += 1
      else processing += 1
      sum += stage.percentage
    }
    return {
      total, success, failed, processing,
      percentage: total ? Math.round(sum / total) : 0,
    }
  })

  function canRetryBatchItem(item) {
    const stage = batchStage(item)
    return !!item.document_id && stage.failed && !item.retrying && !item.uploading
  }

  // ---------------- 上传 ----------------
  async function doBatchUpload() {
    if (uploading.value) return
    if (!courseId.value) {
      ElMessage.warning('请先选择课程')
      return
    }
    const rawFiles = uploadFileList.value.map((f) => f.raw).filter(Boolean)
    if (!rawFiles.length) {
      ElMessage.warning('请先选择要上传的文件')
      return
    }

    uploading.value = true
    const now = Date.now()
    const validFiles = []
    const validItems = []
    const items = []
    const allowedExts = ['.pdf', '.txt', '.docx', '.md']

    // 前端先按后端同一套规则快速校验：格式/空文件/50MB；不合法文件不发送
    for (const file of rawFiles) {
      const ext = `.${(file.name.split('.').pop() || '').toLowerCase()}`
      const item = {
        uid: `batch-${now}-${items.length}-${file.name}`,
        file_name: file.name,
        file_size: file.size,
        document_id: null,
        uploading: false,
        upload_failed: false,
        duplicate: false,
        error: '',
        retrying: false,
        created_at: new Date().toISOString(),
      }
      items.push(item)
      if (!allowedExts.includes(ext)) {
        item.upload_failed = true
        item.error = `文件格式不支持，仅支持 PDF/TXT/DOCX/MD，收到: ${ext || '无扩展名'}`
      } else if (!file.size) {
        item.upload_failed = true
        item.error = '文件为空'
      } else if (file.size > MAX_UPLOAD_BYTES) {
        item.upload_failed = true
        item.error = '文件大小超限，最大 50MB'
      } else {
        item.uploading = true
        validFiles.push(file)
        validItems.push(item)
      }
    }

    // 新批次放在最上面；保留最近 50 条
    batchItems.value = [...items, ...batchItems.value].slice(0, 50)
    uploadFileList.value = []

    if (!validFiles.length) {
      uploading.value = false
      ElMessage.error('没有可上传的文件，请查看文件列表中的失败原因')
      return
    }

    const formData = new FormData()
    for (const file of validFiles) formData.append('files', file)

    try {
      const result = await api.uploadDocumentsBatch(formData, courseId.value)
      const results = result?.items || []
      // 后端按提交顺序逐个返回；有效文件与 validItems 一一对应
      validItems.forEach((item, index) => {
        const r = results[index]
        item.uploading = false
        if (!r) {
          item.upload_failed = true
          item.error = '后端未返回该文件的处理结果'
        } else if (r.ok) {
          item.document_id = r.document_id
          item.duplicate = !!r.duplicate
          item.error = r.duplicate ? (r.message || '相同文件已存在，已跳过') : ''
        } else {
          item.upload_failed = true
          item.error = r.message || '上传失败'
        }
      })
      const accepted = validItems.filter((i) => i.document_id).length
      const failedCount = items.filter((i) => i.upload_failed).length
      if (accepted) ElMessage.success(`已接收 ${accepted} 个文件，后台正在逐篇构建知识图谱`)
      if (failedCount) ElMessage.warning(`${failedCount} 个文件未能进入处理队列，请查看失败原因`)

      await reloadDocuments()
      recoverBatchItemsFromDocuments(documents.value)
      // 之前已有一批在途文档时 hasInFlightDoc 不会“从 false 变 true”，需显式续上轮询
      if (hasInFlightDoc.value) startPolling()
    } catch (e) {
      // 网络超时/断网不等于后端没收到：先回读文档列表，按文件名+大小认领已建档的记录
      await reloadDocuments()
      recoverBatchItemsFromDocuments(documents.value, { includeFailed: true })
      const unresolved = validItems.filter((i) => !i.document_id)
      unresolved.forEach((item) => {
        item.uploading = false
        item.upload_failed = true
        item.error = e.message || '上传失败'
      })
      if (unresolved.length) {
        ElMessage.error(`上传失败：${e.message}`)
      } else {
        ElMessage.warning('上传请求超时，但后端已接收文件，正在继续跟踪处理进度')
      }
    } finally {
      uploading.value = false
      batchItems.value = [...batchItems.value]
    }
  }

  /**
   * 本地批次记录与后端文档列表对号（文件名 + 字节数，且一文档只认领一次）。
   * 两种场景：上传超时但后端已建档；页面刷新后恢复批次面板真实状态。
   */
  function recoverBatchItemsFromDocuments(list = documents.value, { includeFailed = false } = {}) {
    const claimed = new Set(
      batchItems.value.filter((i) => i.document_id).map((i) => String(i.document_id)),
    )
    let changed = false
    for (const item of batchItems.value) {
      if (item.document_id) continue
      if (item.upload_failed && !includeFailed) continue
      const match = list.find((d) => !claimed.has(String(d.doc_id)) && isSameDocument(d, item))
      if (!match) continue
      item.document_id = match.doc_id
      item.upload_failed = false
      item.error = ''
      claimed.add(String(match.doc_id))
      changed = true
    }
    if (changed) batchItems.value = [...batchItems.value]
  }

  async function retryBatchItem(item) {
    if (!item?.document_id || item.retrying) return
    item.retrying = true
    try {
      await api.retryDocument(item.document_id)
      item.upload_failed = false
      item.error = ''
      ElMessage.success(`「${item.file_name}」已重新进入处理队列`)
      await reloadDocuments()
      if (hasInFlightDoc.value) startPolling()
    } catch (e) {
      item.error = e.message || '重试失败'
      ElMessage.error(`「${item.file_name}」重试失败：${item.error}`)
    } finally {
      item.retrying = false
      batchItems.value = [...batchItems.value]
    }
  }

  function isDocRetrying(row) {
    return !!retryingDocs[row.doc_id]
  }

  async function retryDocumentRow(row) {
    if (!row?.doc_id || retryingDocs[row.doc_id]) return
    retryingDocs[row.doc_id] = true
    try {
      await api.retryDocument(row.doc_id)
      ElMessage.success(`「${row.file_name}」已重新进入处理队列`)
      await reloadDocuments()
      if (hasInFlightDoc.value) startPolling()
    } catch (e) {
      ElMessage.error(`重试失败：${e.message}`)
    } finally {
      retryingDocs[row.doc_id] = false
    }
  }

  function clearBatchItems() {
    batchItems.value = []
    if (courseId.value) {
      try {
        localStorage.removeItem(batchStorageKey(courseId.value))
      } catch {
        /* ignore */
      }
    }
  }

  async function deleteDocument(doc) {
    try {
      await ElMessageBox.confirm(
        '删除该文档后，该文档对应的资源将被删除，是否继续？',
        '删除文档',
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
    try {
      await api.deleteDocument(doc.doc_id)
      ElMessage.success('文档已删除')
      await reloadDocuments()
    } catch (e) {
      ElMessage.error(`删除失败：${e.message}`)
    }
  }

  // ---------------- 进度轮询 ----------------
  let pollTimer = null
  let pollStartedAt = 0
  const hasInFlightDoc = computed(() => documents.value.some(docIsInFlight))

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  async function pollDocuments() {
    const prev = new Map(documents.value.map((d) => [d.doc_id, `${d.parse_status}|${d.extract_status}`]))
    const list = await refreshDocuments()
    if (!list) return // 已被更新的请求取代
    recoverBatchItemsFromDocuments(list)
    for (const d of list) {
      const key = `${d.parse_status}|${d.extract_status}|${docGraphStatus(d)}`
      const oldKey = prev.get(d.doc_id)
      if (!oldKey || oldKey === key) continue
      const oldGraph = oldKey.split('|')[2]
      const graph = docGraphStatus(d)
      if (graph === 'COMPLETED' && oldGraph !== 'COMPLETED') {
        ElMessage.success(`「${d.file_name}」知识图谱构建完成`)
      } else if (
        (d.parse_status === 'FAILED' || d.extract_status === 'FAILED' || graph === 'FAILED')
        && !oldKey.includes('FAILED')
      ) {
        ElMessage.error(`「${d.file_name}」处理失败：${d.error_message || '请检查文件后重试'}`)
      }
    }
  }

  function startPolling() {
    if (pollTimer) return
    pollStartedAt = Date.now()
    pollTimer = setInterval(() => {
      // 教师切去其它 Tab 时页内数据照样更新（el-tabs 的 pane 是 v-show 保活，
      // 进度表不会冻住）；完成/失败仍走全局 ElMessage，不在本页也能收到。
      // 无课程、无进行中任务或超过 30 分钟兜底时自动停止。
      if (
        !courseId.value
        || !hasInFlightDoc.value
        || Date.now() - pollStartedAt > DOC_POLL_TIMEOUT
      ) {
        stopPolling()
        return
      }
      pollDocuments().catch(() => { /* 轮询失败静默，下一轮自动重试 */ })
    }, DOC_POLL_INTERVAL)
  }

  watch([hasInFlightDoc, courseId], ([inFlight, cid]) => {
    if (inFlight && cid) startPolling()
    else stopPolling()
  }, { immediate: true })

  // 每次从别的 Tab 切回文档页刷新一次（原页 activeTab watcher 的行为）；
  // 首次进入由页面级 useDocumentList 在课程变化时加载，避免重复请求。
  watch(active, (isActive, wasActive) => {
    if (isActive && !wasActive && courseId.value) reloadDocuments()
  })

  // 切换课程：恢复该课程的本地批次面板；没有 document_id 的记录视为上传中断
  watch(courseId, (cid) => {
    loadBatchItemsForCourse(cid)
    // 原版保护：上传仍在进行时不要把尚未回填 document_id 的项判成「中断」
    if (cid && !uploading.value) markInterruptedBatchItems()
  }, { immediate: true })

  onBeforeUnmount(stopPolling)

  return {
    uploadFileList,
    uploading,
    batchItems,
    batchSummary,
    batchDocument,
    batchStage,
    canRetryBatchItem,
    doBatchUpload,
    retryBatchItem,
    retryDocumentRow,
    isDocRetrying,
    clearBatchItems,
    deleteDocument,
    recoverBatchItemsFromDocuments,
    startPolling,
    stopPolling,
  }
}
