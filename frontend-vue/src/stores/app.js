import { defineStore } from 'pinia'
import { api } from '../api'

/**
 * 全局状态：角色、后端健康状态、课程列表
 * 课程列表来自后端 /api/v1/courses（已不再使用 localStorage）。
 */
export const useAppStore = defineStore('app', {
  state: () => ({
    role: 'teacher', // 'teacher' | 'student'
    backendOnline: false,
    healthChecked: false,
    courses: [], // [{course_id, course_name, node_count, ...}] 后端课程列表
    currentCourseId: '', // 当前选中的课程 ID（字符串）
    isLoading: false, // 课程列表加载中
    coursesLoaded: false, // 是否已成功拉取过课程列表
  }),
  getters: {
    courseById: (state) => (id) =>
      state.courses.find((c) => String(c.course_id) === String(id)),
  },
  actions: {
    setRole(role) {
      this.role = role
    },
    async checkHealth() {
      try {
        const resp = await fetch('/health', { signal: AbortSignal.timeout(3000) })
        this.backendOnline = resp.ok
      } catch {
        this.backendOnline = false
      } finally {
        this.healthChecked = true
      }
    },
    /** 拉取课程列表（默认命中缓存，force=true 强制刷新） */
    async fetchCourses(force = false) {
      if (this.coursesLoaded && !force) return this.courses
      this.isLoading = true
      try {
        const data = await api.listCourses({ page_size: 100 })
        this.courses = data.items || []
        this.coursesLoaded = true
        return this.courses
      } finally {
        this.isLoading = false
      }
    },
    /** 创建课程：调用后端，成功后刷新列表并选中新课程 */
    async createCourse(name, extra = {}) {
      const data = await api.createCourse({ course_name: name, ...extra })
      // 刷新失败不阻断已成功的结果（例如 Neo4j 暂不可用时列表接口会报错）
      this.fetchCourses(true).catch(() => {})
      this.currentCourseId = String(data.course_id)
      return data
    },
    /** 删除课程：调用后端，成功后刷新列表 */
    async deleteCourse(id) {
      const data = await api.deleteCourse(id, true)
      this.fetchCourses(true).catch(() => {})
      if (String(this.currentCourseId) === String(id)) this.currentCourseId = ''
      return data
    },
  },
})
