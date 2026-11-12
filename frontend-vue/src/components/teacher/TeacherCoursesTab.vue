<template>
  <div>
    <div class="course-toolbar">
      <span class="stats-text">共 {{ courses.length }} 门课程</span>

      <!-- 课程身份筛选：教师既能管理自己创建的课程，也能管理被邀请协作的课程。
           这里只做展示层分组，真正的权限仍由后端按课程逐一判定。 -->
      <div class="course-filter" role="group" aria-label="课程范围">
        <button
          v-for="opt in filterOptions"
          :key="opt.value"
          type="button"
          class="filter-btn"
          :class="{ active: filter === opt.value }"
          @click="filter = opt.value"
        >
          {{ opt.label }} {{ opt.count }}
        </button>
      </div>

      <div class="toolbar-actions">
        <el-button :icon="Link" @click="emit('join')">加入课程</el-button>
        <el-button type="primary" :icon="Plus" @click="emit('create')">新建课程</el-button>
        <el-button :icon="Promotion" @click="emit('invite')">邀请学生</el-button>
      </div>
    </div>

    <MyCourseGrid
      :courses="filteredCourses"
      role="teacher"
      :loading="loading"
      show-create
      :empty-text="emptyText"
      @create="emit('create')"
      @enter="emit('enter', $event)"
      @members="emit('members', $event)"
      @leave="emit('leave', $event)"
      @settings="emit('settings', $event)"
      @delete="emit('delete', $event)"
    />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Link, Plus, Promotion } from '@element-plus/icons-vue'
import MyCourseGrid from '../course/MyCourseGrid.vue'

const props = defineProps({
  courses: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['create', 'invite', 'join', 'enter', 'members', 'leave', 'settings', 'delete'])

// 我创建的 = is_owner；我协作的 = 已通过邀请加入的协作教师（is_owner=false 且 my_role=teacher）。
// 兼容旧后端 / 旧缓存里缺字段的情况：只有明确 is_owner === false 才算协作。
const ownedCourses = computed(() => props.courses.filter((c) => c.is_owner !== false))
const collabCourses = computed(
  () => props.courses.filter((c) => c.is_owner === false && c.my_role === 'teacher'),
)

const filter = ref('all')
const filterOptions = computed(() => [
  { value: 'all', label: '全部', count: props.courses.length },
  { value: 'owned', label: '我创建的', count: ownedCourses.value.length },
  { value: 'collab', label: '我协作的', count: collabCourses.value.length },
])

const filteredCourses = computed(() => {
  if (filter.value === 'owned') return ownedCourses.value
  if (filter.value === 'collab') return collabCourses.value
  return props.courses
})

const emptyText = computed(() => {
  if (filter.value === 'owned') return '还没有自己创建的课程，点击「新建课程」开始'
  if (filter.value === 'collab') return '还没有协作课程，可通过「加入课程」或教师邀请加入'
  return '暂无课程，点击「新建课程」开始'
})
</script>

<style scoped>
.course-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  flex-wrap: wrap;
  margin-bottom: var(--space-3);
}
.course-toolbar .toolbar-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-left: auto;
}
.stats-text {
  color: var(--color-text-secondary);
  font-size: var(--font-size-label);
}

.course-filter {
  display: inline-flex;
  gap: 2px;
  padding: 3px;
  background: var(--bg-soft);
  border: 1px solid var(--border-light);
  border-radius: 10px;
}
.filter-btn {
  padding: 4px 11px;
  font-size: 12.5px;
  color: var(--text-secondary);
  background: transparent;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  transition: all .2s ease;
}
.filter-btn:hover { color: var(--brand-500); }
.filter-btn.active {
  background: #fff;
  color: var(--brand-600);
  box-shadow: var(--shadow-card);
  font-weight: 600;
}
</style>
