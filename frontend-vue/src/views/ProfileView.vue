<template>
  <div class="profile-view">
    <PageHeader title="个人中心" desc="完善个人资料，资料会展示在你所加入课程的成员列表中" />

    <!-- 基本资料（含头像） -->
    <div v-show="activeSection === 'basic'" class="panel">
      <el-card class="page-card" shadow="never">
        <!-- 头像区（与基本资料合并） -->
        <div class="avatar-head">
          <div class="avatar-preview" title="点击更换头像" @click="avatarDialog = true">
            <el-avatar :size="72" :src="avatarSrc">{{ initial }}</el-avatar>
            <span class="avatar-edit-badge">
              <el-icon><Camera /></el-icon>
            </span>
          </div>
          <div class="avatar-info">
            <div class="avatar-name">{{ store.displayName || '未命名用户' }}</div>
            <div class="avatar-sub">点击头像即可更换 · 支持 jpg / png / webp，不超过 2MB</div>
          </div>
          <el-button type="primary" plain @click="avatarDialog = true">更换头像</el-button>
        </div>

        <el-divider content-position="left">账号信息</el-divider>
        <div class="identity">
          <div class="identity-row">
            <span class="identity-label">用户名</span>
            <span class="identity-value">{{ profile.username || '—' }}</span>
          </div>
          <div class="identity-row">
            <span class="identity-label">身份</span>
            <el-tag size="small" :type="roleTagType" effect="dark">{{ roleText }}</el-tag>
          </div>
          <div class="identity-row">
            <span class="identity-label">邮箱</span>
            <span class="identity-value">{{ profile.email || '—' }}</span>
          </div>
          <div class="identity-row">
            <span class="identity-label">注册时间</span>
            <span class="identity-value">{{ fmtTime(profile.created_at) }}</span>
          </div>
        </div>

        <el-form :model="form" label-width="92px" @submit.prevent>
          <el-divider content-position="left">基本信息</el-divider>

          <el-row :gutter="16">
            <el-col :span="12">
              <el-form-item label="姓名">
                <el-input v-model="form.real_name" maxlength="50" placeholder="真实姓名（选填）" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="昵称">
                <el-input v-model="form.nickname" maxlength="50" placeholder="展示用昵称（选填）" />
              </el-form-item>
            </el-col>
          </el-row>

          <el-row :gutter="16">
            <el-col :span="12">
              <el-form-item label="性别">
                <el-select v-model="form.gender" placeholder="选填" clearable style="width: 100%">
                  <el-option label="男" value="male" />
                  <el-option label="女" value="female" />
                  <el-option label="其他" value="other" />
                  <el-option label="不愿透露" value="unknown" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="学校 / 机构">
                <el-input v-model="form.school" maxlength="100" placeholder="选填" />
              </el-form-item>
            </el-col>
          </el-row>

          <!-- 学生专属（管理员没有学籍，不展示这几项——后端也会静默丢弃这些字段） -->
          <template v-if="isStudent">
            <el-divider content-position="left">学籍信息</el-divider>
            <el-row :gutter="16">
              <el-col :span="12">
                <el-form-item label="学号">
                  <el-input v-model="form.student_no" maxlength="50" placeholder="选填" />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="学院">
                  <el-input v-model="form.college" maxlength="100" placeholder="选填" />
                </el-form-item>
              </el-col>
            </el-row>
            <el-row :gutter="16">
              <el-col :span="12">
                <el-form-item label="专业">
                  <el-input v-model="form.major" maxlength="50" placeholder="选填" />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="年级">
                  <el-input v-model="form.grade" maxlength="20" placeholder="例如：2023 级（选填）" />
                </el-form-item>
              </el-col>
            </el-row>
            <el-row :gutter="16">
              <el-col :span="12">
                <el-form-item label="班级">
                  <el-input v-model="form.class_name" maxlength="50" placeholder="选填" />
                </el-form-item>
              </el-col>
            </el-row>
          </template>

          <!-- 教师专属（管理员不是教师，不展示工号/职称/研究方向） -->
          <template v-else-if="isTeacher">
            <el-divider content-position="left">教师信息</el-divider>
            <el-row :gutter="16">
              <el-col :span="12">
                <el-form-item label="教师工号">
                  <el-input v-model="form.teacher_no" maxlength="50" placeholder="选填" />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="学院">
                  <el-input v-model="form.college" maxlength="100" placeholder="选填" />
                </el-form-item>
              </el-col>
            </el-row>
            <el-row :gutter="16">
              <el-col :span="12">
                <el-form-item label="职称">
                  <el-input v-model="form.title" maxlength="50" placeholder="例如：副教授（选填）" />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="研究方向">
                  <el-input v-model="form.research_area" maxlength="100" placeholder="选填" />
                </el-form-item>
              </el-col>
            </el-row>
          </template>

          <el-divider content-position="left">个人简介</el-divider>
          <el-form-item label="个人简介">
            <el-input
              v-model="form.bio"
              type="textarea"
              :rows="3"
              maxlength="200"
              show-word-limit
              placeholder="选填，最多 200 字"
            />
          </el-form-item>

          <div class="form-actions">
            <el-button @click="load" :disabled="saving">重置</el-button>
            <el-button type="primary" :loading="saving" @click="save">保存资料</el-button>
          </div>
        </el-form>
      </el-card>
    </div>

    <!-- 密码管理（内联表单） -->
    <div v-show="activeSection === 'password'" class="panel">
      <el-card class="page-card" shadow="never">
        <el-divider content-position="left">密码管理</el-divider>
        <el-alert
          class="pwd-alert"
          type="info"
          :closable="false"
          show-icon
          title="为了账号安全，建议定期更换登录密码"
        />
        <el-form :model="pwdForm" label-width="100px" class="pwd-form" @submit.prevent>
          <el-form-item label="原密码">
            <el-input
              v-model="pwdForm.old_password"
              type="password"
              show-password
              autocomplete="current-password"
              placeholder="请输入当前密码"
            />
          </el-form-item>
          <el-form-item label="新密码">
            <el-input
              v-model="pwdForm.new_password"
              type="password"
              show-password
              autocomplete="new-password"
              placeholder="至少 6 位"
            />
          </el-form-item>
          <el-form-item label="确认新密码">
            <el-input
              v-model="pwdForm.confirm_password"
              type="password"
              show-password
              autocomplete="new-password"
              placeholder="再次输入新密码"
            />
          </el-form-item>
          <div class="form-actions">
            <el-button type="primary" :loading="changingPwd" @click="changePassword">确认修改</el-button>
          </div>
        </el-form>
      </el-card>
    </div>

    <!-- 注销账号 -->
    <div v-show="activeSection === 'deactivate'" class="panel">
      <el-card class="page-card" shadow="never">
        <el-divider content-position="left">注销账号</el-divider>
        <div class="danger-zone">
          <div class="danger-text">
            <div class="danger-title">注销当前账号</div>
            <div class="danger-desc">
              注销后账号将被停用，无法再次登录，该操作不可撤销。你的历史学习数据仍会保留。
            </div>
          </div>
          <el-button type="danger" @click="openDeactivate">注销账号</el-button>
        </div>
      </el-card>
    </div>

    <!-- 更换头像弹窗 -->
    <el-dialog
      v-model="avatarDialog"
      title="更换头像"
      width="380px"
      align-center
      class="avatar-dialog"
    >
      <div class="av-dialog-body">
        <div class="av-dialog-preview" :class="{ 'has-image': !!avatarSrc }">
          <el-avatar :size="232" shape="square" :src="avatarSrc">{{ initial }}</el-avatar>
          <button
            v-if="avatarSrc"
            type="button"
            class="av-dialog-del"
            title="删除头像"
            @click="removeAvatar"
          >
            <el-icon><Delete /></el-icon>
          </button>
        </div>
        <div class="av-dialog-actions">
          <el-button class="av-download" :disabled="!avatarSrc" @click="downloadAvatar">下载</el-button>
          <el-upload
            class="av-upload"
            :show-file-list="false"
            :before-upload="beforeAvatarUpload"
            :http-request="doUploadAvatar"
            accept=".jpg,.jpeg,.png,.webp"
          >
            <el-button type="primary" :loading="uploading">更换</el-button>
          </el-upload>
        </div>
      </div>
    </el-dialog>

    <!-- 注销确认弹窗 -->
    <el-dialog v-model="deactivateDialog" title="注销账号" width="420px" align-center>
      <div class="deactivate-body">
        <el-icon class="deactivate-warn"><WarningFilled /></el-icon>
        <p class="deactivate-tip">
          注销后账号将无法登录，且该操作不可撤销。请输入登录密码以确认注销。
        </p>
        <el-input
          v-model="deactivateForm.password"
          type="password"
          show-password
          placeholder="请输入登录密码"
        />
      </div>
      <template #footer>
        <el-button :disabled="deactivating" @click="deactivateDialog = false">取消</el-button>
        <el-button type="danger" :loading="deactivating" @click="confirmDeactivate">确认注销</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Camera, Delete, WarningFilled } from '@element-plus/icons-vue'
