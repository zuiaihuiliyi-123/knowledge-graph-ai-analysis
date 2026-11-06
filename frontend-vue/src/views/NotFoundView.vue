<template>
  <div class="not-found-view">
    <el-card class="nf-card" shadow="never">
      <el-result icon="warning" title="页面不存在" sub-title="你访问的地址可能已被移动或输入有误。">
        <template #extra>
          <el-button type="primary" @click="goHome">回到首页</el-button>
          <el-button @click="goBack">返回上一页</el-button>
        </template>
      </el-result>
      <p class="nf-path">当前地址：{{ route.fullPath }}</p>
    </el-card>
  </div>
</template>

<script setup>
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

function goHome() {
  const raw = localStorage.getItem('kg_user')
  let role
  try {
    role = JSON.parse(raw || 'null')?.role || 'student'
  } catch {
    role = 'student'
  }
  router.replace(role === 'admin' ? '/admin' : '/course-center')
}

function goBack() {
  if (window.history.length > 1) router.back()
  else goHome()
}
</script>

<style scoped>
.not-found-view {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 70vh;
  padding: var(--space-4);
}
.nf-card {
  width: min(560px, 100%);
  border-radius: var(--radius-lg);
}
.nf-path {
  margin: 0;
  padding: 0 24px 20px;
  text-align: center;
  font-size: 12px;
  color: var(--color-text-muted);
  word-break: break-all;
}
</style>
