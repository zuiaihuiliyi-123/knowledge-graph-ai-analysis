<template>
  <div>
    <div class="page-header">
      <h2 class="page-title">教师工作台</h2>
      <p class="page-desc">上传课程资料 → 自动生成知识图谱 → 审核与手动修正</p>
    </div>

    <el-tabs v-model="activeTab">
      <!-- ===================== Tab 0：课程管理 ===================== -->
      <el-tab-pane name="courses">
        <template #label><span class="tab-label"><el-icon><Notebook /></el-icon>课程管理</span></template>

        <div class="course-toolbar">
          <span class="stats-text">共 {{ store.courses.length }} 门课程</span>
          <el-button type="primary" :icon="Plus" @click="openCreateCourse">新建课程</el-button>
        </div>

        <div v-loading="store.isLoading" class="course-grid-wrap">
          <el-empty
            v-if="!store.isLoading && !store.courses.length"
            description="暂无课程，点击「新建课程」或上传文档自动创建"
          >
            <el-button type="primary" :icon="Plus" @click="openCreateCourse">新建课程</el-button>
          </el-empty>

          <div v-else class="course-grid">
            <div v-for="c in store.courses" :key="c.course_id" class="course-card">
              <div class="course-card-head">
                <span class="course-name" :title="c.course_name">{{ c.course_name }}</span>
                <el-tag size="small" :type="c.status === 1 ? 'success' : 'info'">
                  {{ c.status === 1 ? '正常' : '停用' }}
                </el-tag>
              </div>
              <div class="course-desc" :title="c.description">{{ c.description || '暂无课程简介' }}</div>

              <div class="course-stats">
                <div class="course-stat">
                  <el-icon color="#409eff"><Document /></el-icon>
                  <span class="stat-num">{{ c.document_count ?? 0 }}</span>
                  <span class="stat-label">文档</span>
                </div>
                <div class="course-stat">
                  <el-icon color="#e6a23c"><DataAnalysis /></el-icon>
                  <span class="stat-num">{{ c.node_count ?? 0 }}</span>
                  <span class="stat-label">知识点</span>
                </div>
                <div class="course-stat">
                  <el-icon color="#67c23a"><Connection /></el-icon>
                  <span class="stat-num">{{ c.edge_count ?? 0 }}</span>
                  <span class="stat-label">关系</span>
                </div>
              </div>

              <div class="course-card-foot">
                <span class="course-updated"><el-icon><Clock /></el-icon> 更新于 {{ fmtTime(c.updated_at) }}</span>
                <div class="course-actions">
                  <el-button size="small" type="primary" plain :icon="View" @click="viewCourse(c)">查看</el-button>
                  <el-button size="small" type="warning" plain :icon="EditPen" @click="editCourse(c)">编辑</el-button>
                  <el-button size="small" type="danger" plain :icon="Delete" @click="deleteCourse(c)">删除</el-button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <!-- ===================== Tab 1：文档上传 ===================== -->
      <el-tab-pane name="upload">
        <template #label><span class="tab-label"><el-icon><Upload /></el-icon>文档上传</span></template>

        <el-card class="page-card">
          <el-form label-width="90px">
            <el-form-item label="课程名称">
              <el-input
                v-model="courseName"
                placeholder="例如：数据结构（留空则使用文件名）"
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
                  <div class="el-upload__tip">支持 PDF / DOCX / TXT / MD，单文件不超过 50MB</div>
                </template>
              </el-upload>
            </el-form-item>
            <el-form-item>
              <el-button
                type="primary"
                size="large"
                :icon="Upload"
                :loading="uploading"
                :disabled="!selectedFile"
                @click="doUpload"
              >
                开始分析并构建知识图谱
              </el-button>
            </el-form-item>
          </el-form>
        </el-card>

        <!-- 构建流程 Stepper（上传成功后展示处理阶段） -->
        <el-card v-if="uploadStage !== 'idle'" class="page-card">
          <el-steps :active="stepActive" finish-status="success" align-center>
            <el-step title="上传文件" description="课程资料已接收" />
            <el-step title="解析文档" description="提取文本内容" />
            <el-step title="知识抽取" description="LLM 识别知识点" />
            <el-step title="构建关系" description="识别前置 / 包含等关系" />
            <el-step title="生成图谱" description="写入知识图谱" />
          </el-steps>

          <el-alert
            v-if="uploadStage === 'uploading'"
            type="info"
            :closable="false"
            title="正在解析文档并调用大模型提取知识…"
            description="长文档会分块逐次调用 LLM，整个过程可能需要 1-5 分钟，请勿关闭页面。"
            class="uploading-alert"
          />
          <el-alert
            v-else-if="uploadStage === 'error'"
            type="error"
            :closable="false"
            :title="uploadError"
            description="请检查后端服务是否在线、API Key 是否有效，然后重新上传。"
            class="uploading-alert"
          />
        </el-card>

        <!-- 构建结果 -->
        <el-card v-if="uploadResult" class="page-card result-card">
          <template #header>
            <div class="result-header">
              <span class="result-title"><el-icon color="#67c23a"><SuccessFilled /></el-icon> 知识图谱构建完成</span>
              <el-button size="small" type="primary" :icon="ArrowRight" @click="goReview">
                前往图谱审核
              </el-button>
            </div>
          </template>
          <el-descriptions :column="2" border>
            <el-descriptions-item label="课程 ID">
              <code>{{ uploadResult.course_id }}</code>
            </el-descriptions-item>
            <el-descriptions-item label="文档名称">{{ uploadResult.filename }}</el-descriptions-item>
            <el-descriptions-item label="知识点数量">{{ uploadResult.entity_count ?? 0 }}</el-descriptions-item>
            <el-descriptions-item label="关系数量">{{ uploadResult.relation_count ?? 0 }}</el-descriptions-item>
            <el-descriptions-item label="解析状态">{{ uploadResult.parse_status }}</el-descriptions-item>
            <el-descriptions-item label="抽取状态">{{ uploadResult.extract_status }}</el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-tab-pane>

      <!-- ===================== Tab 2：图谱预览 ===================== -->
      <el-tab-pane name="preview">
        <template #label><span class="tab-label"><el-icon><View /></el-icon>图谱预览</span></template>
        <el-card class="page-card">
          <div class="toolbar">
            <CourseSelector v-model="previewCourseId" @change="onPreviewCourseChange" />
            <el-input
              v-model="previewSearch"
              placeholder="搜索知识点…"
              clearable
              :prefix-icon="Search"
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

      <!-- ===================== Tab 3：编辑图谱（三栏审核） ===================== -->
      <el-tab-pane name="edit">
        <template #label><span class="tab-label"><el-icon><EditPen /></el-icon>编辑图谱</span></template>

        <el-card class="page-card">
          <div class="toolbar">
            <CourseSelector v-model="editCourseId" @change="onEditCourseChange" />
            <el-button type="primary" :icon="Plus" @click="openAddNode">新增知识点</el-button>
            <el-button type="success" plain :icon="Connection" @click="openAddEdge">新增关系</el-button>
            <el-button :icon="Refresh" circle title="刷新" @click="refreshEdit" />
            <span v-if="editCourseId" class="stats-text">节点 {{ editNodes.length }}</span>
          </div>
        </el-card>

        <!-- 三栏：知识点列表 | 图谱 | 详情面板 -->
        <el-row :gutter="12" class="workspace">
          <el-col :span="5">
            <el-card class="panel-card">
              <template #header>
                <div class="panel-header">知识点列表（{{ filteredEditNodes.length }}）</div>
              </template>
              <el-input
                v-model="nodeListSearch"
                placeholder="搜索知识点…"
                clearable
                :prefix-icon="Search"
                size="small"
                class="list-search"
              />
              <div class="panel-scroll">
                <el-empty
                  v-if="!editCourseId"
                  description="请先选择课程"
                  :image-size="60"
                />
                <el-empty
                  v-else-if="!filteredEditNodes.length"
                  description="暂未生成知识点"
                  :image-size="60"
                />
                <div
                  v-for="n in filteredEditNodes"
                  :key="n.id"
                  class="node-item"
                  :class="{ active: selectedNode?.id === n.id }"
                  @click="selectNode(n)"
                >
                  <i class="node-dot" :style="{ background: nodeColor(n.type) }"></i>
                  <span class="node-item-label">{{ n.label }}</span>
                  <el-tag size="small" type="info" effect="plain">{{ nodeTypeLabel(n.type) }}</el-tag>
                </div>
              </div>
            </el-card>
          </el-col>

          <el-col :span="13">
            <el-card class="panel-card graph-panel">
              <GraphCanvas
                ref="editGraphRef"
                :course-id="editCourseId"
                :editable="true"
                @node-click="onEditNodeClick"
                @edge-click="onEditEdgeClick"
              />
            </el-card>
          </el-col>

          <el-col :span="6">
            <el-card class="panel-card">
              <template #header>
                <div class="panel-header">知识点详情</div>
              </template>
              <div class="panel-scroll">
                <el-empty
                  v-if="!selectedNode"
                  description="点击左侧列表或图谱节点查看详情"
                  :image-size="60"
                />
                <template v-else>
                  <div class="detail-title">
                    <span class="detail-name">{{ selectedNode.label }}</span>
                    <el-tag size="small">{{ nodeTypeLabel(selectedNode.type) }}</el-tag>
                    <el-tag v-if="selectedNode.properties?.is_manual" size="small" type="warning" effect="plain">人工</el-tag>
                  </div>
                  <el-descriptions :column="1" border size="small">
                    <el-descriptions-item label="描述">
                      {{ selectedNode.description || '暂无描述' }}
                    </el-descriptions-item>
                    <el-descriptions-item v-if="selectedNode.properties?.confidence != null" label="置信度">
                      {{ selectedNode.properties.confidence }}
                    </el-descriptions-item>
                  </el-descriptions>

                  <el-divider content-position="left">编辑</el-divider>
                  <el-form label-position="top" size="small">
                    <el-form-item label="名称">
                      <el-input v-model="editForm.name" />
                    </el-form-item>
                    <el-form-item label="类别">
                      <el-select v-model="editForm.category" style="width: 100%">
                        <el-option label="概念" value="概念" />
                        <el-option label="定理" value="定理" />
                        <el-option label="公式" value="公式" />
                        <el-option label="方法" value="方法" />
                      </el-select>
                    </el-form-item>
                    <el-form-item label="描述">
                      <el-input v-model="editForm.description" type="textarea" :rows="3" />
                    </el-form-item>
                  </el-form>
                  <div class="detail-actions">
                    <el-button type="primary" size="small" :loading="savingNode" @click="saveNode">保存</el-button>
                    <el-button type="danger" size="small" plain :loading="deletingNode" @click="deleteNode">删除</el-button>
                  </div>

                  <el-divider content-position="left">前置知识</el-divider>
                  <el-button size="small" :loading="prereqLoading" @click="loadPrereqs">查询前置知识</el-button>
                  <el-empty
                    v-if="!prereqLoading && prereqLoaded && !prereqs.length"
                    description="未查询到前置知识"
                    :image-size="50"
                  />
                  <ul class="prereq-list">
                    <li v-for="p in prereqs" :key="p.name">
                      <el-tag size="small" type="info">{{ p.depth }} 级前置</el-tag>
                      <span class="prereq-name">{{ p.name }}</span>
                      <div class="prereq-desc">{{ p.description }}</div>
                    </li>
                  </ul>
                </template>
              </div>
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>
    </el-tabs>

    <!-- 预览 Tab 的节点详情抽屉（只读） -->
    <NodeDetailDrawer
      v-model="drawerVisible"
      :node="drawerNode"
      :course-id="previewCourseId"
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
            <el-option v-for="n in editNodes" :key="n.id" :label="n.label" :value="n.id" />
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
            <el-option v-for="n in editNodes" :key="n.id" :label="n.label" :value="n.id" />
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
          <el-descriptions-item label="源">{{ clickedEdge.sourceLabel || clickedEdge.source }}</el-descriptions-item>
          <el-descriptions-item label="关系">
            <el-tag size="small">{{ edgeLabel(clickedEdge) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="目标">{{ clickedEdge.targetLabel || clickedEdge.target }}</el-descriptions-item>
        </el-descriptions>
      </template>
      <template #footer>
        <el-button @click="edgeDialogVisible = false">关闭</el-button>
        <el-button type="danger" :loading="deleteEdgeLoading" @click="removeEdge">删除关系</el-button>
      </template>
    </el-dialog>

    <!-- 新建课程对话框 -->
    <el-dialog v-model="createCourseVisible" title="新建课程" width="440px">
      <el-form label-width="80px">
        <el-form-item label="课程名称" required>
          <el-input
            v-model="createCourseForm.name"
            placeholder="例如：数据结构"
            @keyup.enter="submitCreateCourse"
          />
        </el-form-item>
        <el-form-item label="课程简介">
          <el-input
            v-model="createCourseForm.description"
            type="textarea"
            :rows="3"
            placeholder="可选"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createCourseVisible = false">取消</el-button>
        <el-button type="primary" :loading="createCourseLoading" @click="submitCreateCourse">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, watch, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  UploadFilled, Upload, View, EditPen, Refresh, FullScreen, Plus, Connection, ArrowRight, Search, SuccessFilled,
  Notebook, Document, Delete, DataAnalysis, Clock,
} from '@element-plus/icons-vue'
import { api } from '../api'
import { useAppStore } from '../stores/app'
import GraphCanvas from '../components/GraphCanvas.vue'
import CourseSelector from '../components/CourseSelector.vue'
import NodeDetailDrawer from '../components/NodeDetailDrawer.vue'
import { edgeTypeLabel, nodeTypeLabel, nodeColor } from '../utils/graphStyle'

const store = useAppStore()
const route = useRoute()
const TEACHER_TABS = ['courses', 'upload', 'preview', 'edit']
const activeTab = ref(TEACHER_TABS.includes(route.query.tab) ? route.query.tab : 'courses')

// 从侧边栏菜单带 tab 参数跳转时同步切换
watch(
  () => route.query.tab,
  (tab) => {
    if (TEACHER_TABS.includes(tab)) activeTab.value = tab
  }
)

// 从教师端数据总览「查看知识图谱」深链进入时，预选课程并切到预览 Tab
watch(
  () => route.query.course_id,
  (cid) => {
    if (cid) {
      previewCourseId.value = String(cid)
      if (activeTab.value !== 'preview') activeTab.value = 'preview'
    }
  },
  { immediate: true }
)

// ===================== 上传 =====================
const courseName = ref('')
const selectedFile = ref(null)
const uploading = ref(false)
const uploadResult = ref(null)
const uploadError = ref('')
const uploadStage = ref('idle') // idle | uploading | done | error

const stepActive = computed(() => {
  if (uploadStage.value === 'done') return 5
  return 1 // 上传完成，正在解析/抽取/构建
})

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
  uploadError.value = ''
  uploadStage.value = 'uploading'
  try {
    const formData = new FormData()
    formData.append('file', selectedFile.value)
    // course_name 后端必填：为空时用文件名（去扩展名）兜底
    const name = courseName.value.trim() || selectedFile.value.name.replace(/\.[^.]+$/, '')
    const result = await api.uploadCourse(formData, name)
    uploadResult.value = result
    uploadStage.value = 'done'
    // 上传会自动在后端建课程，刷新课程列表并选中新课程
    store.fetchCourses(true).catch(() => {})
    store.currentCourseId = String(result.course_id)
    ElMessage.success('知识图谱构建完成')
  } catch (e) {
    uploadStage.value = 'error'
    uploadError.value = e.message || '上传失败'
    ElMessage.error(`上传失败：${e.message}`)
  } finally {
    uploading.value = false
  }
}