import PageHeader from '../components/PageHeader.vue'
import { api } from '../api'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const route = useRoute()
const router = useRouter()

// 菜单在最左侧应用导航栏（App.vue 侧栏）中的「个人中心」分组下，
// 这里根据路由参数 tab 决定展示哪个面板
const SECTIONS = ['basic', 'password', 'deactivate']
const activeSection = computed(() => {
  const t = route.query.tab
  return SECTIONS.includes(t) ? t : 'basic'
})

const isTeacher = computed(() => store.role === 'teacher')
const isStudent = computed(() => store.role === 'student')
const isAdmin = computed(() => store.role === 'admin')
const roleText = computed(() => ({ teacher: '教师', student: '学生', admin: '管理员' }[store.role] || '学生'))
const roleTagType = computed(() => ({ teacher: 'warning', student: 'success', admin: 'primary' }[store.role] || 'success'))
const profile = computed(() => store.profile || {})
const initial = computed(() => (store.displayName || '?').slice(0, 1).toUpperCase())

// 头像直链：后端对未设置头像返回 404，el-avatar 会回退到插槽里的首字母
const avatarSrc = computed(() => store.avatarUrl || undefined)

const saving = ref(false)
const uploading = ref(false)

// 头像弹窗
const avatarDialog = ref(false)

