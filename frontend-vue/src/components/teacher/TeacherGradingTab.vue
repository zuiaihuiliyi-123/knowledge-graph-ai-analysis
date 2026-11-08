<template>
  <div>
    <TeacherCoursePicker
      v-if="!courseId"
      description="请先选择要批改的课程"
      :courses="courses"
      :model-value="courseId"
      :show-go-courses="false"
      @update:model-value="emit('update:courseId', $event)"
    />

    <template v-else>
      <!-- 与课程文档/图谱管理/题库管理保持一致：当前课程 + 可切换课程 -->
      <TeacherContextBar
        back-text="返回课程列表"
        :course-name="courseName"
        :courses="courses"
        :course-id="courseId"
        show-switcher
        @back="emit('go-courses')"
        @update:course-id="emit('update:courseId', $event)"
      />

      <el-card class="page-card">
        <div class="chart-title-line">
          <el-icon><EditPen /></el-icon> 批改进度
          <span class="class-note">（主观题提交后不判分，批改后才给出分数；≥60 分记为答对）</span>
          <el-button size="small" text type="primary" @click="loadGrading">刷新</el-button>
        </div>
        <div v-loading="gradingLoading" class="class-summary">
          <div class="class-kpi" :class="{ highlight: (gradingSummary?.pending ?? 0) > 0 }">
            <div class="class-kpi-value">{{ gradingSummary?.pending ?? 0 }}</div>
            <div class="class-kpi-label">待批改</div>
          </div>
          <div class="class-kpi">
            <div class="class-kpi-value">{{ gradingSummary?.graded ?? 0 }}</div>
            <div class="class-kpi-label">已批改</div>
          </div>
          <div class="class-kpi">
            <div class="class-kpi-value">{{ gradingSummary?.manual_total ?? 0 }}</div>
            <div class="class-kpi-label">主观题作答</div>
          </div>
          <div class="class-kpi">
            <div class="class-kpi-value">{{ gradingSummary?.avg_score ?? 0 }}</div>
            <div class="class-kpi-label">已批改平均分</div>
          </div>
        </div>
      </el-card>

      <el-card class="page-card">
        <div class="chart-title-line">
          <el-icon><DocumentChecked /></el-icon> {{ gradingStatus === 'GRADED' ? '已批改作答（可复查与改判）' : '待批改作答' }}
          <span class="class-note">
            {{ gradingStatus === 'GRADED'
              ? '（按批改时间倒序；改分数或评语后点「改判」即覆盖旧结果）'
              : '（先交先批；批改后就地更新为学生得分）' }}
          </span>
        </div>

        <div class="question-toolbar">
          <el-radio-group v-model="gradingStatus" @change="onGradingStatusChange">
            <el-radio-button value="PENDING">待批改</el-radio-button>
            <el-radio-button value="GRADED">已批改</el-radio-button>
          </el-radio-group>
          <el-select v-model="gradingDocFilter" placeholder="全部学习资料" clearable style="width: 200px" @change="changeDocumentFilter">
            <el-option v-for="d in documents" :key="d.doc_id" :label="d.file_name" :value="String(d.doc_id)" />
          </el-select>
          <!-- 按学生筛选（选项来自批改台已加载的记录，无需额外请求） -->
          <el-select v-model="gradingStudentFilter" placeholder="全部学生" clearable filterable style="width: 160px" @change="changeStudentFilter">
            <el-option v-for="s in gradingStudentOptions" :key="s.value" :label="s.label" :value="s.value" />
          </el-select>
          <el-input-number v-model="gradingBatchScore" :min="0" :max="100" :step="10" controls-position="right" style="width: 130px" />
          <el-input v-model="gradingBatchComment" placeholder="批量评语（可选）" style="width: 200px" />
          <el-button :disabled="!gradingSelection.length" type="primary" plain @click="batchGrade">
            批量{{ gradingStatus === 'GRADED' ? '改判' : '给分' }}（已选 {{ gradingSelection.length }}）
          </el-button>
        </div>
        <el-table
          :data="gradingList"
          v-loading="gradingLoading"
          row-key="record_id"
          max-height="560"
          @selection-change="onGradingSelection"
        >
          <el-table-column type="selection" width="46" />
          <el-table-column label="学生" width="120">
            <template #default="{ row }">{{ row.student_name }}</template>
          </el-table-column>
          <el-table-column label="题型" width="90">
            <template #default="{ row }"><el-tag size="small" effect="plain">{{ row.q_type_label }}</el-tag></template>
          </el-table-column>
          <el-table-column label="题干" min-width="180" show-overflow-tooltip>
            <template #default="{ row }">{{ row.stem }}</template>
          </el-table-column>
          <el-table-column label="学生解答 / 参考答案" min-width="260">
            <template #default="{ row }">
              <div v-if="row.q_type === 'FILL'">
                <div v-for="b in row.blanks" :key="b.key" class="grade-blank-row">
                  <span class="grade-blank-label">{{ b.label }}</span>
                  <span class="grade-blank-user">{{ blankAnswerOf(row, b.key) || '（未填）' }}</span>
                  <span class="grade-blank-ref">参考：{{ b.answer }}</span>
                </div>
              </div>
              <div v-else>
                <div class="grade-essay-user">{{ row.user_answer || '（未作答）' }}</div>
                <div class="grade-blank-ref">参考答案：{{ row.reference_answer }}</div>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="answered_at" label="提交时间" width="150" />
          <!-- 已批改视图：复查用（当前得分、评语、批改时间），改动后可再次提交改判 -->
          <el-table-column v-if="gradingStatus === 'GRADED'" label="批改结果" min-width="200">
            <template #default="{ row }">
              <el-tag size="small" :type="row.is_correct ? 'success' : 'danger'" effect="plain">
                {{ row.score ?? 0 }} 分 · {{ row.is_correct ? '通过' : '未通过' }}
              </el-tag>
              <div class="class-note">{{ row.comment || '（无评语）' }}</div>
            </template>
          </el-table-column>
          <el-table-column v-if="gradingStatus === 'GRADED'" prop="graded_at" label="批改时间" width="150" />
          <el-table-column :label="gradingStatus === 'GRADED' ? '改判' : '给分'" width="240" fixed="right">
            <template #default="{ row }">
              <div class="opt-row">
                <el-input-number
                  v-model="gradingScore[row.record_id]"
                  :min="0"
                  :max="100"
                  :step="10"
                  controls-position="right"
                  size="small"
                  style="width: 110px"
                />
                <el-button size="small" type="primary" @click="submitGrade(row)">
                  {{ gradingStatus === 'GRADED' ? '改判' : '批改' }}
                </el-button>
              </div>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty
              :description="gradingStatus === 'GRADED'
                ? '还没有已批改的主观题作答（批改后会出现在这里，可随时改判）'
                : '没有待批改的作答（学生提交填空/解答题后会出现在这里）'"
              :image-size="80"
            />
          </template>
        </el-table>

        <el-pagination
          v-if="gradingTotal > gradingPageSize"
          class="table-pagination"
          v-model:current-page="gradingPage"
          :page-size="gradingPageSize"
          :total="gradingTotal"
          layout="total, prev, pager, next"
          @current-change="loadGrading"
        />
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { toRef } from 'vue'
import { EditPen, DocumentChecked } from '@element-plus/icons-vue'
import TeacherCoursePicker from './TeacherCoursePicker.vue'
import TeacherContextBar from './TeacherContextBar.vue'
import { useTeacherGrading } from '../../composables/teacher/useTeacherGrading'

