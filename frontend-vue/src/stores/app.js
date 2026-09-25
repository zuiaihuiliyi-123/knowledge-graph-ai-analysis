import { defineStore } from 'pinia'
import { api } from '../api'

const TOKEN_KEY = 'kg_token'
const USER_KEY = 'kg_user'
const STATUS_DISMISS_KEY = 'kg_backend_status_dismissed'
const LEARNING_CTX_KEY = 'kg_learning_context'
const SIDEBAR_KEY = 'kg_sidebar_collapsed'

function readLearningContext() {
  try {
    return JSON.parse(localStorage.getItem(LEARNING_CTX_KEY) || 'null') || {}
  } catch {
    return {}
  }
}

function writeLearningContext(ctx) {
  try {
    localStorage.setItem(
      LEARNING_CTX_KEY,
      JSON.stringify({ currentCourseId: ctx.currentCourseId, currentDocumentId: ctx.currentDocumentId })
    )
  } catch { /* 存储不可用时静默忽略 */ }
}

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
    // 健康检查进行中（App.vue 侧栏底部的状态点：检测中显示黄色脉冲）
    healthChecking: false,
    // 侧栏折叠状态（App.vue 的收起/展开按钮；跨刷新保留，避免每次进页面都要重新收起）
    sidebarCollapsed: localStorage.getItem(SIDEBAR_KEY) === '1',
    // 右下角后端服务状态浮窗是否已被用户关闭（会话级，下次登录重新显示）
    backendStatusDismissed: sessionStorage.getItem(STATUS_DISMISS_KEY) === '1',
    courses: [], // [{course_id, course_name, node_count, ...}] 后端课程列表（= 我可访问的课程）
    currentCourseId: '', // 当前选中的课程 ID（字符串）
    isLoading: false,
    coursesLoaded: false,
    // 个人资料（课程中心改造）：t_user_profile 的可编辑字段 + 身份信息
    // 未登录或尚未拉取时为 null，消费方一律走 getters.displayName / avatarUrl 兜底
    profile: null,
    // 学生端统一学习上下文（Phase 7）：Course → Document 后建立，所有学习 Tab 共享
    learningContext: {
      ...readLearningContext(), // 恢复持久化的 { currentCourseId, currentDocumentId }
      courseList: [], // 当前可选课程列表（与 courses 冗余，便于上下文聚合）
      documentList: [], // 当前课程的文档列表
    },
  }),
  getters: {
    isLoggedIn: (state) => !!state.token,
    // 角色由登录用户决定；未登录时默认 student（仅兜底，受路由守卫保护不会真正用到）
    role: (state) => state.user?.role || 'student',
    isTeacher: (state) => (state.user?.role || 'student') === 'teacher',
    /** 管理员：平台治理角色，与教师/学生是并列的三档之一 */
    isAdmin: (state) => (state.user?.role || 'student') === 'admin',
    username: (state) => state.user?.username || '',
    /** 展示名：昵称 > 真实姓名 > 登录响应里的 display_name > 用户名 */
    displayName: (state) =>
      state.profile?.nickname || state.profile?.real_name
      || state.user?.nickname || state.user?.real_name || state.user?.display_name
      || state.user?.username || '',
    /** 头像直链；无头像返回空串，由调用方回退为姓名首字母色块 */
    avatarUrl: (state) =>
      state.profile?.avatar_url || state.user?.avatar_url || '',
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
      // 登录响应已带 display_name / nickname / real_name / avatar_url，
      // 先据此填充 profile，侧边栏首屏就有正确昵称与头像（随后 fetchProfile 再补全完整资料）
      this.profile = this.profile || {
        user_id: user?.user_id,
        username: user?.username,
        role: user?.role,
        nickname: user?.nickname || null,
        real_name: user?.real_name || null,
        display_name: user?.display_name || null,
        avatar_url: user?.avatar_url || null,
      }
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
    /**
     * 清除「首次登录必须改密」标记（本人改密成功后调用）。
     *
     * 后端已把 t_user.must_change_password 置 0，这里同步本地副本 ——
     * 否则路由守卫仍按 localStorage 里的旧标记把人一直挡在改密页。
     */
    clearMustChangePassword() {
      if (!this.user) return
      this.user = { ...this.user, must_change_password: 0 }
      localStorage.setItem(USER_KEY, JSON.stringify(this.user))
    },
    logout() {
      this.token = ''
      this.user = null
      this.profile = null
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(USER_KEY)
      localStorage.removeItem(LEARNING_CTX_KEY)
      this.courses = []
      this.coursesLoaded = false
      this.currentCourseId = ''
      // 学习上下文也一并清空，避免下一个登录的账号看到上一个账号的课程/文档
      this.learningContext.currentCourseId = null
      this.learningContext.currentDocumentId = null
      this.learningContext.courseList = []
      this.learningContext.documentList = []
      // 退出后回到登录页，状态浮窗重新显示
      this.backendStatusDismissed = false
      sessionStorage.removeItem(STATUS_DISMISS_KEY)
    },

    // ---- 健康检查 ----
    async checkHealth() {
      this.healthChecking = true
      try {
        const resp = await fetch('/health', { signal: AbortSignal.timeout(3000) })
        this.backendOnline = resp.ok
      } catch {
        this.backendOnline = false
      } finally {
        this.healthChecked = true
        this.healthChecking = false
      }
    },

    /** 收起 / 展开侧栏（持久化到 localStorage） */
    toggleSidebar() {
      this.sidebarCollapsed = !this.sidebarCollapsed
      try {
        localStorage.setItem(SIDEBAR_KEY, this.sidebarCollapsed ? '1' : '0')
      } catch { /* 存储不可用时仅本次会话生效 */ }
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

    // ---- 个人资料（课程中心改造） ----
    /**
     * 拉取当前用户完整资料；失败不抛错（资料拿不到不应阻断任何页面）。
     *
     * 未登录时必须直接返回：登录页也会挂载 App，若无脑请求 /profile 会拿到 401，
     * 而 axios 拦截器对 401 的处理是「清 token + 跳登录页」，在登录页上会造成多余跳转。
     */
    async fetchProfile(force = false) {
      if (!this.token) return null
      if (this.profile && this.profile.__full && !force) return this.profile
      const data = await api.getProfile()
      this.profile = { ...data, __full: true }
      return this.profile
    },
    /** 资料保存后立即生效：侧边栏/头像无需刷新页面即可更新 */
    applyProfile(profile) {
      this.profile = { ...(this.profile || {}), ...profile, __full: true }
      if (this.user) {
        this.user = {
          ...this.user,
          nickname: this.profile.nickname,
          real_name: this.profile.real_name,
          avatar_url: this.profile.avatar_url,
        }
        localStorage.setItem(USER_KEY, JSON.stringify(this.user))
      }
    },

    // ---- 课程中心：加入 / 申请 / 退出 / 邀请 ----
    /** 任何「成员关系发生变化」的操作之后统一调用：让课程列表与已选上下文保持一致 */
    async refreshAfterMembershipChange() {
      await this.fetchCourses(true).catch(() => {})
      // 若当前上下文指向的课程已不在我的课程里（被移除/已退出），清空上下文
      const cid = this.learningContext.currentCourseId
      if (cid && !this.courses.some((c) => String(c.course_id) === String(cid))) {
        this.learningContext.currentCourseId = null
        this.learningContext.currentDocumentId = null
        this.learningContext.documentList = []
      }
      if (this.currentCourseId
          && !this.courses.some((c) => String(c.course_id) === String(this.currentCourseId))) {
        this.currentCourseId = ''
      }
      return this.courses
    },
    /** 用加课码加入课程（返回 {status:'approved'|'pending'}） */
    async joinByCode(joinCode, reason = null) {
      const data = await api.joinByCode(joinCode, reason)
      await this.refreshAfterMembershipChange()
      return data
    },
    /** 申请加入公开课程 */
    async applyToCourse(courseId, reason = null) {
      const data = await api.applyToCourse(courseId, reason)
      return data
    },
    /** 学生退出课程（软移除，学习记录与收藏保留） */
    async leaveCourse(courseId) {
      const data = await api.removeMember(courseId, this.user?.user_id, true)
      await this.refreshAfterMembershipChange()
      return data
    },
    /** 接受邀请加入课程 */
    async acceptInvite(token) {
      const data = await api.acceptInvite(token)
      await this.refreshAfterMembershipChange()
      return data
    },

    // ---- 学习上下文（Phase 7：Course → Document 统一） ----
    /** 建立 / 更新学习上下文（courseId/documentId 传 null 表示清空该层） */
    setLearningContext({ courseId = null, documentId = null } = {}) {
      if (courseId !== undefined) this.learningContext.currentCourseId = courseId ? String(courseId) : null
      if (documentId !== undefined) this.learningContext.currentDocumentId = documentId ? String(documentId) : null
      writeLearningContext(this.learningContext)
    },
    /** 切换课程时清空文档层（必须重新选文档，禁止沿用上一课程的文档） */
    clearLearningDocument() {
      this.learningContext.currentDocumentId = null
      this.learningContext.documentList = []
      writeLearningContext(this.learningContext)
    },
    /** 拉取某课程文档列表，写入 learningContext.documentList 并返回 */
    async fetchDocuments(courseId) {
      if (!courseId) {
        this.learningContext.documentList = []
        return []
      }
      const docs = await api.getDocuments(courseId)
      this.learningContext.documentList = docs || []
      return this.learningContext.documentList
    },
  },
})
