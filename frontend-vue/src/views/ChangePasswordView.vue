<template>
  <div class="change-pwd-view">
    <PageHeader
      title="修改密码"
      :desc="forced
        ? '当前密码由管理员分配或系统生成，请先设置一个只有您自己知道的新密码'
        : '为了账号安全，建议定期更换登录密码'"
    />

    <el-alert
      v-if="forced"
      class="pwd-forced-tip"
      type="warning"
      show-icon
      :closable="false"
      title="首次登录需要修改密码"
      description="为保证账号安全，在修改密码之前无法使用其它功能。修改成功后即可正常进入系统。"
    />

    <el-card class="page-card pwd-form-card" shadow="never">
      <el-form :model="pwdForm" label-width="100px" @submit.prevent>
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
          <!-- 强制改密期间不提供「返回」：此时没有可返回的业务页面，
               守卫会把任何其它路由再送回来，按钮点了也像没反应 -->
          <el-button v-if="!forced" :disabled="changingPwd" @click="goBack">返回</el-button>
          <el-button type="primary" :loading="changingPwd" @click="changePassword">确认修改</el-button>
        </div>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import PageHeader from '../components/PageHeader.vue'
import { api } from '../api'
import { useAppStore } from '../stores/app'

const route = useRoute()
const router = useRouter()
const store = useAppStore()

// 强制改密模式：由路由守卫带 ?forced=1 进来，或本地用户标记仍为 1（刷新后仍成立）
const forced = computed(
  () => route.query.forced === '1' || !!store.user?.must_change_password
)

const pwdForm = reactive({ old_password: '', new_password: '', confirm_password: '' })
const changingPwd = ref(false)

function goBack() {
  router.back()
}

/** 按角色回到首页（强制改密成功后离开改密页） */
function goHome() {
  router.replace(store.isAdmin ? { path: '/admin' } : { path: '/course-center', query: { tab: 'mine' } })
}

async function changePassword() {
  if (!pwdForm.old_password) { ElMessage.warning('请输入原密码'); return }
  if (!pwdForm.new_password || pwdForm.new_password.length < 6) { ElMessage.warning('新密码长度至少 6 位'); return }
  if (pwdForm.new_password !== pwdForm.confirm_password) { ElMessage.warning('两次输入的新密码不一致'); return }
  if (pwdForm.new_password === pwdForm.old_password) { ElMessage.warning('新密码不能与原密码相同'); return }
  changingPwd.value = true
  try {
    await api.changePassword({ old_password: pwdForm.old_password, new_password: pwdForm.new_password })
    // 后端已清除改密标记，本地标记同步清掉，否则守卫还会把人挡在改密页
    store.clearMustChangePassword()
    ElMessage.success('密码修改成功')
    pwdForm.old_password = ''
    pwdForm.new_password = ''
    pwdForm.confirm_password = ''
    if (forced.value) goHome()
  } catch (err) {
    ElMessage.error(err?.message || '密码修改失败')
  } finally {
    changingPwd.value = false
  }
}
</script>

<style scoped>
.change-pwd-view {
  max-width: 640px;
}
.pwd-forced-tip {
  margin-bottom: var(--space-4);
}
.pwd-form-card :deep(.el-card__body) {
  padding: var(--space-5);
}
.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-top: var(--space-4);
}
</style>
