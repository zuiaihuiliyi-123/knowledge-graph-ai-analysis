import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../../api'
import { useLatestRequest } from '../useLatestRequest'

export const PROGRESS_BINS = [
  { value: '0', label: '0–20%' },
  { value: '20', label: '20–40%' },
  { value: '40', label: '40–60%' },
  { value: '60', label: '60–80%' },
  { value: '80', label: '80–100%' },
]

/** 教师端教学监测：班级学习数据、搜索/筛选/排序/分页、学生详情 */
export function useTeacherMonitor({ courseId, active }) {
  const monitorData = ref(null)
  const monitorLoading = ref(false)
  const monitorSearch = ref('')
  const monitorProgressFilter = ref('')
  const monitorPage = ref(1)
  const monitorPageSize = ref(10)
  const monitorSort = ref({ prop: 'progress', order: 'ascending' })
  const monitorDetailVisible = ref(false)
  const monitorDetailStudent = ref(null)
  const request = useLatestRequest()

  const progressBins = PROGRESS_BINS

  const monitorKpis = computed(() => {
    const d = monitorData.value
    return [
      { label: '已开始学习人数', value: d?.student_count ?? 0, color: '#4f6ef7' },
      { label: '平均学习进度', value: (d?.avg_progress ?? 0) + '%', color: '#e6a23c' },
      { label: '课程知识点总数', value: d?.total_knowledge ?? 0, color: '#67c23a' },
    ]
  })

  const filteredMonitorStudents = computed(() => {
    let list = monitorData.value?.students || []
    const kw = monitorSearch.value.trim().toLowerCase()
    if (kw) {
      list = list.filter(
        (s) => (s.student_name || '').toLowerCase().includes(kw)
          || (s.username || '').toLowerCase().includes(kw),
      )
    }
    if (monitorProgressFilter.value !== '') {
      const min = Number(monitorProgressFilter.value)
      list = list.filter((s) => (min === 80 ? s.progress >= 80 : s.progress >= min && s.progress < min + 20))
    }
    const { prop, order } = monitorSort.value
    const dir = order === 'descending' ? -1 : 1
    return [...list].sort((a, b) => {
      const av = prop === 'student_name' ? a.student_name : a[prop]
      const bv = prop === 'student_name' ? b.student_name : b[prop]
      if (av === bv) return 0
      return (av > bv ? 1 : -1) * dir
    })
  })

  const pagedMonitorStudents = computed(() => {
    const start = (monitorPage.value - 1) * monitorPageSize.value
    return filteredMonitorStudents.value.slice(start, start + monitorPageSize.value)
  })

  function onMonitorSortChange({ prop, order }) {
    monitorSort.value = { prop: prop || 'progress', order: order || 'ascending' }
  }

  async function loadMonitorData() {
    const cid = String(courseId.value || '')
    if (!cid) {
      request.invalidate()
      monitorData.value = null
      monitorLoading.value = false
      return
    }
    const { token, signal } = request.beginRead()
    monitorLoading.value = true
    try {
      const data = await api.getTeacherStudentsProgress(cid, { signal })
      if (!request.isLatest(token) || String(courseId.value) !== cid) return
      monitorData.value = data
    } catch (e) {
      if (request.isLatest(token) && String(courseId.value) === cid) {
        ElMessage.warning(`班级学习情况加载失败：${e.message}`)
        monitorData.value = null
      }
    } finally {
      if (request.isLatest(token)) monitorLoading.value = false
    }
  }

  function openMonitorStudentDetail(row) {
    monitorDetailStudent.value = row
    monitorDetailVisible.value = true
  }

  function progressColor(p) {
    if (p >= 80) return '#67c23a'
    if (p >= 40) return '#e6a23c'
    return '#f56c6c'
  }

  function statusText(p) {
    if (p >= 100) return '已完成'
    if (p > 0) return '进行中'
    return '未开始'
  }

  function statusType(p) {
    if (p >= 100) return 'success'
    if (p > 0) return 'primary'
    return 'info'
  }

  // 切课：失效在途请求并收起详情；数据本身由 courseId watch 重新加载
  watch(courseId, (cid) => {
    request.invalidate()
    monitorDetailVisible.value = false
    monitorDetailStudent.value = null
    if (!cid) {
      monitorData.value = null
      monitorLoading.value = false
      return
    }
    if (active.value) loadMonitorData()
  }, { immediate: true })

  // 每次进入教学监测 Tab 刷新（与原页 activeTab watcher 一致）
  watch(active, (isActive) => {
    if (isActive && courseId.value) loadMonitorData()
  })

  return {
    monitorData,
    monitorLoading,
    monitorSearch,
    monitorProgressFilter,
    monitorPage,
    monitorPageSize,
    monitorSort,
    monitorDetailVisible,
    monitorDetailStudent,
    progressBins,
    monitorKpis,
    filteredMonitorStudents,
    pagedMonitorStudents,
    onMonitorSortChange,
    loadMonitorData,
    openMonitorStudentDetail,
    progressColor,
    statusText,
    statusType,
  }
}
