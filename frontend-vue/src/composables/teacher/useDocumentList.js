import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../../api'
import { useLatestRequest } from '../useLatestRequest'

/**
 * 教师端课程文档列表（页面级共享）。
 *
 * 文档列表同时被「课程文档 / 图谱管理 / 题库 / 批改」使用，因此由页面统一持有，
 * 各 Tab 只读；上传与轮询在课程文档 Tab 内进行，写完通过 refreshDocuments 回到这里。
 *
 * 竞态：
 * - 同一课程的并发请求会合并成同一个 Promise（首屏课程 watch 与 open=1 自动选文档
 *   同时触发时，不会互相作废）；
 * - 用户主动刷新（force）可打断正在进行的静默轮询；
 * - 只有「当前课程的最后一次强制请求」能写入列表，切课后旧响应直接丢弃。
 */
export function useDocumentList(courseId) {
  const documents = ref([])
  const documentsLoading = ref(false)
  const request = useLatestRequest()
  // loading 用计数器：静默轮询不碰它；旧的可见请求结束时也不能提前关掉新请求的 loading
  let visiblePending = 0
  let inflight = null

  async function fetchDocuments({ silent = false, force = false } = {}) {
    const target = String(courseId.value || '')
    if (!target) {
      if (!silent) documents.value = []
      return []
    }
    // 同课程的请求合并：open=1 watcher 与课程 watch 同时触发时只发一次
    if (!force && inflight && inflight.target === target) return inflight.promise

    const { token, signal } = request.beginRead()
    if (!silent) {
      visiblePending += 1
      documentsLoading.value = true
    }
    const promise = (async () => {
      try {
        const list = await api.getDocuments(target, { signal })
        // 课程已切换或已有更新请求：丢弃旧响应，避免把上一门课的文档写进当前列表
        if (!request.isLatest(token) || String(courseId.value) !== target) return null
        documents.value = Array.isArray(list) ? list : []
        return documents.value
      } catch (e) {
        if (request.isLatest(token) && String(courseId.value) === target) {
          if (!silent) {
            documents.value = []
            ElMessage.warning(`文档列表加载失败：${e.message}`)
          }
          return []
        }
        return null
      } finally {
        if (!silent) {
          visiblePending = Math.max(0, visiblePending - 1)
          if (visiblePending === 0) documentsLoading.value = false
        }
      }
    })()
    inflight = { target, promise }
    try {
      return await promise
    } finally {
      if (inflight && inflight.promise === promise) inflight = null
    }
  }

  /** 前台加载（用户点刷新 / 上传后回读）：强制发新请求，能打断静默轮询 */
  const loadDocuments = () => fetchDocuments({ force: true })
  /** 仅确保当前课程有列表：同课程在途请求直接复用（open=1 / 首屏加载用） */
  const ensureDocuments = () => fetchDocuments()
  const refreshDocuments = () => fetchDocuments({ silent: true })

  watch(courseId, (cid) => {
    if (!cid) {
      request.invalidate()
      documents.value = []
      documentsLoading.value = false
      visiblePending = 0
      inflight = null
      return
    }
    ensureDocuments()
  }, { immediate: true })

  return { documents, documentsLoading, loadDocuments, ensureDocuments, refreshDocuments }
}