function goReview() {
  if (uploadResult.value?.course_id) {
    editCourseId.value = uploadResult.value.course_id
  }
  activeTab.value = 'edit'
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

// ===================== 编辑（三栏审核） =====================
const editGraphRef = ref(null)
const editCourseId = ref('')
const editNodes = ref([])
const nodeListSearch = ref('')
const selectedNode = ref(null)
const editForm = ref({ name: '', category: '概念', description: '' })
const savingNode = ref(false)
const deletingNode = ref(false)
const prereqs = ref([])
const prereqLoading = ref(false)
const prereqLoaded = ref(false)

const addNodeVisible = ref(false)
const addNodeForm = ref({ name: '', category: '概念', description: '' })
const addNodeLoading = ref(false)
const addEdgeVisible = ref(false)
const addEdgeForm = ref({ source: '', type: 'PRECEDES', target: '' })
const addEdgeLoading = ref(false)
const edgeDialogVisible = ref(false)
const clickedEdge = ref(null)
const deleteEdgeLoading = ref(false)

const filteredEditNodes = computed(() => {
  const kw = nodeListSearch.value.trim().toLowerCase()
  if (!kw) return editNodes.value
  return editNodes.value.filter(
    (n) =>
      (n.label || '').toLowerCase().includes(kw) ||
      (n.description || '').toLowerCase().includes(kw)
  )
})

async function loadEditNodes() {
  if (!editCourseId.value) {
    editNodes.value = []
    return
  }
  try {
    const data = await api.getGraphV1(editCourseId.value, { limit: 800 })
    editNodes.value = data.nodes || []
  } catch {
    editNodes.value = []
  }
}

watch(editCourseId, () => {
  selectedNode.value = null
  prereqs.value = []
  prereqLoaded.value = false
  loadEditNodes()
})

// 选中节点后同步编辑表单
watch(selectedNode, (n) => {
  prereqs.value = []
  prereqLoaded.value = false
  if (n) {
    editForm.value = {
      name: n.label || '',
      category: n.properties?.category || '概念',
      description: n.description || '',
    }
  }
})

function onEditCourseChange() {
  selectedNode.value = null
  editNodes.value = []
}

function selectNode(node) {
  selectedNode.value = node
  editGraphRef.value?.focusNode(node.id)
}

function onEditNodeClick(node) {
  selectedNode.value = node
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

async function saveNode() {
  if (!selectedNode.value) return
  if (!editForm.value.name.trim()) {
    ElMessage.warning('名称不能为空')
    return
  }
  savingNode.value = true
  try {
    await api.updateNode(editCourseId.value, selectedNode.value.id, {
      name: editForm.value.name.trim(),
      category: editForm.value.category,
      description: editForm.value.description,
    })
    ElMessage.success('保存成功')
    selectedNode.value = null
    refreshEdit()
  } catch (e) {
    ElMessage.error(`保存失败：${e.message}`)
  } finally {
    savingNode.value = false
  }
}

async function deleteNode() {
  const node = selectedNode.value
  if (!node) return
  try {
    await ElMessageBox.confirm(
      `确定删除知识点「${node.label}」及其全部关系吗？`,
      '删除确认',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger',
      }
    )
  } catch {
    return
  }
  deletingNode.value = true
  try {
    await api.deleteNode(editCourseId.value, node.id)
    ElMessage.success('已删除')
    selectedNode.value = null
    refreshEdit()
  } catch (e) {
    ElMessage.error(`删除失败：${e.message}`)
  } finally {
    deletingNode.value = false
  }
}

async function loadPrereqs() {
  if (!selectedNode.value) return
  prereqLoading.value = true
  try {
    const res = await api.getPrerequisites(selectedNode.value.label, editCourseId.value)
    prereqs.value = res.prerequisites || []
  } catch (e) {
    ElMessage.warning(`查询失败：${e.message}`)
  } finally {
    prereqLoading.value = false
    prereqLoaded.value = true
  }
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
    ElMessage.error(`新增失败：${e.message}`)
  } finally {
    addNodeLoading.value = false
  }
}