const props = defineProps({
  active: { type: Boolean, default: false },
  courses: { type: Array, default: () => [] },
  courseId: { type: [String, Number], default: '' },
  courseName: { type: String, default: '' },
  documents: { type: Array, default: () => [] },
})

const emit = defineEmits(['update:courseId', 'pending-change', 'go-courses'])

const {
  gradingSummary,
  gradingList,
  gradingLoading,
  gradingTotal,
  gradingPage,
  gradingPageSize,
  gradingDocFilter,
  gradingStatus,
  gradingStudentFilter,
  gradingStudentOptions,
  gradingSelection,
  gradingScore,
  gradingBatchScore,
  gradingBatchComment,
  loadGrading,
  onGradingSelection,
  onGradingStatusChange,
  changeDocumentFilter,
  changeStudentFilter,
  submitGrade,
  batchGrade,
  blankAnswerOf,
} = useTeacherGrading({
  courseId: toRef(props, 'courseId'),
  active: toRef(props, 'active'),
  onSummary: (pending) => emit('pending-change', pending),
})

/** 题库「待批改」卡片直达时复位页码（原版 gotoGrading 的行为） */
function resetForDirectEntry() {
  gradingPage.value = 1
}

defineExpose({ resetForDirectEntry })
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
.grade-blank-row { display: flex; gap: 8px; align-items: baseline; line-height: 1.7; font-size: 12.5px; }
.grade-blank-label { color: var(--text-secondary); flex: 0 0 auto; }
.grade-blank-user { color: var(--text-primary); font-weight: 600; flex: 0 0 auto; }
.grade-blank-ref { color: var(--text-secondary); }
.grade-essay-user { color: var(--text-primary); line-height: 1.6; margin-bottom: 4px; white-space: pre-wrap; }
.opt-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.table-pagination {
  margin-top: 12px;
  justify-content: flex-end;
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
</style>
