<template>
  <div class="course-selector">
    <span class="label">课程</span>
    <el-select
      :model-value="modelValue"
      filterable
      allow-create
      default-first-option
      placeholder="选择或输入 course_id"
      style="width: 260px"
      @update:model-value="onChange"
    >
      <el-option
        v-for="c in store.courses"
        :key="c.courseId"
        :label="`${c.name}（${c.courseId}）`"
        :value="c.courseId"
      />
    </el-select>
    <el-tooltip content="后端暂无课程列表接口，已上传的课程记录在本地；也可直接输入 course_id 查询" placement="top">
      <el-icon class="hint-icon"><QuestionFilled /></el-icon>
    </el-tooltip>
  </div>
</template>

<script setup>
import { QuestionFilled } from '@element-plus/icons-vue'
import { useAppStore } from '../stores/app'

defineProps({
  modelValue: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'change'])
const store = useAppStore()

function onChange(val) {
  if (val) store.registerCourse({ courseId: val, name: val })
  emit('update:modelValue', val || '')
  emit('change', val || '')
}
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