function openAddEdge() {
  if (!editCourseId.value) {
    ElMessage.warning('请先选择课程')
    return
  }
  if (!editNodes.value.length) loadEditNodes()
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
    ElMessage.error(`创建失败：${e.message}`)
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
      confirmButtonClass: 'el-button--danger',
    })
  } catch {
    return
  }
  deleteEdgeLoading.value = true
  try {
    await api.deleteEdge(editCourseId.value, clickedEdge.value.id)
    ElMessage.success('关系已删除')
    edgeDialogVisible.value = false
    refreshEdit()
  } catch (e) {
    ElMessage.error(`删除失败：${e.message}`)
  } finally {
    deleteEdgeLoading.value = false
  }
}

function refreshEdit() {
  editGraphRef.value?.refresh()
  loadEditNodes()
}

function afterNodeChanged() {
  drawerVisible.value = false
  refreshPreview()
}

function edgeLabel(edge) {
  return edgeTypeLabel(edge?.type, edge?.label)
}

// ===================== 课程管理 =====================
const createCourseVisible = ref(false)
const createCourseForm = ref({ name: '', description: '' })
const createCourseLoading = ref(false)

// 进入课程管理 Tab 时强制刷新，保证节点/关系数最新
watch(activeTab, (tab) => {
  if (tab === 'courses') store.fetchCourses(true).catch(() => {})
})

