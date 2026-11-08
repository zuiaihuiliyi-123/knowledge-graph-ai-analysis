<template>
  <el-dialog
    :model-value="modelValue"
    title="从文档导入试题"
    width="1000px"
    :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="question-toolbar">
      <el-select v-model="importDocId" placeholder="选择要解析的课程文档" clearable filterable style="width: 300px">
        <el-option v-for="d in documents" :key="d.doc_id" :label="d.file_name" :value="String(d.doc_id)" />
      </el-select>
      <span class="class-note">最多解析</span>
      <el-input-number v-model="importMax" :min="1" :max="500" controls-position="right" style="width: 120px" />
      <el-button type="primary" :loading="importLoading" :disabled="!importDocId" @click="runPreview">解析预览</el-button>
      <span class="class-note">仅解析不写库；适用「题目区 + 答案区」版式的试题文档（PDF/TXT/DOCX/MD）</span>
    </div>

    <el-alert
      v-if="importStats"
      class="coverage-alert"
      type="info"
      :closable="false"
      show-icon
      :title="`识别 ${importStats.total} 题 · 有答案 ${importStats.with_answer} · 待复核 ${importStats.needs_review} · 缺答案 ${importStats.answer_missing} · 重复 ${importStats.duplicates}`"
      :description="importStatsText"
    />

    <el-table
      v-if="importItems.length"
      :data="importItems"
      max-height="420"
      size="small"
      row-key="number"
      @selection-change="onSelection"
    >
      <el-table-column type="selection" width="44" :selectable="importSelectable" />
      <el-table-column label="题号" width="60"><template #default="{ row }">{{ row.number }}</template></el-table-column>
      <el-table-column label="题型" width="86">
        <template #default="{ row }"><el-tag size="small" effect="plain">{{ questionTypeLabel(row.q_type) }}</el-tag></template>
      </el-table-column>
      <el-table-column label="题干" min-width="240" show-overflow-tooltip>
        <template #default="{ row }">{{ row.stem }}</template>
      </el-table-column>
      <el-table-column label="选项" width="66">
        <template #default="{ row }">{{ row.options.length || '—' }}</template>
      </el-table-column>
      <el-table-column label="答案（可直接填写）" min-width="240">
        <template #default="{ row }">
          <!-- 单选 -->
          <el-select
            v-if="row.q_type === 'SINGLE'"
            v-model="row.answer" size="small" placeholder="选择答案" style="width: 120px"
            @change="recomputeImportState(row)"
          >
            <el-option v-for="o in row.options" :key="o.key" :label="o.key" :value="o.key" />
          </el-select>
          <!-- 多选 -->
          <el-select
            v-else-if="row.q_type === 'MULTI'"
            v-model="row.answer" multiple collapse-tags size="small"
            placeholder="选择答案（可多选）" style="width: 170px"
            @change="recomputeImportState(row)"
          >
            <el-option v-for="o in row.options" :key="o.key" :label="o.key" :value="o.key" />
          </el-select>
          <!-- 判断 -->
          <el-select
            v-else-if="row.q_type === 'JUDGE'"
            v-model="row.answer" size="small" placeholder="对 / 错" style="width: 100px"
            @change="recomputeImportState(row)"
          >
            <el-option label="正确" value="true" />
            <el-option label="错误" value="false" />
          </el-select>
          <!-- 填空：按空位逐个填写参考答案 -->
          <div v-else-if="row.q_type === 'FILL'">
            <div v-for="b in row.options" :key="b.key" class="opt-row">
              <span class="class-note" style="min-width: 52px">{{ b.label || ('第' + b.key + '空') }}</span>
              <el-input
                v-model="b.answer" size="small" placeholder="参考答案" style="width: 150px"
                @change="recomputeImportState(row)"
              />
            </div>
          </div>
          <!-- 解答 -->
          <el-input
            v-else
            v-model="row.answer" type="textarea" :rows="2" size="small"
            placeholder="参考答案（教师批改依据）"
            @change="recomputeImportState(row)"
          />
          <div v-if="!hasImportAnswer(row)" class="class-note">填写答案后即可勾选导入</div>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag
            size="small"
            :type="row.import_status === 'READY' ? 'success' : (row.import_status === 'NEEDS_REVIEW' ? 'warning' : 'danger')"
          >{{ importStatusLabel(row.import_status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="警告" min-width="130" show-overflow-tooltip>
        <template #default="{ row }"><span class="cell-empty">{{ warnText(row) || '—' }}</span></template>
      </el-table-column>
    </el-table>

    <el-alert
      v-if="importReport"
      class="coverage-alert"
      type="success"
      :closable="false"
      show-icon
      :title="`已导入 ${importReport.imported} / ${importReport.total} 题（跳过 ${importReport.skipped.length}，待复核 ${importReport.needs_review}）`"
      :description="`批次 ${importReport.batch_id} · ${importReport.hint}`"
    />

    <el-collapse v-if="importReport && importReport.skipped && importReport.skipped.length" style="margin-top: 8px">
      <el-collapse-item :title="`未导入 ${importReport.skipped.length} 题的拒绝原因`">
        <div v-for="(s, i) in importReport.skipped" :key="i" class="class-note">
          第 {{ s.number || (s.index + 1) }} 题：{{ s.reason }}
        </div>
      </el-collapse-item>
    </el-collapse>

    <template #footer>
      <el-button :type="importReport ? 'primary' : ''" @click="emit('update:modelValue', false)">
        {{ importReport ? '完成' : '关闭' }}
      </el-button>
      <el-button :disabled="!selection.length" :loading="importLoading" @click="submit(false)">
        导入为暂存题（已选 {{ selection.length }}）
      </el-button>
      <el-button type="primary" :disabled="!selection.length" :loading="importLoading" @click="submit(true)">
        导入并直接启用
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
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

const emit = defineEmits(['update:modelValue', 'imported'])

const importLoading = ref(false)
const importDocId = ref('')
const importMax = ref(200)
const importItems = ref([])
const selection = ref([])
const importStats = ref(null)
const importReport = ref(null)
const previewRequest = useLatestRequest()

const importStatsText = computed(() => {
  const s = importStats.value
  if (!s) return ''
  const dist = Object.entries(s.by_type || {})
    .map(([k, v]) => `${questionTypeLabel(k)} ${v}`).join('、') || '—'
  const parts = [`题型分布：${dist}`]
  if (!s.answer_section_found) parts.push('未识别到答案区（答案需人工补齐）')
  if (s.sections && s.sections.length) {
    parts.push(`章节：${s.sections.slice(0, 3).join(' / ')}${s.sections.length > 3 ? ' …' : ''}`)
  }
  parts.push('导入的题目默认「停用」，复核后再启用')
  return parts.join(' · ')
})

watch(() => props.modelValue, (visible) => {
  if (!visible) return
  importItems.value = []
  selection.value = []
  importStats.value = null
  importReport.value = null
  importDocId.value = props.defaultDocumentId ? String(props.defaultDocumentId) : ''
})

/** 缺答案的题不允许勾选导入（主观题没有参考答案就无法批改） */
function importSelectable(row) {
  return !!row.q_type && !!row.stem && row.import_status !== 'ANSWER_MISSING'
}

/** 该行是否已有可用答案（填空题要求每个空都填） */
function hasImportAnswer(row) {
  if (!row) return false
  if (row.q_type === 'FILL') {
    return (row.options || []).length > 0 && row.options.every((b) => String(b.answer || '').trim())
  }
  if (row.q_type === 'MULTI') {
    return Array.isArray(row.answer) && row.answer.length > 0
  }
  return !!(row.answer && String(row.answer).trim())
}

/** 行内补答案后重算状态：缺答案 → ANSWER_MISSING；补完 → READY / NEEDS_REVIEW */
function recomputeImportState(row) {
  const others = (row.warnings || []).filter((w) => w !== 'answer_missing')
  const ok = hasImportAnswer(row)
  row.warnings = ok ? others : [...others, 'answer_missing']
  row.import_status = !ok ? 'ANSWER_MISSING' : (others.length ? 'NEEDS_REVIEW' : 'READY')
}

function importStatusLabel(status) {
  return { READY: '可导入', NEEDS_REVIEW: '待复核', ANSWER_MISSING: '缺答案' }[status] || status
}

function warnText(row) {
  const labels = {
    answer_missing: '缺答案', multi_type_needs_review: '多选需确认',
    judge_type_guessed: '判断题待确认', duplicate_stem: '题干重复',
    no_options_no_answer: '无选项无答案',
  }
  return (row.warnings || []).map((w) => labels[w] || w).join('、')
}

function onSelection(rows) {
  selection.value = rows || []
}

/** 解析预览：只读，不写库 */
async function runPreview() {
  if (!props.courseId || !importDocId.value) return
  const token = previewRequest.begin()
  importLoading.value = true
  importReport.value = null
  try {
    const data = await api.previewImportQuestions({
      course_id: props.courseId,
      document_id: importDocId.value,
      max_questions: importMax.value,
    })
    if (!previewRequest.isLatest(token)) return
    importItems.value = (data.items || []).map((it) => {
      if (it.q_type === 'MULTI' && !Array.isArray(it.answer)) it.answer = []
      if (it.q_type === 'FILL' && Array.isArray(it.options)) {
        it.options.forEach((b) => { if (b.answer === undefined) b.answer = '' })
      }
      return it
    })
    importStats.value = data.stats || null
    selection.value = []
    const importable = importItems.value.filter(importSelectable).length
    ElMessage.success(`解析完成：识别 ${data.stats.total} 题，可导入 ${importable} 题（缺答案的题可直接在表格里填写）`)
  } catch (e) {
    if (!previewRequest.isLatest(token)) return
    importItems.value = []
    importStats.value = null
    ElMessage.error(`解析失败：${e.message}`)
  } finally {
    if (previewRequest.isLatest(token)) importLoading.value = false
  }
}

/** 提交导入：默认入库为停用的暂存题；activate=true 则直接启用 */
async function submit(activate) {
  if (!selection.value.length) return
  importLoading.value = true
  try {
    const data = await api.commitImportQuestions({
      course_id: props.courseId,
      document_id: importDocId.value,
      items: selection.value,
      activate,
    })
    importReport.value = data
    // 导入成功后清空预览与勾选：否则同一批题还能再点一次提交，等于重复导入一遍
    importItems.value = []
    selection.value = []
    importStats.value = null
    ElMessage.success(activate
      ? `已导入并启用 ${data.imported} 题，可关闭本窗口`
      : `已导入 ${data.imported} 题为暂存题（默认停用），可关闭本窗口`)
    emit('imported')
  } catch (e) {
    ElMessage.error(`导入失败：${e.message}`)
  } finally {
    importLoading.value = false
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
.cell-empty { color: #c0c4cc; }
</style>
