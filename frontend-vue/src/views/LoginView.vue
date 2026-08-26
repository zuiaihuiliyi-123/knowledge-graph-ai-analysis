<template>
  <div class="login-page">
    <div class="login-card">
      <div class="brand">
        <div class="brand-icon">📚</div>
        <h1 class="brand-title">课程知识图谱系统</h1>
        <p class="brand-sub">AIGC 智能构建与学习</p>
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
                :prefix-icon="User"
              />
            </el-form-item>
            <el-form-item label="密码" prop="password">
              <el-input
                v-model="loginForm.password"
                type="password"
                show-password
                placeholder="请输入密码"
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
</template>

<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { User, Lock } from '@element-plus/icons-vue'
import { useAppStore } from '../stores/app'

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
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #eef2ff 0%, #f5f7fa 50%, #e8f4fd 100%);
}
.login-card {
  width: 400px;
  background: #fff;
  border-radius: 12px;
  padding: 32px 36px 24px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.08);
}
.brand {
  text-align: center;
  margin-bottom: 16px;
}
.brand-icon {
  font-size: 42px;
}
.brand-title {
  margin: 8px 0 2px;
  font-size: 20px;
  color: #303133;
}
.brand-sub {
  margin: 0;
  font-size: 13px;
  color: #909399;
}
.submit-btn {
  width: 100%;
  margin-top: 4px;
}
</style>
