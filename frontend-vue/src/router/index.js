import { createRouter, createWebHistory } from 'vue-router'
import LoginView from '../views/LoginView.vue'
import TeacherView from '../views/TeacherView.vue'
import StudentView from '../views/StudentView.vue'

function readRole() {
  try {
    return JSON.parse(localStorage.getItem('kg_user') || 'null')?.role
  } catch {
    return null
  }
}

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: LoginView,
      meta: { title: '登录', public: true },
    },
    {
      path: '/',
      redirect: '/dashboard',
    },
    {
      path: '/dashboard',
      name: 'dashboard',
      component: () => import('../views/DashboardView.vue'),
      meta: { title: '数据总览（演示占位）' },
    },
    { path: '/teacher', name: 'teacher', component: TeacherView, meta: { title: '教师工作台' } },
    { path: '/student', name: 'student', component: StudentView, meta: { title: '学习空间' } },
  ],
})

// 路由守卫：未登录访问受保护页 → 跳登录并携带回跳地址；已登录访问登录页 → 按角色回首页
router.beforeEach((to) => {
  const token = localStorage.getItem('kg_token')
  if (!to.meta?.public && !token) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.name === 'login' && token) {
    return { path: '/dashboard' }
  }
})

router.afterEach((to) => {
  if (to.meta?.title) {
    document.title = `${to.meta.title} - 智育数据 · 课程知识图谱系统`
  }
})

export default router
