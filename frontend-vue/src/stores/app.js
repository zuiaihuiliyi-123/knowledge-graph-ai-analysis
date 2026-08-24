import { defineStore } from 'pinia'

const COURSES_KEY = 'kg_courses_registry'

/**
 * 全局状态：角色、后端健康状态、课程注册表
 * 说明：后端目前没有"课程列表"接口，因此用 localStorage 记录上传过的课程，
 * 供图谱预览/编辑/问答时选择。后端补上课程列表接口后可替换为服务端数据。
 */
export const useAppStore = defineStore('app', {
  state: () => ({
    role: 'teacher', // 'teacher' | 'student'
    backendOnline: false,
    healthChecked: false,
    courses: JSON.parse(localStorage.getItem(COURSES_KEY) || '[]'),
  }),
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
    registerCourse({ courseId, name }) {
      const exist = this.courses.find((c) => c.courseId === courseId)
      if (!exist) {
        this.courses.push({ courseId, name: name || courseId })
      } else if (name) {
        exist.name = name
      }
      localStorage.setItem(COURSES_KEY, JSON.stringify(this.courses))
    },
  },
})
