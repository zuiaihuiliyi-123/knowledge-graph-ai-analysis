<template>
  <div>
    <div class="page-header">
      <h2 class="page-title">👩‍🏫 教师工作台</h2>
      <p class="page-desc">上传课程资料，自动生成知识图谱；支持预览与手动编辑</p>
    </div>

    <el-tabs v-model="activeTab">
      <!-- ===================== Tab 1：文档上传 ===================== -->
      <el-tab-pane label="📤 文档上传" name="upload">
        <el-card class="page-card">
          <el-form label-width="90px">
            <el-form-item label="课程名称">
              <el-input
                v-model="courseName"
                placeholder="例如：数据结构（可选）"
                style="max-width: 420px"
              />
            </el-form-item>
            <el-form-item label="课程文档">
              <el-upload
                drag
                :auto-upload="false"
                :limit="1"
                :on-change="onFileChange"
                :on-remove="onFileRemove"
                accept=".pdf,.txt,.docx,.md"
                style="max-width: 520px"
              >
                <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
                <div class="el-upload__text">将文件拖到此处，或<em>点击选择</em></div>
                <template #tip>
                  <div class="el-upload__tip">
                    支持 PDF / DOCX / TXT / MD，单文件不超过 50MB
                  </div>
                </template>
              </el-upload>
            </el-form-item>
            <el-form-item>
              <el-button
                type="primary"
                size="large"
                :loading="uploading"
                :disabled="!selectedFile"
                @click="doUpload"
              >
                🚀 开始分析并构建知识图谱
              </el-button>
            </el-form-item>
          </el-form>

          <el-alert
            v-if="uploading"
            type="info"
            :closable="false"
            title="正在解析文档并调用大模型提取知识…"
            description="长文档会分块逐次调用 LLM，整个过程可能需要 1-5 分钟，请勿关闭页面。"
            class="uploading-alert"
          />
        </el-card>

        <!-- 上传结果 -->
        <el-card v-if="uploadResult" class="page-card" style="border-left: 4px solid #67c23a">
          <template #header>
            <div class="result-header">
              <span>✅ 知识图谱构建完成</span>
              <el-button size="small" type="primary" @click="goPreview">前往图谱预览 →</el-button>
            </div>
          </template>
          <el-descriptions :column="2" border>
            <el-descriptions-item label="课程 ID">
              <code>{{ uploadResult.course_id }}</code>
            </el-descriptions-item>
            <el-descriptions-item label="文档名称">
              {{ uploadResult.filename }}
            </el-descriptions-item>
            <el-descriptions-item label="知识点数量">
              {{ uploadResult.entity_count ?? 0 }}
            </el-descriptions-item>
            <el-descriptions-item label="关系数量">
              {{ uploadResult.relation_count ?? 0 }}
            </el-descriptions-item>
            <el-descriptions-item label="解析状态">
              {{ uploadResult.parse_status }}
            </el-descriptions-item>
            <el-descriptions-item label="抽取状态">
              {{ uploadResult.extract_status }}
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-tab-pane>

      <!-- ===================== Tab 2：图谱预览 ===================== -->
      <el-tab-pane label="🔍 图谱预览" name="preview">
        <el-card class="page-card">
          <div class="toolbar">
            <CourseSelector v-model="previewCourseId" @change="onPreviewCourseChange" />
            <el-input
              v-model="previewSearch"
              placeholder="搜索知识点…"
              clearable
              style="width: 220px"
            />
            <el-button :icon="Refresh" circle title="刷新" @click="refreshPreview" />
            <el-button :icon="FullScreen" circle title="适配视图" @click="fitPreview" />
            <span v-if="previewStats" class="stats-text">
              节点 {{ previewStats.nodeCount }} · 关系 {{ previewStats.edgeCount }}
            </span>
          </div>
        </el-card>
        <el-card class="page-card graph-card">
          <GraphCanvas
            ref="previewGraphRef"
            :course-id="previewCourseId"
            :search-text="previewSearch"
            @node-click="onNodeClick"
            @loaded="(s) => (previewStats = s)"
          />
        </el-card>
      </el-tab-pane>

      <!-- ===================== Tab 3：编辑图谱 ===================== -->
      <el-tab-pane label="✏️ 编辑图谱（开发中）" name="edit">
        <el-card class="page-card">
          <el-alert
            type="info"
            :closable="false"
            title="节点 / 关系编辑功能开发中"
            description="后端图谱节点编辑接口（6.3.3/6.3.4）尚未实现，当前仅支持预览浏览。"
            style="margin-bottom: 12px"
          />
          <div class="toolbar">
            <CourseSelector v-model="editCourseId" @change="onEditCourseChange" />
            <el-button type="primary" :icon="Plus" disabled>新增知识点</el-button>
            <el-button type="success" plain :icon="Connection" disabled>新增关系</el-button>
            <el-button :icon="Refresh" circle title="刷新" @click="refreshEdit" />
            <span class="stats-text edit-hint">节点 / 关系编辑开发中，暂仅支持预览</span>
          </div>
        </el-card>
        <el-card class="page-card graph-card">
          <GraphCanvas
            ref="editGraphRef"
            :course-id="editCourseId"
            :editable="true"
            @node-click="onEditNodeClick"
            @edge-click="onEditEdgeClick"
          />
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 节点详情/编辑抽屉 -->
    <NodeDetailDrawer
      v-model="drawerVisible"
      :node="drawerNode"
      :editable="activeTab === 'edit'"
      :course-id="activeTab === 'edit' ? editCourseId : previewCourseId"
      @saved="afterNodeChanged"
      @deleted="afterNodeChanged"
    />

    <!-- 新增知识点对话框 -->
    <el-dialog v-model="addNodeVisible" title="新增知识点" width="480px">
      <el-form label-width="80px">
        <el-form-item label="名称" required>
          <el-input v-model="addNodeForm.name" placeholder="知识点名称" />
        </el-form-item>
        <el-form-item label="类别">
          <el-select v-model="addNodeForm.category" style="width: 100%">
            <el-option label="概念" value="概念" />
            <el-option label="定理" value="定理" />
            <el-option label="公式" value="公式" />
            <el-option label="方法" value="方法" />
          </el-select>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="addNodeForm.description" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addNodeVisible = false">取消</el-button>
        <el-button type="primary" :loading="addNodeLoading" @click="submitAddNode">确定</el-button>
      </template>
    </el-dialog>

    <!-- 新增关系对话框 -->
    <el-dialog v-model="addEdgeVisible" title="新增关系" width="480px">
      <el-form label-width="80px">
        <el-form-item label="源知识点">
          <el-select v-model="addEdgeForm.source" filterable style="width: 100%">
            <el-option v-for="n in editNodes" :key="n.id" :label="n.label" :value="n.label" />
          </el-select>
        </el-form-item>
        <el-form-item label="关系类型">
          <el-select v-model="addEdgeForm.type" style="width: 100%">
            <el-option label="前置知识（PRECEDES）" value="PRECEDES" />
            <el-option label="包含（CONTAINS）" value="CONTAINS" />
            <el-option label="相关概念（RELATED_TO）" value="RELATED_TO" />
            <el-option label="应用（APPLIES_TO）" value="APPLIES_TO" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标知识点">
          <el-select v-model="addEdgeForm.target" filterable style="width: 100%">
            <el-option v-for="n in editNodes" :key="n.id" :label="n.label" :value="n.label" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addEdgeVisible = false">取消</el-button>
        <el-button type="primary" :loading="addEdgeLoading" @click="submitAddEdge">确定</el-button>
      </template>
    </el-dialog>

    <!-- 关系详情/删除对话框 -->
    <el-dialog v-model="edgeDialogVisible" title="关系详情" width="440px">
      <template v-if="clickedEdge">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="源">
            {{ clickedEdge.sourceLabel || clickedEdge.source }}
          </el-descriptions-item>
          <el-descriptions-item label="关系">
            <el-tag size="small">{{ edgeLabel(clickedEdge) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="目标">
            {{ clickedEdge.targetLabel || clickedEdge.target }}
          </el-descriptions-item>
        </el-descriptions>
      </template>
      <template #footer>
        <el-button @click="edgeDialogVisible = false">关闭</el-button>
        <el-button
          type="danger"
          :loading="deleteEdgeLoading"
          disabled
          @click="removeEdge"
        >删除关系（开发中）</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  UploadFilled,
  Refresh,
  FullScreen,
  Plus,
  Connection,
} from '@element-plus/icons-vue'
import { api } from '../api'
import { useAppStore } from '../stores/app'
import GraphCanvas from '../components/GraphCanvas.vue'
import CourseSelector from '../components/CourseSelector.vue'
import NodeDetailDrawer from '../components/NodeDetailDrawer.vue'
import { edgeTypeLabel } from '../utils/graphStyle'

const store = useAppStore()
const route = useRoute()
const activeTab = ref(['upload', 'preview', 'edit'].includes(route.query.tab) ? route.query.tab : 'upload')

// 从侧边栏菜单带 tab 参数跳转时同步切换
watch(
  () => route.query.tab,
  (tab) => {
    if (['upload', 'preview', 'edit'].includes(tab)) activeTab.value = tab
  }
)

// ===================== 上传 =====================
const courseName = ref('')
const selectedFile = ref(null)
const uploading = ref(false)
const uploadResult = ref(null)

function onFileChange(file) {
  selectedFile.value = file.raw
}
function onFileRemove() {
  selectedFile.value = null
}

async function doUpload() {
  if (!selectedFile.value) return
  uploading.value = true
  uploadResult.value = null
  try {
    const formData = new FormData()
    formData.append('file', selectedFile.value)
    // course_name 后端必填：为空时用文件名（去扩展名）兜底
    const name = courseName.value.trim() || selectedFile.value.name.replace(/\.[^.]+$/, '')
    const result = await api.uploadCourse(formData, name)
    uploadResult.value = result
    // 上传会自动在后端建课程，刷新课程列表并选中新课程
    store.fetchCourses(true).catch(() => {})
    store.currentCourseId = String(result.course_id)
    ElMessage.success('知识图谱构建完成')
  } catch (e) {
    ElMessage.error(`上传失败：${e.message}`)
  } finally {
    uploading.value = false
  }
}

function goPreview() {
  if (uploadResult.value?.course_id) {
    previewCourseId.value = uploadResult.value.course_id
  }
  activeTab.value = 'preview'
}

// ===================== 预览 =====================
const previewGraphRef = ref(null)
const previewCourseId = ref('')
const previewSearch = ref('')
const previewStats = ref(null)
const drawerVisible = ref(false)
const drawerNode = ref(null)

function onPreviewCourseChange() {
  previewStats.value = null
}
function refreshPreview() {
  previewGraphRef.value?.refresh()
}
function fitPreview() {
  previewGraphRef.value?.fitView()
}
function onNodeClick(node) {
  drawerNode.value = node
  drawerVisible.value = true
}

// ===================== 编辑 =====================
const editGraphRef = ref(null)
const editCourseId = ref('')
const editNodes = ref([])
const addNodeVisible = ref(false)
const addNodeForm = ref({ name: '', category: '概念', description: '' })
const addNodeLoading = ref(false)
const addEdgeVisible = ref(false)
const addEdgeForm = ref({ source: '', type: 'PRECEDES', target: '' })
const addEdgeLoading = ref(false)
const edgeDialogVisible = ref(false)
const clickedEdge = ref(null)
const deleteEdgeLoading = ref(false)

function onEditCourseChange() {
  editNodes.value = []
}

function onEditNodeClick(node) {
  // 记录当前节点列表，供新增关系下拉使用
  if (!editNodes.value.length) {
    collectNodes()
  }
  drawerNode.value = node
  drawerVisible.value = true
}

function onEditEdgeClick(edge) {
  if (!edge) {
    edgeDialogVisible.value = false
    return
  }
  clickedEdge.value = {
    ...edge,
    sourceLabel: editNodes.value.find((n) => n.id === edge.source)?.label || edge.source,
    targetLabel: editNodes.value.find((n) => n.id === edge.target)?.label || edge.target,
  }
  edgeDialogVisible.value = true
}

function collectNodes() {
  // 从图谱组件当前渲染的数据中收集节点（通过组件暴露的 loaded 数据不存，这里改为 API 拉取）
  api
    .getGraphV1(editCourseId.value, { limit: 800 })
    .then((data) => {
      editNodes.value = data.nodes || []
    })
    .catch(() => {})
}

function openAddNode() {
  if (!editCourseId.value) {
    ElMessage.warning('请先选择课程')
    return
  }
  addNodeForm.value = { name: '', category: '概念', description: '' }
  addNodeVisible.value = true
}

async function submitAddNode() {
  if (!addNodeForm.value.name.trim()) {
    ElMessage.warning('名称不能为空')
    return
  }
  addNodeLoading.value = true
  try {
    await api.createNode(editCourseId.value, {
      name: addNodeForm.value.name.trim(),
      category: addNodeForm.value.category,
      description: addNodeForm.value.description,
    })
    ElMessage.success('新增成功')
    addNodeVisible.value = false
    refreshEdit()
  } catch (e) {
    ElMessage.error(`新增失败：${e.message}（该接口后端尚未实现，见开发报告"后端待补接口"）`)
  } finally {
    addNodeLoading.value = false
  }
}

function openAddEdge() {
  if (!editCourseId.value) {
    ElMessage.warning('请先选择课程')
    return
  }
  if (!editNodes.value.length) collectNodes()
  addEdgeForm.value = { source: '', type: 'PRECEDES', target: '' }
  addEdgeVisible.value = true
}

async function submitAddEdge() {
  const { source, type, target } = addEdgeForm.value
  if (!source || !target) {
    ElMessage.warning('请选择源和目标知识点')
    return
  }
  if (source === target) {
    ElMessage.warning('源和目标不能相同')
    return
  }
  addEdgeLoading.value = true
  try {
    await api.createEdge(editCourseId.value, { source, type, target })
    ElMessage.success('关系创建成功')
    addEdgeVisible.value = false
    refreshEdit()
  } catch (e) {
    ElMessage.error(`创建失败：${e.message}（该接口后端尚未实现，见开发报告"后端待补接口"）`)
  } finally {
    addEdgeLoading.value = false
  }
}

async function removeEdge() {
  if (!clickedEdge.value) return
  try {
    await ElMessageBox.confirm('确定删除该关系吗？', '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  deleteEdgeLoading.value = true
  try {
    await api.deleteEdge(editCourseId.value, {
      source: clickedEdge.value.sourceLabel || clickedEdge.value.source,
      target: clickedEdge.value.targetLabel || clickedEdge.value.target,
      type: clickedEdge.value.type,
    })
    ElMessage.success('关系已删除')
    edgeDialogVisible.value = false
    refreshEdit()
  } catch (e) {
    ElMessage.error(`删除失败：${e.message}（该接口后端尚未实现，见开发报告"后端待补接口"）`)
  } finally {
    deleteEdgeLoading.value = false
  }
}

function refreshEdit() {
  editNodes.value = []
  editGraphRef.value?.refresh()
}

function afterNodeChanged() {
  drawerVisible.value = false
  refreshEdit()
}

function edgeLabel(edge) {
  return edgeTypeLabel(edge?.type, edge?.label)
}
</script>

<style scoped>
.page-header {
  margin-bottom: 8px;
}
.page-title {
  margin: 0 0 4px;
  color: #303133;
}
.page-desc {
  margin: 0 0 12px;
  color: #909399;
  font-size: 13px;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.stats-text {
  color: #909399;
  font-size: 13px;
}
.edit-hint {
  margin-left: auto;
}
.graph-card {
  height: 620px;
}
.graph-card :deep(.el-card__body) {
  height: 100%;
  padding: 12px;
}
.result-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.raw-json {
  background: #f5f7fa;
  border-radius: 6px;
  padding: 12px;
  font-size: 12px;
  max-height: 360px;
  overflow: auto;
}
.uploading-alert {
  margin-top: 8px;
  max-width: 520px;
}
</style>
