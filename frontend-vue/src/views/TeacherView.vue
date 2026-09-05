<template>
  <div>
    <div class="page-header">
      <h2 class="page-title">课程管理</h2>
      <p class="page-desc">管理课程、课程文档、知识图谱与教学监测</p>
    </div>

    <el-tabs v-model="activeTab" @tab-change="onTabChange">
      <!-- ===================== Tab 0：课程列表 ===================== -->
      <el-tab-pane name="courses">
        <template #label><span class="tab-label"><el-icon><Notebook /></el-icon>课程管理</span></template>

        <div class="course-toolbar">
          <span class="stats-text">共 {{ store.courses.length }} 门课程</span>
          <el-button type="primary" :icon="Plus" @click="openCreateCourse">新建课程</el-button>
        </div>

        <div v-loading="store.isLoading" class="course-grid-wrap">
          <el-empty
            v-if="!store.isLoading && !store.courses.length"
            description="暂无课程，点击「新建课程」开始"
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
              <div class="course-desc" :title="c.description">
                {{ c.course_code ? `【${c.course_code}】` : '' }}{{ c.description || '暂无课程简介' }}
              </div>

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
                  <el-button size="small" type="primary" plain :icon="Files" @click="manageDocuments(c)">管理文档</el-button>
                  <el-button size="small" type="danger" plain :icon="Delete" @click="deleteCourse(c)">删除</el-button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <!-- ===================== Tab 1：课程文档 ===================== -->
      <el-tab-pane name="documents">
        <template #label><span class="tab-label"><el-icon><FolderOpened /></el-icon>课程文档</span></template>

        <div class="context-bar">
          <el-button text :icon="Back" @click="backToCourses">返回课程列表</el-button>
          <el-divider direction="vertical" />
          <span class="context-title">{{ currentCourseName }}</span>
          <el-tag v-if="currentCourse?.course_code" size="small" type="info">
            {{ currentCourse.course_code }}
          </el-tag>
        </div>

        <!-- 上传入口（上传到当前课程，不再自动建课） -->
        <el-card class="page-card upload-card">
          <template #header><span class="panel-header">上传文档</span></template>
          <el-form label-width="80px">
            <el-form-item label="选择文件">
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
                  <div class="el-upload__tip">上传到当前课程，支持 PDF / DOCX / TXT / MD，单文件不超过 50MB</div>
                </template>
              </el-upload>
            </el-form-item>
            <el-form-item>
              <el-button
                type="primary"
                :icon="Upload"
                :loading="uploading"
                :disabled="!selectedFile"
                @click="doUpload"
              >
                上传并构建知识图谱
              </el-button>
            </el-form-item>
          </el-form>
          <el-alert
            v-if="uploadResult"
            type="success"
            :closable="false"
            class="upload-result-alert"
            :title="`「${uploadResult.filename}」上传成功：知识点 ${uploadResult.entity_count ?? 0}，关系 ${uploadResult.relation_count ?? 0}`"
          />
          <el-alert
            v-if="uploadError"
            type="error"
            :closable="false"
            :title="uploadError"
            class="upload-result-alert"
          />
        </el-card>

        <!-- 文档列表 -->
        <el-card class="page-card">
          <template #header>
            <div class="doc-toolbar">
              <span class="panel-header">文档列表（{{ documents.length }}）</span>
              <el-button :icon="Refresh" circle size="small" @click="loadDocuments" />
            </div>
          </template>
          <el-table :data="documents" v-loading="documentsLoading">
            <el-table-column prop="file_name" label="文件名" min-width="180" show-overflow-tooltip />
            <el-table-column prop="file_type" label="类型" width="80" align="center" />
            <el-table-column label="大小" width="100" align="center">
              <template #default="{ row }">{{ fmtSize(row.file_size) }}</template>
            </el-table-column>
            <el-table-column label="解析状态" width="100" align="center">
              <template #default="{ row }">
                <el-tag :type="parseStatusType(row.parse_status)" size="small">{{ parseStatusText(row.parse_status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="抽取状态" width="100" align="center">
              <template #default="{ row }">
                <el-tag :type="extractStatusType(row.extract_status)" size="small">{{ extractStatusText(row.extract_status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="entity_count" label="知识点" width="80" align="center" />
            <el-table-column prop="relation_count" label="关系" width="70" align="center" />
            <el-table-column label="创建时间" width="150">
              <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="300" fixed="right">
              <template #default="{ row }">
                <el-button size="small" type="primary" plain :icon="View" @click="viewDocumentGraph(row)">查看图谱</el-button>
                <el-button size="small" type="warning" plain :icon="EditPen" @click="editDocumentGraph(row)">编辑</el-button>
                <el-button size="small" type="success" plain :icon="UserFilled" @click="monitorDocument(row)">监测</el-button>
                <el-button size="small" type="danger" plain :icon="Delete" @click="deleteDocument(row)">删除</el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无文档，请在上方上传" :image-size="80" />
            </template>
          </el-table>
        </el-card>
      </el-tab-pane>

      <!-- ===================== Tab 2：文档图谱预览 ===================== -->
      <el-tab-pane name="preview">
        <template #label><span class="tab-label"><el-icon><View /></el-icon>图谱预览</span></template>

        <div class="context-bar">
          <el-button text :icon="Back" @click="backToDocuments">返回文档列表</el-button>
          <el-divider direction="vertical" />
          <span class="context-title">{{ currentCourseName }}</span>
          <el-tag size="small" type="info">{{ currentDocumentName || '文档' }}</el-tag>
        </div>

        <el-card class="page-card">
          <div class="toolbar">
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
            :course-id="currentCourseId"
            :document-id="currentDocumentId"
            :search-text="previewSearch"
            @node-click="onNodeClick"
            @loaded="(s) => (previewStats = s)"
          />
        </el-card>
      </el-tab-pane>

      <!-- ===================== Tab 3：编辑图谱（三栏审核） ===================== -->
      <el-tab-pane name="edit">
        <template #label><span class="tab-label"><el-icon><EditPen /></el-icon>编辑图谱</span></template>

        <div class="context-bar">
          <el-button text :icon="Back" @click="backToDocuments">返回文档列表</el-button>
          <el-divider direction="vertical" />
          <span class="context-title">{{ currentCourseName }}</span>
          <el-tag size="small" type="info">{{ currentDocumentName || '文档' }}</el-tag>
        </div>

        <el-card class="page-card">
          <div class="toolbar">
            <el-button type="primary" :icon="Plus" @click="openAddNode">新增知识点</el-button>
            <el-button type="success" plain :icon="Connection" @click="openAddEdge">新增关系</el-button>
            <el-button :icon="Refresh" circle title="刷新" @click="refreshEdit" />
            <span v-if="currentCourseId" class="stats-text">节点 {{ editNodes.length }}</span>
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
                  v-if="!currentCourseId"
                  description="请先选择文档"
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
                :course-id="currentCourseId"
                :document-id="currentDocumentId"
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

      <!-- ===================== Tab 4：教学监测 ===================== -->
      <el-tab-pane name="monitor">
        <template #label><span class="tab-label"><el-icon><UserFilled /></el-icon>教学监测</span></template>

        <div class="context-bar">
          <el-button text :icon="Back" @click="backToDocuments">返回文档列表</el-button>
          <el-divider direction="vertical" />
          <span class="context-title">{{ currentCourseName }}</span>
          <el-tag size="small" type="info">{{ currentDocumentName || '文档' }}</el-tag>
        </div>

        <!-- 第一层：班级学习情况（真实学生数据） -->
        <el-card v-if="currentCourseId" class="page-card">
          <div class="chart-title-line">
            <el-icon><UserFilled /></el-icon> 班级学习情况
            <span class="class-note">（无选课关系表，班级学生 = 在该课程有学习记录的学生）</span>
          </div>

          <div v-loading="monitorLoading" class="class-summary">
            <div v-for="k in monitorKpis" :key="k.label" class="class-kpi">
              <div class="class-kpi-value" :style="{ color: k.color }">{{ k.value }}</div>
              <div class="class-kpi-label">{{ k.label }}</div>
            </div>
          </div>

          <el-empty
            v-if="!monitorLoading && monitorData && !monitorData.student_count"
            description="暂无学生学习记录（尚未有学生开始学习该课程）"
            :image-size="80"
          />
          <div v-else ref="progressDistRef" class="dist-chart"></div>
        </el-card>

        <!-- 第二层：学生学习情况表格 -->
        <el-card v-if="currentCourseId" class="page-card">
          <div class="chart-title-line"><el-icon><User /></el-icon> 学生学习情况</div>

          <div class="table-toolbar">
            <el-input
              v-model="monitorSearch"
              placeholder="搜索学生姓名 / 用户名"
              clearable
              :prefix-icon="Search"
              style="width: 240px"
            />
            <el-select v-model="monitorProgressFilter" placeholder="进度筛选" clearable style="width: 150px">
              <el-option
                v-for="b in progressBins"
                :key="b.value"
                :label="b.label"
                :value="b.value"
              />
            </el-select>
          </div>

          <el-table
            :data="pagedMonitorStudents"
            v-loading="monitorLoading"
            :default-sort="{ prop: 'progress', order: 'ascending' }"
            @sort-change="onMonitorSortChange"
          >
            <el-table-column prop="student_name" label="学生" sortable="custom" min-width="140">
              <template #default="{ row }">
                <span class="student-cell">
                  {{ row.student_name }}
                  <span v-if="row.username && row.username !== row.student_name" class="student-username">
                    @{{ row.username }}
                  </span>
                </span>
              </template>
            </el-table-column>
            <el-table-column prop="total_knowledge" label="知识点总数" width="110" align="center" />
            <el-table-column prop="mastered_count" label="已掌握" width="90" align="center" />
            <el-table-column prop="progress" label="学习进度" sortable="custom" min-width="150">
              <template #default="{ row }">
                <el-progress
                  :percentage="row.progress"
                  :color="progressColor(row.progress)"
                  :stroke-width="10"
                />
              </template>
            </el-table-column>
            <el-table-column label="当前学习" min-width="160">
              <template #default="{ row }">
                <span v-if="row.current_name" class="current-name">{{ row.current_name }}</span>
                <span v-else class="cell-empty">—</span>
              </template>
            </el-table-column>
            <el-table-column label="推荐学习" min-width="180">
              <template #default="{ row }">
                <template v-if="row.recommended && row.recommended.length">
                  <el-tag
                    v-for="r in row.recommended.slice(0, 3)"
                    :key="r.kp_id || r.name"
                    size="small"
                    class="rec-tag"
                  >{{ r.name }}</el-tag>
                </template>
                <span v-else class="cell-empty">—</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="90" align="center">
              <template #default="{ row }">
                <el-tag :type="statusType(row.progress)" size="small" effect="plain">
                  {{ statusText(row.progress) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="90" align="center" fixed="right">
              <template #default="{ row }">
                <el-button size="small" type="primary" link @click="openMonitorStudentDetail(row)">
                  查看
                </el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无学生学习记录" :image-size="80" />
            </template>
          </el-table>

          <el-pagination
            v-if="filteredMonitorStudents.length > monitorPageSize"
            class="table-pagination"
            v-model:current-page="monitorPage"
            v-model:page-size="monitorPageSize"
            :page-sizes="[10, 20, 50]"
            :total="filteredMonitorStudents.length"
            layout="total, sizes, prev, pager, next"
          />
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 预览 Tab 的节点详情抽屉（只读） -->
    <NodeDetailDrawer
      v-model="drawerVisible"
      :node="drawerNode"
      :course-id="currentCourseId"
      :document-id="currentDocumentId"
      @saved="afterNodeChanged"
      @deleted="afterNodeChanged"
    />

    <!-- 教学监测：学生详情抽屉 -->
    <el-drawer v-model="monitorDetailVisible" title="学生学习详情" direction="rtl" size="480px">
      <template v-if="monitorDetailStudent">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="学生">
            {{ monitorDetailStudent.student_name }}
            <span
              v-if="monitorDetailStudent.username && monitorDetailStudent.username !== monitorDetailStudent.student_name"
              class="detail-username"
            >@{{ monitorDetailStudent.username }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="当前课程">{{ currentCourseName }}</el-descriptions-item>
          <el-descriptions-item label="学习进度">
            <el-progress
              :percentage="monitorDetailStudent.progress"
              :color="progressColor(monitorDetailStudent.progress)"
              :stroke-width="10"
            />
          </el-descriptions-item>
          <el-descriptions-item label="已掌握数量">
            {{ monitorDetailStudent.mastered_count }} / {{ monitorDetailStudent.total_knowledge }}
          </el-descriptions-item>
          <el-descriptions-item label="未学习数量">{{ monitorDetailStudent.unmastered_count }}</el-descriptions-item>
          <el-descriptions-item label="当前学习知识点">
            {{ monitorDetailStudent.current_name || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="收藏数量">{{ monitorDetailStudent.favorite_count }}</el-descriptions-item>
        </el-descriptions>

        <el-divider content-position="left">推荐学习知识点</el-divider>
        <ul v-if="monitorDetailStudent.recommended && monitorDetailStudent.recommended.length" class="rec-list">
          <li v-for="r in monitorDetailStudent.recommended" :key="r.kp_id || r.name">
            <el-tag size="small" effect="plain">{{ r.category || '知识点' }}</el-tag>
            <span class="rec-name">{{ r.name }}</span>
            <div class="rec-reason">{{ r.reason }}</div>
          </li>
        </ul>
        <el-empty v-else description="暂无推荐" :image-size="60" />

        <div class="drawer-actions">
          <el-button type="primary" @click="goMonitorGraph">查看知识图谱</el-button>
        </div>
      </template>
    </el-drawer>

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
import { ref, watch, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  UploadFilled, Upload, View, EditPen, Refresh, FullScreen, Plus, Connection, ArrowRight, Search, SuccessFilled,
  Notebook, Document, Delete, DataAnalysis, Clock, User, UserFilled, Back, Files, FolderOpened,
} from '@element-plus/icons-vue'
import { api } from '../api'
import { useAppStore } from '../stores/app'
import GraphCanvas from '../components/GraphCanvas.vue'
import NodeDetailDrawer from '../components/NodeDetailDrawer.vue'
import { edgeTypeLabel, nodeTypeLabel, nodeColor } from '../utils/graphStyle'

const store = useAppStore()
const route = useRoute()
const router = useRouter()
const TEACHER_TABS = ['courses', 'documents', 'preview', 'edit', 'monitor']
const activeTab = ref(TEACHER_TABS.includes(route.query.tab) ? route.query.tab : 'courses')

// ===================== 当前上下文：课程 + 文档（教师端不搬 student learningContext） =====================
const currentCourseId = ref('')
const currentDocumentId = ref('')

const currentCourse = computed(() => store.courseById(currentCourseId.value) || null)
const currentCourseName = computed(() => {
  const c = currentCourse.value
  return c ? c.course_name : (currentCourseId.value ? `课程 #${currentCourseId.value}` : '')
})
const currentDocument = computed(() =>
  documents.value.find((d) => String(d.doc_id) === String(currentDocumentId.value)) || null
)
const currentDocumentName = computed(() => currentDocument.value?.file_name || '')

// ===================== 文档列表 / 上传 =====================
const documents = ref([])
const documentsLoading = ref(false)
const selectedFile = ref(null)
const uploading = ref(false)
const uploadResult = ref(null)
const uploadError = ref('')

function onFileChange(file) {
  selectedFile.value = file.raw
}
function onFileRemove() {
  selectedFile.value = null
}

async function loadDocuments() {
  if (!currentCourseId.value) {
    documents.value = []
    return
  }
  documentsLoading.value = true
  try {
    documents.value = await api.getDocuments(currentCourseId.value)
  } catch (e) {
    documents.value = []
    ElMessage.warning(`文档列表加载失败：${e.message}`)
  } finally {
    documentsLoading.value = false
  }
}

async function doUpload() {
  if (!selectedFile.value) return
  if (!currentCourseId.value) {
    ElMessage.warning('请先选择课程')
    return
  }
  uploading.value = true
  uploadResult.value = null
  uploadError.value = ''
  try {
    const formData = new FormData()
    formData.append('file', selectedFile.value)
    // 上传到当前课程：只传 course_id，不再传 course_name / 自动建课
    const result = await api.uploadCourse(formData, currentCourseId.value)
    uploadResult.value = result
    ElMessage.success('知识图谱构建完成')
    await loadDocuments()
  } catch (e) {
    uploadError.value = e.message || '上传失败'
    ElMessage.error(`上传失败：${e.message}`)
  } finally {
    uploading.value = false
  }
}

async function deleteDocument(doc) {
  try {
    await ElMessageBox.confirm(
      '删除该文档后，该文档对应的资源将被删除，是否继续？',
      '删除文档',
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
    await api.deleteDocument(doc.doc_id)
    ElMessage.success('文档已删除')
    await loadDocuments()
  } catch (e) {
    ElMessage.error(`删除失败：${e.message}`)
  }
}

// ===================== 预览 =====================
const previewGraphRef = ref(null)
const previewSearch = ref('')
const previewStats = ref(null)
const drawerVisible = ref(false)
const drawerNode = ref(null)

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
function afterNodeChanged() {
  drawerVisible.value = false
  refreshPreview()
}

// ===================== 编辑（三栏审核） =====================
const editGraphRef = ref(null)
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
  if (!currentCourseId.value) {
    editNodes.value = []
    return
  }
  try {
    const data = await api.getGraphV1(currentCourseId.value, currentDocumentId.value, { limit: 800 })
    editNodes.value = data.nodes || []
  } catch {
    editNodes.value = []
  }
}

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
    await api.updateNode(currentCourseId.value, currentDocumentId.value, selectedNode.value.id, {
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
    await api.deleteNode(currentCourseId.value, currentDocumentId.value, node.id)
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
    const res = await api.getPrerequisites(selectedNode.value.label, currentCourseId.value, currentDocumentId.value)
    prereqs.value = res.prerequisites || []
  } catch (e) {
    ElMessage.warning(`查询失败：${e.message}`)
  } finally {
    prereqLoading.value = false
    prereqLoaded.value = true
  }
}

function openAddNode() {
  if (!currentCourseId.value) {
    ElMessage.warning('请先选择文档')
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
    await api.createNode(currentCourseId.value, currentDocumentId.value, {
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
  if (!currentCourseId.value) {
    ElMessage.warning('请先选择文档')
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
    await api.createEdge(currentCourseId.value, currentDocumentId.value, { source, type, target })
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
    await api.deleteEdge(currentCourseId.value, currentDocumentId.value, clickedEdge.value.id)
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

function edgeLabel(edge) {
  return edgeTypeLabel(edge?.type, edge?.label)
}

// ===================== 课程管理 =====================
const createCourseVisible = ref(false)
const createCourseForm = ref({ name: '', description: '' })
const createCourseLoading = ref(false)

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
    if (String(currentCourseId.value) === id) {
      currentCourseId.value = ''
      currentDocumentId.value = ''
    }
  } catch (e) {
    ElMessage.error(`删除失败：${e.message}`)
  }
}

// ===================== 教学监测 =====================
const monitorData = ref(null)
const monitorLoading = ref(false)
const monitorSearch = ref('')
const monitorProgressFilter = ref('')
const monitorPage = ref(1)
const monitorPageSize = ref(10)
const monitorSort = ref({ prop: 'progress', order: 'ascending' })
const monitorDetailVisible = ref(false)
const monitorDetailStudent = ref(null)
const progressDistRef = ref(null)
let progressDistChart = null

const progressBins = [
  { value: '0', label: '0–20%' },
  { value: '20', label: '20–40%' },
  { value: '40', label: '40–60%' },
  { value: '60', label: '60–80%' },
  { value: '80', label: '80–100%' },
]

const monitorKpis = computed(() => {
  const d = monitorData.value
  return [
    { label: '已开始学习人数', value: d?.student_count ?? 0, color: '#409eff' },
    { label: '平均学习进度', value: (d?.avg_progress ?? 0) + '%', color: '#e6a23c' },
    { label: '课程知识点总数', value: d?.total_knowledge ?? 0, color: '#67c23a' },
  ]
})

const filteredMonitorStudents = computed(() => {
  let list = monitorData.value?.students || []
  const kw = monitorSearch.value.trim().toLowerCase()
  if (kw) {
    list = list.filter(
      (s) =>
        (s.student_name || '').toLowerCase().includes(kw) ||
        (s.username || '').toLowerCase().includes(kw)
    )
  }
  if (monitorProgressFilter.value !== '') {
    const min = Number(monitorProgressFilter.value)
    list = list.filter((s) =>
      min === 80 ? s.progress >= 80 : s.progress >= min && s.progress < min + 20
    )
  }
  const { prop, order } = monitorSort.value
  const dir = order === 'descending' ? -1 : 1
  return [...list].sort((a, b) => {
    const av = prop === 'student_name' ? a.student_name : a[prop]
    const bv = prop === 'student_name' ? b.student_name : b[prop]
    if (av === bv) return 0
    return (av > bv ? 1 : -1) * dir
  })
})

const pagedMonitorStudents = computed(() => {
  const start = (monitorPage.value - 1) * monitorPageSize.value
  return filteredMonitorStudents.value.slice(start, start + monitorPageSize.value)
})

function onMonitorSortChange({ prop, order }) {
  monitorSort.value = { prop: prop || 'progress', order: order || 'ascending' }
}

async function loadMonitorData() {
  if (!currentCourseId.value) {
    monitorData.value = null
    return
  }
  monitorLoading.value = true
  try {
    monitorData.value = await api.getTeacherStudentsProgress(currentCourseId.value)
    if (activeTab.value === 'monitor') {
      await nextTick()
      renderProgressDist()
    }
  } catch (e) {
    ElMessage.warning(`班级学习情况加载失败：${e.message}`)
    monitorData.value = null
  } finally {
    monitorLoading.value = false
  }
}

function openMonitorStudentDetail(row) {
  monitorDetailStudent.value = row
  monitorDetailVisible.value = true
}

function goMonitorGraph() {
  if (!currentCourseId.value || !currentDocumentId.value) return
  router.push({
    path: '/teacher',
    query: { tab: 'preview', course_id: currentCourseId.value, document_id: currentDocumentId.value },
  })
}

function progressColor(p) {
  if (p >= 80) return '#67c23a'
  if (p >= 40) return '#e6a23c'
  return '#f56c6c'
}

function statusText(p) {
  if (p >= 100) return '已完成'
  if (p > 0) return '进行中'
  return '未开始'
}

function statusType(p) {
  if (p >= 100) return 'success'
  if (p > 0) return 'primary'
  return 'info'
}

function renderProgressDist() {
  if (!progressDistRef.value) {
    progressDistChart?.dispose()
    progressDistChart = null
    return
  }
  if (progressDistChart && progressDistChart.getDom() !== progressDistRef.value) {
    progressDistChart.dispose()
    progressDistChart = null
  }
  if (!progressDistChart) progressDistChart = echarts.init(progressDistRef.value)
  const students = monitorData.value?.students || []
  const labels = progressBins.map((b) => b.label)
  const counts = progressBins.map((b) => {
    const min = Number(b.value)
    return students.filter((s) =>
      min === 80 ? s.progress >= 80 : s.progress >= min && s.progress < min + 20
    ).length
  })
  const option = {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: '3%', right: '4%', bottom: '8%', top: '10%', containLabel: true },
    xAxis: {
      type: 'category',
      data: labels,
      name: '学习进度',
      nameTextStyle: { color: '#909399', fontSize: 11 },
      axisLabel: { color: '#606266', fontSize: 11 },
      axisLine: { lineStyle: { color: '#dcdfe6' } },
    },
    yAxis: {
      type: 'value',
      name: '学生数',
      nameTextStyle: { color: '#909399', fontSize: 11 },
      minInterval: 1,
      axisLabel: { color: '#909399' },
      splitLine: { lineStyle: { color: '#f0f0f0', type: 'dashed' } },
    },
    series: [{
      type: 'bar',
      barWidth: '50%',
      itemStyle: {
        borderRadius: [6, 6, 0, 0],
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: '#409eff' },
          { offset: 1, color: '#79bbff' },
        ]),
      },
      data: counts,
    }],
  }
  if (counts.every((v) => v === 0)) option.graphic = {
    type: 'text', left: 'center', top: 'middle',
    style: { text: '暂无数据', fill: '#909399', fontSize: 14 },
  }
  progressDistChart.setOption(option)
}

function handleMonitorResize() {
  progressDistChart?.resize()
}

// ===================== 导航 =====================
function goCourses() {
  router.push({ path: '/teacher', query: { tab: 'courses' } })
}
function manageDocuments(course) {
  // 入参是课程卡片对象 c（非 id）：必须取 course.course_id，否则 String(对象) 会得到 "[object Object]"
  router.push({ path: '/teacher', query: { tab: 'documents', course_id: String(course.course_id) } })
}
function backToCourses() {
  goCourses()
}
function backToDocuments() {
  if (currentCourseId.value) {
    router.push({ path: '/teacher', query: { tab: 'documents', course_id: currentCourseId.value } })
  } else {
    goCourses()
  }
}
function viewDocumentGraph(doc) {
  router.push({
    path: '/teacher',
    query: { tab: 'preview', course_id: String(doc.course_id), document_id: String(doc.doc_id) },
  })
}
function editDocumentGraph(doc) {
  router.push({
    path: '/teacher',
    query: { tab: 'edit', course_id: String(doc.course_id), document_id: String(doc.doc_id) },
  })
}
function monitorDocument(doc) {
  router.push({
    path: '/teacher',
    query: { tab: 'monitor', course_id: String(doc.course_id), document_id: String(doc.doc_id) },
  })
}

// 用户直接点击 Tab 头：同步路由（缺失参数的守卫统一由 route watcher 处理）
function onTabChange(name) {
  if (name === 'courses') {
    router.replace({ path: '/teacher', query: { tab: 'courses' } })
  } else if (name === 'documents') {
    router.replace({
      path: '/teacher',
      query: { tab: 'documents', course_id: currentCourseId.value || undefined },
    })
  } else {
    router.replace({
      path: '/teacher',
      query: {
        tab: name,
        course_id: currentCourseId.value || undefined,
        document_id: currentDocumentId.value || undefined,
      },
    })
  }
}

// ===================== 路由同步 + 守卫 =====================
// 深链 /teacher?tab=xxx&course_id=yyy&document_id=zzz：同步上下文并守卫缺失参数
watch(
  () => [route.query.tab, route.query.course_id, route.query.document_id],
  ([tab, cid, did]) => {
    if (!tab) {
      activeTab.value = 'courses'
      currentCourseId.value = ''
      currentDocumentId.value = ''
      return
    }
    if (!TEACHER_TABS.includes(tab)) {
      activeTab.value = 'courses'
      return
    }
    if (['preview', 'edit', 'monitor'].includes(tab)) {
      if (!cid) {
        ElMessage.warning('请先选择课程')
        router.replace({ path: '/teacher', query: { tab: 'courses' } })
        return
      }
      if (!did) {
        ElMessage.warning('请先选择一个文档')
        router.replace({ path: '/teacher', query: { tab: 'documents', course_id: String(cid) } })
        return
      }
      currentCourseId.value = String(cid)
      currentDocumentId.value = String(did)
    } else if (tab === 'documents') {
      if (!cid) {
        ElMessage.warning('请先选择课程')
        router.replace({ path: '/teacher', query: { tab: 'courses' } })
        return
      }
      currentCourseId.value = String(cid)
      currentDocumentId.value = ''
    } else {
      currentCourseId.value = ''
      currentDocumentId.value = ''
    }
    activeTab.value = tab
  },
  { immediate: true }
)

// 课程上下文变化 → 加载文档列表
watch(currentCourseId, (cid) => {
  if (cid) loadDocuments()
  else documents.value = []
})

// Tab 变化 → 按需加载数据
watch(activeTab, (tab) => {
  if (tab === 'courses') store.fetchCourses(true).catch(() => {})
  if (tab === 'documents' && currentCourseId.value) loadDocuments()
  if (tab === 'preview') {
    drawerVisible.value = false
    drawerNode.value = null
  }
  if (tab === 'edit') {
    selectedNode.value = null
    prereqs.value = []
    prereqLoaded.value = false
    if (currentCourseId.value) loadEditNodes()
  }
  if (tab === 'monitor' && currentCourseId.value) loadMonitorData()
})

onMounted(() => {
  store.fetchCourses(true).catch(() => {})
  window.addEventListener('resize', handleMonitorResize)
  // 深链直达时 activeTab 初始即等于目标 tab，activeTab watcher 不会触发，需在此补一次加载
  if (activeTab.value === 'documents' && currentCourseId.value) loadDocuments()
  if (activeTab.value === 'edit' && currentCourseId.value) loadEditNodes()
  if (activeTab.value === 'monitor' && currentCourseId.value) loadMonitorData()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleMonitorResize)
  progressDistChart?.dispose()
})

// ===================== 工具函数 =====================
function fmtTime(t) {
  return t ? String(t).slice(0, 16) : '—'
}
function fmtSize(bytes) {
  if (bytes == null) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
function parseStatusType(s) {
  return s === 'PARSED' ? 'success' : s === 'FAILED' ? 'danger' : s === 'PARSING' ? 'warning' : 'info'
}
function parseStatusText(s) {
  return { UPLOADED: '待解析', PARSING: '解析中', PARSED: '已解析', FAILED: '解析失败' }[s] || s
}
function extractStatusType(s) {
  return s === 'COMPLETED' ? 'success' : s === 'FAILED' ? 'danger' : s === 'EXTRACTING' ? 'warning' : 'info'
}
function extractStatusText(s) {
  return { PENDING: '待抽取', EXTRACTING: '抽取中', COMPLETED: '已完成', FAILED: '抽取失败' }[s] || s
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

/* ===== 上下文栏（课程/文档） ===== */
.context-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  padding: 8px 12px;
  background: #fafbfc;
  border: 1px solid #f0f2f5;
  border-radius: 8px;
}
.context-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

/* ===== 文档列表 / 上传 ===== */
.upload-card {
  margin-bottom: 12px;
}
.upload-result-alert {
  margin-top: 12px;
}
.doc-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
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

/* ===== 教学监测 ===== */
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
.dist-chart {
  height: 220px;
}
.table-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.student-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}
.student-username {
  font-size: 12px;
  color: #909399;
  font-weight: 400;
}
.current-name {
  color: #409eff;
}
.cell-empty {
  color: #c0c4cc;
}
.rec-tag {
  margin: 2px 4px 2px 0;
}
.table-pagination {
  margin-top: 12px;
  justify-content: flex-end;
}
.detail-username {
  font-size: 12px;
  color: #909399;
  margin-left: 6px;
}
.rec-list {
  list-style: none;
  padding: 0;
  margin: 12px 0 0;
}
.rec-list li {
  padding: 8px 0;
  border-bottom: 1px dashed #e4e7ed;
}
.rec-name {
  font-weight: 600;
  margin-left: 8px;
}
.rec-reason {
  color: #909399;
  font-size: 12px;
  margin-top: 4px;
}
.drawer-actions {
  margin-top: 16px;
}
</style>
