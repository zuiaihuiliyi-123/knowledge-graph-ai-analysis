import { createRouter, createWebHistory } from 'vue-router'
import TeacherView from '../views/TeacherView.vue'
import StudentView from '../views/StudentView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/teacher' },
    { path: '/teacher', name: 'teacher', component: TeacherView, meta: { title: '教师工作台' } },
    { path: '/student', name: 'student', component: StudentView, meta: { title: '学习空间' } },
  ],
})

router.afterEach((to) => {
  if (to.meta?.title) {
    document.title = `${to.meta.title} - 课程知识图谱系统`
  }
})

export default router
