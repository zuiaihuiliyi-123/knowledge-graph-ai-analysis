<template>
  <!-- 登录页为独立整页，不套侧边栏布局 -->
  <router-view v-if="$route.name === 'login'" />

  <el-container v-else class="app-layout">
    <!-- 侧边栏 -->
    <el-aside width="230px" class="app-aside">
      <div class="logo">
        <span class="logo-icon">📚</span>
        <div>
          <div class="logo-title">课程知识图谱系统</div>
          <div class="logo-sub">AIGC 智能构建与学习</div>
        </div>
      </div>

      <div class="user-box">
        <div class="user-avatar">{{ store.username.slice(0, 1).toUpperCase() }}</div>
        <div class="user-meta">
          <div class="user-name">{{ store.username }}</div>
          <el-tag size="small" :type="store.role === 'teacher' ? 'warning' : 'success'">
            {{ store.role === 'teacher' ? '教师' : '学生' }}
          </el-tag>
        </div>
        <el-button text size="small" type="danger" @click="onLogout">退出</el-button>
      </div>

      <el-menu :default-active="$route.path" router class="app-menu">
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

    <!-- 主内容区 -->
    <el-main class="app-main">
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup>
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Upload, Compass } from '@element-plus/icons-vue'
import { useAppStore } from './stores/app'

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
.app-aside {
  background: #fff;
  border-right: 1px solid #e4e7ed;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 16px 12px;
}
.logo-icon {
  font-size: 30px;
}
.logo-title {
  font-weight: 700;
  font-size: 15px;
  color: #303133;
}
.logo-sub {
  font-size: 12px;
  color: #909399;
}
.user-box {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px 12px;
  border-bottom: 1px solid #f0f2f5;
}
.user-avatar {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: #409eff;
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
  color: #303133;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 2px;
}
.app-menu {
  border-right: none;
  flex: 1;
}
.menu-sub {
  height: 36px;
  line-height: 36px;
  color: #909399;
  font-size: 13px;
  padding-left: 44px !important;
}
.aside-footer {
  padding: 12px 16px 16px;
  border-top: 1px solid #f0f2f5;
  font-size: 12px;
  color: #909399;
}
.health-line {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #606266;
}
.health-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.health-dot.on {
  background: #67c23a;
}
.health-dot.off {
  background: #f56c6c;
}
.health-tip {
  margin-top: 6px;
}
.health-tip code {
  font-size: 11px;
}
.app-main {
  padding: 16px;
  overflow-y: auto;
  background: #f5f7fa;
}
</style>
