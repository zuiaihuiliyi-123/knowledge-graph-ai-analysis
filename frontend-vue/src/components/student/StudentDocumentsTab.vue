<template>
  <div>
    <el-card class="page-card">
      <div class="sd-head">
        <div class="sd-head-left">
          <span class="sd-title">课程文档</span>
          <span v-if="courseName" class="sd-course">{{ courseName }}</span>
        </div>
        <div class="sd-head-right">
          <el-input
            v-model="docFilter"
            placeholder="搜索文档名"
            clearable
            size="small"
            style="width: 180px"
          />
          <el-button :icon="Refresh" circle size="small" title="刷新" @click="loadCourseDocuments" />
        </div>
      </div>
    </el-card>

    <el-empty
      v-if="!courseId"
      description="请先选择课程，查看该课程的文档"
      :image-size="90"
    >
      <el-button type="primary" @click="emit('select-context')">选择课程</el-button>
    </el-empty>

    <el-card v-else v-loading="docLoading" class="page-card">
      <el-empty
        v-if="!docLoading && !filteredDocuments.length"
        :description="docFilter ? '没有匹配的文档' : '该课程暂无文档'"
        :image-size="80"
      />
      <ul v-else class="sd-list">
        <li v-for="d in filteredDocuments" :key="d.doc_id" class="sd-item">
          <div class="sd-item-main">
            <div class="sd-item-top">
              <span class="sd-item-name" :title="d.file_name">{{ d.file_name }}</span>
              <span class="sd-badge">{{ d.file_type }}</span>
              <span v-if="docProgress[d.doc_id]?.percent > 1" class="sd-badge is-progress">
                已读 {{ docProgress[d.doc_id].percent }}%
              </span>
              <span v-if="docProgress[d.doc_id]?.percent >= 99" class="sd-badge is-done">已读完</span>
            </div>
            <div class="sd-item-meta">
              {{ fmtDocSize(d.file_size) }} · {{ d.entity_count || 0 }} 个知识点 ·
              {{ d.relation_count || 0 }} 条关系
              <template v-if="docProgress[d.doc_id]?.page > 1">
                · 上次读到第 {{ docProgress[d.doc_id].page }} 页
              </template>
            </div>
            <div v-if="docProgress[d.doc_id]?.percent > 1" class="sd-item-bar">
              <div class="sd-item-bar-fill" :style="{ width: docProgress[d.doc_id].percent + '%' }" />
            </div>
          </div>
          <div class="sd-item-actions">
            <el-button type="primary" :icon="Reading" @click="readDocument(d)">
              {{ docProgress[d.doc_id]?.percent > 1 ? '继续阅读' : '在线阅读' }}
            </el-button>
            <el-button :icon="Download" plain @click="downloadDocument(d)">下载</el-button>
          </div>
        </li>
      </ul>
    </el-card>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Download, Reading, Refresh } from '@element-plus/icons-vue'
import { useAppStore } from '../../stores/app'
import { useLatestRequest, isAbortError } from '../../composables/useLatestRequest'
import { formatBytes } from '../../utils/format'
import { downloadDocumentFile } from '../../utils/download'
import { getReadingProgress } from '../../utils/readingProgress'

const props = defineProps({
  active: { type: Boolean, default: false },
  courseId: { type: [String, Number], default: '' },
  courseName: { type: String, default: '' },
})

const emit = defineEmits(['select-context'])

const store = useAppStore()
const router = useRouter()
const docFilter = ref('')
const docLoading = ref(false)
// 阅读进度存在浏览器本地，仅用于列表上的续读提示，不参与任何业务判断
const docProgress = reactive({})
const request = useLatestRequest()

const filteredDocuments = computed(() => {
  const list = store.learningContext.documentList || []
  const q = docFilter.value.trim().toLowerCase()
  return q ? list.filter((d) => String(d.file_name).toLowerCase().includes(q)) : list
})

