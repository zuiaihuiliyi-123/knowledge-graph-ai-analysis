<template>
  <div class="register-page">
    <!-- 左侧品牌展示区：与登录页共用 -->
    <AuthBrandPanel />

    <!-- 右侧注册区 -->
    <div class="form-panel">
      <div class="register-card">
        <div class="form-brand-row">
          <div class="form-logo"><BrandMark :size="26" /></div>
          <div>
            <div class="form-title">创建账号</div>
            <div class="form-sub">注册后即可创建课程或加入课程学习</div>
          </div>
        </div>

        <el-form
          ref="formRef"
          :model="form"
          :rules="rules"
          size="large"
          label-position="top"
          @keyup.enter="handleRegister"
        >
          <el-form-item label="用户名" prop="username">
            <el-input v-model="form.username" placeholder="请输入用户名" :prefix-icon="User" clearable />
          </el-form-item>
          <el-form-item label="密码" prop="password">
            <el-input
              v-model="form.password"
              type="password"
              placeholder="至少 6 位"
              :prefix-icon="Lock"
              show-password
            />
          </el-form-item>
          <el-form-item label="确认密码" prop="confirm">
            <el-input
              v-model="form.confirm"
              type="password"
              placeholder="再次输入密码"
              :prefix-icon="Lock"
              show-password
            />
          </el-form-item>
          <el-form-item label="身份" prop="role">
            <el-radio-group v-model="form.role">
              <el-radio value="student">学生</el-radio>
              <el-radio value="teacher">教师</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item v-if="form.role === 'teacher'" label="邀请码" prop="teacherInviteCode">
            <el-input
              v-model="form.teacherInviteCode"
              placeholder="请输入管理员提供的教师邀请码"
              :prefix-icon="Key"
            />
          </el-form-item>
          <el-form-item>
            <el-button
              type="primary"
              class="submit-btn"
              :loading="loading"
              :disabled="store.healthChecked && !store.backendOnline"
              :title="store.healthChecked && !store.backendOnline ? '后端服务未连接，请先启动后端服务' : ''"
              @click="handleRegister"
            >
              {{ loading ? '注册中…' : '注 册' }}
            </el-button>
          </el-form-item>
        </el-form>

        <div class="role-tips">
          <el-icon><InfoFilled /></el-icon>
          <span>教师注册需管理员提供的邀请码；教师可创建课程、上传资料并管理成员。学生无需邀请码，通过加课码或邀请链接加入课程。</span>
        </div>

        <div class="form-footer">
          <el-button link type="primary" @click="router.push('/login')">已有账号？去登录</el-button>
        </div>
      </div>
      <div class="form-status">
        <span class="status-dot" :class="statusClass" />
        {{ statusText }}
      </div>
    </div>

    <BackendStatusCard />
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { User, Lock, InfoFilled, Key } from '@element-plus/icons-vue'
import { useAppStore } from '../stores/app'
import AuthBrandPanel from '../components/AuthBrandPanel.vue'
import BackendStatusCard from '../components/BackendStatusCard.vue'
import BrandMark from '../components/BrandMark.vue'

const route = useRoute()
const router = useRouter()
const store = useAppStore()

const formRef = ref(null)
const loading = ref(false)
const form = reactive({ username: '', password: '', confirm: '', role: 'student', teacherInviteCode: '' })

const statusClass = computed(() => {
  if (store.healthChecking || !store.healthChecked) return 'checking'
  return store.backendOnline ? 'on' : 'off'
})
const statusText = computed(() => {
  if (store.healthChecking || !store.healthChecked) return '正在检测后端服务…'
  return store.backendOnline
    ? '后端服务在线，可正常注册'
    : '服务暂时不可用，请稍后重试或联系管理员'
})

const rules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 20, message: '用户名长度需在 3-20 个字符之间', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 位', trigger: 'blur' },
  ],
  confirm: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    {
      validator: (_rule, value, callback) => {
        if (value !== form.password) callback(new Error('两次输入的密码不一致'))
        else callback()
      },
      trigger: 'blur',
    },
  ],
  role: [{ required: true, message: '请选择身份', trigger: 'change' }],
  teacherInviteCode: [
    {
      // 只有选择「教师」时才必填：学生注册不需要邀请码，故用函数式校验而非常驻 required
      validator: (_rule, value, callback) => {
        if (form.role === 'teacher' && !String(value || '').trim()) {
          callback(new Error('教师注册需要邀请码，请联系管理员获取'))
        } else {
          callback()
        }
      },
      trigger: 'blur',
    },
  ],
}

function safeRedirect() {
  const raw = route.query.redirect
  const target = Array.isArray(raw) ? raw[0] : raw
  // 只接受站内绝对路径，避免被构造成 //evil.com 之类的开放重定向
  if (typeof target === 'string' && target.startsWith('/') && !target.startsWith('//')) {
    return target
  }
  return '/course-center?tab=mine'
}

async function handleRegister() {
  if (!formRef.value) return
  try {
    await formRef.value.validate()
  } catch {
    return
  }
  if (loading.value) return
  loading.value = true
  const username = form.username.trim()

  // 第一步：注册。失败就是注册失败，绝不能因为后面的自动登录失败而误报「注册失败」
  try {
    await store.register({
      username,
      password: form.password,
      role: form.role,
      // 后端只认 teacher_invite_code；学生注册不需要，传 undefined 让请求体保持干净
      teacher_invite_code: form.role === 'teacher' ? form.teacherInviteCode.trim() : undefined,
    })
  } catch (e) {
    ElMessage.error(e.message || '注册失败')
    loading.value = false
    return
  }

  // 第二步：自动登录。失败时账号已经创建成功，只需引导去登录页并带上用户名
  try {
    await store.login(username, form.password)
    // 邀请链接等被路由守卫拦截的页面：注册并登录后回到原目标（只接受站内路径）
    router.replace(safeRedirect())
  } catch (e) {
    ElMessage.warning(
      `注册成功，但自动登录失败${e?.message ? `（${e.message}）` : ''}，请使用刚注册的账号手动登录`,
    )
    router.push({ path: '/login', query: { username } })
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  store.checkHealth().catch(() => {})
})
</script>

<style scoped>
.register-page {
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
  overflow-y: auto;
}
.register-card {
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

.submit-btn {
  width: 100%;
  height: 44px;
  font-size: 15px;
  letter-spacing: 4px;
  border-radius: 10px;
}

.role-tips {
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
.role-tips .el-icon { color: var(--brand-500); flex-shrink: 0; margin-top: 2px; }

.form-footer {
  display: flex;
  justify-content: center;
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
.status-dot.checking {
  background: #f0a020;
  box-shadow: 0 0 8px rgba(240,160,32,.6);
  animation: kg-status-pulse 1.2s ease-in-out infinite;
}
@keyframes kg-status-pulse {
  0%, 100% { opacity: .45; }
  50% { opacity: 1; }
}

/* ============ 响应式 ============ */
@media (max-width: 960px) {
  .form-panel { width: 100%; }
}
</style>