// 密码管理
const pwdForm = reactive({ old_password: '', new_password: '', confirm_password: '' })
const changingPwd = ref(false)

// 注销账号
const deactivateDialog = ref(false)
const deactivateForm = reactive({ password: '' })
const deactivating = ref(false)

const FIELDS = ['avatar_url', 'real_name', 'nickname', 'gender', 'school', 'college', 'bio',
  'student_no', 'major', 'grade', 'class_name', 'teacher_no', 'title', 'research_area']

// 各角色可提交的字段（与后端 ProfileService.editable_fields 的白名单一一对应）。
// 提交前就按角色收敛，是为了不让用户遇到「填了但保存后变空」——后端对越权字段是静默丢弃的。
const SHARED_FIELDS = ['real_name', 'nickname', 'gender', 'school', 'bio']
const STUDENT_ONLY = ['student_no', 'college', 'major', 'grade', 'class_name']
const TEACHER_ONLY = ['teacher_no', 'college', 'title', 'research_area']
const editableFields = computed(() => {
  if (isAdmin.value) return SHARED_FIELDS
  return isTeacher.value ? [...SHARED_FIELDS, ...TEACHER_ONLY] : [...SHARED_FIELDS, ...STUDENT_ONLY]
})

const form = reactive(Object.fromEntries(FIELDS.map((f) => [f, ''])))

const fmtTime = (t) => (t ? String(t).slice(0, 16) : '—')

function fillForm(p) {
  for (const f of FIELDS) form[f] = p?.[f] ?? ''
}

async function load() {
  try {
    const data = await store.fetchProfile(true)
    fillForm(data)
  } catch (e) {
    ElMessage.error(e?.message || '加载资料失败')
  }
}

async function save() {
  saving.value = true
  try {
    // 只提交当前角色可编辑的字段；空串表示清空（后端会把空串写为 NULL）
    const payload = {}
    for (const f of editableFields.value) payload[f] = form[f] === '' ? '' : form[f]
    const data = await api.updateProfile(payload)
    store.applyProfile(data)
    fillForm(data)
    ElMessage.success('资料已保存')
  } catch (e) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

// ---- 修改密码（内联） ----
async function changePassword() {
  if (!pwdForm.old_password) { ElMessage.warning('请输入原密码'); return }
  if (!pwdForm.new_password || pwdForm.new_password.length < 6) { ElMessage.warning('新密码长度至少 6 位'); return }
  if (pwdForm.new_password !== pwdForm.confirm_password) { ElMessage.warning('两次输入的新密码不一致'); return }
  if (pwdForm.new_password === pwdForm.old_password) { ElMessage.warning('新密码不能与原密码相同'); return }
  changingPwd.value = true
  try {
    await api.changePassword({ old_password: pwdForm.old_password, new_password: pwdForm.new_password })
    ElMessage.success('密码修改成功，请使用新密码重新登录')
    pwdForm.old_password = ''
    pwdForm.new_password = ''
    pwdForm.confirm_password = ''
  } catch (err) {
    ElMessage.error(err?.message || '密码修改失败')
  } finally {
    changingPwd.value = false
  }
}

// ---- 头像 ----

/** 客户端预校验（与后端一致：类型白名单 + 2MB），避免白传一次大文件 */
function beforeAvatarUpload(file) {
  const okType = ['image/jpeg', 'image/png', 'image/webp'].includes(file.type)
  if (!okType) {
    ElMessage.error('头像格式不支持（仅 jpg / jpeg / png / webp）')
    return false
  }
  if (file.size > 2 * 1024 * 1024) {
    ElMessage.error('头像过大（最大 2MB）')
    return false
  }
  return true
}

async function doUploadAvatar(options) {
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', options.file)
    const data = await api.uploadAvatar(fd)
    // 立即反映到侧边栏（store.applyProfile 会同步 localStorage 里的 kg_user）
    store.applyProfile({ ...(store.profile || {}), avatar_url: data.avatar_url })
    ElMessage.success('头像已更新')
    options.onSuccess?.(data)
  } catch (e) {
    ElMessage.error(e?.message || '头像上传失败')
    options.onError?.(e)
  } finally {
    uploading.value = false
  }
}

