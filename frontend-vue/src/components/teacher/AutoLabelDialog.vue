<template>
  <!-- 标注过程会真实写库：误点遮罩/按 Esc 关闭会让教师在不知情下离开，运行中一律不允许关闭 -->
  <el-dialog
    :model-value="modelValue"
    title="批量知识点自动标注"
    width="880px"
    :close-on-click-modal="false"
    :close-on-press-escape="!loading"
    :before-close="beforeClose"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="question-toolbar">
      <el-select v-model="form.document_id" placeholder="全部学习资料" clearable style="width: 200px">
        <el-option v-for="d in documents" :key="d.doc_id" :label="d.file_name" :value="String(d.doc_id)" />
      </el-select>
      <span class="class-note">每题候选数</span>
      <el-input-number v-model="form.top_k" :min="1" :max="10" controls-position="right" style="width: 110px" />
      <span class="class-note">写入阈值</span>
      <el-input-number
        v-model="form.apply_threshold"
        :min="0" :max="1" :step="0.05" :precision="2"
        controls-position="right" style="width: 130px"
      />
      <el-checkbox v-model="form.only_missing">只处理未挂知识点的题</el-checkbox>
    </div>
    <div class="opt-row" style="margin: 8px 0 12px">
      <el-button :loading="loading" @click="run(false)">仅预览（不写库）</el-button>
      <el-button type="primary" :loading="loading" @click="run(true)">预览并写入达阈值项</el-button>
      <span class="class-note">写入只影响「建议分数 ≥ 阈值」的题，且默认跳过已有知识点的题</span>
    </div>

    <el-alert
      v-if="preview"
      class="coverage-alert"
      type="info"
      :closable="false"
      show-icon
      :title="`扫描 ${preview.scanned} 道 · 有建议 ${preview.with_candidates} 道 · 已写入 ${preview.applied} 道`"
      :description="metaText"
    />

    <el-table v-if="preview" :data="preview.items" max-height="380" size="small">
      <el-table-column label="题目" min-width="220" show-overflow-tooltip>
        <template #default="{ row }">
          <el-tag size="small" effect="plain" class="kp-tag">{{ questionTypeLabel(row.q_type) }}</el-tag>
          {{ row.stem }}
        </template>
      </el-table-column>
      <el-table-column label="当前知识点" width="130">
        <template #default="{ row }">{{ row.current_kp_name || '（未挂）' }}</template>
      </el-table-column>
      <el-table-column label="建议知识点" min-width="180">
        <template #default="{ row }">
          <span v-if="row.candidates.length">
            {{ row.candidates[0].name }}
            <el-tag
              size="small"
              :type="row.candidates[0].confidence === 'high' ? 'success' : (row.candidates[0].confidence === 'medium' ? 'warning' : 'info')"
            >{{ row.candidates[0].score }}</el-tag>
          </span>
          <span v-else class="cell-empty">无建议</span>
        </template>
      </el-table-column>
      <el-table-column label="写入" width="80">
        <template #default="{ row }">
          <el-tag v-if="row.applied" size="small" type="success">已写入</el-tag>
          <span v-else class="cell-empty">—</span>
        </template>
      </el-table-column>
    </el-table>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../../api'
import { questionTypeLabel } from '../../utils/question'
import { useLatestRequest } from '../../composables/useLatestRequest'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  courseId: { type: [String, Number], default: '' },
  documents: { type: Array, default: () => [] },
  defaultDocumentId: { type: [String, Number], default: '' },
})

const emit = defineEmits(['update:modelValue', 'applied'])

const form = reactive({
  document_id: '', only_missing: true, top_k: 3, apply_threshold: 0.6,
})
const loading = ref(false)
const preview = ref(null)
const request = useLatestRequest()

const metaText = computed(() => {
  const m = preview.value?.meta
  if (!m) return ''
  const parts = [
    `候选来源：${catalogSourceLabel(m.catalog_source)}（${m.catalog_size} 个知识点）`,
    m.graph_available ? '图谱可用' : '图谱不可用',
    m.embedding_configured ? '向量召回可用' : '未配置 EMBEDDING_API_KEY',
  ]
  if (m.reason) parts.push(m.reason)
  return parts.join(' · ')
})

function catalogSourceLabel(src) {
  return { graph: 'Neo4j 图谱', cache: '本地缓存', injected: '外部数据', none: '无' }[src] || src || '未知'
}

watch(() => props.modelValue, (visible) => {
  if (!visible) return
  preview.value = null
  form.document_id = props.defaultDocumentId ? String(props.defaultDocumentId) : ''
})

/** 标注请求进行中不允许关闭（请求不会因此取消，关闭只会让结果无人看见） */
function beforeClose(done) {
  if (loading.value) {
    ElMessage.warning('正在标注，请等待本次请求完成')
    return
  }
  done()
}

/** apply=false 仅预览；apply=true 写入「建议分 ≥ 阈值且非仅关系推断」的题 */
async function run(apply) {
  if (!props.courseId) {
    ElMessage.warning('请先选择课程')
    return
  }
  const token = request.begin()
  loading.value = true
  try {
    const data = await api.autoLabelQuestions({
      course_id: props.courseId,
      document_id: form.document_id || undefined,
      only_missing: form.only_missing,
      apply,
      top_k: form.top_k,
      apply_threshold: form.apply_threshold,
    })
    if (!request.isLatest(token)) return
    preview.value = data
    if (apply) {
      ElMessage.success(`已写入 ${data.applied} 道题（扫描 ${data.scanned} 道，有建议 ${data.with_candidates} 道）`)
      emit('applied')
    } else {
      ElMessage.success(`预览完成：扫描 ${data.scanned} 道，有建议 ${data.with_candidates} 道`)
    }
  } catch (e) {
    if (request.isLatest(token)) ElMessage.error(`自动标注失败：${e.message}`)
  } finally {
    if (request.isLatest(token)) loading.value = false
  }
}
</script>

<style scoped>
.question-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.class-note {
  font-size: 12px;
  color: #c0c4cc;
}
.opt-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.coverage-alert { margin-bottom: 12px; }
.kp-tag { margin-right: 4px; }
.cell-empty { color: #c0c4cc; }
</style>
