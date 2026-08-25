<template>
  <div class="course-selector">
    <span class="label">课程</span>
    <el-select
      :model-value="modelValue"
      filterable
      clearable
      :loading="store.isLoading"
      placeholder="选择课程"
      style="width: 240px"
      @update:model-value="onChange"
    >
      <el-option
        v-for="c in store.courses"
        :key="c.course_id"
        :label="optionLabel(c)"
        :value="String(c.course_id)"
      />
    </el-select>
    <el-button :icon="Plus" circle size="small" title="新建课程" @click="openCreate" />
    <el-tooltip content="课程列表来自后端；点击 + 新建课程" placement="top">
      <el-icon class="hint-icon"><QuestionFilled /></el-icon>
    </el-tooltip>

    <!-- 新建课程对话框 -->
    <el-dialog v-model="createVisible" title="新建课程" width="420px">
      <el-form label-width="80px" @submit.prevent>
        <el-form-item label="课程名称" required>
          <el-input
            v-model="createName"
            placeholder="例如：数据结构"
            @keyup.enter="submitCreate"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Plus, QuestionFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useAppStore } from '../stores/app'

defineProps({
  modelValue: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'change'])
const store = useAppStore()

const createVisible = ref(false)
const createName = ref('')
const creating = ref(false)

function optionLabel(c) {
  return c.node_count ? `${c.course_name}（${c.node_count} 知识点）` : c.course_name
}

function onChange(val) {
  store.currentCourseId = val ? String(val) : ''
  emit('update:modelValue', val || '')
  emit('change', val || '')
}

function openCreate() {
  createName.value = ''
  createVisible.value = true
}

async function submitCreate() {
  const name = createName.value.trim()
  if (!name) {
    ElMessage.warning('课程名称不能为空')
    return
  }
  creating.value = true
  try {
    const data = await store.createCourse(name)
    createVisible.value = false
    createName.value = ''
    onChange(String(data.course_id))
    ElMessage.success(`课程「${name}」创建成功`)
  } catch (e) {
    ElMessage.error(`创建失败：${e.message}`)
  } finally {
    creating.value = false
  }
}

onMounted(() => {
  store.fetchCourses().catch(() => {})
})
</script>

<style scoped>
.course-selector {
  display: flex;
  align-items: center;
  gap: 8px;
}
.label {
  color: #606266;
  font-size: 14px;
}
.hint-icon {
  color: #c0c4cc;
  cursor: help;
}
</style>
