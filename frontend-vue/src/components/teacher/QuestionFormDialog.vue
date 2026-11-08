<template>
  <el-dialog
    :model-value="modelValue"
    :title="question ? '编辑题目' : '新增题目'"
    width="720px"
    :close-on-click-modal="false"
    :before-close="handleBeforeClose"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <el-form label-width="96px">
      <el-form-item label="题型">
        <el-radio-group v-model="form.q_type" :disabled="!!question">
          <el-radio v-for="t in QUESTION_TYPES" :key="t.value" :value="t.value">{{ t.label }}</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="题干">
        <el-input v-model="form.stem" type="textarea" :rows="3" maxlength="1000" show-word-limit placeholder="请输入题干" />
      </el-form-item>

      <template v-if="form.q_type === 'SINGLE' || form.q_type === 'MULTI'">
        <el-form-item v-for="(opt, idx) in form.options" :key="opt.key" :label="`选项 ${opt.key}`">
          <div class="opt-row">
            <el-input v-model="opt.text" placeholder="选项内容" />
            <el-button :icon="Delete" text type="danger" :disabled="form.options.length <= 2" @click="removeOption(idx)" />
          </div>
        </el-form-item>
        <el-form-item label=" ">
          <el-button :icon="Plus" size="small" :disabled="form.options.length >= 8" @click="addOption">添加选项</el-button>
        </el-form-item>
        <el-form-item label="正确答案">
          <el-radio-group v-if="form.q_type === 'SINGLE'" v-model="form.singleAnswer">
            <el-radio v-for="o in form.options" :key="o.key" :value="o.key">{{ o.key }}</el-radio>
          </el-radio-group>
          <el-checkbox-group v-else v-model="form.multiAnswer">
            <el-checkbox v-for="o in form.options" :key="o.key" :value="o.key">{{ o.key }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
      </template>

      <!-- 填空题：空位编辑器（每空必须有参考答案；学生提交后进入「批改」队列） -->
      <template v-else-if="form.q_type === 'FILL'">
        <el-alert
          class="coverage-alert"
          type="info"
          :closable="false"
          show-icon
          title="填空题不自动判分"
          description="每空必须填写参考答案。学生提交后进入「批改」队列，由你逐题给分（可给部分分 0~100）。"
        />
        <el-form-item v-for="(b, idx) in form.blanks" :key="b.key" :label="b.label">
          <div class="opt-row">
            <el-input v-model="b.answer" placeholder="参考答案（必填）" />
            <el-input v-model="b.hint" placeholder="空位提示（可选，如：单位 kg）" />
            <el-input-number v-model="b.score" :min="0" :max="100" :step="5" controls-position="right" style="width: 120px" />
            <el-button :icon="Delete" text type="danger" :disabled="form.blanks.length <= 1" @click="removeBlank(idx)" />
          </div>
        </el-form-item>
        <el-form-item label=" ">
          <el-button :icon="Plus" size="small" :disabled="form.blanks.length >= 20" @click="addBlank">添加空位</el-button>
          <span class="class-note">（分值仅用于教师批改时参考）</span>
        </el-form-item>
      </template>

      <!-- 解答题：参考答案（必填；批改前不会下发给学生） -->
      <el-form-item v-else-if="form.q_type === 'ESSAY'" label="参考答案">
        <el-input
          v-model="form.essayAnswer"
          type="textarea"
          :rows="4"
          maxlength="4000"
          show-word-limit
          placeholder="必填：参考答案/解答要点。学生提交后进入「批改」队列，批改前不会下发给学生。"
        />
      </el-form-item>

      <el-form-item v-else label="正确答案">
        <el-radio-group v-model="form.judgeAnswer">
          <el-radio value="true">正确</el-radio>
          <el-radio value="false">错误</el-radio>
        </el-radio-group>
      </el-form-item>

      <el-form-item label="解析">
        <el-input v-model="form.analysis" type="textarea" :rows="2" maxlength="1000" placeholder="可选：答案解析（提交后展示给学生）" />
      </el-form-item>
      <el-form-item label="难度">
        <el-rate v-model="form.difficulty" :max="5" />
      </el-form-item>
      <el-form-item label="所属文档">
        <el-select v-model="form.document_id" placeholder="课程通用题（任意文档练习均可见）" clearable style="width: 100%">
          <el-option v-for="d in documents" :key="d.doc_id" :label="d.file_name" :value="String(d.doc_id)" />
        </el-select>
      </el-form-item>
      <el-form-item label="关联知识点">
        <div class="opt-row">
          <el-select v-model="form.kp_id" placeholder="可选：关联图谱知识点（用于推荐练题）" clearable filterable style="flex: 1">
            <el-option v-for="n in kpOptions" :key="n.id" :label="n.label" :value="n.id" />
          </el-select>
          <el-button
            :loading="kpCandLoading"
            :disabled="!question"
            @click="loadKpCandidates"
          >自动标注</el-button>
        </div>
        <div v-if="!question" class="class-note">（新增题目请先保存，再使用「自动标注」）</div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="handleCancel">取消</el-button>
      <el-button type="primary" :loading="saving" @click="submit">保存</el-button>
    </template>
  </el-dialog>

  <!-- 单题知识点候选（三层证据 + 融合打分） -->
  <el-dialog v-model="kpCandVisible" title="知识点自动标注建议" width="640px">
    <el-alert
      v-if="kpCandMeta"
      class="coverage-alert"
      type="info"
      :closable="false"
      show-icon
      :title="`候选来源：${catalogSourceLabel(kpCandMeta.catalog_source)}（知识点 ${kpCandMeta.catalog_size} 个）`"
      :description="kpCandDescription"
    />
    <el-table :data="kpCandList" v-loading="kpCandLoading" max-height="360">
      <el-table-column label="知识点" min-width="170">
        <template #default="{ row }">
          <span>{{ row.name }}</span>
          <el-tag v-if="row.category" size="small" effect="plain" class="kp-tag">{{ row.category }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="分数" width="80">
        <template #default="{ row }">{{ row.score }}</template>
      </el-table-column>
      <el-table-column label="置信度" width="90">
        <template #default="{ row }">
          <el-tag
            size="small"
            :type="row.confidence === 'high' ? 'success' : (row.confidence === 'medium' ? 'warning' : 'info')"
          >{{ { high: '高', medium: '中' }[row.confidence] || '低' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="依据" min-width="150">
        <template #default="{ row }">
          <span v-if="row.sources?.lexical" class="kp-src">字面 {{ row.sources.lexical }}</span>
          <span v-if="row.sources?.vector" class="kp-src">向量 {{ row.sources.vector }}</span>
          <span v-if="row.sources?.graph" class="kp-src">图谱 {{ row.sources.graph }}</span>
          <span v-if="row.graph_only" class="kp-src">仅关系推断</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="86" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" @click="adoptCandidate(row)">采纳</el-button>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty
          description="没有候选：请确认本课程已建知识图谱；配置 EMBEDDING_API_KEY 可启用语义召回"
          :image-size="70"
        />
      </template>
    </el-table>
    <div class="class-note">采纳只填入表单，点击「保存」才写入题库；自动标注不会覆盖你已有的判断。</div>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Plus } from '@element-plus/icons-vue'
import { api } from '../../api'
import { QUESTION_TYPES } from '../../utils/question'
import { useLatestRequest } from '../../composables/useLatestRequest'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  question: { type: Object, default: null },
  preset: { type: Object, default: null },
  documents: { type: Array, default: () => [] },
  kpOptions: { type: Array, default: () => [] },
  saving: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue', 'submit'])

const form = reactive({
  q_type: 'SINGLE', stem: '', analysis: '', difficulty: 3, kp_id: '', document_id: '',
  options: [], singleAnswer: 'A', multiAnswer: [], judgeAnswer: 'true',
  blanks: [], essayAnswer: '',
})

const kpCandVisible = ref(false)
const kpCandLoading = ref(false)
const kpCandList = ref([])
const kpCandMeta = ref(null)
const candidateRequest = useLatestRequest()

function emptyOptions() {
  return ['A', 'B', 'C', 'D'].map((k) => ({ key: k, text: '' }))
}

/** 填空题默认空位（1 个；最多 20 个，与后端 MAX_BLANKS 对齐） */
function emptyBlanks() {
  return [{ key: 1, label: '第1空', hint: '', score: 50, answer: '' }]
}

/** 打开弹窗时回填题型/选项/答案/文档/知识点（新增时支持预设知识点） */
function initForm() {
  const row = props.question
  const preset = props.preset || {}
  form.q_type = row ? row.q_type : 'SINGLE'
  form.stem = row ? row.stem : ''
  form.analysis = row ? (row.analysis || '') : ''
  form.difficulty = row ? (row.difficulty || 3) : 3
  form.kp_id = row ? (row.kp_id || '') : (preset.kp_id || '')
  form.document_id = row
    ? (row.document_id ? String(row.document_id) : '')
    : (preset.document_id || '')

  const isJudge = row && row.q_type === 'JUDGE'
  const isFill = row && row.q_type === 'FILL'
  const isEssay = row && row.q_type === 'ESSAY'

  // 选择题回填选项（不重排编号）；判断题/填空/解答不使用选项数组
  form.options = (row && (row.q_type === 'SINGLE' || row.q_type === 'MULTI'))
    ? (Array.isArray(row.options)
        ? row.options.map((o) => ({ key: String(o.key), text: o.text }))
        : emptyOptions())
    : []

  // 填空题：回填空位（每空含参考答案与分值，教师视角才有 answer）
  form.blanks = isFill && Array.isArray(row.options)
    ? row.options.map((b, i) => ({
        key: b.key ?? i + 1,
        label: b.label || `第${i + 1}空`,
        hint: b.hint || '',
        score: Number(b.score) || 0,
        answer: b.answer || '',
      }))
    : emptyBlanks()

  // 解答题：回填参考答案（存储为字符串）
  form.essayAnswer = isEssay && typeof row.answer === 'string' ? row.answer : ''

  const ans = row ? row.answer : null
  form.singleAnswer = row && row.q_type === 'SINGLE' ? String(ans) : (form.options[0]?.key || 'A')
  form.multiAnswer = row && row.q_type === 'MULTI' && Array.isArray(ans)
    ? ans.map((x) => String(x).toUpperCase())
    : []
  form.judgeAnswer = row && row.q_type === 'JUDGE'
    ? (String(ans) === 'false' ? 'false' : 'true')
    : 'true'

  if (!form.options.length && !isJudge && !isFill && !isEssay) {
    form.options = emptyOptions()
  }
  kpCandVisible.value = false
  // 记录打开时的快照，用于「有未保存内容就拦一层」的关闭确认
  baseline = JSON.stringify(form)
}

// ===== 未保存修改保护：关闭弹窗（点 X / Esc / 取消）前确认 =====
let baseline = ''

function isDirty() {
  return baseline !== '' && JSON.stringify(form) !== baseline
}

function handleBeforeClose(done) {
  if (!isDirty()) {
    done()
    return
  }
  ElMessageBox.confirm(
    '题目还没保存，关闭后填写的内容会丢失。确定关闭吗？',
    '放弃编辑',
    { type: 'warning', confirmButtonText: '放弃', cancelButtonText: '继续编辑' },
  ).then(() => done()).catch(() => {})
}

function handleCancel() {
  handleBeforeClose(() => emit('update:modelValue', false))
}

watch(() => props.modelValue, (visible) => {
  if (visible) initForm()
  else kpCandVisible.value = false
})

/** 添加选项：取 A-H 中第一个未使用的编号（不重排已有编号，避免答案键错位） */
function addOption() {
  const used = new Set(form.options.map((o) => o.key))
  const next = 'ABCDEFGH'.split('').find((k) => !used.has(k))
  if (next) form.options.push({ key: next, text: '' })
}

/** 删除选项：同步剔除该键在答案中的引用 */
function removeOption(idx) {
  if (form.options.length <= 2) return
  const [removed] = form.options.splice(idx, 1)
  form.multiAnswer = form.multiAnswer.filter((k) => k !== removed.key)
  if (form.singleAnswer === removed.key) {
    form.singleAnswer = form.options[0]?.key || ''
  }
}

/** 添加空位：编号顺延（不重排已有编号，避免答案错位） */
function addBlank() {
  if (form.blanks.length >= 20) return
  const nextKey = form.blanks.reduce((m, b) => Math.max(m, Number(b.key) || 0), 0) + 1
  form.blanks.push({ key: nextKey, label: `第${nextKey}空`, hint: '', score: 0, answer: '' })
}

function removeBlank(idx) {
  if (form.blanks.length <= 1) return
  form.blanks.splice(idx, 1)
}

/** 组装提交体：空选项被过滤；document_id 留空即「课程通用题」 */
function buildPayload() {
  const base = {
    q_type: form.q_type,
    stem: form.stem,
    analysis: form.analysis || null,
    difficulty: form.difficulty,
    kp_id: form.kp_id || null,
    document_id: form.document_id || null,
  }
  if (form.q_type === 'JUDGE') {
    return { ...base, options: [], answer: form.judgeAnswer }
  }
  if (form.q_type === 'FILL') {
    return {
      ...base,
      options: form.blanks.map((b, i) => ({
        key: Number(b.key) || i + 1,
        label: b.label || `第${i + 1}空`,
        hint: b.hint || '',
        score: Number(b.score) || 0,
        answer: (b.answer || '').trim(),
      })),
      // 参考答案由每个空位携带；后端会同步落一份数组，这里不重复传
      answer: null,
    }
  }
  if (form.q_type === 'ESSAY') {
    return { ...base, options: [], answer: (form.essayAnswer || '').trim() }
  }
  const options = form.options
    .filter((o) => (o.text || '').trim())
    .map((o) => ({ key: o.key, text: o.text.trim() }))
  return {
    ...base,
    options,
    answer: form.q_type === 'MULTI' ? form.multiAnswer : form.singleAnswer,
  }
}

function submit() {
  const payload = buildPayload()
  if (!payload.stem || !payload.stem.trim()) {
    ElMessage.warning('请填写题干')
    return
  }
  const selectTypes = ['SINGLE', 'MULTI']
  if (selectTypes.includes(payload.q_type) && payload.options.length < 2) {
    ElMessage.warning('选择题至少需要 2 个非空选项')
    return
  }
  if (payload.q_type === 'MULTI' && !payload.answer.length) {
    ElMessage.warning('请勾选多选题的正确答案')
    return
  }
  if (payload.q_type === 'FILL') {
    if (!payload.options.length) {
      ElMessage.warning('填空题至少需要 1 个空位')
      return
    }
    if (payload.options.some((b) => !b.answer)) {
      ElMessage.warning('请为每个空位填写参考答案（教师批改依据）')
      return
    }
  }
  if (payload.q_type === 'ESSAY' && !payload.answer) {
    ElMessage.warning('请填写解答题的参考答案（教师批改依据）')
    return
  }
  emit('submit', payload)
}

// ---------------- 单题自动标注 ----------------
const kpCandDescription = computed(() => {
  const m = kpCandMeta.value
  if (!m) return ''
  const parts = [
    m.graph_available ? '图谱可用（关系扩展已启用）' : '图谱不可用（仅按名称字面匹配）',
    m.vector_available
      ? '向量召回已启用'
      : (m.embedding_configured ? '向量召回本次失败' : '未配置 EMBEDDING_API_KEY（向量召回关闭）'),
  ]
  if (m.reason) parts.push(m.reason)
  return parts.join(' · ')
})

function catalogSourceLabel(src) {
  return { graph: 'Neo4j 图谱', cache: '本地缓存', injected: '外部数据', none: '无' }[src] || src || '未知'
}

async function loadKpCandidates() {
  if (!props.question) return
  kpCandVisible.value = true
  const token = candidateRequest.begin()
  kpCandLoading.value = true
  try {
    const data = await api.getKpCandidates(props.question.question_id, 5)
    if (!candidateRequest.isLatest(token)) return
    kpCandList.value = data.candidates || []
    kpCandMeta.value = data.meta || null
  } catch (e) {
    if (!candidateRequest.isLatest(token)) return
    kpCandList.value = []
    kpCandMeta.value = null
    ElMessage.warning(`自动标注失败：${e.message}`)
  } finally {
    if (candidateRequest.isLatest(token)) kpCandLoading.value = false
  }
}

function adoptCandidate(row) {
  form.kp_id = row.kp_id
  kpCandVisible.value = false
  ElMessage.success(`已填入知识点「${row.name}」，保存后生效`)
}
</script>

<style scoped>
.opt-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.class-note {
  font-size: 12px;
  color: #c0c4cc;
}
.coverage-alert {
  margin-bottom: 12px;
}
.kp-tag { margin-left: 6px; }
.kp-src {
  display: inline-block;
  margin-right: 8px;
  color: var(--text-secondary);
  font-size: 12.5px;
}
</style>
