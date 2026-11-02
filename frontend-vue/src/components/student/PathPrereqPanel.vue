<template>
  <el-card class="page-card path-panel">
    <template #header>
      <b><el-icon class="icon-gap" color="var(--color-primary)"><Search /></el-icon>前置知识查询</b>
    </template>
    <div class="btn-row">
      <el-input v-model="prereqName" placeholder="知识点名称" style="max-width: 320px" />
      <el-button :loading="loading" @click="doQueryPrereqs">查询</el-button>
    </div>
    <template v-if="result && result.count">
      <el-divider content-position="left">共 {{ result.count }} 个前置知识</el-divider>
      <div v-for="p in result.prerequisites" :key="p.name" class="rec-item">
        <div class="rec-name">
          <el-tag size="small" type="info">{{ p.reason || `${p.depth} 级前置` }}</el-tag>
          <b>{{ p.name }}</b>
        </div>
        <div class="rec-desc">{{ p.description }}</div>
      </div>
    </template>
    <el-empty
      v-else-if="result && !loading"
      description="未查询到前置知识（请确认知识点名称准确）"
      :image-size="60"
    />
  </el-card>
</template>

<script setup>
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { api } from '../../api'
import { useLatestRequest, isAbortError } from '../../composables/useLatestRequest'
import '../../styles/path-panels.css'

const props = defineProps({
  ctxKey: { type: String, default: '' },
  courseId: { type: [String, Number], default: '' },
  documentId: { type: [String, Number], default: '' },
})

const prereqName = ref('')
const result = ref(null)
const loading = ref(false)
const request = useLatestRequest()

async function doQueryPrereqs() {
  if (!prereqName.value.trim()) {
    ElMessage.warning('请输入知识点名称')
    return
  }
  const key = props.ctxKey
  const { token, signal } = request.beginRead()
  loading.value = true
  result.value = null
  try {
    const data = await api.getPrerequisites(
      prereqName.value.trim(), props.courseId, props.documentId, { signal },
    )
    if (!request.isLatest(token) || props.ctxKey !== key) return
    result.value = data
  } catch (e) {
    if (isAbortError(e)) return
    if (request.isLatest(token) && props.ctxKey === key) {
      ElMessage.error(`查询失败：${e.message}`)
    }
  } finally {
    if (request.isLatest(token)) loading.value = false
  }
}

// 切换学习资料：取消在途请求并清空旧文档的查询结果
watch(() => props.ctxKey, () => {
  request.invalidate()
  loading.value = false
  result.value = null
})
</script>
