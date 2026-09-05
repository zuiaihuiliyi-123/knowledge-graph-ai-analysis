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
      meta: { title: '数据总览' },
    },
    { path: '/teacher', name: 'teacher', component: TeacherView, meta: { title: '课程管理' } },
    { path: '/student', name: 'student', component: StudentView, meta: { title: '学习空间' } },
  ],
})

// 路由守卫：未登录访问受保护页 → 跳登录并携带回跳地址；已登录访问登录页 → 按角色回首页
router.beforeEach((to) => {
  const token = localStorage.getItem('kg_token')
  const role = readRole()
  if (!to.meta?.public && !token) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.name === 'login' && token) {
    return role === 'teacher'
      ? { path: '/dashboard' }
      : { path: '/student', query: { tab: 'overview' } }
  }
  // 学生访问教师专属页（数据总览 / 教师端课程管理）→ 转「学习总览」驾驶舱，
  // 避免学生误入教师界面看到「新建课程」等按钮后触发「仅教师可操作」权限报错。
  if (role === 'student' && (to.name === 'dashboard' || to.name === 'teacher')) {
    return { path: '/student', query: { tab: 'overview' } }
  }
})

router.afterEach((to) => {
  if (to.meta?.title) {
    document.title = `${to.meta.title} - 智育数据 · 课程知识图谱系统`
  }
})

export default router
