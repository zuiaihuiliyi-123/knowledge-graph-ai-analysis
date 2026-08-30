<template>
  <div class="login-page">
    <!-- 左侧品牌区 -->
    <div class="brand-panel">
      <div class="brand-inner">
        <div class="brand-logo">
          <img :src="brandImage" alt="A10 赛题项目理解" />
        </div>
        <h1 class="brand-title">课程知识图谱智能构建<br />与学习系统</h1>
        <p class="brand-sub">基于 AIGC · 服务外包创新创业大赛 A10</p>
        <ul class="brand-features">
          <li><span class="feat-icon">✦</span>LLM 智能抽取知识点与关系</li>
          <li><span class="feat-icon">✦</span>知识图谱可视化与教师编辑</li>
          <li><span class="feat-icon">✦</span>RAG 问答 · 学习路径推荐</li>
        </ul>
      </div>
    </div>

    <!-- 右侧登录 / 注册表单 -->
    <div class="form-panel">
      <div class="login-card">
        <div class="mobile-brand">
          <img :src="brandImage" alt="" />
          <span>课程知识图谱系统</span>
        </div>

        <el-tabs v-model="mode" stretch>
          <el-tab-pane label="登录" name="login">
            <el-form
              ref="loginFormRef"
              :model="loginForm"
              :rules="loginRules"
              label-position="top"
            >
              <el-form-item label="用户名" prop="username">
                <el-input
                  v-model="loginForm.username"
                  placeholder="请输入用户名"
                  size="large"
                  :prefix-icon="User"
                />
              </el-form-item>
              <el-form-item label="密码" prop="password">
                <el-input
                  v-model="loginForm.password"
                  type="password"
                  show-password
                  placeholder="请输入密码"
                  size="large"
                  :prefix-icon="Lock"
                  @keyup.enter="onLogin"
                />
              </el-form-item>
              <el-button
                type="primary"
                size="large"
                class="submit-btn"
                :loading="loading"
                @click="onLogin"
              >
                登 录
              </el-button>
            </el-form>
          </el-tab-pane>

          <el-tab-pane label="注册" name="register">
            <el-form
              ref="regFormRef"
              :model="regForm"
              :rules="regRules"
              label-position="top"
            >
              <el-form-item label="用户名" prop="username">
                <el-input
                  v-model="regForm.username"
                  placeholder="请输入用户名"
                  :prefix-icon="User"
                />
              </el-form-item>
              <el-form-item label="密码" prop="password">
                <el-input
                  v-model="regForm.password"
                  type="password"
                  show-password
                  placeholder="至少 6 位"
                  :prefix-icon="Lock"
                />
              </el-form-item>
              <el-form-item label="确认密码" prop="confirm">
                <el-input
                  v-model="regForm.confirm"
                  type="password"
                  show-password
                  placeholder="再次输入密码"
                  :prefix-icon="Lock"
                  @keyup.enter="onRegister"
                />
              </el-form-item>
              <el-form-item label="身份" prop="role">
                <el-radio-group v-model="regForm.role">
                  <el-radio value="student">学生</el-radio>
                  <el-radio value="teacher">教师</el-radio>
                </el-radio-group>
              </el-form-item>
              <el-button
                type="primary"
                size="large"
                class="submit-btn"
                :loading="loading"
                @click="onRegister"
              >
                注册并登录
              </el-button>
            </el-form>
          </el-tab-pane>
        </el-tabs>
      </div>
    </div>

    <!-- 右下角后端服务状态（模仿教师/学生端） -->
    <div
      class="backend-status"
      :class="store.backendOnline ? 'online' : store.healthChecked ? 'offline' : 'checking'"
    >
      <img :src="brandImage" class="status-avatar" alt="服务状态" />
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
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { User, Lock } from '@element-plus/icons-vue'
import { useAppStore } from '../stores/app'
import brandImage from '../phtotos/A10赛题项目理解(1).png'

const store = useAppStore()
const router = useRouter()
const route = useRoute()

