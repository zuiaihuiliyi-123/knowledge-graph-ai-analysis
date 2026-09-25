<template>
  <div class="login-page">
    <!-- 左侧品牌展示区：与注册页共用 -->
    <AuthBrandPanel />

    <!-- 右侧登录区 -->
    <div class="form-panel">
      <div class="login-card">
        <div class="form-brand-row">
          <div class="form-logo"><BrandMark :size="26" /></div>
          <div>
            <div class="form-title">欢迎使用</div>
            <div class="form-sub">课程知识图谱智能构建与学习导航系统</div>
          </div>
        </div>

        <el-form ref="formRef" :model="form" :rules="rules" size="large" @keyup.enter="handleLogin">
          <el-form-item prop="username">
            <el-input v-model="form.username" placeholder="请输入用户名" :prefix-icon="User" clearable />
          </el-form-item>
          <el-form-item prop="password">
            <el-input
              v-model="form.password"
              type="password"
              placeholder="请输入密码"
              :prefix-icon="Lock"
              show-password
              clearable
            />
          </el-form-item>
          <el-form-item>
            <el-button
              type="primary"
              class="login-btn"
              :loading="loading"
              @click="handleLogin"
            >
              {{ loading ? '登录中…' : '登 录' }}
            </el-button>
          </el-form-item>
        </el-form>

        <div class="quick-demo">
          <span class="quick-label">演示账号一键填充：</span>
          <el-tag
            v-for="d in demos"
            :key="d.role"
            class="quick-tag"
            effect="plain"
            @click="fillDemo(d)"
          >{{ d.label }}</el-tag>
        </div>

        <div class="login-tips">
          <el-icon><InfoFilled /></el-icon>
          <span>首次使用可在账号注册入口创建账号；教师创建课程、上传资料后即可体验完整流程。</span>
        </div>

        <div class="form-footer">
          <el-button link type="primary" @click="router.push('/register')">没有账号？去注册</el-button>
          <el-button link @click="router.push('/invite')">加课码 / 邀请链接</el-button>
        </div>
      </div>
      <div class="form-status">
        <span class="status-dot" :class="store.backendOnline ? 'on' : 'off'" />
        {{ store.backendOnline ? '后端服务在线，可正常登录' : '后端服务未连接，请先启动后端服务' }}
      </div>
    </div>

    <BackendStatusCard />
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { User, Lock, InfoFilled } from '@element-plus/icons-vue'
import { useAppStore } from '../stores/app'
import AuthBrandPanel from '../components/AuthBrandPanel.vue'
import BackendStatusCard from '../components/BackendStatusCard.vue'
import BrandMark from '../components/BrandMark.vue'

const router = useRouter()
const store = useAppStore()

const formRef = ref(null)
const loading = ref(false)
const form = reactive({ username: '', password: '', role: 'student' })
const demos = [
  { role: 'teacher', label: '教师 demo_teacher', username: 'demo_teacher', password: 'demo123456' },
  { role: 'student', label: '学生 demo_student', username: 'demo_student', password: 'demo123456' },
  { role: 'admin', label: '管理员 demo_admin', username: 'demo_admin', password: 'demo123456' },
]
const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

function fillDemo(d) {
  form.username = d.username
  form.password = d.password
  form.role = d.role
}

async function handleLogin() {
  if (!formRef.value) return
  try {
    await formRef.value.validate()
  } catch {
    return
  }
  loading.value = true
  try {
    await store.login(form.username, form.password)
    ElMessage.success('登录成功')
    router.push('/course-center')
  } catch (e) {
    ElMessage.error(e.message || '登录失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  store.checkHealth().catch(() => {})
})
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  background: #f3f5fb;
}

/* ============ 右侧表单区 ============ */
.form-panel {
  width: 520px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px;
  position: relative;
}
.login-card {
  width: 100%;
  max-width: 380px;
  animation: kg-fade-up .55s cubic-bezier(.22,.8,.36,1) both;
}
.form-brand-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 26px;
}
.form-logo {
  width: 46px;
  height: 46px;
  border-radius: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--gradient-brand);
  box-shadow: var(--shadow-primary);
}
.form-title { font-size: 22px; font-weight: 700; color: var(--text-primary); }
.form-sub { font-size: 12px; color: var(--text-secondary); margin-top: 3px; }

.login-btn {
  width: 100%;
  height: 44px;
  font-size: 15px;
  letter-spacing: 4px;
  border-radius: 10px;
}

.quick-demo {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin: 4px 0 14px;
}
.quick-label { font-size: 12px; color: var(--text-secondary); }
.quick-tag { cursor: pointer; border-radius: 7px; transition: all .2s; }
.quick-tag:hover { transform: translateY(-1px); }

.login-tips {
  display: flex;
  gap: 7px;
  font-size: 12px;
  color: var(--text-muted);
  line-height: 1.7;
  background: var(--brand-50);
  border: 1px solid #e3ebff;
  border-radius: 10px;
  padding: 10px 12px;
}
.login-tips .el-icon { color: var(--brand-500); flex-shrink: 0; margin-top: 2px; }

.form-footer {
  display: flex;
  justify-content: space-between;
  margin-top: 18px;
}

.form-status {
  position: absolute;
  bottom: 26px;
  font-size: 12px;
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  gap: 7px;
}
.status-dot { width: 8px; height: 8px; border-radius: 50%; }
.status-dot.on { background: #18b87a; box-shadow: 0 0 8px rgba(24,184,122,.7); }
.status-dot.off { background: #f5475d; box-shadow: 0 0 8px rgba(245,71,93,.6); }

/* ============ 响应式 ============ */
@media (max-width: 960px) {
  .form-panel { width: 100%; }
}
</style>
