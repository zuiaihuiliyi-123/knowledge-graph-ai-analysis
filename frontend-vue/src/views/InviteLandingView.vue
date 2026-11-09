<template>
  <!-- 邀请落地页：独立整页（不进主框架），未加入前不应出现侧边栏与其它课程入口 -->
  <div class="invite-page">
    <div class="invite-box">
      <div class="brand">
        <el-icon :size="30" color="#4f6ef7"><DataAnalysis /></el-icon>
        <div class="brand-text">
          <div class="brand-title">智育数据</div>
          <div class="brand-sub">课程知识图谱智能系统</div>
        </div>
      </div>

      <div v-if="loading" class="state" v-loading="true" element-loading-text="正在校验邀请…"></div>

      <div v-else-if="needsLogin" class="state">
        <el-result
          icon="info"
          title="你收到一个课程邀请"
          sub-title="登录或注册后即可查看课程详情并接受邀请。"
        >
          <template #extra>
            <el-button type="primary" @click="goLogin">登录后接受</el-button>
            <el-button @click="goRegister">注册新账号</el-button>
          </template>
        </el-result>
      </div>

      <div v-else-if="error" class="state">
        <el-result icon="warning" title="邀请不可用" :sub-title="error">
          <template #extra>
            <el-button type="primary" @click="goCenter">返回课程中心</el-button>
          </template>
        </el-result>
      </div>

      <div v-else class="state">
        <el-card shadow="never" class="course-card">
          <div class="course-name">{{ info.course_name }}</div>
          <div class="course-sub">
            {{ info.teacher_name || '—' }}
            <span v-if="info.organization"> · {{ info.organization }}</span>
          </div>
          <div v-if="info.description" class="course-desc">{{ info.description }}</div>

          <div class="meta-list">
            <div class="meta-row">
              <span class="meta-label">邀请人</span>
              <span>{{ info.inviter_name || '—' }}</span>
            </div>
            <div class="meta-row">
              <span class="meta-label">加入身份</span>
              <span>{{ info.role === 'teacher' ? '协作教师' : '学生' }}</span>
            </div>
            <div class="meta-row">
              <span class="meta-label">课程成员</span>
              <span>{{ info.member_count ?? 0 }} 人</span>
            </div>
            <div class="meta-row">
              <span class="meta-label">有效期至</span>
              <span>{{ fmtTime(info.expires_at) }}</span>
            </div>
          </div>

          <template v-if="alreadyMember">
            <el-alert
              type="success"
              :closable="false"
              show-icon
              title="你已经是该课程成员"
              description="可以直接进入课程继续学习。"
            />
            <el-button type="primary" class="action-btn" @click="enterCourse">进入课程</el-button>
          </template>

          <template v-else>
            <el-alert
              v-if="info.effective_status !== 'active'"
              type="warning"
              :closable="false"
              show-icon
              :title="statusText(info.effective_status)"
            />
            <el-button
              type="primary"
              class="action-btn"
              :loading="accepting"
              :disabled="info.effective_status !== 'active'"
              @click="accept"
            >
              接受邀请并加入课程
            </el-button>
          </template>
        </el-card>

        <div class="footer-links">
          <el-button text @click="goCenter">返回课程中心</el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { DataAnalysis } from '@element-plus/icons-vue'
import { api } from '../api'
import { formatDateTime } from '../utils/format'
import { useAppStore } from '../stores/app'

const route = useRoute()
const router = useRouter()
const store = useAppStore()

const loading = ref(true)
const error = ref('')
const info = ref({})
const accepting = ref(false)
// 邀请页现在是公开路由：未登录用户先看到登录/注册引导，
// 登录成功后由 redirect 带回本页，再拉取课程详情。
const needsLogin = ref(false)

const token = computed(() => String(route.params.token || ''))
const alreadyMember = computed(() => info.value.my_relation === 'MEMBER'
  || info.value.my_relation === 'OWNER'
  || info.value.my_relation === 'TEACHER_MEMBER')

const STATUS_TEXT = {
  used: '该邀请链接已被使用',
  revoked: '该邀请链接已被撤销',
  expired: '该邀请链接已过期，请向教师索取新的链接',
  active: '',
}
const statusText = (s) => STATUS_TEXT[s] || '该邀请链接当前不可用'

const fmtTime = (t) => formatDateTime(t)

async function load() {
  loading.value = true
  error.value = ''
  needsLogin.value = false
  if (!store.isLoggedIn) {
    needsLogin.value = true
    loading.value = false
    return
  }
  try {
    info.value = await api.previewInvite(token.value)
  } catch (e) {
    error.value = e?.message || '邀请链接无效'
  } finally {
    loading.value = false
  }
}

function goLogin() {
  router.push({ path: '/login', query: { redirect: route.fullPath } })
}

function goRegister() {
  router.push({ path: '/register', query: { redirect: route.fullPath } })
}

async function accept() {
  accepting.value = true
  try {
    const data = await store.acceptInvite(token.value)
    ElMessage.success(data.already_member
      ? `你已经是「${data.course_name}」的成员`
      : `已加入课程「${data.course_name}」`)
    enterCourseById(data.course_id, data.course_name)
  } catch (e) {
    ElMessage.error(e?.message || '接受邀请失败')
    // 失败（例如链接刚被用掉）时重新拉一次状态，让页面反映真实情况
    load()
  } finally {
    accepting.value = false
  }
}

function enterCourse() {
  enterCourseById(info.value.course_id, info.value.course_name)
}

function enterCourseById(courseId, courseName) {
  store.currentCourseId = String(courseId)
  store.setLearningContext({ courseId, documentId: null })
  const isTeacher = store.role === 'teacher'
  router.push({
    path: isTeacher ? '/teacher' : '/student',
    query: { tab: 'documents', course_id: String(courseId) },
  })
}

function goCenter() {
  router.push({ path: '/course-center', query: { tab: 'mine' } })
}

onMounted(load)
</script>

<style scoped>
.invite-page {
  min-height: 100vh;
  background: var(--bg-page, #f5f7fa);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-5);
}

.invite-box {
  width: 100%;
  max-width: 520px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  justify-content: center;
  margin-bottom: var(--space-5);
}
.brand-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--color-text-primary);
  letter-spacing: 1px;
}
.brand-sub {
  font-size: 12px;
  color: var(--color-text-muted);
  margin-top: 2px;
}

.state {
  min-height: 180px;
}

.course-card {
  border-radius: var(--radius-lg);
}
.course-card :deep(.el-card__body) {
  padding: var(--space-5);
}

.course-name {
  font-size: 20px;
  font-weight: var(--font-weight-bold);
  color: var(--color-text-primary);
}
.course-sub {
  font-size: 13px;
  color: var(--color-text-secondary);
  margin-top: 4px;
}
.course-desc {
  font-size: 13px;
  color: var(--color-text-muted);
  line-height: 1.6;
  margin-top: var(--space-3);
}

.meta-list {
  margin: var(--space-4) 0;
  border-top: 1px solid var(--color-border-light);
  border-bottom: 1px solid var(--color-border-light);
  padding: var(--space-3) 0;
}
.meta-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
  color: var(--color-text-regular);
  padding: 4px 0;
}
.meta-label {
  color: var(--color-text-muted);
}

.action-btn {
  width: 100%;
  margin-top: var(--space-3);
}

.footer-links {
  text-align: center;
  margin-top: var(--space-3);
}
</style>