// 课程管理为默认 Tab（无 CourseSelector 触发加载），组件挂载时确保课程列表已加载
onMounted(() => {
  store.fetchCourses(true).catch(() => {})
})

function openCreateCourse() {
  createCourseForm.value = { name: '', description: '' }
  createCourseVisible.value = true
}

async function submitCreateCourse() {
  const name = createCourseForm.value.name.trim()
  if (!name) {
    ElMessage.warning('课程名称不能为空')
    return
  }
  createCourseLoading.value = true
  try {
    await store.createCourse(name, { description: createCourseForm.value.description.trim() })
    createCourseVisible.value = false
    ElMessage.success(`课程「${name}」创建成功`)
  } catch (e) {
    ElMessage.error(`创建失败：${e.message}`)
  } finally {
    createCourseLoading.value = false
  }
}

function viewCourse(c) {
  previewCourseId.value = String(c.course_id)
  activeTab.value = 'preview'
}

function editCourse(c) {
  editCourseId.value = String(c.course_id)
  activeTab.value = 'edit'
}

async function deleteCourse(c) {
  const id = String(c.course_id)
  try {
    await ElMessageBox.confirm(
      `确定删除课程「${c.course_name}」吗？将同时删除该课程下的文档、图谱数据及学习记录，此操作不可恢复。`,
      '删除课程',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger',
      }
    )
  } catch {
    return
  }
  try {
    await store.deleteCourse(id)
    ElMessage.success('课程已删除')
    if (String(previewCourseId.value) === id) previewCourseId.value = ''
    if (String(editCourseId.value) === id) editCourseId.value = ''
  } catch (e) {
    ElMessage.error(`删除失败：${e.message}`)
  }
}

