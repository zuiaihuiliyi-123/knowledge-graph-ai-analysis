<template>
  <el-dialog
    :model-value="modelValue"
    title="邀请加入课程"
    width="620px"
    @update:model-value="$emit('update:modelValue', $event)"
    @open="reload"
  >
    <div class="invite-create">
      <el-form inline @submit.prevent>
        <el-form-item label="邀请角色">
          <el-radio-group v-model="role">
            <el-radio value="student">学生</el-radio>
            <!-- 邀请协作教师 = 组建管理团队，仅课程创建者可操作 -->
            <el-radio v-if="isOwner" value="teacher">协作教师</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="有效期">
          <el-input-number v-model="days" :min="1" :max="365" :step="1" />
          <span class="unit">天</span>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="creating" @click="generate">生成邀请链接</el-button>
        </el-form-item>
      </el-form>
      <div class="tip">
        邀请链接使用随机令牌，学生打开后可直接加入（无需审核），且不受「关闭加入」影响。
        链接可设置有效期，也可随时撤销。
        <template v-if="!isOwner">协作教师只能邀请学生；邀请协作教师需要课程创建者操作。</template>
      </div>
    </div>

    <div v-if="justCreated" class="fresh-link">
      <div class="fresh-label">新生成的链接（请尽快复制）</div>
      <div class="link-row invite-link-row">
        <el-input :model-value="fullUrl(justCreated.token)" readonly>
          <template #append>
            <el-button :icon="CopyDocument" @click="copy(fullUrl(justCreated.token))">复制</el-button>
          </template>
        </el-input>
      </div>
      <div class="fresh-meta">
        角色：{{ justCreated.role === 'teacher' ? '协作教师' : '学生' }} ·
        有效期至 {{ fmtTime(justCreated.expires_at) }}
      </div>
    </div>

    <el-divider content-position="left">已有邀请</el-divider>

    <el-table :data="invites" v-loading="loading" size="small" empty-text="还没有邀请链接">
      <el-table-column label="角色" width="92">
        <template #default="{ row }">
          <el-tag size="small" effect="plain" :type="row.role === 'teacher' ? 'warning' : 'success'">
            {{ row.role === 'teacher' ? '教师' : '学生' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="statusType(row.effective_status)" effect="plain">
            {{ statusText(row.effective_status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="有效期至" width="140">
        <template #default="{ row }">{{ fmtTime(row.expires_at) }}</template>
      </el-table-column>
      <el-table-column label="使用者" min-width="100">
        <template #default="{ row }">{{ row.used_by_name || '—' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="row.effective_status === 'active'"
            size="small"
            text
            :icon="CopyDocument"
            @click="copy(fullUrl(row.token))"
          >复制</el-button>
          <el-button
            v-if="row.effective_status === 'active' && (row.role !== 'teacher' || isOwner)"
            size="small"
            text
            type="danger"
            @click="revoke(row)"
          >撤销</el-button>
        </template>
      </el-table-column>
    </el-table>

    <template #footer>
      <el-button @click="$emit('update:modelValue', false)">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CopyDocument } from '@element-plus/icons-vue'
import { api } from '../../api'
import { formatDateTime } from '../../utils/format'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  courseId: { type: [String, Number], default: '' },
  // 是否课程创建者：协作教师只能邀请学生，不能组建教师团队 / 撤销教师邀请
  isOwner: { type: Boolean, default: true },
})
const emit = defineEmits(['update:modelValue', 'changed'])

const role = ref('student')
const days = ref(7)
const creating = ref(false)
const loading = ref(false)
const invites = ref([])
const justCreated = ref(null)

function fmtTime(t) {
  return formatDateTime(t)
}

/** 拼出可直接发给学生的完整地址（用当前站点 origin，避免后端返回相对路径无法直接粘贴） */
function fullUrl(token) {
  return `${window.location.origin}/invite/${token}`
}

const STATUS_TEXT = { active: '生效中', used: '已使用', revoked: '已撤销', expired: '已过期' }
const STATUS_TYPE = { active: 'success', used: 'info', revoked: 'info', expired: 'warning' }
const statusText = (s) => STATUS_TEXT[s] || s
const statusType = (s) => STATUS_TYPE[s] || 'info'

async function reload() {
  if (!props.courseId) return
  // 协作教师打开对话框时不能停留在「协作教师」单选项上（该选项此时不渲染）
  if (!props.isOwner && role.value === 'teacher') role.value = 'student'
  loading.value = true
  try {
    const data = await api.listInvites(props.courseId)
    invites.value = data.items || []
  } catch (e) {
    ElMessage.error(e?.message || '加载邀请列表失败')
  } finally {
    loading.value = false
  }
}

async function generate() {
  creating.value = true
  try {
    const data = await api.createInvite(props.courseId, {
      role: role.value,
      expires_in_days: days.value,
    })
    justCreated.value = data
    await reload()
    emit('changed')
  } catch (e) {
    ElMessage.error(e?.message || '生成邀请失败')
  } finally {
    creating.value = false
  }
}

async function revoke(row) {
  const ok = await ElMessageBox.confirm('撤销后该链接立即失效，确定继续吗？', '撤销邀请', {
    type: 'warning',
  }).catch(() => false)
  if (!ok) return
  try {
    await api.revokeInvite(props.courseId, row.invite_id)
    ElMessage.success('已撤销该邀请链接')
    if (justCreated.value?.invite_id === row.invite_id) justCreated.value = null
    await reload()
    emit('changed')
  } catch (e) {
    ElMessage.error(e?.message || '撤销失败')
  }
}

async function copy(text) {
  const ok = await copyText(text)
  if (ok) ElMessage.success('邀请链接已复制，发给学生即可')
  else ElMessage.warning(`复制失败，请手动复制：${text}`)
}

async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    /* 回退 */
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}

watch(() => props.modelValue, (v) => {
  if (!v) justCreated.value = null
})
</script>

<style scoped>
.invite-create {
  background: var(--color-bg-soft);
  border-radius: var(--radius-md);
  padding: 12px 14px 2px;
}
.invite-create :deep(.el-form-item) {
  margin-bottom: 12px;
}
.unit {
  margin-left: 6px;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.tip {
  font-size: 12px;
  color: var(--color-text-muted);
  line-height: 1.6;
  padding-bottom: 10px;
}

.fresh-link {
  margin-top: var(--space-3);
  border: 1px solid var(--color-primary-light, #d4ddfc);
  background: var(--color-bg-soft);
  border-radius: var(--radius-md);
  padding: 12px 14px;
}
.fresh-label {
  font-size: 12px;
  color: var(--color-primary);
  margin-bottom: 6px;
}
.fresh-meta {
  font-size: 12px;
  color: var(--color-text-muted);
  margin-top: 6px;
}
</style>