async function removeAvatar() {
  try {
    await api.deleteAvatar()
    store.applyProfile({ ...(store.profile || {}), avatar_url: '' })
    ElMessage.success('头像已删除')
  } catch (e) {
    ElMessage.error(e?.message || '头像删除失败')
  }
}

function downloadAvatar() {
  if (!avatarSrc.value) return
  const a = document.createElement('a')
  a.href = avatarSrc.value
  a.download = `${store.displayName || 'avatar'}`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

// ---- 注销账号 ----
function openDeactivate() {
  deactivateForm.password = ''
  deactivateDialog.value = true
}

async function confirmDeactivate() {
  if (!deactivateForm.password) { ElMessage.warning('请输入登录密码'); return }
  deactivating.value = true
  try {
    await api.deactivateAccount({ password: deactivateForm.password })
    deactivateDialog.value = false
    ElMessage.success('账号已注销')
    store.logout()
    router.push('/login')
  } catch (e) {
    ElMessage.error(e?.message || '注销失败')
  } finally {
    deactivating.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.profile-view {
  width: 100%;
}

/* 内容区收窄，避免基本资料横向铺得过开 */
.panel {
  max-width: 760px;
}

/* ===== 头像区（并入基本资料顶部） ===== */
.avatar-head {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-1) 0 var(--space-4);
}
.avatar-preview {
  position: relative;
  cursor: pointer;
  border-radius: 50%;
  line-height: 0;
}
.avatar-preview :deep(.el-avatar) {
  background: var(--gradient-brand);
  font-size: 28px;
  font-weight: 700;
  box-shadow: 0 0 0 3px var(--brand-50), 0 8px 18px -8px rgba(79, 110, 247, .55);
}
.avatar-edit-badge {
  position: absolute;
  right: -2px;
  bottom: -2px;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--brand-500, #4f6ef7);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  border: 2px solid #fff;
  box-shadow: 0 2px 6px rgba(0, 0, 0, .18);
}
.avatar-info { flex: 1; min-width: 0; }
.avatar-name {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}
.avatar-sub {
  margin-top: 4px;
  font-size: 12.5px;
  color: var(--color-text-muted);
}

/* ===== 账号信息 ===== */
.identity {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: var(--space-2);
}
.identity-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  font-size: 13px;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  background: var(--bg-soft);
}
.identity-label { font-size: 12.5px; color: var(--color-text-muted); }
.identity-value {
  font-weight: 500;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ===== 密码管理 ===== */
.pwd-alert { margin-bottom: var(--space-4); border-radius: var(--radius-sm); }
.pwd-form { max-width: 460px; }

/* ===== 注销账号 ===== */
.danger-zone {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border: 1px solid rgba(245, 108, 108, .3);
  border-radius: var(--radius-sm);
  background: rgba(245, 108, 108, .06);
}
.danger-title { font-size: 14px; font-weight: 600; color: var(--text-primary); }
.danger-desc {
  margin-top: 4px;
  font-size: 12.5px;
  color: var(--color-text-muted);
  max-width: 460px;
}

/* ===== 表单 ===== */
.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-top: var(--space-4);
}

/* ===== 更换头像弹窗 ===== */
.av-dialog-body {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-5);
  padding: var(--space-2) 0 var(--space-3);
}
.av-dialog-preview {
  position: relative;
  line-height: 0;
  border-radius: 8px;
  overflow: hidden;
}
.av-dialog-preview :deep(.el-avatar) {
  background: var(--gradient-brand);
  font-size: 64px;
  font-weight: 700;
  border-radius: 8px;
}
.av-dialog-del {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  border: none;
  cursor: pointer;
  color: #fff;
  font-size: 26px;
  background: rgba(0, 0, 0, 0);
  opacity: 0;
  transition: opacity .18s ease, background .18s ease;
}
.av-dialog-preview.has-image:hover .av-dialog-del {
  opacity: 1;
  background: rgba(0, 0, 0, .38);
}
.av-dialog-actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  justify-content: center;
}
.av-dialog-actions .av-download { min-width: 120px; }
.av-dialog-actions :deep(.av-upload) { display: inline-block; }
.av-dialog-actions :deep(.av-upload .el-button) { min-width: 120px; }

/* ===== 注销确认弹窗 ===== */
.deactivate-body {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: var(--space-3);
}
.deactivate-warn {
  font-size: 40px;
  color: #f56c6c;
}
.deactivate-tip {
  margin: 0 0 var(--space-2);
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text-muted);
}
</style>
