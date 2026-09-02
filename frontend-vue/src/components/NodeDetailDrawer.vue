<template>
  <el-drawer
    :model-value="modelValue"
    :title="editable ? '知识点编辑' : '知识点详情'"
    direction="rtl"
    size="420px"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <template v-if="node">
      <template v-if="editable">
        <el-form label-position="top">
          <el-form-item label="名称">
            <el-input v-model="form.name" />
          </el-form-item>
          <el-form-item label="类别">
            <el-select v-model="form.category" style="width: 100%">
              <el-option label="概念" value="概念" />
              <el-option label="定理" value="定理" />
              <el-option label="公式" value="公式" />
              <el-option label="方法" value="方法" />
            </el-select>
          </el-form-item>
          <el-form-item label="描述">
            <el-input v-model="form.description" type="textarea" :rows="4" />
          </el-form-item>
        </el-form>
        <div class="btn-row">
          <el-button type="primary" :loading="saving" @click="save">保存修改</el-button>
          <el-button type="danger" plain :loading="deleting" @click="remove">删除节点</el-button>
        </div>
      </template>

      <template v-else>
        <el-descriptions :column="1" border>
          <el-descriptions-item label="名称">{{ node.label }}</el-descriptions-item>
          <el-descriptions-item label="类别">
            <el-tag size="small">{{ nodeTypeLabel(node.type) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="描述">
            {{ node.description || '暂无描述' }}
          </el-descriptions-item>
          <el-descriptions-item label="是否人工添加">
            {{ node.properties?.is_manual ? '是' : '否' }}
          </el-descriptions-item>
          <el-descriptions-item v-if="node.properties?.confidence != null" label="置信度">
            {{ node.properties.confidence }}
          </el-descriptions-item>
        </el-descriptions>

        <template v-if="showMastery">
          <el-divider content-position="left">学习进度</el-divider>
          <el-button
            :type="mastered ? 'success' : 'primary'"
            :loading="masteryLoading"
            style="width: 100%"
            @click="$emit('toggle-mastery')"
          >
            {{ mastered ? '已掌握（点击取消标记）' : '标记为已掌握' }}
          </el-button>
        </template>

        <el-divider content-position="left">前置知识</el-divider>
        <el-button size="small" :loading="prereqLoading" @click="loadPrereqs">
          查询前置知识
        </el-button>
        <el-empty
          v-if="!prereqLoading && prereqLoaded && !prereqs.length"
          description="未查询到前置知识"
          :image-size="60"
        />
        <ul class="prereq-list">
          <li v-for="p in prereqs" :key="p.name">
            <el-tag size="small" type="info">{{ p.depth }} 级前置</el-tag>
            <span class="prereq-name">{{ p.name }}</span>
            <div class="prereq-desc">{{ p.description }}</div>
          </li>
        </ul>

        <template v-if="related.length">
          <el-divider content-position="left">相关知识</el-divider>
          <ul class="prereq-list">
            <li
              v-for="r in related"
              :key="r.id || r.label"
              class="related-item"
              @click="$emit('jump-to', r)"
            >
              <el-tag size="small" effect="plain" :type="categoryTagType(r.properties?.category)">
                {{ r.properties?.category || '知识点' }}
              </el-tag>
              <span class="prereq-name">{{ r.label }}</span>
              <el-icon class="related-jump"><Right /></el-icon>
            </li>
          </ul>
        </template>

        <template v-if="showExpand">
          <el-divider content-position="left">图谱展开</el-divider>
          <el-button
            size="small"
            style="width: 100%"
            :type="expanded ? 'warning' : 'primary'"
            plain
            @click="$emit('expand-toggle')"
          >
            {{ expanded ? '收起相关知识' : '展开相关知识' }}
          </el-button>
        </template>
      </template>
    </template>
  </el-drawer>
</template>

<script setup>
import { ref, watch, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Right } from '@element-plus/icons-vue'
import { api } from '../api'
import { nodeTypeLabel } from '../utils/graphStyle'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  node: { type: Object, default: null },
  editable: { type: Boolean, default: false },
  courseId: { type: String, default: '' },
  /** 学生端：是否显示"掌握"标记按钮 */
  showMastery: { type: Boolean, default: false },
  /** 当前节点是否已掌握 */
  mastered: { type: Boolean, default: false },
  /** 标记进行中（禁用按钮） */
  masteryLoading: { type: Boolean, default: false },
  /** P6：显示"展开/收起相关知识"按钮（学生端局部展开模式） */
  showExpand: { type: Boolean, default: false },
  /** P6：当前节点是否已展开 */
  expanded: { type: Boolean, default: false },
  /** P6：相关知识（客户端一阶邻居，点击可跳转聚焦） */
  related: { type: Array, default: () => [] },
})
const emit = defineEmits([
  'update:modelValue',
  'saved',
  'deleted',
  'toggle-mastery',
  'expand-toggle',
  'jump-to',
])

const form = ref({ name: '', category: '概念', description: '' })
const saving = ref(false)
const deleting = ref(false)
const prereqs = ref([])
const prereqLoading = ref(false)
const prereqLoaded = ref(false)

const originalName = computed(() => props.node?.label || '')

watch(
  () => props.node,
  (n) => {
    if (n) {
      form.value = {
        name: n.label || '',
        category: n.properties?.category || '概念',
        description: n.description || '',
      }
    }
    prereqs.value = []
    prereqLoaded.value = false
  }
)

function categoryTagType(category) {
  const map = { 概念: 'primary', 定理: 'danger', 公式: 'warning', 方法: 'success' }
  return map[category] || 'info'
}

async function save() {
  if (!form.value.name.trim()) {
    ElMessage.warning('名称不能为空')
    return
  }
  saving.value = true
  try {
    await api.updateNode(props.courseId, props.node.id, {
      name: form.value.name.trim(),
      category: form.value.category,
      description: form.value.description,
    })
    ElMessage.success('保存成功')
    emit('saved')
  } catch (e) {
    ElMessage.error(`保存失败：${e.message}`)
  } finally {
    saving.value = false
  }
}

async function remove() {
  try {
    await ElMessageBox.confirm(
      `确定删除知识点「${originalName.value}」及其全部关系吗？`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  deleting.value = true
  try {
    await api.deleteNode(props.courseId, props.node.id)
    ElMessage.success('已删除')
    emit('deleted')
  } catch (e) {
    ElMessage.error(`删除失败：${e.message}`)
  } finally {
    deleting.value = false
  }
}

async function loadPrereqs() {
  prereqLoading.value = true
  try {
    const res = await api.getPrerequisites(originalName.value, props.courseId)
    prereqs.value = res.prerequisites || []
  } catch (e) {
    ElMessage.warning(`查询失败：${e.message}`)
  } finally {
    prereqLoading.value = false
    prereqLoaded.value = true
  }
}
</script>

<style scoped>
.btn-row {
  display: flex;
  gap: 12px;
  margin-top: 16px;
}
.prereq-list {
  list-style: none;
  padding: 0;
  margin: 12px 0 0;
}
.prereq-list li {
  padding: 8px 0;
  border-bottom: 1px dashed #e4e7ed;
}
.prereq-name {
  font-weight: 600;
  margin-left: 8px;
}
.prereq-desc {
  color: #909399;
  font-size: 12px;
  margin-top: 4px;
}
.related-item {
  display: flex;
  align-items: center;
  cursor: pointer;
  transition: background 0.2s;
}
.related-item:hover {
  background: #f5f7fa;
}
.related-jump {
  margin-left: auto;
  color: #c0c4cc;
  font-size: 14px;
}
</style>
