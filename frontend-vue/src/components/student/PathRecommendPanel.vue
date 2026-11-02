<template>
  <el-card class="page-card path-panel">
    <template #header>
      <b><el-icon class="icon-gap" color="var(--color-primary)"><Aim /></el-icon>推荐下一步学习内容</b>
    </template>
    <p class="tip">输入你已掌握的知识点（每行一个），系统根据图谱前置关系推荐可学习的知识点</p>
    <el-input
      v-model="masteredText"
      type="textarea"
      :rows="5"
      placeholder="Python基础&#10;数据结构绪论&#10;…"
    />
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
      description="未找到可推荐的知识点（请确认已掌握知识点名称与图谱一致）"
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
})

const masteredText = ref('')
const recommendations = ref([])
const loading = ref(false)
const checked = ref(false)
const request = useLatestRequest()

async function doRecommend() {
  const mastered = masteredText.value
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)
  const key = props.ctxKey
  const { token, signal } = request.beginRead()
  loading.value = true
  try {
    const res = await api.recommendNext(
      mastered, props.courseId, props.documentId, { signal },
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

// 切换学习资料：取消在途请求并清空旧文档的推荐结果
watch(() => props.ctxKey, () => {
  request.invalidate()
  loading.value = false
  recommendations.value = []
  checked.value = false
})
</script>
