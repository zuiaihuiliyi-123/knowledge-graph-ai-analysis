<template>
  <el-dialog
    :model-value="modelValue"
    title="课程设置"
    width="540px"
    :close-on-click-modal="false"
    :before-close="handleBeforeClose"
    @update:model-value="$emit('update:modelValue', $event)"
    @open="syncForm"
  >
    <el-form :model="form" label-width="92px" @submit.prevent>
      <el-form-item label="课程名称">
        <el-input v-model="form.course_name" maxlength="50" show-word-limit />
      </el-form-item>
      <el-form-item label="课程简介">
        <el-input v-model="form.description" type="textarea" :rows="3" maxlength="500" show-word-limit />
      </el-form-item>
      <el-form-item label="课程分类">
        <el-input v-model="form.category" maxlength="30" placeholder="例如：计算机" />
      </el-form-item>
      <el-form-item label="开课院系">
        <el-input v-model="form.organization" maxlength="100" placeholder="例如：软件学院" />
      </el-form-item>

      <el-form-item label="加入方式">
        <el-radio-group v-model="form.join_mode">
          <el-radio value="auto">直接加入</el-radio>
          <el-radio value="approval">审核后加入</el-radio>
          <el-radio value="closed">关闭加入</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="允许发现">
        <el-switch v-model="form.isPublicBool" active-text="学生可在「发现课程」中看到并申请" />
      </el-form-item>

      <el-form-item label="加课码">
        <div class="joincode-row">
          <code class="joincode">{{ form.join_code || '—' }}</code>
          <el-button size="small" :icon="CopyDocument" @click="copyCode">复制</el-button>
          <el-button size="small" :loading="refreshing" @click="refreshCode">刷新</el-button>
        </div>
        <div class="tip">刷新后旧加课码立即失效，请把新码重新发给学生。</div>
      </el-form-item>
    </el-form>

    <el-divider />

    <div class="danger-zone">
      <div class="danger-text">
        <div class="danger-title">删除课程</div>
        <div class="tip">
          将同时删除该课程的文档、知识图谱、学习记录、收藏、成员与邀请，且不可恢复。
        </div>
      </div>
      <el-button type="danger" plain @click="doDelete">删除课程</el-button>
    </div>

    <template #footer>
      <el-button @click="handleCancel">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CopyDocument } from '@element-plus/icons-vue'
import { api } from '../../api'
import { useAppStore } from '../../stores/app'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  course: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue', 'saved', 'deleted'])

const store = useAppStore()
const saving = ref(false)
const refreshing = ref(false)

const form = reactive({
  course_name: '',
  description: '',
  category: '',
  organization: '',
  join_mode: 'approval',
  isPublicBool: true,
  join_code: '',
})

function syncForm() {
  const c = props.course || {}
  form.course_name = c.course_name || ''
  form.description = c.description || ''
  form.category = c.category || ''
  form.organization = c.organization || ''
  form.join_mode = c.join_mode || 'approval'
  form.isPublicBool = c.is_public !== 0
  form.join_code = c.join_code || ''
  baseline = dirtyKey()
  // 卡片数据里可能没有 join_code（列表接口对教师也会返回），保险起见再拉一次
  if (!form.join_code && c.course_id) {
    api.getJoinCode(c.course_id)
      .then((d) => { form.join_code = d.join_code || '' })
      .catch(() => {})
  }
}

// ===== 未保存修改保护：表单和打开时的快照比对，关闭前确认 =====
// 加课码不算「修改」——刷新加课码是独立动作，不改表单字段
let baseline = ''

function dirtyKey() {
  return JSON.stringify([
    form.course_name, form.description, form.category,
    form.organization, form.join_mode, form.isPublicBool,
  ])
}

function isDirty() {
  return baseline !== '' && dirtyKey() !== baseline
}

function handleBeforeClose(done) {
  if (!isDirty()) {
    done()
    return
  }
  ElMessageBox.confirm(
    '课程设置有改动还没保存，关闭后会丢失。确定关闭吗？',
    '放弃修改',
    { type: 'warning', confirmButtonText: '放弃', cancelButtonText: '继续编辑' },
  ).then(() => done()).catch(() => {})
}

function handleCancel() {
  handleBeforeClose(() => emit('update:modelValue', false))
}

async function save() {
  saving.value = true
  try {
    await api.updateCourse(props.course.course_id, {
      course_name: form.course_name?.trim() || undefined,
      description: form.description?.trim() ?? undefined,
      category: form.category?.trim() || null,
      organization: form.organization?.trim() || null,
      join_mode: form.join_mode,
      is_public: form.isPublicBool ? 1 : 0,
    })
    ElMessage.success('课程设置已保存')
    emit('saved')
    emit('update:modelValue', false)
  } catch (e) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function refreshCode() {
  const ok = await ElMessageBox.confirm(
    '刷新后旧加课码立即失效，已把旧码发给学生的需要重新发送。确定刷新吗？',
    '刷新加课码',
    { type: 'warning' },
  ).catch(() => false)
  if (!ok) return
  refreshing.value = true
  try {
    const d = await api.refreshJoinCode(props.course.course_id)
    form.join_code = d.join_code
    ElMessage.success('加课码已刷新')
    emit('saved')
  } catch (e) {
    ElMessage.error(e?.message || '刷新失败')
  } finally {
    refreshing.value = false
  }
}

async function copyCode() {
  if (!form.join_code) return
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(form.join_code)
    } else {
      const ta = document.createElement('textarea')
      ta.value = form.join_code
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    ElMessage.success(`已复制加课码：${form.join_code}`)
  } catch {
    ElMessage.warning(`复制失败，请手动输入：${form.join_code}`)
  }
}

async function doDelete() {
  const ok = await ElMessageBox.confirm(
    `确定删除课程「${props.course?.course_name}」吗？\n\n该课程的文档、知识图谱、学习记录、收藏、成员与邀请都会一并删除，且不可恢复。`,
    '删除课程',
    { type: 'error', confirmButtonText: '确认删除', cancelButtonText: '取消' },
  ).catch(() => false)
  if (!ok) return
  try {
    await store.deleteCourse(props.course.course_id)
    ElMessage.success('课程已删除')
    emit('deleted', props.course)
    emit('update:modelValue', false)
  } catch (e) {
    ElMessage.error(e?.message || '删除失败')
  }
}
</script>

<style scoped>
.joincode-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.joincode {
  font-family: var(--font-family-number);
  font-size: 15px;
  letter-spacing: 2px;
  color: var(--color-primary);
  background: var(--color-bg-soft);
  padding: 4px 10px;
  border-radius: var(--radius-sm);
}
.tip {
  font-size: 12px;
  color: var(--color-text-muted);
  line-height: 1.6;
}

.danger-zone {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.danger-title {
  font-size: 13px;
  font-weight: var(--font-weight-semibold);
  color: var(--color-danger);
  margin-bottom: 2px;
}
.danger-text {
  min-width: 0;
}
</style>
