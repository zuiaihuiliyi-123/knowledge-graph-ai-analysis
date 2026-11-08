<template>
  <el-dialog
    :model-value="modelValue"
    title="新建课程"
    width="520px"
    :close-on-click-modal="false"
    @update:model-value="$emit('update:modelValue', $event)"
    @closed="resetForm"
  >
    <el-form ref="formRef" :model="form" :rules="rules" label-width="92px" @submit.prevent>
      <el-form-item label="课程名称" prop="course_name">
        <el-input
          v-model="form.course_name"
          placeholder="例如：数据结构"
          maxlength="50"
          show-word-limit
          @keyup.enter="submit"
        />
      </el-form-item>
      <el-form-item label="课程简介">
        <el-input
          v-model="form.description"
          type="textarea"
          :rows="3"
          maxlength="500"
          show-word-limit
          placeholder="选填：一句话说明这门课讲什么"
        />
      </el-form-item>
      <el-form-item label="课程分类">
        <el-input v-model="form.category" placeholder="选填，例如：计算机 / 数学" maxlength="30" />
      </el-form-item>
      <el-form-item label="开课院系">
        <el-input v-model="form.organization" placeholder="选填，例如：软件学院" maxlength="100" />
      </el-form-item>
      <el-form-item label="加入方式">
        <el-radio-group v-model="form.join_mode">
          <el-radio value="auto">直接加入</el-radio>
          <el-radio value="approval">审核后加入</el-radio>
          <el-radio value="closed">关闭加入</el-radio>
        </el-radio-group>
        <div class="field-tip">
          {{ JOIN_MODE_TIP[form.join_mode] }}
        </div>
      </el-form-item>
      <el-form-item label="允许发现">
        <el-switch v-model="form.isPublicBool" active-text="学生可在「发现课程」中看到并申请" />
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="$emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">创建</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAppStore } from '../../stores/app'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'created'])

const store = useAppStore()
const router = useRouter()
const formRef = ref(null)
const submitting = ref(false)

const JOIN_MODE_TIP = {
  auto: '学生用加课码加入后立即可学习，无需审核',
  approval: '学生提交后进入待审核，由你在「学生管理」中同意',
  closed: '加课码不可用，只能通过邀请链接加入',
}

const form = reactive({
  course_name: '',
  description: '',
  category: '',
  organization: '',
  join_mode: 'approval',
  isPublicBool: true,
})

const rules = {
  course_name: [
    { required: true, message: '请输入课程名称', trigger: 'blur' },
    { max: 50, message: '课程名最长 50 个字符', trigger: 'blur' },
  ],
}

watch(() => props.modelValue, (v) => {
  if (v) formRef.value?.clearValidate?.()
})

function resetForm() {
  form.course_name = ''
  form.description = ''
  form.category = ''
  form.organization = ''
  form.join_mode = 'approval'
  form.isPublicBool = true
  formRef.value?.clearValidate?.()
}

async function submit() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  submitting.value = true
  try {
    const data = await store.createCourse(form.course_name.trim(), {
      description: form.description?.trim() || null,
      category: form.category?.trim() || null,
      organization: form.organization?.trim() || null,
      join_mode: form.join_mode,
      is_public: form.isPublicBool ? 1 : 0,
    })
    emit('created', data)
    emit('update:modelValue', false)
    showJoinCode(data)
  } catch (e) {
    ElMessage.error(e?.message || '创建课程失败')
  } finally {
    submitting.value = false
  }
}

/** 创建成功后把加课码直接摆到教师面前，并给一个「去上传文档」的下一步行动 */
function showJoinCode(data) {
  if (!data?.join_code) {
    ElMessage.success('课程创建成功')
    return
  }
  ElMessageBox.confirm(
    `课程「${data.course_name}」已创建。\n加课码：${data.join_code}\n\n把它发给学生，学生即可在「课程中心 → 加入课程」中输入加入。\n\n现在去上传课程文档，系统会自动抽取知识点并构建知识图谱。`,
    '创建成功',
    {
      confirmButtonText: '去上传文档',
      cancelButtonText: '稍后再说',
      type: 'success',
      customClass: 'joincode-alert',
    },
  )
    // 教师建课的下一步几乎必然是上传文档：直接把入口递到面前，避免建完课「课程去哪了」的断点
    .then(() => router.push({ path: '/teacher', query: { tab: 'documents', course_id: String(data.course_id) } }))
    .catch(() => {})
}
</script>

<style scoped>
.field-tip {
  font-size: 12px;
  color: var(--color-text-muted);
  line-height: 1.5;
  margin-top: 2px;
}
</style>
