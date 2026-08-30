<template>
  <!-- 登录页为独立整页，不套侧边栏布局 -->
  <router-view v-if="$route.name === 'login'" />

  <el-container v-else class="app-layout">
    <!-- 深色侧边栏（模仿视频风格） -->
    <el-aside width="230px" class="app-aside">
      <div class="logo">
        <el-icon class="logo-icon" :size="26"><DataAnalysis /></el-icon>
        <div>
          <div class="logo-title">智育数据</div>
          <div class="logo-sub">课程知识图谱智能系统</div>
        </div>
      </div>

      <div class="user-box">
        <div class="user-avatar">{{ store.username.slice(0, 1).toUpperCase() }}</div>
        <div class="user-meta">
          <div class="user-name">{{ store.username }}</div>
          <el-tag size="small" :type="store.role === 'teacher' ? 'warning' : 'success'" effect="dark">
            {{ store.role === 'teacher' ? '教师' : '学生' }}
          </el-tag>
        </div>
        <el-button text size="small" type="danger" @click="onLogout" class="logout-btn">退出</el-button>
      </div>

      <el-menu
        :default-active="$route.path"
        router
        class="app-menu"
        background-color="transparent"
        text-color="#b0b8d1"
        active-text-color="#409eff"
      >
        <!-- 数据总览（新增，模仿视频首页） -->
        <el-menu-item index="/dashboard">
          <el-icon><DataAnalysis /></el-icon>
          <span>数据总览</span>
        </el-menu-item>

        <template v-if="store.role === 'teacher'">
          <el-menu-item index="/teacher?tab=upload">
            <el-icon><Upload /></el-icon><span>文档上传</span>
          </el-menu-item>
          <el-menu-item index="/teacher?tab=preview" class="menu-sub">
            <span>· 图谱预览</span>
          </el-menu-item>
          <el-menu-item index="/teacher?tab=edit" class="menu-sub">
            <span>· 编辑图谱</span>
          </el-menu-item>
        </template>
        <template v-else>
          <el-menu-item index="/student?tab=browse">
            <el-icon><Compass /></el-icon><span>图谱浏览</span>
          </el-menu-item>
          <el-menu-item index="/student?tab=qa" class="menu-sub">
            <span>· 智能问答</span>
          </el-menu-item>
          <el-menu-item index="/student?tab=path" class="menu-sub">
            <span>· 学习路径推荐</span>
          </el-menu-item>
        </template>
      </el-menu>

      <div class="aside-footer">
        <div class="health-line">
          <i class="health-dot" :class="store.backendOnline ? 'on' : 'off'"></i>
          <span v-if="store.backendOnline">后端服务在线</span>
          <span v-else-if="store.healthChecked">后端服务离线</span>
          <span v-else>检查后端中…</span>
        </div>
        <div class="health-tip">启动后端：<code>python -m uvicorn app.main:app --reload</code></div>
      </div>
    </el-aside>

    <!-- 主内容区（浅色背景） -->
    <el-main class="app-main">
      <router-view />
    </el-main>

    <!-- 右下角后端服务状态（教师/学生端） -->
    <div
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
    </div>
  </el-container>
</template>

<script setup>
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Upload, Compass, DataAnalysis } from '@element-plus/icons-vue'
import { useAppStore } from './stores/app'
import statusImage from './phtotos/A10赛题项目理解(1).png'

const store = useAppStore()
const router = useRouter()

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
}

.logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 16px 14px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
}
.logo-icon {
  font-size: 28px;
}
.logo-title {
  font-weight: 700;
  font-size: 16px;
  color: #e8ecf4;
  letter-spacing: 1px;
}
.logo-sub {
  font-size: 11px;
  color: #6b7394;
  margin-top: 2px;
}

.user-box {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px 12px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
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
.menu-sub {
  height: 38px !important;
  line-height: 38px !important;
  padding-left: 52px !important;
  font-size: 13px;
  color: #7b83a5 !important;
  margin: 0 8px !important;
}
.menu-sub:hover {
  color: #409eff !important;
  background-color: transparent !important;
}

.aside-footer {
  padding: 12px 16px 16px;
  border-top: 1px solid rgba(255,255,255,0.06);
  font-size: 12px;
  color: #6b7394;
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

/* ===== 主内容区 ===== */
.app-main {
  padding: 20px;
  overflow-y: auto;
  background: #f0f2f8;
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
  padding: 12px 18px 12px 12px;
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
</style>
