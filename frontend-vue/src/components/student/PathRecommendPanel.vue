<template>
  <el-card class="page-card path-panel">
    <template #header>
      <b><el-icon class="icon-gap" color="var(--color-primary)"><Aim /></el-icon>推荐下一步学习内容</b>
    </template>
    <p class="tip">选择你已掌握的知识点（默认带入你在图谱中标记为已掌握的项），系统根据图谱前置关系推荐可学习的知识点</p>
    <el-select
      v-model="mastered"
      multiple
      filterable
      allow-create
      default-first-option
      clearable
      collapse-tags
      collapse-tags-tooltip
      :max-collapse-tags="4"
      placeholder="搜索或选择已掌握的知识点"
      style="width: 100%"
      @change="onMasteredChange"
      :no-data-text="knowledgeOptions.length ? '无匹配知识点' : '当前文档暂无知识点，可直接输入名称后回车'"
    >
      <el-option
        v-for="o in knowledgeOptions"
        :key="o.id ?? o.label"
        :label="o.label"
        :value="o.label"
      />
    </el-select>
    <div class="btn-row">
      <el-button type="primary" :loading="loading" @click="doRecommend">
        推荐下一步
      </el-button>
    </div>

    <template v-if="recommendations.length">
      <el-divider content-position="left">推荐结果（{{ recommendations.length }}）</el-divider>
      <div v-for="r in recommendations" :key="r.name" class="rec-item">
        <div class="rec-name">
          <el-tag size="small" :type="categoryTagType(r.category)">{{ r.category }}</el-tag>
          <b>{{ r.name }}</b>
        </div>
        <div class="rec-desc">{{ r.description }}</div>
        <div class="rec-reason"><el-icon class="icon-gap" color="var(--color-warning)"><Opportunity /></el-icon>{{ r.reason }}</div>
      </div>
    </template>
    <el-empty
      v-else-if="checked && !loading"
      description="未找到可推荐的知识点，可在「图谱浏览」中标记更多已掌握知识点，或换一个已掌握项再试"
      :image-size="60"
    />
  </el-card>
</template>

<script setup>
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Aim, Opportunity } from '@element-plus/icons-vue'
import { api } from '../../api'
import { categoryTagType } from '../../utils/graphStyle'
import { useLatestRequest, isAbortError } from '../../composables/useLatestRequest'
import '../../styles/path-panels.css'

const props = defineProps({
  ctxKey: { type: String, default: '' },
  courseId: { type: [String, Number], default: '' },
  documentId: { type: [String, Number], default: '' },
  /** 当前文档图谱节点（{ id, label }），用于候选选择，避免用户手输名称匹配不上 */
  knowledgeOptions: { type: Array, default: () => [] },
  /** 已在图谱中标记为已掌握的知识点名称，作为默认选中项 */
  masteredNames: { type: Array, default: () => [] },
})

const mastered = ref([])
const recommendations = ref([])
const loading = ref(false)
const checked = ref(false)
const request = useLatestRequest()
// 用户手动改过选择后，不再被父级刷新的 masteredNames 覆盖
let touched = false

function syncMastered() {
  mastered.value = [...props.masteredNames]
}

async function doRecommend() {
  const names = mastered.value.map((s) => String(s).trim()).filter(Boolean)
  const key = props.ctxKey
  const { token, signal } = request.beginRead()
  loading.value = true
  try {
    const res = await api.recommendNext(
      names, props.courseId, props.documentId, { signal },
    )
    if (!request.isLatest(token) || props.ctxKey !== key) return
    recommendations.value = res.recommendations || []
    checked.value = true
  } catch (e) {
    if (isAbortError(e)) return
    if (!request.isLatest(token) || props.ctxKey !== key) return
    ElMessage.error(`推荐失败：${e.message}`)
  } finally {
    if (request.isLatest(token)) loading.value = false
  }
}

// 切换学习资料：取消在途请求、清空旧文档的推荐结果，并重新带入已掌握项
watch(() => props.ctxKey, () => {
  request.invalidate()
  loading.value = false
  recommendations.value = []
  checked.value = false
  touched = false
  syncMastered()
}, { immediate: true })

// 图谱里的已掌握标记变化（如刚在抽屉里标记）时同步默认选中
watch(() => props.masteredNames, () => {
  if (!touched) syncMastered()
}, { deep: true })

function onMasteredChange() {
  touched = true
}
</script>
