<template>
  <div>
    <TeacherCoursePicker
      v-if="!courseId"
      description="请先选择要管理文档的课程"
      :courses="courses"
      :model-value="courseId"
      :tip="!courses.length ? '暂无课程，可先点击「新建课程」创建' : ''"
      show-create
      @update:model-value="emit('update:courseId', $event)"
      @go-courses="emit('go-courses')"
      @create="emit('create-course')"
    />

    <template v-else>
      <TeacherContextBar
        back-text="返回课程列表"
        :course-name="courseName"
        :course-code="courseCode"
        :courses="courses"
        :course-id="courseId"
        show-switcher
        @back="emit('back')"
        @update:course-id="emit('update:courseId', $event)"
      />

      <!-- 批量上传入口（上传到当前课程，每份文档独立解析/抽取/建图） -->
      <el-card class="page-card upload-card">
        <template #header>
          <div class="doc-toolbar">
            <span class="panel-header">批量上传文档</span>
            <span class="stats-text">当前课程：{{ courseName }}</span>
          </div>
        </template>
        <el-form label-width="80px">
          <el-form-item label="选择文件">
            <el-upload
              drag
              multiple
              v-model:file-list="uploadFileList"
              :auto-upload="false"
              accept=".pdf,.txt,.docx,.md"
              style="max-width: 520px"
            >
              <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
              <div class="el-upload__text">将文件拖到此处，或<em>点击选择</em>（可多选）</div>
              <template #tip>
                <div class="el-upload__tip">
                  一次可选择多个文件，每个文件独立构建知识图谱；支持 PDF / DOCX / TXT / MD，单文件不超过 50MB
                </div>
              </template>
            </el-upload>
          </el-form-item>
          <el-form-item>
            <el-button
              type="primary"
              :icon="Upload"
              :loading="uploading"
              :disabled="!uploadFileList.length"
              @click="doBatchUpload"
            >
              上传并构建知识图谱（{{ uploadFileList.length }} 个文件）
            </el-button>
            <el-button v-if="batchItems.length" :disabled="uploading" @click="clearBatchItems">
              清空处理记录
            </el-button>
          </el-form-item>
        </el-form>

        <!-- 批量处理结果：每个文件独立状态，数据全部来自后端真实状态 -->
        <template v-if="batchItems.length">
          <div class="batch-summary">
            <div class="batch-summary__stats">
              <span>总数 <b>{{ batchSummary.total }}</b></span>
              <span class="is-success">成功 <b>{{ batchSummary.success }}</b></span>
              <span class="is-danger">失败 <b>{{ batchSummary.failed }}</b></span>
              <span class="is-muted">进行中 <b>{{ batchSummary.processing }}</b></span>
            </div>
            <el-progress
              class="batch-summary__progress"
              :percentage="batchSummary.percentage"
              :status="batchSummary.failed ? 'exception' : (batchSummary.percentage === 100 ? 'success' : '')"
              :stroke-width="12"
              :striped="batchSummary.processing > 0"
              :striped-flow="batchSummary.processing > 0"
              :indeterminate="batchSummary.processing > 0"
              :duration="3"
              :format="() => (batchSummary.processing > 0 ? '处理中' : `${batchSummary.percentage}%`)"
            />
          </div>
          <el-alert
            v-if="pollTimedOut && batchSummary.processing > 0"
            type="warning"
            show-icon
            :closable="false"
            class="batch-timeout"
            title="进度自动刷新已停止"
            description="仍有文档超过 30 分钟未处理完，可能是后端队列卡住或 LLM 调用失败。可稍后重新进入本页查看，或对卡住的文档点「重试」。"
          />
          <el-table :data="batchItems" size="small" class="batch-table">
            <el-table-column prop="file_name" label="文件" min-width="180" show-overflow-tooltip />
            <el-table-column label="状态" width="120" align="center">
              <template #default="{ row }">
                <el-tag :type="batchStage(row).type" size="small">{{ batchStage(row).text }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="进度" min-width="150">
              <template #default="{ row }">
                <el-progress
                  :percentage="batchStage(row).percentage"
                  :status="batchStage(row).progressStatus"
                  :stroke-width="8"
                  :striped="batchStage(row).inFlight"
                  :striped-flow="batchStage(row).inFlight"
                  :indeterminate="batchStage(row).indeterminate"
                  :duration="2"
                  :format="() => batchStage(row).text"
                />
              </template>
            </el-table-column>
            <el-table-column label="结果 / 失败原因" min-width="260" show-overflow-tooltip>
              <template #default="{ row }">
                <span v-if="batchStage(row).failed" class="batch-error">{{ batchStage(row).message || '处理失败' }}</span>
                <span v-else-if="batchStage(row).completed" class="batch-ok">
                  知识点 {{ batchDocument(row)?.entity_count ?? 0 }}，关系 {{ batchDocument(row)?.relation_count ?? 0 }}
                </span>
                <span v-else class="is-muted">{{ batchStage(row).message || '处理中…' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="100" align="center">
              <template #default="{ row }">
                <el-button
                  v-if="canRetryBatchItem(row)"
                  size="small"
                  type="warning"
                  plain
                  :icon="Refresh"
                  :loading="row.retrying"
                  @click="retryBatchItem(row)"
                >
                  重试
                </el-button>
                <span v-else class="is-muted">—</span>
              </template>
            </el-table-column>
          </el-table>
        </template>
      </el-card>

      <!-- 文档列表 -->
      <el-card class="page-card">
        <template #header>
          <div class="doc-toolbar">
            <span class="panel-header">文档列表（{{ documents.length }}）</span>
            <el-button :icon="Refresh" circle size="small" aria-label="刷新文档列表" @click="reloadDocuments" />
          </div>
        </template>
        <el-table :data="documents" v-loading="documentsLoading" class="doc-table">
          <el-table-column prop="file_name" label="文件名" min-width="180" show-overflow-tooltip />
          <el-table-column prop="file_type" label="类型" width="80" align="center" />
          <el-table-column label="大小" width="100" align="center">
            <template #default="{ row }">{{ fmtSize(row.file_size) }}</template>
          </el-table-column>
          <el-table-column label="解析状态" width="100" align="center">
            <template #default="{ row }">
              <el-tooltip :disabled="!row.error_message" :content="row.error_message || ''" placement="top">
                <el-tag :type="parseStatusType(row.parse_status)" size="small">{{ parseStatusText(row.parse_status) }}</el-tag>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column label="抽取状态" width="100" align="center">
            <template #default="{ row }">
              <el-tooltip :disabled="!row.error_message" :content="row.error_message || ''" placement="top">
                <el-tag :type="extractStatusType(row.extract_status)" size="small">{{ extractStatusText(row.extract_status) }}</el-tag>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column label="图谱状态" width="110" align="center">
            <template #default="{ row }">
              <el-tooltip :disabled="!row.error_message" :content="row.error_message || ''" placement="top">
                <el-tag :type="graphStatusType(row)" size="small">{{ graphStatusText(row) }}</el-tag>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column label="生成进度" min-width="180">
            <template #default="{ row }">
              <el-progress
                :percentage="docProgress(row)"
                :status="docProgressStatus(row)"
                :stroke-width="10"
                :striped="docIsInFlight(row)"
                :striped-flow="docIsInFlight(row)"
              />
            </template>
          </el-table-column>
          <el-table-column prop="entity_count" label="知识点" width="80" align="center" />
          <el-table-column prop="relation_count" label="关系" width="70" align="center" />
          <el-table-column label="创建时间" width="150">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="470" fixed="right">
            <template #default="{ row }">
              <el-button size="small" type="primary" :icon="Reading" :disabled="row.is_placeholder" @click="emit('read', row)">在线阅读</el-button>
              <el-button size="small" :icon="Download" plain :disabled="row.is_placeholder" @click="downloadRow(row)">下载</el-button>
              <el-button size="small" type="success" plain :icon="UserFilled" :disabled="row.is_placeholder" @click="emit('monitor', row)">监测</el-button>
              <el-button
                v-if="isDocumentFailed(row)"
                size="small"
                type="warning"
                plain
                :icon="Refresh"
                :loading="isDocRetrying(row)"
                @click="retryDocumentRow(row)"
              >重试</el-button>
              <el-button size="small" type="danger" plain :icon="Delete" :disabled="row.is_placeholder" @click="deleteDocument(row)">删除</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty description="暂无文档，请在上方上传" :image-size="80" />
          </template>
        </el-table>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { toRef } from 'vue'
import { ElMessage } from 'element-plus'
import {
  UploadFilled, Upload, Refresh, Reading, Download, UserFilled, Delete,
} from '@element-plus/icons-vue'
import TeacherCoursePicker from './TeacherCoursePicker.vue'
import TeacherContextBar from './TeacherContextBar.vue'
import { useCourseDocuments } from '../../composables/teacher/useCourseDocuments'
import { formatBytes, formatTime } from '../../utils/format'
import { downloadDocumentFile } from '../../utils/download'
import {
  docIsInFlight,
  docProgress,
  docProgressStatus,
  extractStatusText,
  extractStatusType,
  graphStatusText,
  graphStatusType,
  isDocumentFailed,
  parseStatusText,
  parseStatusType,
} from '../../utils/documentStatus'

const props = defineProps({
  active: { type: Boolean, default: false },
  courses: { type: Array, default: () => [] },
  courseId: { type: [String, Number], default: '' },
  courseName: { type: String, default: '' },
  courseCode: { type: String, default: '' },
  documents: { type: Array, default: () => [] },
  documentsLoading: { type: Boolean, default: false },
  reloadDocuments: { type: Function, required: true },
  refreshDocuments: { type: Function, required: true },
})

const emit = defineEmits(['update:courseId', 'back', 'read', 'monitor', 'create-course', 'go-courses'])

const {
  uploadFileList,
  uploading,
  batchItems,
  batchSummary,
  batchDocument,
  batchStage,
  pollTimedOut,
  canRetryBatchItem,
  doBatchUpload,
  retryBatchItem,
  retryDocumentRow,
  isDocRetrying,
  clearBatchItems,
  deleteDocument,
} = useCourseDocuments({
  courseId: toRef(props, 'courseId'),
  active: toRef(props, 'active'),
  documents: toRef(props, 'documents'),
  reloadDocuments: () => props.reloadDocuments(),
  refreshDocuments: () => props.refreshDocuments(),
})

function fmtSize(bytes) {
  return formatBytes(bytes)
}

async function downloadRow(row) {
  try {
    await downloadDocumentFile(row)
  } catch (e) {
    ElMessage.error(`下载失败：${e.message}`)
  }
}
</script>

<style scoped>
.stats-text {
  color: var(--color-text-secondary);
  font-size: var(--font-size-label);
}
.panel-header {
  font-weight: var(--font-weight-semibold);
  font-size: var(--font-size-body);
}
.upload-card {
  margin-bottom: var(--space-3);
}
.doc-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}
.batch-summary {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  margin: var(--space-3) 0 var(--space-2);
  flex-wrap: wrap;
}
.batch-summary__stats {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--font-size-label);
  color: var(--color-text-secondary);
  white-space: nowrap;
}
.batch-summary__stats .is-success b { color: var(--el-color-success); }
.batch-summary__stats .is-danger b { color: var(--el-color-danger); }
.batch-summary__stats .is-muted b { color: var(--color-text-secondary); }
.batch-timeout { margin: 8px 0; }
.batch-summary__progress {
  flex: 1;
  min-width: 220px;
}
.batch-table {
  margin-top: var(--space-2);
}
.batch-error {
  color: var(--el-color-danger);
}
.batch-ok {
  color: var(--el-color-success);
}
.is-muted {
  color: var(--color-text-secondary);
}
.doc-table :deep(.el-table__header th) {
  background: var(--color-bg-soft);
  color: var(--color-text-regular);
  font-weight: var(--font-weight-semibold);
}

/* v2 视觉增强 */
.doc-table :deep(.el-table__header th) {
  background: #f4f6fc !important;
  color: var(--text-secondary);
  letter-spacing: .3px;
}
.upload-card :deep(.el-upload-dragger) {
  width: 100%;
  border-radius: var(--radius-lg);
  border: 1.5px dashed var(--brand-300);
  background:
    radial-gradient(60% 80% at 50% 0, rgba(91, 141, 239, .06), transparent 70%),
    #fbfcff;
  padding: 26px 20px;
  transition: border-color .2s, background .2s, box-shadow .2s;
}
.upload-card :deep(.el-upload-dragger:hover) {
  border-color: var(--brand-500);
  background:
    radial-gradient(60% 80% at 50% 0, rgba(91, 141, 239, .1), transparent 70%),
    var(--brand-50);
  box-shadow: 0 10px 26px -16px rgba(79, 110, 247, .5);
}
.upload-card :deep(.el-icon--upload) {
  width: 56px;
  height: 56px;
  margin: 0 auto 10px;
  border-radius: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--gradient-brand);
  color: #fff;
  font-size: 26px;
  box-shadow: 0 10px 22px -10px rgba(79, 110, 247, .7);
}
.upload-card :deep(.el-icon--upload svg) { color: #fff; }
.upload-card :deep(.el-upload__text) { color: var(--text-secondary); font-size: 13.5px; }
</style>