function fmtTime(t) {
  return t ? String(t).slice(0, 16) : '—'
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
.result-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}
.result-card {
  border-left: 4px solid #67c23a;
}
.uploading-alert {
  margin-top: 16px;
}

/* ===== 三栏审核工作区 ===== */
.workspace {
  margin-top: 0;
}
.panel-card {
  height: 640px;
  display: flex;
  flex-direction: column;
}
.panel-card :deep(.el-card__body) {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 12px;
}
.panel-header {
  font-weight: 600;
  font-size: 14px;
}
.panel-scroll {
  flex: 1;
  overflow-y: auto;
  margin-top: 10px;
}
.list-search {
  margin-bottom: 8px;
}
.node-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
}
.node-item:hover {
  background: #f5f9ff;
}
.node-item.active {
  background: #ecf5ff;
}
.node-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}
.node-item-label {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 图谱面板：body 铺满 */
.graph-panel :deep(.el-card__body) {
  padding: 0;
}

/* 详情面板 */
.detail-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.detail-name {
  font-size: 16px;
  font-weight: 700;
  color: #303133;
}
.detail-actions {
  display: flex;
  gap: 12px;
  margin-top: 12px;
}
.prereq-list {
  list-style: none;
  padding: 0;
  margin: 12px 0 0;
}
.prereq-list li {
  padding: 8px 0;
  border-bottom: 1px dashed #e4e7ed;
}
.prereq-name {
  font-weight: 600;
  margin-left: 8px;
}
.prereq-desc {
  color: #909399;
  font-size: 12px;
  margin-top: 4px;
}

/* ===== 课程管理 ===== */
.course-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.course-grid-wrap {
  min-height: 200px;
}
.course-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}
.course-card {
  background: #fff;
  border: 1px solid var(--border-light);
  border-radius: 10px;
  padding: 16px;
  box-shadow: var(--shadow-card);
  display: flex;
  flex-direction: column;
  gap: 12px;
  transition: transform 0.2s, box-shadow 0.2s;
}
.course-card:hover {
  transform: translateY(-3px);
  box-shadow: var(--shadow-hover);
}
.course-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.course-name {
  font-size: 15px;
  font-weight: 700;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}
.course-desc {
  font-size: 12px;
  color: #909399;
  line-height: 1.5;
  min-height: 36px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.course-stats {
  display: flex;
  gap: 8px;
}
.course-stat {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px;
  border-radius: 8px;
  background: #fafbfc;
  border: 1px solid #f0f2f5;
}
.stat-num {
  font-size: 16px;
  font-weight: 700;
  color: #303133;
  font-family: 'DIN Alternate', 'Helvetica Neue', sans-serif;
}
.stat-label {
  font-size: 11px;
  color: #909399;
}
.course-card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  border-top: 1px solid #f0f2f5;
  padding-top: 10px;
}
.course-updated {
  font-size: 12px;
  color: #c0c4cc;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.course-actions {
  display: flex;
  gap: 4px;
}
</style>
