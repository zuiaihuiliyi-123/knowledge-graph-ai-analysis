<template>
  <div class="admin-resources">
    <PageHeader title="资源管理" desc="课程 → 文档 → 知识抽取 → 知识图谱的全链路资源视图">
      <template #extra>
        <el-button :loading="loading" @click="loadAll">刷新</el-button>
      </template>
    </PageHeader>

    <!-- 资源概览 -->
    <div class="metric-row">
      <MetricTile label="文档总数" :value="counts.document_count" :icon="Document" tone="blue" />
      <MetricTile label="课程总数" :value="counts.course_count" :icon="Notebook" tone="violet" />
      <MetricTile
        label="抽取成功"
        :value="extraction.success"
        :icon="CircleCheck"
        tone="green"
        :hint="`共 ${extraction.total ?? 0} 份文档`"
      />
      <MetricTile label="抽取失败" :value="extraction.failed" :icon="WarningFilled" tone="red" />
      <MetricTile label="抽取中" :value="extraction.processing" :icon="Loading" tone="amber" />
      <MetricTile
        label="知识节点"
        :value="graphAvailable ? counts.node_count : null"
        :icon="Share"
        tone="slate"
        :hint="graphAvailable ? '' : 'Neo4j 不可用，无法统计'"
      />
    </div>

    <el-card class="page-card" shadow="never">
      <el-tabs v-model="tab">
        <!-- ============ 文档资源 ============ -->
        <el-tab-pane label="文档资源" name="documents">
          <div class="toolbar">
            <el-input
              v-model="docQuery.keyword"
              placeholder="搜索文件名 / 课程名"
              clearable
              style="width: 230px"
              @keyup.enter="searchDocs"
              @clear="searchDocs"
            >
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>
            <el-select
              v-model="docQuery.course_id"
              placeholder="全部课程"
              clearable
              filterable
              style="width: 210px"
              @change="searchDocs"
            >
              <el-option
                v-for="c in courses"
                :key="c.course_id"
                :label="c.course_name"
                :value="c.course_id"
              />
            </el-select>
            <el-select v-model="docQuery.file_type" placeholder="全部类型" clearable style="width: 120px" @change="searchDocs">
              <el-option v-for="t in fileTypes" :key="t" :label="t" :value="t" />
            </el-select>
            <el-select v-model="docQuery.parse_status" placeholder="解析状态" clearable style="width: 130px" @change="searchDocs">
              <el-option v-for="s in parseStatuses" :key="s" :label="s" :value="s" />
            </el-select>
            <el-select v-model="docQuery.extract_status" placeholder="抽取状态" clearable style="width: 130px" @change="searchDocs">
              <el-option v-for="s in extractStatuses" :key="s" :label="s" :value="s" />
            </el-select>
            <el-button type="primary" @click="searchDocs">查询</el-button>
            <el-button @click="resetDocs">重置</el-button>
            <span class="stats-text" style="margin-left: auto">共 {{ docTotal }} 份文档</span>
          </div>

          <el-table :data="documents" v-loading="docLoading" stripe row-key="doc_id" @sort-change="onDocSort">
            <el-table-column prop="file_name" label="文件名" min-width="220" sortable="custom">
              <template #default="{ row }">
                <div class="fname">{{ row.file_name }}</div>
                <div class="fsub">ID {{ row.doc_id }} · {{ row.file_type }} · {{ fmtSize(row.file_size) }}</div>
              </template>
            </el-table-column>
            <el-table-column label="所属课程" min-width="170">
              <template #default="{ row }">
                <span class="dim">{{ row.course_name || `课程 ${row.course_id}` }}</span>
              </template>
            </el-table-column>
            <el-table-column label="上传者" width="120">
              <template #default="{ row }"><span class="dim">{{ row.uploader_name || '—' }}</span></template>
            </el-table-column>
            <el-table-column label="解析" width="104">
              <template #default="{ row }">
                <el-tag size="small" :type="statusTag(row.parse_status)" effect="plain">
                  {{ row.parse_status || '—' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="抽取" width="112">
              <template #default="{ row }">
                <el-tag size="small" :type="statusTag(row.extract_status)" effect="plain">
                  {{ row.extract_status || '—' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="节点 / 关系" width="110">
              <template #default="{ row }">
                <span class="num">{{ row.entity_count || 0 }}</span> /
                <span class="num">{{ row.relation_count || 0 }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="上传时间" width="140" sortable="custom">
              <template #default="{ row }"><span class="dim">{{ fmtTime(row.created_at) }}</span></template>
            </el-table-column>
            <el-table-column label="操作" width="130" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="openDoc(row)">详情</el-button>
                <el-button link type="danger" @click="tryDeleteDoc(row)">删除</el-button>
              </template>
            </el-table-column>
            <template #empty><div class="empty-line">没有符合条件的文档</div></template>
          </el-table>

          <div class="pager">
            <el-pagination
              v-model:current-page="docQuery.page"
              v-model:page-size="docQuery.page_size"
              :total="docTotal"
              :page-sizes="[10, 20, 50, 100]"
              layout="total, sizes, prev, pager, next, jumper"
              background
              @current-change="loadDocs"
              @size-change="searchDocs"
            />
          </div>
        </el-tab-pane>

        <!-- ============ 抽取任务 ============ -->
        <el-tab-pane label="知识抽取任务" name="tasks">
          <el-alert
            class="tab-note"
            type="info"
            :closable="false"
            show-icon
            title="本系统的知识抽取在文档上传时同步执行，没有独立的异步任务表；任务视图由文档的解析/抽取状态直接派生，因此「任务」与「文档」一一对应。"
          />

          <div class="toolbar">
            <el-input
              v-model="taskQuery.keyword"
              placeholder="搜索文件名 / 课程名"
              clearable
              style="width: 230px"
              @keyup.enter="searchTasks"
              @clear="searchTasks"
            >
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>
            <el-select
              v-model="taskQuery.course_id"
              placeholder="全部课程"
              clearable
              filterable
              style="width: 210px"
              @change="searchTasks"
            >
              <el-option v-for="c in courses" :key="c.course_id" :label="c.course_name" :value="c.course_id" />
            </el-select>
            <el-select v-model="taskQuery.status" placeholder="全部状态" clearable style="width: 130px" @change="searchTasks">
              <el-option label="成功" value="success" />
              <el-option label="失败" value="failed" />
              <el-option label="处理中" value="processing" />
              <el-option label="待处理" value="pending" />
            </el-select>
            <el-button type="primary" @click="searchTasks">查询</el-button>
            <el-button @click="resetTasks">重置</el-button>
            <span class="stats-text" style="margin-left: auto">共 {{ taskTotal }} 个任务</span>
          </div>

          <!-- 任务状态分布 -->
          <div class="task-summary">
            <div v-for="s in taskSummaryCells" :key="s.key" class="ts-cell" :class="s.key">
              <div class="ts-value">{{ s.value }}</div>
              <div class="ts-label">{{ s.label }}</div>
            </div>
          </div>

          <el-table :data="tasks" v-loading="taskLoading" stripe row-key="task_id">
            <el-table-column label="任务" min-width="200">
              <template #default="{ row }">
                <div class="fname">{{ row.file_name }}</div>
                <!-- 任务与文档一一对应：task_id 就是 doc_id（见 sql_db.list_extraction_tasks） -->
                <div class="fsub">文档 ID {{ row.task_id }}</div>
              </template>
            </el-table-column>
            <el-table-column label="所属课程" min-width="160">
              <template #default="{ row }">
                <span class="dim">{{ row.course_name || `课程 ${row.course_id}` }}</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="taskTag(row.status)" effect="light">{{ taskLabel(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="产出" width="140">
              <template #default="{ row }">
                <span class="num">{{ row.entity_count || 0 }}</span> 节点 /
                <span class="num">{{ row.relation_count || 0 }}</span> 关系
              </template>
            </el-table-column>
            <el-table-column label="分块" width="76">
              <template #default="{ row }"><span class="dim">{{ row.chunk_count || 0 }}</span></template>
            </el-table-column>
            <el-table-column label="耗时" width="90">
              <template #default="{ row }">
                <span class="dim">{{ fmtDuration(row.duration_seconds) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="开始时间" width="140">
              <template #default="{ row }"><span class="dim">{{ fmtTime(row.started_at) }}</span></template>
            </el-table-column>
            <el-table-column label="结束时间" width="140">
              <template #default="{ row }"><span class="dim">{{ fmtTime(row.finished_at) }}</span></template>
            </el-table-column>
            <el-table-column label="操作" width="220" fixed="right">
              <template #default="{ row }">
                <el-button v-if="row.error_message" link type="danger" @click="openTask(row)">失败原因</el-button>
                <el-button link type="primary" @click="jumpToDoc(row)">查看文档</el-button>
              </template>
            </el-table-column>
            <template #empty><div class="empty-line">没有符合条件的抽取任务</div></template>
          </el-table>

          <div class="pager">
            <el-pagination
              v-model:current-page="taskQuery.page"
              v-model:page-size="taskQuery.page_size"
              :total="taskTotal"
              :page-sizes="[10, 20, 50, 100]"
              layout="total, sizes, prev, pager, next, jumper"
              background
              @current-change="loadTasks"
              @size-change="searchTasks"
            />
          </div>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <!-- 文档详情 -->
    <el-drawer v-model="docVisible" title="文档详情" size="520px" :destroy-on-close="true">
      <div v-if="actingDoc" class="detail-body">
        <div class="dh-name">{{ actingDoc.file_name }}</div>
        <div class="dh-tags">
          <el-tag size="small" :type="statusTag(actingDoc.parse_status)" effect="plain">
            解析 {{ actingDoc.parse_status || '—' }}
          </el-tag>
          <el-tag size="small" :type="statusTag(actingDoc.extract_status)" effect="plain">
            抽取 {{ actingDoc.extract_status || '—' }}
          </el-tag>
          <span class="dim">ID {{ actingDoc.doc_id }}</span>
        </div>

        <el-divider content-position="left">资源信息</el-divider>
        <div class="kv-grid">
          <div class="kv"><span class="kv-k">所属课程</span><span class="kv-v">{{ actingDoc.course_name || `课程 ${actingDoc.course_id}` }}</span></div>
          <div class="kv"><span class="kv-k">上传者</span><span class="kv-v">{{ actingDoc.uploader_name || '—' }}</span></div>
          <div class="kv"><span class="kv-k">文件类型</span><span class="kv-v">{{ actingDoc.file_type }}</span></div>
          <div class="kv"><span class="kv-k">文件大小</span><span class="kv-v">{{ fmtSize(actingDoc.file_size) }}</span></div>
          <div class="kv"><span class="kv-k">上传时间</span><span class="kv-v">{{ fmtTime(actingDoc.created_at) }}</span></div>
          <div class="kv"><span class="kv-k">最近更新</span><span class="kv-v">{{ fmtTime(actingDoc.updated_at) }}</span></div>
        </div>

        <el-divider content-position="left">知识抽取产出</el-divider>
        <div class="stat-grid">
          <div class="stat-cell">
            <div class="sc-value">{{ actingDoc.chunk_count || 0 }}</div>
            <div class="sc-label">文本分块</div>
          </div>
          <div class="stat-cell">
            <div class="sc-value">{{ actingDoc.entity_count || 0 }}</div>
            <div class="sc-label">知识节点</div>
          </div>
          <div class="stat-cell">
            <div class="sc-value">{{ actingDoc.relation_count || 0 }}</div>
            <div class="sc-label">知识关系</div>
          </div>
        </div>

        <template v-if="actingDoc.error_message">
          <el-divider content-position="left">错误信息</el-divider>
          <div class="err-box">{{ actingDoc.error_message }}</div>
        </template>

        <el-alert
          class="dlg-alert"
          type="warning"
          :closable="false"
          show-icon
          title="删除文档会同步清理其知识图谱节点、向量、学习记录、收藏与题库，与教师端删除走同一套完整性流程。"
        />
      </div>
      <template #footer>
        <el-button @click="docVisible = false">关闭</el-button>
        <el-button type="danger" :loading="submitting" @click="tryDeleteDoc(actingDoc)">删除该文档</el-button>
      </template>
    </el-drawer>

    <!-- 抽取失败原因 -->
    <el-dialog v-model="taskVisible" title="抽取失败原因" width="520px" align-center>
      <p class="dlg-tip">{{ actingTask?.file_name }}</p>
      <div class="err-box">{{ actingTask?.error_message || '无错误信息' }}</div>
      <template #footer>
        <el-button type="primary" @click="taskVisible = false">知道了</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Search, Document, Notebook, CircleCheck, WarningFilled, Loading, Share,
} from '@element-plus/icons-vue'
import PageHeader from '../../components/PageHeader.vue'
import MetricTile from '../../components/admin/MetricTile.vue'
import { api } from '../../api'

const tab = ref('documents')
const loading = ref(false)
const submitting = ref(false)

const counts = ref({})
const extraction = ref({})
const graphAvailable = ref(true)
const courses = ref([])
const fileTypes = ['pdf', 'txt', 'md', 'docx']
const parseStatuses = ['PENDING', 'PARSING', 'PARSED', 'FAILED']
const extractStatuses = ['PENDING', 'EXTRACTING', 'COMPLETED', 'FAILED']

// 文档
const documents = ref([])
const docTotal = ref(0)
const docLoading = ref(false)
const docQuery = reactive({
  keyword: '', course_id: null, file_type: '', parse_status: '', extract_status: '',
  page: 1, page_size: 20, sort_by: 'doc_id', sort_order: 'desc',
})

// 任务
const tasks = ref([])
const taskTotal = ref(0)
const taskLoading = ref(false)
const taskSummary = ref({})
const taskQuery = reactive({
  keyword: '', course_id: null, status: '', page: 1, page_size: 20,
})

const docVisible = ref(false)
const taskVisible = ref(false)
const actingDoc = ref(null)
const actingTask = ref(null)

const fmtTime = (t) => (t ? String(t).slice(0, 16) : '—')
const statusTag = (s) =>
  ({ COMPLETED: 'success', PARSED: 'success', PARSING: 'warning', EXTRACTING: 'warning', FAILED: 'danger' }[s] || 'info')
const taskTag = (s) =>
  ({ success: 'success', failed: 'danger', processing: 'warning', pending: 'info' }[s] || 'info')
const taskLabel = (s) =>
  ({ success: '成功', failed: '失败', processing: '处理中', pending: '待处理' }[s] || s || '—')

function fmtSize(bytes) {
  const n = Number(bytes || 0)
  if (!n) return '0 B'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}

// duration_seconds 为 null 表示任务未结束（没有 finished_at），而不是耗时 0
function fmtDuration(sec) {
  if (sec === null || sec === undefined) return '—'
  const s = Number(sec)
  if (s < 60) return `${s.toFixed(1)} 秒`
  return `${Math.floor(s / 60)} 分 ${Math.round(s % 60)} 秒`
}

const taskSummaryCells = computed(() => {
  const s = taskSummary.value || {}
  return [
    { key: 'success', label: '成功', value: s.success ?? 0 },
    { key: 'failed', label: '失败', value: s.failed ?? 0 },
    { key: 'processing', label: '处理中', value: s.processing ?? 0 },
    { key: 'pending', label: '待处理', value: s.pending ?? 0 },
  ]
})

// ---- 加载 ----
async function loadOverview() {
  loading.value = true
  try {
    const data = await api.adminDashboard()
    counts.value = data.counts || {}
    extraction.value = data.extraction || {}
    graphAvailable.value = !!data.graph_available
  } catch (e) {
    ElMessage.error(e?.message || '加载资源概览失败')
  } finally {
    loading.value = false
  }
}

async function loadOptions() {
  try {
    const data = await api.adminOptions()
    courses.value = data.courses || []
  } catch {
    // 课程下拉失败不影响列表本身
  }
}

async function loadDocs() {
  docLoading.value = true
  try {
    const params = { ...docQuery }
    Object.keys(params).forEach((k) => {
      if (params[k] === '' || params[k] === null) delete params[k]
    })
    const data = await api.adminListDocuments(params)
    documents.value = data.items || []
    docTotal.value = data.total || 0
  } catch (e) {
    ElMessage.error(e?.message || '加载文档列表失败')
  } finally {
    docLoading.value = false
  }
}

function searchDocs() {
  docQuery.page = 1
  loadDocs()
}

function resetDocs() {
  docQuery.keyword = ''
  docQuery.course_id = null
  docQuery.file_type = ''
  docQuery.parse_status = ''
  docQuery.extract_status = ''
  docQuery.sort_by = 'doc_id'
  docQuery.sort_order = 'desc'
  searchDocs()
}

function onDocSort({ prop, order }) {
  if (!prop || !order) {
    docQuery.sort_by = 'doc_id'
    docQuery.sort_order = 'desc'
  } else {
    docQuery.sort_by = prop
    docQuery.sort_order = order === 'ascending' ? 'asc' : 'desc'
  }
  searchDocs()
}

async function loadTasks() {
  taskLoading.value = true
  try {
    const params = { ...taskQuery }
    Object.keys(params).forEach((k) => {
      if (params[k] === '' || params[k] === null) delete params[k]
    })
    const data = await api.adminExtractionTasks(params)
    tasks.value = data.items || []
    taskTotal.value = data.total || 0
    taskSummary.value = data.summary || {}
  } catch (e) {
    ElMessage.error(e?.message || '加载抽取任务失败')
  } finally {
    taskLoading.value = false
  }
}

function searchTasks() {
  taskQuery.page = 1
  loadTasks()
}

function resetTasks() {
  taskQuery.keyword = ''
  taskQuery.course_id = null
  taskQuery.status = ''
  searchTasks()
}

async function loadAll() {
  await Promise.all([loadOverview(), loadDocs(), loadTasks()])
}

// ---- 交互 ----
function openDoc(row) {
  actingDoc.value = row
  docVisible.value = true
}

function openTask(row) {
  actingTask.value = row
  taskVisible.value = true
}

// 从任务跳到对应文档：切回文档页并按 doc_id 过滤（任务与文档同 id）
function jumpToDoc(row) {
  tab.value = 'documents'
  docQuery.keyword = row.file_name
  docQuery.page = 1
  loadDocs()
}

async function tryDeleteDoc(row) {
  if (!row) return
  try {
    await ElMessageBox.confirm(
      `删除文档「${row.file_name}」会同时清理它在知识图谱中的节点与关系、向量数据、`
      + `学生的学习记录与收藏、以及由它生成的题目。该操作不可恢复。`,
      '删除文档资源',
      { type: 'warning', confirmButtonText: '确认删除', confirmButtonClass: 'el-button--danger' },
    )
    submitting.value = true
    await api.adminDeleteDocument(row.doc_id)
    ElMessage.success('文档及其关联数据已删除')
    docVisible.value = false
    await loadOverview()
    await loadDocs()
    await loadTasks()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e?.message || '删除文档失败')
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  await loadOptions()
  await loadAll()
})
</script>

<style scoped>
.metric-row {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 12px;
  margin-bottom: 14px;
}

.toolbar { width: 100%; margin-bottom: 12px; }
.tab-note { margin-bottom: 14px; border-radius: var(--radius-sm); }

.fname {
  font-weight: 600;
  color: var(--text-primary);
  font-size: 13.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fsub { font-size: 11.5px; color: var(--color-text-muted); margin-top: 1px; }
.dim { color: var(--color-text-muted); font-size: 12.5px; }
.num { font-family: var(--font-family-number); font-weight: 600; color: var(--text-primary); }

.pager { display: flex; justify-content: flex-end; margin-top: 14px; }
.empty-line { padding: 22px 0; text-align: center; color: var(--color-text-muted); font-size: 13px; }

.task-summary {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
  margin-bottom: 14px;
}
.ts-cell {
  padding: 10px 12px;
  border-radius: var(--radius-md);
  background: var(--bg-soft);
  border: 1px solid var(--border-light);
}
.ts-value {
  font-family: var(--font-family-number);
  font-size: 20px;
  font-weight: 700;
  color: var(--text-primary);
}
.ts-label { margin-top: 2px; font-size: 12px; color: var(--color-text-secondary); }
.ts-cell.success .ts-value { color: #18b87a; }
.ts-cell.failed .ts-value { color: #f5475d; }
.ts-cell.processing .ts-value { color: #f5a623; }

/* ---- 详情 ---- */
.detail-body { padding: 0 4px 12px; }
.dh-name { font-size: 15.5px; font-weight: 700; color: var(--text-primary); word-break: break-all; }
.dh-tags { display: flex; align-items: center; gap: 6px; margin-top: 8px; flex-wrap: wrap; }

.kv-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; }
.kv {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 7px 10px;
  border-radius: var(--radius-sm);
  background: var(--bg-soft);
  font-size: 13px;
  min-width: 0;
}
.kv-k { color: var(--color-text-secondary); font-size: 12.5px; flex-shrink: 0; }
.kv-v {
  color: var(--text-primary);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stat-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.stat-cell { padding: 10px 6px; border-radius: var(--radius-sm); background: var(--bg-soft); text-align: center; }
.sc-value {
  font-family: var(--font-family-number);
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
}
.sc-label { margin-top: 2px; font-size: 11.5px; color: var(--color-text-secondary); }

.err-box {
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  background: #fff1f2;
  border: 1px solid #ffd4d9;
  color: #c0304a;
  font-size: 12.5px;
  line-height: 1.6;
  word-break: break-all;
}
.dlg-tip { margin: 0 0 10px; font-size: 13px; color: var(--text-regular); word-break: break-all; }
.dlg-alert { margin-top: 14px; border-radius: var(--radius-sm); }

@media (max-width: 1440px) {
  .metric-row { grid-template-columns: repeat(3, 1fr); }
}
@media (max-width: 1024px) {
  .metric-row { grid-template-columns: repeat(2, 1fr); }
  .task-summary { grid-template-columns: repeat(2, 1fr); }
}
</style>
