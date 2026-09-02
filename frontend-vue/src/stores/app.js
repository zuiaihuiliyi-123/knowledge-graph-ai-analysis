import { defineStore } from 'pinia'
import { api } from '../api'

const TOKEN_KEY = 'kg_token'
const USER_KEY = 'kg_user'
const STATUS_DISMISS_KEY = 'kg_backend_status_dismissed'

function readUser() {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || 'null')
  } catch {
    return null
  }
}

/**
 * 全局状态：认证（token/user）、角色、后端健康状态、课程列表
 * 课程列表来自后端 /api/v1/courses（已不再使用 localStorage）。
 */
export const useAppStore = defineStore('app', {
  state: () => ({
    token: localStorage.getItem(TOKEN_KEY) || '',
    user: readUser(),
    backendOnline: false,
    healthChecked: false,
    // 右下角后端服务状态浮窗是否已被用户关闭（会话级，下次登录重新显示）
    backendStatusDismissed: sessionStorage.getItem(STATUS_DISMISS_KEY) === '1',
    courses: [], // [{course_id, course_name, node_count, ...}] 后端课程列表
    currentCourseId: '', // 当前选中的课程 ID（字符串）
    isLoading: false,
    coursesLoaded: false,
  }),
  getters: {
    isLoggedIn: (state) => !!state.token,
    // 角色由登录用户决定；未登录时默认 student（仅兜底，受路由守卫保护不会真正用到）
    role: (state) => state.user?.role || 'student',
    username: (state) => state.user?.username || '',
    courseById: (state) => (id) =>
      state.courses.find((c) => String(c.course_id) === String(id)),
  },
  actions: {
    // ---- 认证 ----
    setAuth({ token, user }) {
      this.token = token
      this.user = user
      localStorage.setItem(TOKEN_KEY, token)
      localStorage.setItem(USER_KEY, JSON.stringify(user))
      // 重新登录后，右下角后端服务状态浮窗重新显示
      this.backendStatusDismissed = false
      sessionStorage.removeItem(STATUS_DISMISS_KEY)
    },
    /** 登录：成功后写入 token/user 并持久化 */
    async login(username, password) {
      const data = await api.login({ username, password })
      this.setAuth({ token: data.access_token, user: data.user })
      return data
    },
    /** 注册：仅调用后端创建账号，不自动登录 */
    async register(payload) {
      return api.register(payload)
    },
    logout() {
      this.token = ''
      this.user = null
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(USER_KEY)
      this.courses = []
      this.coursesLoaded = false
      this.currentCourseId = ''
      // 退出后回到登录页，状态浮窗重新显示
      this.backendStatusDismissed = false
      sessionStorage.removeItem(STATUS_DISMISS_KEY)
    },

    // ---- 健康检查 ----
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

    /** 关闭右下角后端服务状态浮窗：本次登录会话内不再显示，下次登录重新出现 */
    dismissBackendStatus() {
      this.backendStatusDismissed = true
      sessionStorage.setItem(STATUS_DISMISS_KEY, '1')
    },

    // ---- 课程 ----
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
