<template>
  <el-container class="app-layout">
    <!-- 侧边栏 -->
    <el-aside width="230px" class="app-aside">
      <div class="logo">
        <span class="logo-icon">📚</span>
        <div>
          <div class="logo-title">课程知识图谱系统</div>
          <div class="logo-sub">AIGC 智能构建与学习</div>
        </div>
      </div>

      <div class="role-switch">
        <el-radio-group :model-value="store.role" @update:model-value="onRoleChange">
          <el-radio-button value="teacher">👩‍🏫 教师端</el-radio-button>
          <el-radio-button value="student">👨‍🎓 学生端</el-radio-button>
        </el-radio-group>
      </div>

      <el-menu :default-active="$route.path" router class="app-menu">
        <el-menu-item index="/teacher?tab=upload">
          <el-icon><Upload /></el-icon><span>文档上传</span>
        </el-menu-item>
        <el-menu-item index="/teacher?tab=preview" class="menu-sub">
          <span>· 图谱预览</span>
        </el-menu-item>
        <el-menu-item index="/teacher?tab=edit" class="menu-sub">
          <span>· 编辑图谱</span>
        </el-menu-item>
        <el-menu-item index="/student?tab=browse">
          <el-icon><Compass /></el-icon><span>图谱浏览</span>
        </el-menu-item>
        <el-menu-item index="/student?tab=qa" class="menu-sub">
          <span>· 智能问答</span>
        </el-menu-item>
        <el-menu-item index="/student?tab=path" class="menu-sub">
          <span>· 学习路径推荐</span>
        </el-menu-item>
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

function onRoleChange(role) {
  store.setRole(role)
  router.push(role === 'teacher' ? '/teacher' : '/student')
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
.role-switch {
  padding: 0 16px 8px;
}
.role-switch :deep(.el-radio-group) {
  width: 100%;
}
.role-switch :deep(.el-radio-button) {
  width: 50%;
}
.role-switch :deep(.el-radio-button__inner) {
  width: 100%;
  padding: 8px 0;
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
