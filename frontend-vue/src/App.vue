<template>
  <!-- 独立整页路由（登录 / 注册 / 邀请落地 / 文档阅读器）不套主框架，做到真正全屏 -->
  <router-view v-if="isStandalone" v-slot="{ Component }">
    <transition name="page" mode="out-in">
      <component :is="Component" />
    </transition>
  </router-view>

  <el-container v-else class="app-shell">
    <el-aside
      :class="{ collapsed: store.sidebarCollapsed }"
      :width="store.sidebarCollapsed ? '64px' : '232px'"
      class="app-sidebar"
    >
      <!-- 品牌区（点击回到当前角色的首页：管理员→工作台，其余→课程中心） -->
      <div class="brand" @click="router.push(homePath)">
        <div class="brand-logo"><BrandMark :size="30" /></div>
        <div v-show="!store.sidebarCollapsed" class="brand-text">
          <div class="brand-name">智育数据</div>
          <div class="brand-sub">课程知识图谱智能系统</div>
        </div>
      </div>

      <!-- 用户信息 -->
      <div v-show="!store.sidebarCollapsed" class="sidebar-user">
        <div class="user-avatar" :class="store.role">
          <img
            v-if="showAvatar"
            :src="store.avatarUrl"
            class="user-avatar-img"
            alt=""
            @error="avatarFailed = true"
          />
          <template v-else>{{ avatarText }}</template>
        </div>
        <div class="user-meta">
          <div class="user-name" :title="store.displayName">{{ store.displayName }}</div>
          <div class="user-role" :class="store.role">{{ roleText }}</div>
        </div>
        <el-button
          v-if="store.isLoggedIn"
          text
          size="small"
          class="user-logout"
          :title="'退出登录'"
          @click="handleLogout"
        >
          <el-icon><SwitchButton /></el-icon>
        </el-button>
      </div>
      <div v-show="store.sidebarCollapsed" class="sidebar-user-collapsed">
        <div class="user-avatar small" :class="store.role">
          <img
            v-if="showAvatar"
            :src="store.avatarUrl"
            class="user-avatar-img"
            alt=""
            @error="avatarFailed = true"
          />
          <template v-else>{{ avatarText }}</template>
        </div>
      </div>

      <!-- 导航菜单 -->
      <el-menu
        :default-active="activeMenu"
        :collapse="store.sidebarCollapsed"
        :collapse-transition="false"
        class="sidebar-menu"
        router
      >
        <template v-for="item in menuItems" :key="item.path">
          <el-sub-menu v-if="item.children" :index="item.path">
            <template #title>
              <el-icon><component :is="item.icon" /></el-icon>
              <span>{{ item.title }}</span>
            </template>
            <el-menu-item
              v-for="child in item.children"
              :key="child.path"
              :index="child.path"
              class="sidebar-menu-item"
            >
              <el-icon><component :is="child.icon" /></el-icon>
              <template #title>{{ child.title }}</template>
            </el-menu-item>
          </el-sub-menu>
          <el-menu-item v-else :index="item.path" class="sidebar-menu-item">
            <el-icon><component :is="item.icon" /></el-icon>
            <template #title>{{ item.title }}</template>
          </el-menu-item>
        </template>
      </el-menu>

      <!-- 侧栏底部：后端健康状态 + 折叠按钮 -->
      <div class="sidebar-footer">
        <div v-show="!store.sidebarCollapsed" class="health-line">
          <span class="health-dot" :class="healthClass" />
          <span class="health-text">{{ healthText }}</span>
        </div>
        <el-button
          text
          class="collapse-btn"
          :title="store.sidebarCollapsed ? '展开侧栏' : '收起侧栏'"
          @click="store.toggleSidebar()"
        >
          <el-icon>
            <Expand v-if="store.sidebarCollapsed" />
            <Fold v-else />
          </el-icon>
        </el-button>
      </div>
    </el-aside>

    <el-container class="main-container">
      <el-header class="app-header kg-glass" height="58px">
        <div class="header-left">
          <el-breadcrumb separator="/" class="app-breadcrumb">
            <el-breadcrumb-item :to="{ path: homePath }">{{ homeLabel }}</el-breadcrumb-item>
            <el-breadcrumb-item v-for="(c, i) in breadcrumbs" :key="i">{{ c }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="header-right">
          <span class="header-greet">
            <span class="greet-hi">{{ greetText }}</span>
            <b>{{ store.displayName }}</b>
          </span>
          <div class="header-avatar user-avatar" :class="store.role" @click="router.push('/profile')">
            <img
              v-if="showAvatar"
              :src="store.avatarUrl"
              class="user-avatar-img"
              alt=""
              @error="avatarFailed = true"
            />
            <template v-else>{{ avatarText }}</template>
          </div>
        </div>
      </el-header>

      <el-main class="app-main">
        <router-view v-slot="{ Component }">
          <transition name="page" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </el-main>
    </el-container>

    <!-- 全局 AI 助手（教师 / 学生共用；课程上下文由 AIChatWidget 从 store.learningContext 读取，
         教师端的选中态由 TeacherView 同步写入） -->
    <AIChatWidget v-if="store.isLoggedIn" />
  </el-container>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import {
  HomeFilled, DataAnalysis, Reading, User, UserFilled, EditPen, Notebook, Compass, Fold, Expand, SwitchButton, Share,
  Picture, Lock, CircleClose, Folder, Monitor, Document, Guide, Star,
} from '@element-plus/icons-vue'
import { useAppStore } from './stores/app'
import AIChatWidget from './components/AIChatWidget.vue'
import BrandMark from './components/BrandMark.vue'

const store = useAppStore()
const route = useRoute()
const router = useRouter()

// 这些路由自带整页布局，不显示侧边栏 / 顶栏 / 全局 AI 悬浮球
const STANDALONE_ROUTES = new Set(['login', 'register', 'invite', 'reader'])
const isStandalone = computed(() => STANDALONE_ROUTES.has(route.name))

const teacherMenu = [
  { path: '/course-center', title: '课程中心', icon: HomeFilled },
  { path: '/dashboard', title: '数据总览', icon: DataAnalysis },
  { path: '/teacher', title: '课程管理', icon: EditPen },
  { path: '/teacher?tab=preview', title: '图谱管理', icon: Share },
  { path: '/teacher?tab=questions', title: '题库管理', icon: Notebook },
  {
    path: '/profile',
    title: '个人中心',
    icon: User,
    children: [
      { path: '/profile?tab=basic', title: '基本资料', icon: UserFilled },
      { path: '/profile?tab=password', title: '密码管理', icon: Lock },
      { path: '/profile?tab=deactivate', title: '注销账号', icon: CircleClose },
    ],
  },
]
const studentMenu = [
  { path: '/course-center', title: '课程中心', icon: HomeFilled },
  { path: '/student', title: '学习总览', icon: DataAnalysis },
  {
    // 学习空间分组：课程文档/图谱浏览/做题练习是学习空间内的 Tab，
    // 收进子菜单避免与「学习总览」平铺混淆（index 仅作唯一标识，不参与跳转）
    path: '/student-space',
    title: '学习空间',
    icon: Compass,
    children: [
      { path: '/student?tab=documents', title: '课程文档', icon: Reading },
      { path: '/student?tab=browse', title: '图谱浏览', icon: Compass },
      { path: '/student?tab=path', title: '学习路径推荐', icon: Guide },
      { path: '/student?tab=favorites', title: '收藏夹', icon: Star },
      { path: '/student?tab=practice', title: '做题练习', icon: Notebook },
    ],
  },
  {
    path: '/profile',
    title: '个人中心',
    icon: User,
    children: [
      { path: '/profile?tab=basic', title: '基本资料', icon: UserFilled },
      { path: '/profile?tab=password', title: '密码管理', icon: Lock },
      { path: '/profile?tab=deactivate', title: '注销账号', icon: CircleClose },
    ],
  },
]
// 管理员导航：平台治理 / 用户管理 / 课程管理 / 资源管理 / 课程治理 / 系统监控 / 审计日志
// 刻意不含「课程中心」——管理员不参与教学，其课程视图是 /admin/courses（全平台课程）
const adminMenu = [
  { path: '/admin', title: '工作台', icon: HomeFilled },
  { path: '/admin/users', title: '用户管理', icon: UserFilled },
  { path: '/admin/courses', title: '课程管理', icon: Reading },
  { path: '/admin/resources', title: '资源管理', icon: Folder },
  { path: '/admin/governance', title: '课程治理', icon: Compass },
  { path: '/admin/system', title: '系统监控', icon: Monitor },
  { path: '/admin/audit-logs', title: '审计日志', icon: Document },
  {
    path: '/profile',
    title: '个人中心',
    icon: User,
    children: [
      { path: '/profile?tab=basic', title: '基本资料', icon: UserFilled },
      { path: '/profile?tab=password', title: '密码管理', icon: Lock },
      { path: '/profile?tab=deactivate', title: '注销账号', icon: CircleClose },
    ],
  },
]
const menuItems = computed(() => {
  if (store.role === 'admin') return adminMenu
  return store.role === 'teacher' ? teacherMenu : studentMenu
})

const activeMenu = computed(() => {
  if (route.path === '/teacher') {
    if (route.query.tab === 'questions') return '/teacher?tab=questions'
    if (route.query.tab === 'preview' || route.query.tab === 'edit') return '/teacher?tab=preview'
    return '/teacher'
  }
  if (route.path === '/student') {
    const map = {
      documents: '/student?tab=documents', browse: '/student?tab=browse', qa: '/student?tab=qa',
      path: '/student?tab=path', favorites: '/student?tab=favorites', practice: '/student?tab=practice',
    }
    return map[route.query.tab] || '/student'
  }
  if (route.path === '/profile') {
    const map = { basic: '/profile?tab=basic', password: '/profile?tab=password', deactivate: '/profile?tab=deactivate' }
    return map[route.query.tab] || '/profile?tab=basic'
  }
  return route.path
})

// 面包屑与品牌区/首页链接的目标都随角色变化：管理员的"首页"是 /admin 而不是课程中心
const homePath = computed(() => (store.role === 'admin' ? '/admin' : '/course-center'))
const homeLabel = computed(() => (store.role === 'admin' ? '工作台' : '首页'))

const breadcrumbs = computed(() => {
  const map = {
    '/dashboard': ['数据总览'],
    '/teacher': ['课程管理', route.query.tab ? tabTitle(route.query.tab) : '我的课程'],
    '/student': ['学习空间', route.query.tab ? studentTabTitle(route.query.tab) : '学习总览'],
    '/course-center': ['课程中心'],
    '/profile': ['个人中心'],
    '/reader': ['文档阅读'],
    '/403': ['无权限'],
    '/admin': ['管理工作台'],
    '/admin/users': ['用户管理'],
    '/admin/courses': ['课程管理'],
    '/admin/resources': ['资源管理'],
    '/admin/governance': ['课程治理'],
    '/admin/system': ['系统监控'],
    '/admin/audit-logs': ['审计日志'],
  }
  return map[route.path] || []
})
function tabTitle(t) {
  return { courses: '我的课程', documents: '课程文档', preview: '图谱管理', edit: '图谱管理', monitor: '教学监测', questions: '题库管理', members: '学生管理' }[t] || ''
}
function studentTabTitle(t) {
  return { overview: '学习总览', documents: '课程文档', browse: '图谱浏览', qa: '智能问答', path: '学习路径推荐', favorites: '收藏夹', practice: '做题练习' }[t] || ''
}

// 无头像时回退的首字母：取自侧栏展示的同一个名字（昵称 > 真实姓名 > 用户名），
// 与 ProfileView 的 initial 保持一致，避免「显示小智、字母却是 E」的错位
const avatarText = computed(() => (store.displayName || 'U').slice(0, 1).toUpperCase())
// 真实头像优先，无头像或图片加载失败时回退到上面的首字母色块
const avatarFailed = ref(false)
const showAvatar = computed(() => !!store.avatarUrl && !avatarFailed.value)
// 更换头像后 avatarUrl 会带新的版本号（?v=时间戳），此时重置失败标记再试一次
watch(() => store.avatarUrl, () => { avatarFailed.value = false })
const roleText = computed(() => ({ teacher: '教师', student: '学生', admin: '管理员' }[store.role] || '学生'))
const greetText = computed(() => {
  const h = new Date().getHours()
  if (h < 6) return '夜深了'
  if (h < 12) return '早上好'
  if (h < 14) return '中午好'
  if (h < 18) return '下午好'
  return '晚上好'
})

const healthClass = computed(() => {
  if (store.healthChecking) return 'checking'
  return store.backendOnline ? 'online' : 'offline'
})
const healthText = computed(() => {
  if (store.healthChecking) return '服务检测中…'
  return store.backendOnline ? '后端服务在线' : '后端服务未连接'
})

async function handleLogout() {
  try {
    await ElMessageBox.confirm('确定要退出登录吗？', '提示', { type: 'warning' })
    store.logout()
    await router.replace('/login')
  } catch { /* 取消 */ }
}

onMounted(() => {
  if (store.isLoggedIn) {
    store.fetchProfile().catch(() => {})
    store.checkHealth().catch(() => {})
  }
})
</script>

<style scoped>
.app-shell {
  height: 100vh;
}

/* ============ 侧边栏 ============ */
.app-sidebar {
  position: relative;
  background:
    radial-gradient(120% 60% at 20% 0%, rgba(91, 141, 239, .22) 0%, transparent 55%),
    radial-gradient(120% 80% at 100% 100%, rgba(139, 92, 246, .16) 0%, transparent 50%),
    linear-gradient(180deg, #121a3d 0%, #0c1230 100%);
  transition: width .28s cubic-bezier(.22,.8,.36,1);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-right: 1px solid rgba(255,255,255,.05);
  z-index: 20;
}
.app-sidebar::before {
  /* 细网格纹理 */
  content: '';
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,.025) 1px, transparent 1px);
  background-size: 26px 26px;
  pointer-events: none;
}
.app-sidebar > * { position: relative; z-index: 1; }

