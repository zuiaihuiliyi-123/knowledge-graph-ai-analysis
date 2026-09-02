<template>
  <div class="course-selector" :class="{ compact }">
    <span v-if="!compact" class="label">课程</span>
    <el-select
      :model-value="modelValue"
      filterable
      clearable
      :loading="store.isLoading"
      placeholder="选择课程"
      :style="selectStyle"
      @update:model-value="onChange"
    >
      <el-option
        v-for="c in store.courses"
        :key="c.course_id"
        :label="optionLabel(c)"
        :value="String(c.course_id)"
      >
        <span class="option-row">
          <span class="option-label">{{ optionLabel(c) }}</span>
          <el-icon
            v-if="store.role === 'teacher'"
            class="option-del"
            title="删除课程"
            @click.stop="onDelete(c)"
          >
            <Delete />
          </el-icon>
        </span>
      </el-option>
    </el-select>
    <el-button
      v-if="store.role === 'teacher' && !compact"
      :icon="Plus"
      circle
      size="small"
      title="新建课程"
      @click="openCreate"
    />
    <el-tooltip
      v-if="!compact"
      :content="
        store.role === 'teacher'
          ? '点击 + 新建课程，悬停课程项可删除'
          : '选择课程后浏览知识图谱、问答与学习路径'
      "
      placement="top"
    >
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
import { ref, computed, onMounted } from 'vue'
import { Plus, QuestionFilled, Delete } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAppStore } from '../stores/app'

const props = defineProps({
  modelValue: { type: String, default: '' },
  /** 紧凑模式：用于窄列（如问答左栏），隐藏标签与提示，下拉自适应宽度 */
  compact: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'change'])
const store = useAppStore()

const selectStyle = computed(() =>
  props.compact ? { flex: 1, minWidth: '120px' } : { width: '240px' }
)

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
  if (store.role !== 'teacher') return
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

function onDelete(course) {
  const id = course.course_id
  const name = course.course_name
  ElMessageBox.confirm(
    `确定删除课程「${name}」吗？删除将同时删除该课程下的所有文档、图谱数据及学习记录，此操作不可恢复。`,
    '删除课程',
    {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      confirmButtonClass: 'el-button--danger',
    }
  )
    .then(async () => {
      try {
        await store.deleteCourse(id)
        ElMessage.success('课程已删除')
        // 若删除的是当前选中课程，清空父组件选中值，避免图谱加载已删除的课程
        if (String(props.modelValue) === String(id)) onChange('')
      } catch (e) {
        ElMessage.error(`删除失败：${e.message}`)
      }
    })
    .catch(() => {})
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
.course-selector.compact {
  width: 100%;
}
.label {
  color: #606266;
  font-size: 14px;
}
.hint-icon {
  color: #c0c4cc;
  cursor: help;
}
.option-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
}
.option-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.option-del {
  color: #f56c6c;
  cursor: pointer;
  flex-shrink: 0;
  visibility: hidden;
}
.option-row:hover .option-del {
  visibility: visible;
}
</style>
