<template>
  <!-- 登录页为独立整页，不套侧边栏布局 -->
  <router-view v-if="$route.name === 'login'" />

  <el-container v-else class="app-layout">
    <!-- 深色侧边栏（可折叠） -->
    <el-aside :width="collapsed ? '64px' : '230px'" class="app-aside">
      <div class="logo">
        <el-icon class="logo-icon" :size="26"><DataAnalysis /></el-icon>
        <div v-if="!collapsed" class="logo-text">
          <div class="logo-title">智育数据</div>
          <div class="logo-sub">课程知识图谱智能系统</div>
        </div>
      </div>

      <div class="user-box" :class="{ collapsed }">
        <div class="user-avatar">{{ store.username.slice(0, 1).toUpperCase() }}</div>
        <div v-if="!collapsed" class="user-meta">
          <div class="user-name">{{ store.username }}</div>
          <el-tag size="small" :type="store.role === 'teacher' ? 'warning' : 'success'" effect="dark">
            {{ store.role === 'teacher' ? '教师' : '学生' }}
          </el-tag>
        </div>
        <el-button
          v-if="!collapsed"
          text
          size="small"
          type="danger"
          @click="onLogout"
          class="logout-btn"
        >退出</el-button>
      </div>

      <el-menu
        :default-active="activeMenu"
        :collapse="collapsed"
        :collapse-transition="false"
        router
        class="app-menu"
        background-color="transparent"
        text-color="#b0b8d1"
        active-text-color="#409eff"
      >
        <!-- 数据总览（教师全局统计）/ 学习总览（学生学习驾驶舱） -->
        <el-menu-item :index="store.role === 'teacher' ? '/dashboard' : '/student?tab=overview'">
          <el-icon><DataAnalysis /></el-icon>
          <template #title>{{ store.role === 'teacher' ? '数据总览' : '学习总览' }}</template>
        </el-menu-item>

        <template v-if="store.role === 'teacher'">
          <el-menu-item index="/teacher?tab=courses">
            <el-icon><Notebook /></el-icon>
            <template #title>课程管理</template>
          </el-menu-item>
          <el-menu-item index="/teacher?tab=monitor" class="menu-sub">
            <el-icon><DataLine /></el-icon>
            <template #title>教学监测</template>
          </el-menu-item>
          <el-menu-item index="/teacher?tab=upload">
            <el-icon><Upload /></el-icon>
            <template #title>文档上传</template>
          </el-menu-item>
          <el-menu-item index="/teacher?tab=preview" class="menu-sub">
            <el-icon><Search /></el-icon>
            <template #title>图谱预览</template>
          </el-menu-item>
          <el-menu-item index="/teacher?tab=edit" class="menu-sub">
            <el-icon><EditPen /></el-icon>
            <template #title>编辑图谱</template>
          </el-menu-item>
        </template>
        <template v-else>
          <el-menu-item index="/student?tab=browse">
            <el-icon><Compass /></el-icon>
            <template #title>图谱浏览</template>
          </el-menu-item>
          <el-menu-item index="/student?tab=qa" class="menu-sub">
            <el-icon><ChatDotRound /></el-icon>
            <template #title>智能问答</template>
          </el-menu-item>
          <el-menu-item index="/student?tab=path" class="menu-sub">
            <el-icon><Guide /></el-icon>
            <template #title>学习路径推荐</template>
          </el-menu-item>
          <el-menu-item index="/student?tab=favorites" class="menu-sub">
            <el-icon><StarFilled /></el-icon>
            <template #title>收藏夹</template>
          </el-menu-item>
        </template>
      </el-menu>

      <div class="aside-footer" :class="{ collapsed }">
        <div v-if="!collapsed" class="health-line">
          <i class="health-dot" :class="store.backendOnline ? 'on' : 'off'"></i>
          <span v-if="store.backendOnline">后端服务在线</span>
          <span v-else-if="store.healthChecked">后端服务离线</span>
          <span v-else>检查后端中…</span>
        </div>
        <div v-if="!collapsed" class="health-tip">启动后端：<code>python -m uvicorn app.main:app --reload</code></div>
      </div>
    </el-aside>

    <!-- 右侧：顶部 Header + 主内容 -->
    <el-container class="app-body">
      <el-header class="app-header" height="56px">
        <div class="header-left">
          <el-button
            text
            class="collapse-btn"
            :icon="collapsed ? Expand : Fold"
            @click="collapsed = !collapsed"
          />
          <el-breadcrumb separator="/">
            <el-breadcrumb-item :to="{ path: '/dashboard' }">首页</el-breadcrumb-item>
            <el-breadcrumb-item>{{ pageBase }}</el-breadcrumb-item>
            <el-breadcrumb-item v-if="pageTab">{{ pageTab }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="header-right">
          <span class="welcome">欢迎，{{ store.username }}</span>
        </div>
      </el-header>

      <!-- 主内容区（浅色背景） -->
      <el-main class="app-main">
        <router-view />
      </el-main>
    </el-container>

    <!-- 右下角后端服务状态（教师/学生端） -->
    <div
      v-if="!store.backendStatusDismissed"
      class="backend-status"
      :class="store.backendOnline ? 'online' : store.healthChecked ? 'offline' : 'checking'"
    >
      <img :src="statusImage" class="status-avatar" alt="服务状态" />
      <div class="status-meta">
        <div class="status-title">
          <i class="status-dot"></i>
          <span>
            {{ store.backendOnline ? '后端服务在线' : store.healthChecked ? '后端服务离线' : '检查后端服务…' }}
          </span>
        </div>
        <div class="status-desc">
          {{
            store.backendOnline
              ? '所有功能可正常使用'
              : '请启动后端：python -m uvicorn app.main:app --reload'
          }}
        </div>
      </div>
      <button
        class="status-close"
        type="button"
        aria-label="关闭"
        title="关闭"
        @click="store.dismissBackendStatus()"
      >
        <el-icon :size="12"><Close /></el-icon>
      </button>
    </div>
  </el-container>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Upload, Compass, DataAnalysis, Search, EditPen, ChatDotRound, Guide, Fold, Expand, Notebook, Close, StarFilled, DataLine,
} from '@element-plus/icons-vue'
import { useAppStore } from './stores/app'
import statusImage from './phtotos/A10赛题项目理解(1).png'