const mode = ref('login')
const loading = ref(false)
const loginFormRef = ref(null)
const regFormRef = ref(null)

const loginForm = ref({ username: '', password: '' })
const regForm = ref({ username: '', password: '', confirm: '', role: 'student' })

const loginRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}
const regRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 位', trigger: 'blur' },
  ],
  confirm: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    {
      validator: (_, v, cb) =>
        v === regForm.value.password ? cb() : cb(new Error('两次密码不一致')),
      trigger: 'blur',
    },
  ],
}

function afterLogin() {
  const redirect = route.query.redirect
  const target =
    typeof redirect === 'string' && redirect.startsWith('/')
      ? redirect
      : store.role === 'teacher'
        ? '/teacher'
        : '/student'
  router.push(target)
}

async function onLogin() {
  if (loading.value) return
  try {
    await loginFormRef.value.validate()
  } catch {
    return
  }
  loading.value = true
  try {
    await store.login(loginForm.value.username.trim(), loginForm.value.password)
    ElMessage.success(`欢迎，${store.username}`)
    afterLogin()
  } catch (e) {
    ElMessage.error(`登录失败：${e.message}`)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  store.checkHealth()
})

async function onRegister() {
  if (loading.value) return
  try {
    await regFormRef.value.validate()
  } catch {
    return
  }
  loading.value = true
  try {
    await store.register({
      username: regForm.value.username.trim(),
      password: regForm.value.password,
      role: regForm.value.role,
    })
    ElMessage.success('注册成功，正在登录…')
    await store.login(regForm.value.username.trim(), regForm.value.password)
    afterLogin()
  } catch (e) {
    ElMessage.error(`注册失败：${e.message}`)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  background: #f7f8fc;
}

/* ===== 左侧品牌区 ===== */
.brand-panel {
  flex: 1.1;
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px;
  background: linear-gradient(150deg, #1a1f36 0%, #243b6b 45%, #2f5fb8 100%);
}
.brand-inner {
  position: relative;
  z-index: 1;
  max-width: 460px;
  color: #e8ecf4;
}
.brand-logo {
  width: 150px;
  height: 150px;
  border-radius: 22px;
  overflow: hidden;
  box-shadow: 0 18px 44px rgba(0, 0, 0, 0.35);
  background: #fff;
  margin-bottom: 28px;
}
.brand-logo img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.brand-title {
  font-size: 30px;
  line-height: 1.4;
  font-weight: 700;
  letter-spacing: 1px;
  color: #fff;
  margin: 0 0 12px;
}
.brand-sub {
  font-size: 14px;
  color: #b9c6e8;
  margin: 0 0 32px;
}
.brand-features {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.brand-features li {
  font-size: 14px;
  color: #cdd7f0;
  display: flex;
  align-items: center;
  gap: 10px;
}
.feat-icon {
  color: #6ea8ff;
  font-size: 16px;
}

/* ===== 右侧表单区 ===== */
.form-panel {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px 32px;
}
.login-card {
  width: 400px;
  background: #fff;
  border-radius: 16px;
  padding: 36px 38px 28px;
  box-shadow: 0 18px 60px rgba(31, 48, 92, 0.12);
}
.mobile-brand {
  display: none;
}
.submit-btn {
  width: 100%;
  margin-top: 6px;
  font-size: 16px;
  letter-spacing: 6px;
  border-radius: 8px;
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

/* 响应式：窄屏隐藏品牌区 */
@media (max-width: 860px) {
  .brand-panel {
    display: none;
  }
  .form-panel {
    flex: 1;
  }
  .mobile-brand {
    display: flex;
    align-items: center;
    gap: 10px;
    justify-content: center;
    margin-bottom: 12px;
    font-size: 18px;
    font-weight: 700;
    color: #303133;
  }
  .mobile-brand img {
    width: 34px;
    height: 34px;
    border-radius: 8px;
    object-fit: cover;
  }
}
</style>
