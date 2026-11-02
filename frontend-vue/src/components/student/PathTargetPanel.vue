<template>
  <el-card class="page-card path-panel">
    <template #header>
      <b><el-icon class="icon-gap" color="var(--color-primary)"><Guide /></el-icon>到达目标知识点的学习路径</b>
    </template>
    <p class="tip">输入目标知识点，系统自动生成从入门到该知识点的最短学习路径</p>
    <div class="btn-row">
      <el-input v-model="targetKnowledge" placeholder="目标知识点，如：二叉树的遍历" style="max-width: 320px" />
      <el-button type="primary" :loading="loading" @click="doPathToTarget">
        生成路径
      </el-button>
    </div>

    <template v-if="paths.length">
      <el-divider content-position="left">共 {{ paths.length }} 条路径</el-divider>
      <div v-for="(path, pi) in paths" :key="pi" class="path-plans">
        <div class="path-index">路径 {{ pi + 1 }}</div>
        <div class="path-steps">
          <template v-for="(step, si) in path" :key="si">
            <span class="path-step">
              <el-tag size="small">{{ step.category || '知识点' }}</el-tag>
              <span class="step-name">{{ step.name }}</span>
            </span>
            <span v-if="si < path.length - 1" class="path-arrow">→</span>
          </template>
        </div>
      </div>
    </template>
    <template v-else-if="fallback">
      <div class="rec-item">
        <div class="rec-name">
          <el-tag size="small" :type="categoryTagType(targetNode?.category)">
            {{ targetNode?.category || '知识点' }}
          </el-tag>
          <b>{{ targetNode?.name || targetKnowledge }}</b>
        </div>
        <div class="rec-desc">{{ targetNode?.description }}</div>
        <div class="rec-reason"><el-icon class="icon-gap" color="var(--color-warning)"><Opportunity /></el-icon>{{ reason }}</div>
      </div>
      <el-divider content-position="left">相关概念（{{ related.length }}）</el-divider>
      <div v-for="r in related" :key="r.name" class="rec-item">
        <div class="rec-name">
          <el-tag size="small" :type="categoryTagType(r.category)">{{ r.category }}</el-tag>
          <b>{{ r.name }}</b>
        </div>
        <div class="rec-desc">{{ r.description }}</div>
      </div>
    </template>
    <el-empty
      v-else-if="checked && !loading"
      description="未找到该知识点（请确认名称与图谱一致）"
      :image-size="60"
    />
  </el-card>
</template>

<script setup>
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Guide, Opportunity } from '@element-plus/icons-vue'
import { api } from '../../api'
import { categoryTagType } from '../../utils/graphStyle'
import { useLatestRequest, isAbortError } from '../../composables/useLatestRequest'
import '../../styles/path-panels.css'

const props = defineProps({
  ctxKey: { type: String, default: '' },
  courseId: { type: [String, Number], default: '' },
  documentId: { type: [String, Number], default: '' },
})

// 有真实路径时请求父级把路径高亮到图谱并切到「图谱浏览」
const emit = defineEmits(['highlight-path', 'go-browse'])

const targetKnowledge = ref('')
const paths = ref([])
const loading = ref(false)
const checked = ref(false)
const fallback = ref(false)
const targetNode = ref(null)
const related = ref([])
const reason = ref('')
const request = useLatestRequest()

async function doPathToTarget() {
  if (!targetKnowledge.value.trim()) {
    ElMessage.warning('请输入目标知识点')
    return
  }
  const key = props.ctxKey
  const { token, signal } = request.beginRead()
  loading.value = true
  try {
    const res = await api.pathToTarget(
      targetKnowledge.value.trim(), props.courseId, props.documentId, { signal },
    )
    if (!request.isLatest(token) || props.ctxKey !== key) return
    paths.value = res.paths || []
    fallback.value = !!res.fallback
    targetNode.value = res.target_node || null
    related.value = res.related || []
    reason.value = res.reason || ''
    checked.value = true
    // 有真实路径时，高亮到图谱（对齐课程 + 切到浏览 Tab）
    if (res.paths && res.paths.length && props.ctxKey) {
      emit('highlight-path', res.paths[0].map((s) => s.name))
      emit('go-browse')
      ElMessage.success('已生成路径，正在图谱中高亮')
    } else {
      emit('highlight-path', [])
    }
  } catch (e) {
    if (isAbortError(e)) return
    if (request.isLatest(token) && props.ctxKey === key) {
      ElMessage.error(`生成失败：${e.message}`)
    }
  } finally {
    if (request.isLatest(token)) loading.value = false
  }
}

// 切换学习资料：取消在途请求并清空旧文档的路径结果
watch(() => props.ctxKey, () => {
  request.invalidate()
  loading.value = false
  paths.value = []
  checked.value = false
  fallback.value = false
  targetNode.value = null
  related.value = []
  reason.value = ''
})
</script>
