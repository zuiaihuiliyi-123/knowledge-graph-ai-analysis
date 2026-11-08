<template>
  <div>
    <TeacherCoursePicker
      v-if="!courseId"
      description="请先选择要管理题库的课程"
      :courses="courses"
      :model-value="courseId"
      @update:model-value="emit('update:courseId', $event)"
      @go-courses="emit('go-courses')"
    />

    <template v-else>
      <!-- 与课程文档/图谱管理/学生管理保持一致：当前课程 + 可切换课程 -->
      <TeacherContextBar
        back-text="返回课程列表"
        :course-name="courseName"
        :courses="courses"
        :course-id="courseId"
        show-switcher
        @back="emit('go-courses')"
        @update:course-id="emit('update:courseId', $event)"
      />

      <!-- 第一层：题库总览（题量 / 题型分布 / 作答正确率 / 收藏） -->
      <el-card class="page-card">
        <div class="chart-title-line">
          <el-icon><Collection /></el-icon> 题库总览
        </div>
        <div v-loading="questionsLoading" class="class-summary">
          <div class="class-kpi">
            <div class="class-kpi-value">{{ questionStats?.total ?? 0 }}</div>
            <div class="class-kpi-label">题目总数</div>
          </div>
          <div class="class-kpi">
            <div class="class-kpi-value">{{ questionStats?.active_count ?? 0 }}</div>
            <div class="class-kpi-label">启用中</div>
          </div>
          <div class="class-kpi">
            <div class="class-kpi-value">{{ questionStats?.by_type?.SINGLE ?? 0 }}</div>
            <div class="class-kpi-label">单选题</div>
          </div>
          <div class="class-kpi">
            <div class="class-kpi-value">{{ questionStats?.by_type?.MULTI ?? 0 }}</div>
            <div class="class-kpi-label">多选题</div>
          </div>
          <div class="class-kpi">
            <div class="class-kpi-value">{{ questionStats?.by_type?.JUDGE ?? 0 }}</div>
            <div class="class-kpi-label">判断题</div>
          </div>
          <div class="class-kpi">
            <div class="class-kpi-value">{{ questionStats?.by_type?.FILL ?? 0 }}</div>
            <div class="class-kpi-label">填空题</div>
          </div>
          <div class="class-kpi">
            <div class="class-kpi-value">{{ questionStats?.by_type?.ESSAY ?? 0 }}</div>
            <div class="class-kpi-label">解答题</div>
          </div>
          <!-- 一键直达「主观题批改」页（标签栏 tab 较多时该页可能被溢出隐藏，故给显式入口） -->
          <div
            class="class-kpi"
            :class="{ highlight: (questionStats?.pending_count ?? 0) > 0 }"
            style="cursor: pointer"
            title="点击进入「主观题批改」"
            @click="emit('go-grading')"
          >
            <div class="class-kpi-value">{{ questionStats?.pending_count ?? 0 }}</div>
            <div class="class-kpi-label">待批改 ›</div>
          </div>
          <div class="class-kpi">
            <div class="class-kpi-value">{{ questionStats?.answer_count ?? 0 }}</div>
            <div class="class-kpi-label">累计作答</div>
          </div>
          <div class="class-kpi">
            <div class="class-kpi-value">{{ questionStats?.correct_rate ?? 0 }}%</div>
            <div class="class-kpi-label">平均正确率</div>
          </div>
          <div class="class-kpi">
            <div class="class-kpi-value">{{ questionStats?.favorite_total ?? 0 }}</div>
            <div class="class-kpi-label">被收藏</div>
          </div>
        </div>
      </el-card>

      <!-- 第二层：知识点题目覆盖率（无题知识点 = 补题清单；点击可直接出题） -->
      <el-card class="page-card">
        <div class="chart-title-line">
          <el-icon><Aim /></el-icon> 知识点题目覆盖率
          <span class="class-note">（只统计启用中的题目；点击无题知识点可直接出题）</span>
        </div>

        <el-alert
          v-if="questionCoverage && !questionCoverage.graph_available"
          class="coverage-alert"
          type="warning"
          :closable="false"
          show-icon
          title="图谱不可用，暂时只能给出题量统计"
          description="启动 Neo4j 后刷新，才能看到「无题知识点清单」与「悬空知识点」。"
        />

        <div v-loading="questionCoverageLoading" class="class-summary">
          <div class="class-kpi">
            <div class="class-kpi-value">{{ questionCoverage?.total_kp ?? 0 }}</div>
            <div class="class-kpi-label">知识点总数</div>
          </div>
          <div class="class-kpi">
            <div class="class-kpi-value">{{ questionCoverage?.kp_with_question ?? 0 }}</div>
            <div class="class-kpi-label">已有题知识点</div>
          </div>
          <div class="class-kpi">
            <div class="class-kpi-value">{{ questionCoverage?.kp_without_question ?? 0 }}</div>
            <div class="class-kpi-label">无题知识点</div>
          </div>
          <div class="class-kpi">
            <div class="class-kpi-value">{{ questionCoverage?.coverage_rate ?? 0 }}%</div>
            <div class="class-kpi-label">覆盖率</div>
          </div>
          <div class="class-kpi">
            <div class="class-kpi-value">{{ questionCoverage?.unlinked_question_count ?? 0 }}</div>
            <div class="class-kpi-label">未挂知识点题</div>
          </div>
          <div class="class-kpi">
            <div class="class-kpi-value">{{ questionCoverage?.dangling_count ?? 0 }}</div>
            <div class="class-kpi-label">悬空知识点</div>
          </div>
        </div>

        <div v-if="questionCoverage?.dangling_count" class="coverage-warn">
          <el-icon><WarningFilled /></el-icon>
          <span>
            有 {{ questionCoverage.dangling_count }} 个题目引用的知识点已不在本课程图谱中
            （「按知识点出题」永远选不到这些题），建议重挂知识点：
            <b>{{ danglingKpText }}</b>
          </span>
        </div>

        <div v-if="questionCoverage?.unmatched?.length" class="coverage-list">
          <div class="coverage-list-title">无题知识点（点击直接为它出题）：</div>
          <el-tag
            v-for="kp in questionCoverage.unmatched"
            :key="kp.kp_id"
            class="coverage-tag"
            type="warning"
            effect="plain"
            @click="openQuestionForKp(kp)"
          >
            {{ kp.name }}
          </el-tag>
        </div>
        <div v-else-if="questionCoverage?.graph_available" class="coverage-ok">
          <el-icon><CircleCheckFilled /></el-icon> 本课程所有知识点都已有启用中的题目
        </div>
      </el-card>

      <!-- 第三层：题目列表 -->
      <el-card class="page-card">
        <div class="chart-title-line"><el-icon><Files /></el-icon> 题目列表</div>

        <div class="table-toolbar">
          <el-select v-model="questionDocFilter" placeholder="全部文档（含课程通用题）" clearable style="width: 210px" @change="applyFilters">
            <el-option v-for="d in documents" :key="d.doc_id" :label="d.file_name" :value="String(d.doc_id)" />
          </el-select>
          <el-select v-model="questionFilters.q_type" placeholder="全部题型" clearable style="width: 130px" @change="applyFilters">
            <el-option v-for="t in QUESTION_TYPES" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
          <el-select v-model="questionFilters.is_active" placeholder="全部状态" clearable style="width: 130px" @change="applyFilters">
            <el-option label="启用中" value="1" />
            <el-option label="已停用" value="0" />
          </el-select>
          <el-input
            v-model="questionFilters.keyword"
            placeholder="搜索题干"
            clearable
            :prefix-icon="Search"
            style="width: 190px"
            @keyup.enter="applyFilters"
            @clear="applyFilters"
          />
          <el-button :icon="Refresh" @click="refreshAll()">刷新</el-button>
          <el-button type="primary" :icon="Plus" @click="openQuestionForm(null)">新增题目</el-button>
          <el-button :icon="MagicStick" @click="autoLabelVisible = true">批量自动标注</el-button>
          <el-button :icon="Upload" @click="importVisible = true">从文档导入</el-button>
          <el-button :icon="Star" @click="favoritesVisible = true">题目收藏情况</el-button>
        </div>

        <!-- 批量操作条：勾选后出现 -->
        <div v-if="selectedRows.length" class="batch-bar">
          <span class="batch-count">已选 <b>{{ selectedRows.length }}</b> 道题</span>
          <el-button size="small" @click="runBatchActive(true)">批量启用</el-button>
          <el-button size="small" @click="runBatchActive(false)">批量停用</el-button>
          <el-button size="small" type="danger" plain @click="runBatchRemove">批量删除</el-button>
          <el-button size="small" link @click="clearSelection">取消选择</el-button>
        </div>

        <el-table
          ref="tableRef"
          :data="questionList"
          v-loading="questionsLoading"
          row-key="question_id"
          @selection-change="onSelectionChange"
        >
          <el-table-column type="selection" width="46" fixed="left" />
          <el-table-column prop="stem" label="题干" min-width="260" show-overflow-tooltip />
          <el-table-column label="题型" width="90">
            <template #default="{ row }">
              <el-tag size="small" effect="plain">{{ row.q_type_label || questionTypeLabel(row.q_type) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="知识点" width="150">
            <template #default="{ row }">
              <span v-if="row.kp_id">{{ kpNameById(row.kp_id) || row.kp_id }}</span>
              <span v-else class="cell-empty">课程通用</span>
            </template>
          </el-table-column>
          <el-table-column label="难度" width="90">
            <template #default="{ row }">{{ '★'.repeat(row.difficulty || 0) || '—' }}</template>
          </el-table-column>
          <el-table-column label="作答 / 正确率" width="140">
            <template #default="{ row }">
              <span v-if="row.attempts">{{ row.attempts }} 次 / {{ row.correct_rate }}%</span>
              <span v-else class="cell-empty">—</span>
            </template>
          </el-table-column>
          <el-table-column prop="favorite_count" label="收藏" width="70" />
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="row.is_active ? 'success' : 'info'">{{ row.is_active ? '启用' : '停用' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="200" fixed="right">
            <template #default="{ row }">
              <el-button size="small" type="primary" link @click="openQuestionForm(row)">编辑</el-button>
              <el-button size="small" link @click="toggleQuestionActive(row)">{{ row.is_active ? '停用' : '启用' }}</el-button>
              <el-button size="small" type="danger" link @click="removeQuestion(row)">删除</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty description="本课程还没有题目，点击「新增题目」开始" :image-size="80" />
          </template>
        </el-table>

        <el-pagination
          v-if="questionTotal > questionPageSize"
          class="table-pagination"
          v-model:current-page="questionPage"
          v-model:page-size="questionPageSize"
          :page-sizes="[10, 20, 50]"
          :total="questionTotal"
          layout="total, sizes, prev, pager, next"
          @current-change="onPageChange"
          @size-change="onPageSizeChange"
        />
      </el-card>
    </template>

    <!-- 新增 / 编辑题目（含单题自动标注候选） -->
    <QuestionFormDialog
      v-model="formVisible"
      :question="editingQuestion"
      :preset="formPreset"
      :documents="documents"
      :kp-options="questionKpOptions"
      :saving="formSaving"
      @submit="saveQuestion"
    />

    <!-- 批量自动标注 -->
    <AutoLabelDialog
      v-model="autoLabelVisible"
      :course-id="courseId"
      :documents="documents"
      :default-document-id="questionDocFilter"
      @applied="refreshAll"
    />

    <!-- 文档导入向导 -->
    <QuestionImportDialog
      v-model="importVisible"
      :course-id="courseId"
      :documents="documents"
      :default-document-id="questionDocFilter"
      @imported="refreshAll"
    />

    <!-- 题目收藏情况 -->
    <QuestionFavoritesDialog
      v-model="favoritesVisible"
      :course-id="courseId"
    />
  </div>
</template>

<script setup>
import { ref, toRef } from 'vue'
import {
  Collection, Aim, WarningFilled, CircleCheckFilled, Files, Refresh, Plus, MagicStick, Upload, Star, Search,
} from '@element-plus/icons-vue'
import TeacherCoursePicker from './TeacherCoursePicker.vue'
import QuestionFormDialog from './QuestionFormDialog.vue'
import AutoLabelDialog from './AutoLabelDialog.vue'
import QuestionImportDialog from './QuestionImportDialog.vue'
import QuestionFavoritesDialog from './QuestionFavoritesDialog.vue'
import TeacherContextBar from './TeacherContextBar.vue'
import { useTeacherQuestions } from '../../composables/teacher/useTeacherQuestions'
import { QUESTION_TYPES } from '../../utils/question'

const props = defineProps({
  active: { type: Boolean, default: false },
  courses: { type: Array, default: () => [] },
  courseId: { type: [String, Number], default: '' },
  courseName: { type: String, default: '' },
  documents: { type: Array, default: () => [] },
  ensureDocuments: { type: Function, default: null },
})

const emit = defineEmits(['update:courseId', 'go-courses', 'go-grading'])

const {
  questionList,
  questionsLoading,
  questionTotal,
  questionPage,
  questionPageSize,
  questionStats,
  questionDocFilter,
  questionFilters,
  questionCoverage,
  questionCoverageLoading,
  questionKpOptions,
  formVisible,
  formSaving,
  editingQuestion,
  formPreset,
  autoLabelVisible,
  importVisible,
  favoritesVisible,
  danglingKpText,
  kpNameById,
  refreshAll,
  onPageChange,
  onPageSizeChange,
  openQuestionForm,
  openQuestionForKp,
  saveQuestion,
  toggleQuestionActive,
  removeQuestion,
  batchSetActive,
  batchRemoveQuestions,
  questionTypeLabel,
} = useTeacherQuestions({
  courseId: toRef(props, 'courseId'),
  active: toRef(props, 'active'),
  documents: toRef(props, 'documents'),
  ensureDocuments: () => props.ensureDocuments?.(),
})

// ---------------- 批量选择 ----------------
const tableRef = ref(null)
const selectedRows = ref([])

function onSelectionChange(rows) {
  selectedRows.value = rows
}

function clearSelection() {
  tableRef.value?.clearSelection()
  selectedRows.value = []
}

async function runBatchActive(activeFlag) {
  const rows = selectedRows.value
  if (!rows.length) return
  await batchSetActive(rows, activeFlag)
  clearSelection()
}

async function runBatchRemove() {
  const rows = selectedRows.value
  if (!rows.length) return
  await batchRemoveQuestions(rows)
  clearSelection()
}

/** 透传给 el-select 的 @change（事件会带新值，这里显式忽略） */
function applyFilters() {
  refreshAll()
}
</script>

<style scoped>
.chart-title-line {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 12px;
}
.class-note {
  margin-left: auto;
  font-size: 12px;
  font-weight: 400;
  color: #c0c4cc;
}
.class-summary {
  display: flex;
  gap: 16px;
  margin-bottom: 8px;
}
.class-kpi {
  flex: 1;
  padding: 14px 16px;
  border-radius: 10px;
  background: #fafbfc;
  border: 1px solid #f0f2f5;
}
.class-kpi-value {
  font-size: 24px;
  font-weight: 700;
  font-family: 'DIN Alternate', 'Helvetica Neue', sans-serif;
}
.class-kpi-label {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}
.class-kpi.highlight {
  border-color: rgba(245, 71, 93, .35);
  background: linear-gradient(135deg, rgba(245, 71, 93, .08), rgba(245, 71, 93, .02));
}
.class-kpi.highlight .class-kpi-value { color: #f5475d; }
.coverage-alert {
  margin-bottom: 12px;
}
.coverage-warn {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-top: 12px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #fdf6ec;
  border: 1px solid #f5dab1;
  color: #b88230;
  font-size: 13px;
  line-height: 1.6;
}
.coverage-warn b {
  color: #a5691a;
  word-break: break-all;
}
.coverage-list {
  margin-top: 12px;
}
.coverage-list-title {
  margin-bottom: 8px;
  font-size: 12px;
  color: #909399;
}
.coverage-tag {
  margin: 0 8px 8px 0;
  cursor: pointer;
}
.coverage-ok {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 12px;
  color: #67c23a;
  font-size: 13px;
}
.table-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.cell-empty {
  color: #c0c4cc;
}
.table-pagination {
  margin-top: 12px;
  justify-content: flex-end;
}

/* 批量操作条：勾选后从工具条下方浮出 */
.batch-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  padding: 8px 12px;
  border-radius: 8px;
  background: #f2f6ff;
  border: 1px solid #d8e4ff;
}

.batch-count {
  font-size: 13px;
  color: #3d4d66;
  margin-right: 4px;
}

.batch-count b {
  color: #2f6fd0;
}

/* v2 视觉增强 */
.chart-title-line { color: var(--text-primary); }
.chart-title-line::before {
  content: '';
  width: 4px;
  height: 15px;
  border-radius: 2px;
  background: var(--gradient-brand);
  margin-right: 2px;
}
.class-kpi {
  background: #fff;
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-card);
  transition: transform .22s;
}
.class-kpi:hover { transform: translateY(-2px); }
.class-kpi-value { color: var(--brand-600); font-family: var(--font-family-number); }
.coverage-tag { border-radius: 999px; }
</style>