/* 品牌 */
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 18px 14px;
  cursor: pointer;
}
.brand-logo {
  width: 38px;
  height: 38px;
  border-radius: 11px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #5b8def, #8b5cf6);
  box-shadow: 0 6px 16px -4px rgba(91, 141, 239, .6), inset 0 1px 0 rgba(255,255,255,.25);
  flex-shrink: 0;
}
.brand-text { min-width: 0; }
.brand-name {
  color: #fff;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 1px;
  line-height: 1.2;
}
.brand-sub {
  color: rgba(200, 208, 238, .55);
  font-size: 10px;
  margin-top: 2px;
  white-space: nowrap;
}

/* 用户块 */
.sidebar-user {
  margin: 4px 12px 12px;
  padding: 10px;
  display: flex;
  align-items: center;
  gap: 9px;
  border-radius: 12px;
  background: rgba(255,255,255,.05);
  border: 1px solid rgba(255,255,255,.07);
}
.sidebar-user-collapsed {
  display: flex;
  justify-content: center;
  margin: 4px 0 12px;
}
.user-avatar {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-weight: 700;
  font-size: 15px;
  background: linear-gradient(135deg, #5b8def, #6a5cf6);
  box-shadow: 0 4px 10px -3px rgba(91,141,239,.6);
}
.user-avatar.small { width: 32px; height: 32px; font-size: 14px; }
/* 真实头像图片：铺满色块并跟随圆角，非正方形图用 cover 裁切不变形 */
.user-avatar-img { width: 100%; height: 100%; object-fit: cover; border-radius: inherit; }
.user-avatar.teacher { background: linear-gradient(135deg, #f5a623, #f4794d); box-shadow: 0 4px 10px -3px rgba(245,166,35,.55); }
/* 管理员：沿用品牌紫，与教师橙 / 学生蓝在同一套色系里区分开 */
.user-avatar.admin { background: linear-gradient(135deg, #8b5cf6, #5b8def); box-shadow: 0 4px 10px -3px rgba(139,92,246,.6); }
.user-meta { flex: 1; min-width: 0; }
.user-name {
  color: #e8ecfb;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.user-role {
  display: inline-block;
  margin-top: 3px;
  font-size: 10px;
  line-height: 1;
  padding: 3px 7px;
  border-radius: 5px;
  font-weight: 600;
  background: rgba(34,192,138,.18);
  color: #4fe0ac;
}
.user-role.teacher { background: rgba(245,166,35,.18); color: #ffc266; }
.user-role.admin { background: rgba(139,92,246,.2); color: #c4b0ff; }
.user-logout {
  color: rgba(200,208,238,.6) !important;
  padding: 4px;
}
.user-logout:hover { color: #ff8095 !important; }

/* 菜单 */
.sidebar-menu {
  flex: 1;
  border-right: none;
  background: transparent;
  padding: 2px 10px;
  overflow-y: auto;
  overflow-x: hidden;
}
.sidebar-menu::-webkit-scrollbar { width: 0; }
.sidebar-menu:not(.el-menu--collapse) { width: 100%; }
.sidebar-menu :deep(.el-menu-item) {
  height: 44px;
  line-height: 44px;
  margin-bottom: 4px;
  border-radius: 11px;
  color: #aab3d4;
  font-size: 14px;
  font-weight: 500;
  padding-left: 14px !important;
  position: relative;
  transition: all .22s ease;
}
.sidebar-menu :deep(.el-menu-item .el-icon) {
  font-size: 17px;
  color: inherit;
}
.sidebar-menu :deep(.el-menu-item:hover) {
  background: rgba(255,255,255,.07);
  color: #fff;
}
/* 子菜单分组（学习空间）：分组标题与内联面板跟随侧边栏深色主题 */
.sidebar-menu :deep(.el-sub-menu__title) {
  height: 44px;
  line-height: 44px;
  margin-bottom: 4px;
  border-radius: 11px;
  color: #aab3d4;
  font-size: 14px;
  font-weight: 500;
  padding-left: 14px !important;
  transition: all .22s ease;
}
.sidebar-menu :deep(.el-sub-menu__title:hover) {
  background: rgba(255,255,255,.07);
  color: #fff;
}
/* 分组展开态：标题渐变高亮 */
.sidebar-menu :deep(.el-sub-menu.is-opened > .el-sub-menu__title) {
  background: linear-gradient(90deg, rgba(91,141,239,.18), rgba(106,92,246,.10));
  color: #fff;
  box-shadow: inset 0 0 0 1px rgba(143,176,255,.14);
}
.sidebar-menu :deep(.el-sub-menu.is-active > .el-sub-menu__title) {
  color: #fff;
}
/* 展开箭头：平滑旋转 + 配色 */
.sidebar-menu :deep(.el-sub-menu__icon-arrow) {
  right: 14px;
  width: 13px;
  height: 13px;
  color: #8a93b8;
  transition: transform .25s ease, color .25s ease;
}
.sidebar-menu :deep(.el-sub-menu.is-opened > .el-sub-menu__title .el-sub-menu__icon-arrow) {
  color: #cdd6ff;
}
/* 分组内联面板：磨砂底 + 左侧渐变引导线 */
.sidebar-menu :deep(.el-sub-menu .el-menu) {
  position: relative;
  background: rgba(255,255,255,.05);
  border-radius: 11px;
  padding: 6px;
  margin: 0 0 8px;
  box-shadow: inset 0 0 0 1px rgba(255,255,255,.04);
}
.sidebar-menu :deep(.el-sub-menu .el-menu::before) {
  content: '';
  position: absolute;
  left: 15px;
  top: 13px;
  bottom: 13px;
  width: 2px;
  border-radius: 2px;
  background: linear-gradient(180deg, rgba(91,141,239,.55), rgba(106,92,246,.16));
  pointer-events: none;
}
/* 分组子项：内嵌胶囊 */
.sidebar-menu :deep(.el-sub-menu .el-menu .el-menu-item) {
  padding-left: 30px !important;
  height: 38px;
  line-height: 38px;
  font-size: 13px;
  border-radius: 9px;
  color: #9aa4c8;
  margin: 2px 0;
}
.sidebar-menu :deep(.el-sub-menu .el-menu .el-menu-item .el-icon) {
  font-size: 15px;
}
.sidebar-menu :deep(.el-sub-menu .el-menu .el-menu-item:hover) {
  background: rgba(255,255,255,.09);
  color: #fff;
}
/* 激活子项：渐变胶囊（覆盖全局外溢强调条，避免错位） */
.sidebar-menu :deep(.el-sub-menu .el-menu .el-menu-item.is-active) {
  background: linear-gradient(90deg, rgba(91,141,239,.95), rgba(106,92,246,.85));
  color: #fff;
  font-weight: 600;
  box-shadow: 0 6px 14px -8px rgba(91,141,239,.85), inset 0 1px 0 rgba(255,255,255,.16);
}
.sidebar-menu :deep(.el-sub-menu .el-menu .el-menu-item.is-active::before) {
  display: none;
}
.sidebar-menu :deep(.el-menu-item.is-active) {
  color: #fff;
  font-weight: 600;
  background: linear-gradient(90deg, rgba(91,141,239,.95), rgba(106,92,246,.85));
  box-shadow: 0 8px 18px -8px rgba(91,141,239,.8), inset 0 1px 0 rgba(255,255,255,.18);
}
.sidebar-menu :deep(.el-menu-item.is-active::before) {
  content: '';
  position: absolute;
  left: -10px;
  top: 50%;
  transform: translateY(-50%);
  width: 4px;
  height: 20px;
  border-radius: 0 4px 4px 0;
  background: #8fb0ff;
  box-shadow: 0 0 10px #8fb0ff;
}
/* 折叠态菜单项居中 */
.app-sidebar.collapsed .sidebar-menu { padding: 2px 8px; }
.app-sidebar.collapsed .sidebar-menu :deep(.el-menu-item) {
  padding-left: 0 !important;
  justify-content: center;
  border-radius: 11px;
}

/* 侧栏底部 */
.sidebar-footer {
  padding: 10px 14px 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  border-top: 1px solid rgba(255,255,255,.06);
}
.health-line {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 11px;
  color: rgba(200,208,238,.6);
  white-space: nowrap;
}
.health-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}
.health-dot.online { background: #3ddc97; box-shadow: 0 0 8px #3ddc97; }
.health-dot.offline { background: #ff6b81; box-shadow: 0 0 8px #ff6b81; }
.health-dot.checking { background: #ffc266; box-shadow: 0 0 8px #ffc266; animation: kg-spin-glow 1.4s linear infinite; }
.collapse-btn {
  color: rgba(200,208,238,.6) !important;
  margin-left: auto;
  padding: 5px;
  min-height: auto;
  height: auto;
}
.collapse-btn:hover { color: #fff !important; background: rgba(255,255,255,.08) !important; }

/* ============ 主区域 ============ */
.main-container {
  min-width: 0;
  background: var(--bg-page);
  background-image:
    radial-gradient(60% 40% at 85% -5%, rgba(91,141,239,.06), transparent 70%),
    radial-gradient(50% 35% at 10% -5%, rgba(139,92,246,.05), transparent 70%);
}
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: rgba(255,255,255,.82);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border-bottom: 1px solid var(--border-light);
  padding: 0 22px;
  position: sticky;
  top: 0;
  z-index: 10;
}
.app-breadcrumb { font-size: 13px; }
.app-breadcrumb :deep(.el-breadcrumb__inner) { color: var(--text-secondary); font-weight: 500; }
.app-breadcrumb :deep(.el-breadcrumb__item:last-child .el-breadcrumb__inner) {
  color: var(--text-primary);
  font-weight: 600;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}
.header-greet {
  font-size: 13px;
  color: var(--text-secondary);
}
.header-greet b { color: var(--text-primary); font-weight: 600; }
.header-avatar {
  width: 34px;
  height: 34px;
  cursor: pointer;
  transition: transform .2s ease;
}
.header-avatar:hover { transform: scale(1.08); }

.app-main {
  padding: 20px 22px 28px;
  overflow-y: auto;
}

/* 路由切换动效 */
.page-enter-active { transition: all .3s cubic-bezier(.22,.8,.36,1); }
.page-leave-active { transition: all .18s ease; }
.page-enter-from { opacity: 0; transform: translateY(10px); }
.page-leave-to { opacity: 0; transform: translateY(-6px); }

@media (max-width: 768px) {
  .header-greet { display: none; }
  .app-main { padding: 14px; }
}
</style>