function fmtDocSize(bytes) {
  return formatBytes(bytes, { kbDigits: 0, zeroAsDash: true })
}

function syncDocProgress(list) {
  Object.keys(docProgress).forEach((k) => delete docProgress[k])
  ;(list || []).forEach((d) => {
    const saved = getReadingProgress(d.doc_id)
    if (saved) docProgress[d.doc_id] = saved
  })
}

watch(() => store.learningContext.documentList, syncDocProgress, { immediate: true })

/**
 * 文档列表统一走 store.fetchDocuments：action 内部按请求序号 + 当前课程校验写入，
 * 不会与页面级/选择器触发的请求互相覆盖。这里只额外维护本 Tab 的 loading 与提示。
 */
async function loadCourseDocuments() {
  const cid = String(props.courseId || '')
  if (!cid) return
  const { token, signal } = request.beginRead()
  docLoading.value = true
  try {
    await store.fetchDocuments(cid, { signal })
  } catch (e) {
    // 取消（切换课程/离开 Tab）不算失败
    if (isAbortError(e)) return
    if (request.isLatest(token) && String(props.courseId) === cid) {
      ElMessage.warning(`文档列表加载失败：${e.message}`)
    }
  } finally {
    if (request.isLatest(token)) docLoading.value = false
  }
}

watch([() => props.active, () => props.courseId], ([isActive, cid]) => {
  if (isActive && cid) loadCourseDocuments()
}, { immediate: true })

function readDocument(doc) {
  router.push({
    name: 'reader',
    params: { docId: String(doc.doc_id) },
    query: { course_id: String(doc.course_id ?? props.courseId), from: 'student' },
  })
}

async function downloadDocument(doc) {
  try {
    await downloadDocumentFile(doc)
  } catch (e) {
    ElMessage.error(`下载失败：${e.message}`)
  }
}
</script>

<style scoped>
/* ===== 课程文档（在线阅读入口） ===== */
.sd-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
}
.sd-head-left {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  min-width: 0;
}
.sd-title {
  font-size: var(--font-size-section);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
}
.sd-course {
  font-size: var(--font-size-label);
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sd-head-right {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.sd-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.sd-item {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: 14px 4px;
  border-bottom: 1px solid var(--color-border-light);
}
.sd-item:last-child { border-bottom: none; }
.sd-item-main {
  flex: 1;
  min-width: 0;
}
.sd-item-top {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}
.sd-item-name {
  font-size: var(--font-size-body);
  font-weight: var(--font-weight-medium);
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sd-badge {
  flex-shrink: 0;
  font-size: 11px;
  line-height: 16px;
  padding: 0 5px;
  border-radius: 3px;
  background: var(--color-primary-light);
  color: var(--color-primary);
}
.sd-badge.is-progress {
  background: #fdf6ec;
  color: var(--color-warning);
}
.sd-badge.is-done {
  background: #f0f9eb;
  color: var(--color-success);
}
.sd-item-meta {
  margin-top: 3px;
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}
.sd-item-bar {
  margin-top: 7px;
  height: 4px;
  max-width: 320px;
  border-radius: 2px;
  background: var(--color-border-light);
  overflow: hidden;
}
.sd-item-bar-fill {
  height: 100%;
  background: var(--color-primary);
  border-radius: 2px;
}
.sd-item-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}
@media (max-width: 768px) {
  .sd-item {
    flex-direction: column;
    align-items: stretch;
    gap: var(--space-3);
  }
  .sd-item-actions { justify-content: flex-end; }
}

/* v2 视觉增强 */
.sd-item {
  border-radius: var(--radius-md);
  padding: 14px 12px;
  border-bottom: 1px solid transparent;
  transition: background .18s;
}
.sd-item:hover { background: var(--brand-50); }
.sd-badge { border-radius: 999px; padding: 1px 9px; font-weight: 500; }
.sd-item-bar-fill { background: var(--gradient-brand); }
</style>
