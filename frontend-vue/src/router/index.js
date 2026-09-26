import { createRouter, createWebHistory } from 'vue-router'
import LoginView from '../views/LoginView.vue'
import TeacherView from '../views/TeacherView.vue'
import StudentView from '../views/StudentView.vue'

function readUser() {
  try {
    return JSON.parse(localStorage.getItem('kg_user') || 'null')
  } catch {
    return null
  }
}

function readRole() {
  return readUser()?.role
}

/** 按角色决定登录后的落点（课程中心是教师与学生共用的首页；管理员落管理工作台） */
function homeFor(role) {
  if (role === 'admin') return { path: '/admin' }
  return { path: '/course-center', query: { tab: 'mine' } }
}

// 管理员与教师/学生共用的页面：个人中心与修改密码对三种角色都成立
const SHARED_AUTHED_ROUTES = new Set(['profile', 'change-password'])

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
      // 注册：独立整页（与登录页同样的左右分栏布局），未登录可访问
      path: '/register',
      name: 'register',
      component: () => import('../views/RegisterView.vue'),
      meta: { title: '注册', public: true },
    },
    {
      // 无权限页：学生/教师误入管理员端时落到这里（不静默跳回首页，否则用户会以为自己点错了）
      path: '/403',
      name: 'forbidden',
      component: () => import('../views/ForbiddenView.vue'),
      meta: { title: '无权限' },
    },
    {
      // 根路径按角色分流（函数式 redirect 在导航时才求值，能读到最新角色）
      path: '/',
      redirect: () => homeFor(readRole()),
    },
    {
      // 课程中心：教师 / 学生共用（我的课程 / 发现课程 / 加入课程）
      path: '/course-center',
      name: 'course-center',
      component: () => import('../views/CourseCenterView.vue'),
      meta: { title: '课程中心' },
    },
    {
      // 课程邀请落地页：独立整页（未加入前不应看到侧边栏与其它课程入口）
      // query: 无；token 在 path 上
      path: '/invite/:token',
      name: 'invite',
      component: () => import('../views/InviteLandingView.vue'),
      meta: { title: '课程邀请' },
    },
    {
      // 修改密码：独立整页（从个人中心跳转）
      path: '/change-password',
      name: 'change-password',
      component: () => import('../views/ChangePasswordView.vue'),
      meta: { title: '修改密码' },
    },
    {
      // 个人中心：教师 / 学生 / 管理员共用（管理员复用同一个页面，不另做一份）
      path: '/profile',
      name: 'profile',
      component: () => import('../views/ProfileView.vue'),
      meta: { title: '个人中心' },
    },
    {
      path: '/dashboard',
      name: 'dashboard',
      component: () => import('../views/DashboardView.vue'),
      meta: { title: '数据总览' },
    },
    { path: '/teacher', name: 'teacher', component: TeacherView, meta: { title: '课程管理' } },
    { path: '/student', name: 'student', component: StudentView, meta: { title: '学习空间' } },
    {
      // 文档在线阅读器：独立整页（不走主框架布局），需要高度占满屏幕
      // query: course_id（用于文档信息与知识点）
      //        from（teacher / teacher-graph / student，决定「返回」去向，
      //              见 DocumentReaderView 的 goBack：分别为课程文档列表 / 图谱管理 / 学生文档列表）
      path: '/reader/:docId',
      name: 'reader',
      component: () => import('../views/DocumentReaderView.vue'),
      meta: { title: '文档阅读' },
    },

    // ---- 管理员端（meta.admin 是路由守卫唯一依据；真正的权限判定在后端 require_admin） ----
    {
      path: '/admin',
      name: 'admin',
      component: () => import('../views/admin/AdminDashboardView.vue'),
      meta: { title: '管理工作台', admin: true },
    },
    {
      path: '/admin/users',
      name: 'admin-users',
      component: () => import('../views/admin/AdminUsersView.vue'),
      meta: { title: '用户管理', admin: true },
    },
    {
      path: '/admin/courses',
      name: 'admin-courses',
      component: () => import('../views/admin/AdminCoursesView.vue'),
      meta: { title: '课程管理', admin: true },
    },
    {
      path: '/admin/resources',
      name: 'admin-resources',
      component: () => import('../views/admin/AdminResourcesView.vue'),
      meta: { title: '资源管理', admin: true },
    },
    {
      path: '/admin/governance',
      name: 'admin-governance',
      component: () => import('../views/admin/AdminGovernanceView.vue'),
      meta: { title: '课程治理', admin: true },
    },
    {
      path: '/admin/system',
      name: 'admin-system',
      component: () => import('../views/admin/AdminSystemView.vue'),
      meta: { title: '系统监控', admin: true },
    },
    {
      path: '/admin/audit-logs',
      name: 'admin-audit-logs',
      component: () => import('../views/admin/AdminAuditLogsView.vue'),
      meta: { title: '审计日志', admin: true },
    },
  ],
})

// 路由守卫：未登录访问受保护页 → 跳登录并携带回跳地址；已登录访问登录页 → 按角色回首页
router.beforeEach((to) => {
  const token = localStorage.getItem('kg_token')
  const user = readUser()
  const role = user?.role
  if (!to.meta?.public && !token) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  // 已登录用户不应再看到登录 / 注册页
  if ((to.name === 'login' || to.name === 'register') && token) {
    return homeFor(role)
  }

  // 首次登录强制改密：引导账号的随机密码、以及被管理员重置过密码的账号，
  // 在改密之前只能停留在「修改密码」页（只允许去退出登录，所以 /login 不受限）。
  // 后端不依赖这个守卫做安全判定——它只是把人引导到该去的地方；
  // 未改密并不会让接口变得更宽松（改密标记不改变任何接口的授权结果）。
  if (token && user?.must_change_password && to.name !== 'change-password'
      && !to.meta?.public) {
    return { name: 'change-password', query: { forced: '1' } }
  }

  // 管理员端：仅 admin 可进；学生 / 教师落到 /403（不是静默跳回自己首页，
  // 否则用户点了管理员链接却"什么都没发生"，无法判断是权限问题还是页面出错）
  if (to.meta?.admin && role !== 'admin') {
    return { name: 'forbidden' }
  }
  // 反向：管理员不该进教师 / 学生学习界面（管理员不属于任何课程，进去只会看到空列表）
  if (role === 'admin' && !to.meta?.admin && !to.meta?.public
      && !SHARED_AUTHED_ROUTES.has(to.name)) {
    return { name: 'admin' }
  }

  // 学生访问教师专属页（数据总览 / 教师端课程管理）→ 转「学习总览」驾驶舱，
  // 避免学生误入教师界面看到「新建课程」等按钮后触发「仅教师可操作」权限报错。
  //
  // 注意：这里按【路由 name】精确匹配，因此课程中心 / 个人中心 / 邀请落地页
  // 这三个教师与学生共用的页面不会被拦截，无需额外的 meta.roles 机制。
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