const store = useAppStore()
const router = useRouter()
const route = useRoute()

const collapsed = ref(false)

// Tab 子页面中文名（面包屑 + 侧边栏 active 一致）
// 注：courses 为课程管理默认 Tab，面包屑主级已是「课程管理」，故不再重复显示为第三级
const TAB_LABELS = {
  overview: '学习总览',
  upload: '文档上传',
  preview: '图谱预览',
  edit: '编辑图谱',
  monitor: '教学监测',
  browse: '图谱浏览',
  qa: '智能问答',
  path: '学习路径推荐',
  favorites: '收藏夹',
}

// 侧边栏 active：将 /teacher?tab=upload 等映射为菜单 index，保证 URL / 菜单 / 面包屑三者一致
const activeMenu = computed(() => {
  const tab = route.query.tab
  return tab ? `${route.path}?tab=${tab}` : route.path
})
const pageBase = computed(() => route.meta?.title || '')
const pageTab = computed(() => TAB_LABELS[route.query.tab] || '')

function onLogout() {
  store.logout()
  router.push('/login')
}

onMounted(() => {
  store.checkHealth()
  setInterval(() => store.checkHealth(), 30000)
})
</script>

<style scoped>
.app-layout {
  height: 100vh;
}

/* ===== 深色侧边栏（核心风格改动） ===== */
.app-aside {
  background: linear-gradient(180deg, #1a1f36 0%, #161b2e 100%);
  border-right: 1px solid rgba(255,255,255,0.06);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  color: #b0b8d1;
  transition: width 0.25s ease;
}

.logo {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 0 16px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
  height: 56px;
  box-sizing: border-box;
}
.logo-icon {
  font-size: 26px;
  color: #409eff;
  flex-shrink: 0;
}
.logo-text {
  min-width: 0;
}
.logo-title {
  font-weight: 700;
  font-size: 16px;
  color: #e8ecf4;
  letter-spacing: 1px;
  white-space: nowrap;
}
.logo-sub {
  font-size: 11px;
  color: #6b7394;
  margin-top: 2px;
  white-space: nowrap;
}

.user-box {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px 12px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
}
.user-box.collapsed {
  justify-content: center;
  padding: 12px 0;
}
.user-avatar {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: linear-gradient(135deg, #409eff, #337ecc);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
  font-size: 16px;
  flex-shrink: 0;
}
.user-meta {
  flex: 1;
  min-width: 0;
}
.user-name {
  font-size: 13px;
  color: #e0e4ef;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 2px;
}
.logout-btn {
  color: #f56c6c !important;
}

/* 菜单样式 */
.app-menu {
  border-right: none !important;
  flex: 1;
  padding: 8px 0;
  overflow-y: auto;
}
.app-menu .el-menu-item {
  height: 44px;
  line-height: 44px;
  margin: 2px 8px;
  border-radius: 8px;
  color: #9ba3c4 !important;
}
.app-menu .el-menu-item:hover {
  background-color: rgba(64,158,255,0.1) !important;
  color: #409eff !important;
}
.app-menu .el-menu-item.is-active {
  background-color: rgba(64,158,255,0.15) !important;
  color: #409eff !important;
  font-weight: 600;
}
/* 子菜单项（仅展开态缩进，折叠态交由 Element Plus 显示图标 + Tooltip） */
.app-menu:not(.el-menu--collapse) .menu-sub {
  height: 38px !important;
  line-height: 38px !important;
  padding-left: 52px !important;
  font-size: 13px;
  color: #7b83a5 !important;
  margin: 0 8px !important;
}
.app-menu:not(.el-menu--collapse) .menu-sub:hover {
  color: #409eff !important;
  background-color: transparent !important;
}

.aside-footer {
  padding: 12px 16px 16px;
  border-top: 1px solid rgba(255,255,255,0.06);
  font-size: 12px;
  color: #6b7394;
}
.aside-footer.collapsed {
  padding: 12px 0;
}
.health-line {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #7b83a5;
}
.health-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.health-dot.on {
  background: #67c23a;
  box-shadow: 0 0 6px #67c23a;
}
.health-dot.off {
  background: #f56c6c;
  box-shadow: 0 0 6px #f56c6c;
}
.health-tip {
  margin-top: 6px;
}
.health-tip code {
  font-size: 10px;
  color: #555c7a;
  background: rgba(0,0,0,0.2);
  padding: 1px 4px;
  border-radius: 3px;
}

/* ===== 右侧主体 ===== */
.app-body {
  flex: 1;
  min-width: 0;
}

/* ===== 顶部 Header ===== */
.app-header {
  background: #fff;
  border-bottom: 1px solid var(--border-light);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.collapse-btn {
  font-size: 18px;
  color: var(--text-regular);
}
.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}
.welcome {
  font-size: 13px;
  color: var(--text-secondary);
}

/* ===== 主内容区 ===== */
.app-main {
  padding: 20px;
  overflow-y: auto;
  background: var(--bg-page);
}

/* ===== 右下角后端服务状态浮窗 ===== */
.backend-status {
  position: fixed;
  bottom: 20px;
  right: 20px;
  z-index: 1000;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 32px 12px 12px;
  background: #fff;
  border-radius: 14px;
  border: 1px solid #eef0f6;
  box-shadow: 0 10px 30px rgba(31, 48, 92, 0.16);
}
.status-avatar {
  width: 42px;
  height: 42px;
  border-radius: 10px;
  object-fit: cover;
  flex-shrink: 0;
}
.status-meta {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.status-title {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 13px;
  font-weight: 600;
  color: #303133;
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #909399;
  flex-shrink: 0;
}
.backend-status.online .status-dot {
  background: #67c23a;
  box-shadow: 0 0 6px #67c23a;
}
.backend-status.offline .status-dot {
  background: #f56c6c;
  box-shadow: 0 0 6px #f56c6c;
}
.backend-status.checking .status-dot {
  background: #e6a23c;
}
.status-desc {
  font-size: 11px;
  color: #909399;
  max-width: 320px;
  line-height: 1.5;
}
.status-close {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: #c0c4cc;
  cursor: pointer;
  border-radius: 50%;
  padding: 0;
  transition: all 0.2s;
}
.status-close:hover {
  color: #f56c6c;
  background: #fef0f0;
}
</style>
