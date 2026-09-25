<template>
  <div class="forbidden">
    <el-card class="page-card" shadow="never">
      <div class="fb-body">
        <div class="fb-icon"><el-icon :size="34"><Lock /></el-icon></div>
        <h3 class="fb-title">无权限访问</h3>
        <p class="fb-desc">
          当前账号（{{ roleText }}）没有访问该页面的权限。<br />
          管理员端仅限平台管理员使用。
        </p>
        <div class="fb-actions">
          <el-button @click="router.back()">返回上一页</el-button>
          <el-button type="primary" @click="router.replace(home)">回到{{ homeLabel }}</el-button>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { Lock } from '@element-plus/icons-vue'
import { useAppStore } from '../stores/app'

const router = useRouter()
const store = useAppStore()

const roleText = computed(() => ({ teacher: '教师', student: '学生', admin: '管理员' }[store.role] || '未登录'))
const home = computed(() => (store.role === 'admin' ? '/admin' : '/course-center'))
const homeLabel = computed(() => (store.role === 'admin' ? '工作台' : '课程中心'))
</script>

<style scoped>
.forbidden { max-width: 560px; margin: 8vh auto 0; }
.fb-body {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: var(--space-6) var(--space-4);
  gap: var(--space-3);
}
.fb-icon {
  width: 68px;
  height: 68px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-danger);
  background: rgba(245, 71, 93, .08);
  border: 1px solid rgba(245, 71, 93, .18);
}
.fb-title {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
}
.fb-desc {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-text-muted);
}
.fb-actions {
  display: flex;
  gap: var(--space-2);
  margin-top: var(--space-3);
}
</style>
