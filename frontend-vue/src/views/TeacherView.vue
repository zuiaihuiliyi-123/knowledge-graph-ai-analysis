<template>
  <div>
    <PageHeader title="课程管理" desc="管理课程、课程文档、知识图谱、题库与教学监测" />

    <el-tabs v-model="activeTab" class="main-view-tabs" @tab-change="onTabChange">
      <!-- ===================== Tab 0：课程列表 ===================== -->
      <!-- 课程中心改造：卡片视觉与「课程中心 → 我的课程」统一为同一个组件的两种配置，
           进入课程后仍复用本页既有的 文档 / 图谱管理 / 监测 等 Tab -->
      <el-tab-pane name="courses">
        <template #label><span class="tab-label"><el-icon><Notebook /></el-icon>课程管理</span></template>

        <div class="course-toolbar">
          <span class="stats-text">共 {{ store.courses.length }} 门课程</span>
          <div class="toolbar-actions">
            <el-button type="primary" :icon="Plus" @click="openCreateCourse">新建课程</el-button>
            <el-button :icon="Promotion" @click="openInviteForCurrent">邀请学生</el-button>
          </div>
        </div>

        <MyCourseGrid
          :courses="store.courses"
          role="teacher"
          :loading="store.isLoading"
          show-create
          empty-text="暂无课程，点击「新建课程」开始"
          @create="openCreateCourse"
          @enter="manageDocuments"
          @members="goMembers"
          @leave="leaveCourse"
          @settings="openSettings"
          @delete="deleteCourse"
        />
      </el-tab-pane>

      <!-- ===================== Tab 1：课程文档 ===================== -->
      <el-tab-pane name="documents">
        <template #label><span class="tab-label"><el-icon><FolderOpened /></el-icon>课程文档</span></template>

        <!-- 未选择课程：页内引导选择（不再弹窗强制跳回课程列表） -->
        <el-card v-if="!currentCourseId" class="page-card">
          <el-empty description="请先选择要管理文档的课程">
            <div class="doc-course-pick">
              <el-select
                v-model="currentCourseId"
                placeholder="选择课程"
                filterable
                clearable
                style="width: 260px"
                @change="syncDocumentsRoute"
              >
                <el-option
                  v-for="c in store.courses"
                  :key="c.course_id"
                  :label="c.course_name"
                  :value="String(c.course_id)"
                />
              </el-select>
              <el-button :icon="Notebook" @click="goCourses">前往课程管理</el-button>
              <el-button type="primary" :icon="Plus" @click="openCreateCourse">新建课程</el-button>
            </div>
            <p v-if="!store.courses.length" class="doc-course-pick-tip">暂无课程，可先点击「新建课程」创建</p>
          </el-empty>
        </el-card>

        <template v-else>
        <div class="context-bar">
          <el-button text :icon="Back" @click="backToCourses">返回课程列表</el-button>
          <el-divider direction="vertical" />
          <span class="context-title">{{ currentCourseName }}</span>
          <el-tag v-if="currentCourse?.course_code" size="small" type="info">
            {{ currentCourse.course_code }}
          </el-tag>
          <el-select
            v-model="currentCourseId"
            class="course-switcher"
            size="small"
            filterable
            clearable
            placeholder="切换课程"
            @change="syncDocumentsRoute"
          >
            <el-option
              v-for="c in store.courses"
              :key="c.course_id"
              :label="c.course_name"
              :value="String(c.course_id)"
            />
          </el-select>
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
              <el-button :icon="Refresh" circle size="small" aria-label="刷新文档列表" @click="loadDocuments" />
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
                <el-tag :type="parseStatusType(row.parse_status)" size="small">{{ parseStatusText(row.parse_status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="抽取状态" width="100" align="center">
              <template #default="{ row }">
                <el-tag :type="extractStatusType(row.extract_status)" size="small">{{ extractStatusText(row.extract_status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="生成进度" min-width="180">
              <template #default="{ row }">
                <el-progress
                  :percentage="docProgress(row)"
                  :status="docProgressStatus(row)"
                  :stroke-width="10"
                  :striped="isDocInFlight(row)"
                  :striped-flow="isDocInFlight(row)"
                />
              </template>
            </el-table-column>
            <el-table-column prop="entity_count" label="知识点" width="80" align="center" />
            <el-table-column prop="relation_count" label="关系" width="70" align="center" />
            <el-table-column label="创建时间" width="150">
              <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="392" fixed="right">
              <template #default="{ row }">
                <el-button size="small" type="primary" :icon="Reading" :disabled="row.is_placeholder" @click="readDocument(row)">在线阅读</el-button>
                <el-button size="small" :icon="Download" plain :disabled="row.is_placeholder" @click="downloadDocument(row)">下载</el-button>
                <el-button size="small" type="success" plain :icon="UserFilled" :disabled="row.is_placeholder" @click="monitorDocument(row)">监测</el-button>
                <el-button size="small" type="danger" plain :icon="Delete" :disabled="row.is_placeholder" @click="deleteDocument(row)">删除</el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无文档，请在上方上传" :image-size="80" />
            </template>
          </el-table>
        </el-card>
        </template>
      </el-tab-pane>

      <!-- ===================== Tab 2：学生管理（课程中心新增） ===================== -->
      <el-tab-pane name="members">
        <template #label><span class="tab-label"><el-icon><UserFilled /></el-icon>学生管理</span></template>

        <el-card v-if="!currentCourseId" class="page-card">
          <el-empty description="请先选择要管理学生的课程">
            <div class="doc-course-pick">
              <el-select
                v-model="currentCourseId"
                placeholder="选择课程"
                filterable
                clearable
                style="width: 260px"
                @change="syncDocumentsRoute"
              >
                <el-option
                  v-for="c in store.courses"
                  :key="c.course_id"
                  :label="c.course_name"
                  :value="String(c.course_id)"
                />
              </el-select>
              <el-button :icon="Notebook" @click="goCourses">前往课程管理</el-button>
            </div>
          </el-empty>
        </el-card>

        <template v-else>
          <div class="context-bar">
            <el-button text :icon="Back" @click="backToCourses">返回课程列表</el-button>
            <el-divider direction="vertical" />
            <span class="context-title">{{ currentCourseName }}</span>
            <el-select
              v-model="currentCourseId"
              class="course-switcher"
              size="small"
              filterable
              clearable
              placeholder="切换课程"
              @change="syncDocumentsRoute"
            >
              <el-option
                v-for="c in store.courses"
                :key="c.course_id"
                :label="c.course_name"
                :value="String(c.course_id)"
              />
            </el-select>
            <div class="context-actions">
              <el-button size="small" :icon="Promotion" @click="openInviteForCurrent">邀请学生</el-button>
            </div>
          </div>

          <el-card class="page-card">
            <CourseMembersPanel :course-id="currentCourseId" :teacher-id="currentCourse?.teacher_id" @refresh="reloadCoursesSilently" />
          </el-card>
        </template>
      </el-tab-pane>

      <!-- ===================== Tab 3：图谱管理（查看 + 就地编辑整合） ===================== -->
      <el-tab-pane name="preview">
        <template #label><span class="tab-label"><el-icon><View /></el-icon>图谱管理</span></template>

        <!-- 未选择课程/文档：页内引导选择（不再弹窗强制跳回） -->
        <el-card v-if="!currentCourseId || !currentDocumentId" class="page-card">
          <el-empty description="请先选择要管理图谱的课程和文档">
            <div class="doc-course-pick">
              <el-select v-model="currentCourseId" placeholder="选择课程" filterable clearable style="width: 220px" @change="onContextCourseChange">
                <el-option v-for="c in store.courses" :key="c.course_id" :label="c.course_name" :value="String(c.course_id)" />
              </el-select>
              <el-select v-model="currentDocumentId" placeholder="选择文档" filterable clearable style="width: 240px" :disabled="!currentCourseId" @change="onContextDocChange">
                <el-option v-for="d in documents" :key="d.doc_id" :label="d.file_name" :value="String(d.doc_id)" />
              </el-select>
              <el-button :icon="Notebook" @click="goCourses">前往课程管理</el-button>
            </div>
            <p v-if="currentCourseId && !documents.length" class="doc-course-pick-tip">该课程暂无文档，请先到「课程文档」上传</p>
          </el-empty>
        </el-card>

        <template v-else>
        <div class="context-bar">
          <el-button text :icon="Back" @click="backToDocuments">返回文档列表</el-button>
          <el-divider direction="vertical" />
          <span class="context-title">{{ currentCourseName }}</span>
          <el-tag size="small" type="info">{{ currentDocumentName || '文档' }}</el-tag>
          <el-tag size="small" :type="graphEditMode ? 'warning' : 'success'" effect="plain">{{ graphEditMode ? '编辑模式' : '查看模式' }}</el-tag>
          <!-- 右侧动作组：这两个动作都要离开当前视图，与左边「返回 / 课程 / 文档 / 模式」那组信息标签分开 -->
          <div class="ctx-right">
            <!-- 换一篇文档看它的图谱。与学生端「图谱浏览」上方的「切换资料」是同一个选择器组件，
                 只是教师这里换的是「要管理图谱的文档」，所以用教师自己的措辞。
                 只依赖 currentDocumentId（不依赖文档对象）：文档列表万一没取回来，也还能换一篇。 -->
            <el-button
              v-if="currentDocumentId"
              :icon="Switch"
              @click="docSwitchVisible = true"
            >切换文档</el-button>
            <!-- 在线阅读本文档：与学生端「图谱浏览」上的「在线阅读」同一入口（同一个阅读器整页）。
                 查看/编辑两种模式都显示——改知识点时常常需要回原文核对一句话的措辞。
                 文档元信息还没加载出来时不显示，避免点进去只有空壳。 -->
            <el-button
              v-if="currentDocumentId && currentDocument"
              type="primary"
              plain
              :icon="Reading"
              @click="readCurrentDocument"
            >在线阅读文档</el-button>
          </div>
        </div>

        <!-- 查看模式 -->
        <template v-if="!graphEditMode">
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
            <el-button type="warning" plain :icon="EditPen" @click="enterGraphEdit">编辑图谱</el-button>
          </div>
        </el-card>
        <el-card class="page-card graph-card">
          <!--
            :key 必需，不是顺手加的：切换文档/课程时本页是【就地】换作用域，画布组件不重建。
            而 GraphCanvas 内部的 rawNodes/rawEdges 是普通 let 变量（非响应式），
            依赖它们的 computed（visibleNodeIds / visibleEdges / neighborMap…）在属性变化后
            不会失效——于是新节点被旧 id 集合过滤成空，旧边却仍被画出来，G6 直接抛
            「Unknown element type of id」。按作用域换 key 强制重建，等于回到「换上下文即重新挂载」
            的既有语义（打开阅读器里的 PDF 也是同样的写法）。
            根治要改 GraphCanvas 的数据源为响应式，那是独立一件事，不在本次范围内。
          -->
          <GraphCanvas
            :key="`preview-${currentCourseId}-${currentDocumentId}`"
            ref="previewGraphRef"
            :course-id="currentCourseId"
            :document-id="currentDocumentId"
            :search-text="previewSearch"
            @node-click="onNodeClick"
            @loaded="(s) => (previewStats = s)"
          />
        </el-card>
        </template>

        <!-- 编辑模式（保留原编辑图谱三栏审核界面，就地切换） -->
        <template v-else>
        <el-card class="page-card">
          <div class="toolbar">
            <el-button :icon="View" @click="exitGraphEdit">返回查看</el-button>
            <el-button type="primary" :icon="Plus" @click="openAddNode">新增知识点</el-button>
            <el-button type="success" plain :icon="Connection" @click="openAddEdge">新增关系</el-button>
            <el-button :icon="Refresh" circle title="刷新" @click="refreshEdit" />
            <span v-if="currentCourseId" class="stats-text">节点 {{ editNodes.length }}</span>
          </div>
        </el-card>
        <!-- 三栏：知识点列表 | 图谱 | 详情面板 -->
        <el-row :gutter="12" class="workspace">
          <el-col :xs="24" :sm="5">
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

          <el-col :xs="24" :sm="13">
            <el-card class="panel-card graph-panel">
              <!-- :key 同上：编辑模式换文档也是就地换作用域，不重建会留下上一个文档的陈旧数据 -->
              <GraphCanvas
                :key="`edit-${currentCourseId}-${currentDocumentId}`"
                ref="editGraphRef"
                :course-id="currentCourseId"
                :document-id="currentDocumentId"
                :editable="true"
                @node-click="onEditNodeClick"
                @edge-click="onEditEdgeClick"
              />
            </el-card>
          </el-col>

          <el-col :xs="24" :sm="6">
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
        </template>
        </template>
      </el-tab-pane>

      <!-- ===================== Tab 4：教学监测 ===================== -->
      <el-tab-pane name="monitor">
        <template #label><span class="tab-label"><el-icon><UserFilled /></el-icon>教学监测</span></template>

        <!-- 未选择课程：页内引导选择（不再弹窗强制跳回） -->
        <el-card v-if="!currentCourseId" class="page-card">
          <el-empty description="请先选择要查看教学监测的课程">
            <div class="doc-course-pick">
              <el-select v-model="currentCourseId" placeholder="选择课程" filterable clearable style="width: 260px" @change="onContextCourseChange">
                <el-option v-for="c in store.courses" :key="c.course_id" :label="c.course_name" :value="String(c.course_id)" />
              </el-select>
              <el-button :icon="Notebook" @click="goCourses">前往课程管理</el-button>
            </div>
          </el-empty>
        </el-card>

        <template v-else>
        <div class="context-bar">
          <el-button text :icon="Back" @click="backToDocuments">返回文档列表</el-button>
          <el-divider direction="vertical" />
          <span class="context-title">{{ currentCourseName }}</span>
          <el-tag v-if="currentDocumentName" size="small" type="info">{{ currentDocumentName }}</el-tag>
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
        </template>
      </el-tab-pane>

      <!-- ===================== Tab 5：题库管理（Scope A：单选/多选/判断，答案仅教师可见） ===================== -->
      <el-tab-pane name="questions">
        <template #label><span class="tab-label"><el-icon><Collection /></el-icon>题库管理</span></template>

        <!-- 未选择课程：页内引导选择（与教学监测一致，不弹窗强制跳回） -->
        <el-card v-if="!currentCourseId" class="page-card">
          <el-empty description="请先选择要管理题库的课程">
            <div class="doc-course-pick">
              <el-select v-model="currentCourseId" placeholder="选择课程" filterable clearable style="width: 260px" @change="onContextCourseChange">
                <el-option v-for="c in store.courses" :key="c.course_id" :label="c.course_name" :value="String(c.course_id)" />
              </el-select>
              <el-button :icon="Notebook" @click="goCourses">前往课程管理</el-button>
            </div>
          </el-empty>
        </el-card>

        <template v-else>
          <!-- 第一层：题库总览（题量 / 题型分布 / 作答正确率 / 收藏） -->
          <el-card class="page-card">
            <div class="chart-title-line">
              <el-icon><Collection /></el-icon> 题库总览
              <span class="class-note">（{{ currentCourseName }}）</span>
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
              <!-- F3：一键直达「主观题批改」页（标签栏 tab 较多时该页可能被溢出隐藏，故给显式入口） -->
              <div
                class="class-kpi"
                :class="{ highlight: (questionStats?.pending_count ?? 0) > 0 }"
                style="cursor: pointer"
                title="点击进入「主观题批改」"
                @click="gotoGrading"
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
                @click="createQuestionForKp(kp)"
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
              <el-select v-model="questionDocFilter" placeholder="全部文档（含课程通用题）" clearable style="width: 210px" @change="loadQuestionsTab">
                <el-option v-for="d in documents" :key="d.doc_id" :label="d.file_name" :value="String(d.doc_id)" />
              </el-select>
              <el-select v-model="questionFilters.q_type" placeholder="全部题型" clearable style="width: 130px" @change="loadQuestionsTab">
                <el-option v-for="t in QUESTION_TYPES" :key="t.value" :label="t.label" :value="t.value" />
              </el-select>
              <el-select v-model="questionFilters.is_active" placeholder="全部状态" clearable style="width: 130px" @change="loadQuestionsTab">
                <el-option label="启用中" value="1" />
                <el-option label="已停用" value="0" />
              </el-select>
              <el-input
                v-model="questionFilters.keyword"
                placeholder="搜索题干"
                clearable
                :prefix-icon="Search"
                style="width: 190px"
                @keyup.enter="loadQuestionsTab"
                @clear="loadQuestionsTab"
              />
              <el-button :icon="Refresh" @click="loadQuestionsTab">刷新</el-button>
              <el-button type="primary" :icon="Plus" @click="openQuestionForm(null)">新增题目</el-button>
              <el-button :icon="MagicStick" @click="openAutoLabel">批量自动标注</el-button>
              <el-button :icon="Upload" @click="openImportWizard">从文档导入</el-button>
              <el-button :icon="Star" @click="openQuestionFavorites">题目收藏情况</el-button>
            </div>

            <el-table :data="questionList" v-loading="questionsLoading" row-key="question_id">
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
              @current-change="loadQuestions"
              @size-change="loadQuestionsTab"
            />
          </el-card>

          <!-- 新增 / 编辑题目 -->
          <el-dialog
            v-model="questionFormVisible"
            :title="questionEditingId ? '编辑题目' : '新增题目'"
            width="720px"
            :close-on-click-modal="false"
          >
            <el-form label-width="96px">
              <el-form-item label="题型">
                <el-radio-group v-model="questionForm.q_type" :disabled="!!questionEditingId">
                  <el-radio v-for="t in QUESTION_TYPES" :key="t.value" :value="t.value">{{ t.label }}</el-radio>
                </el-radio-group>
              </el-form-item>
              <el-form-item label="题干">
                <el-input v-model="questionForm.stem" type="textarea" :rows="3" maxlength="1000" show-word-limit placeholder="请输入题干" />
              </el-form-item>

              <template v-if="questionForm.q_type === 'SINGLE' || questionForm.q_type === 'MULTI'">
                <el-form-item v-for="(opt, idx) in questionForm.options" :key="opt.key" :label="`选项 ${opt.key}`">
                  <div class="opt-row">
                    <el-input v-model="opt.text" placeholder="选项内容" />
                    <el-button :icon="Delete" text type="danger" :disabled="questionForm.options.length <= 2" @click="removeQuestionOption(idx)" />
                  </div>
                </el-form-item>
                <el-form-item label=" ">
                  <el-button :icon="Plus" size="small" :disabled="questionForm.options.length >= 8" @click="addQuestionOption">添加选项</el-button>
                </el-form-item>
                <el-form-item label="正确答案">
                  <el-radio-group v-if="questionForm.q_type === 'SINGLE'" v-model="questionForm.singleAnswer">
                    <el-radio v-for="o in questionForm.options" :key="o.key" :value="o.key">{{ o.key }}</el-radio>
                  </el-radio-group>
                  <el-checkbox-group v-else v-model="questionForm.multiAnswer">
                    <el-checkbox v-for="o in questionForm.options" :key="o.key" :value="o.key">{{ o.key }}</el-checkbox>
                  </el-checkbox-group>
                </el-form-item>
              </template>

              <!-- 填空题：空位编辑器（每空必须有参考答案；学生提交后进入「批改」队列） -->
              <template v-else-if="questionForm.q_type === 'FILL'">
                <el-alert
                  class="coverage-alert"
                  type="info"
                  :closable="false"
                  show-icon
                  title="填空题不自动判分"
                  description="每空必须填写参考答案。学生提交后进入「批改」队列，由你逐题给分（可给部分分 0~100）。"
                />
                <el-form-item v-for="(b, idx) in questionForm.blanks" :key="b.key" :label="b.label">
                  <div class="opt-row">
                    <el-input v-model="b.answer" placeholder="参考答案（必填）" />
                    <el-input v-model="b.hint" placeholder="空位提示（可选，如：单位 kg）" />
                    <el-input-number v-model="b.score" :min="0" :max="100" :step="5" controls-position="right" style="width: 120px" />
                    <el-button :icon="Delete" text type="danger" :disabled="questionForm.blanks.length <= 1" @click="removeQuestionBlank(idx)" />
                  </div>
                </el-form-item>
                <el-form-item label=" ">
                  <el-button :icon="Plus" size="small" :disabled="questionForm.blanks.length >= 20" @click="addQuestionBlank">添加空位</el-button>
                  <span class="class-note">（分值仅用于教师批改时参考）</span>
                </el-form-item>
              </template>

              <!-- 解答题：参考答案（必填；批改前不会下发给学生） -->
              <el-form-item v-else-if="questionForm.q_type === 'ESSAY'" label="参考答案">
                <el-input
                  v-model="questionForm.essayAnswer"
                  type="textarea"
                  :rows="4"
                  maxlength="4000"
                  show-word-limit
                  placeholder="必填：参考答案/解答要点。学生提交后进入「批改」队列，批改前不会下发给学生。"
                />
              </el-form-item>

              <el-form-item v-else label="正确答案">
                <el-radio-group v-model="questionForm.judgeAnswer">
                  <el-radio value="true">正确</el-radio>
                  <el-radio value="false">错误</el-radio>
                </el-radio-group>
              </el-form-item>

              <el-form-item label="解析">
                <el-input v-model="questionForm.analysis" type="textarea" :rows="2" maxlength="1000" placeholder="可选：答案解析（提交后展示给学生）" />
              </el-form-item>
              <el-form-item label="难度">
                <el-rate v-model="questionForm.difficulty" :max="5" />
              </el-form-item>
              <el-form-item label="所属文档">
                <el-select v-model="questionForm.document_id" placeholder="课程通用题（任意文档练习均可见）" clearable style="width: 100%">
                  <el-option v-for="d in documents" :key="d.doc_id" :label="d.file_name" :value="String(d.doc_id)" />
                </el-select>
              </el-form-item>
              <el-form-item label="关联知识点">
                <div class="opt-row">
                  <el-select v-model="questionForm.kp_id" placeholder="可选：关联图谱知识点（用于推荐练题）" clearable filterable style="flex: 1">
                    <el-option v-for="n in questionKpOptions" :key="n.id" :label="n.label" :value="n.id" />
                  </el-select>
                  <el-button
                    :loading="kpCandLoading"
                    :disabled="!questionEditingId"
                    @click="loadKpCandidates"
                  >自动标注</el-button>
                </div>
                <div v-if="!questionEditingId" class="class-note">（新增题目请先保存，再使用「自动标注」）</div>
              </el-form-item>
            </el-form>
            <template #footer>
              <el-button @click="questionFormVisible = false">取消</el-button>
              <el-button type="primary" :loading="questionFormLoading" @click="submitQuestionForm">保存</el-button>
            </template>
          </el-dialog>

          <!-- 知识点自动标注：单题候选（Scope C / P3，三层证据 + 融合打分） -->
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
                  <span v-if="row.sources.lexical" class="kp-src">字面 {{ row.sources.lexical }}</span>
                  <span v-if="row.sources.vector" class="kp-src">向量 {{ row.sources.vector }}</span>
                  <span v-if="row.sources.graph" class="kp-src">图谱 {{ row.sources.graph }}</span>
                  <span v-if="row.graph_only" class="kp-src">仅关系推断</span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="86" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" type="primary" @click="adoptKpCandidate(row)">采纳</el-button>
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

          <!-- 批量知识点自动标注（Scope C / P3） -->
          <el-dialog v-model="autoLabelVisible" title="批量知识点自动标注" width="880px">
            <div class="question-toolbar">
              <el-select v-model="autoLabelForm.document_id" placeholder="全部学习资料" clearable style="width: 200px">
                <el-option v-for="d in documents" :key="d.doc_id" :label="d.file_name" :value="String(d.doc_id)" />
              </el-select>
              <span class="class-note">每题候选数</span>
              <el-input-number v-model="autoLabelForm.top_k" :min="1" :max="10" controls-position="right" style="width: 110px" />
              <span class="class-note">写入阈值</span>
              <el-input-number
                v-model="autoLabelForm.apply_threshold"
                :min="0" :max="1" :step="0.05" :precision="2"
                controls-position="right" style="width: 130px"
              />
              <el-checkbox v-model="autoLabelForm.only_missing">只处理未挂知识点的题</el-checkbox>
            </div>
            <div class="opt-row" style="margin: 8px 0 12px">
              <el-button :loading="autoLabelLoading" @click="runAutoLabel(false)">仅预览（不写库）</el-button>
              <el-button type="primary" :loading="autoLabelLoading" @click="runAutoLabel(true)">预览并写入达阈值项</el-button>
              <span class="class-note">写入只影响「建议分数 ≥ 阈值」的题，且默认跳过已有知识点的题</span>
            </div>

            <el-alert
              v-if="autoLabelPreview"
              class="coverage-alert"
              type="info"
              :closable="false"
              show-icon
              :title="`扫描 ${autoLabelPreview.scanned} 道 · 有建议 ${autoLabelPreview.with_candidates} 道 · 已写入 ${autoLabelPreview.applied} 道`"
              :description="autoLabelMetaText"
            />

            <el-table v-if="autoLabelPreview" :data="autoLabelPreview.items" max-height="380" size="small">
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

          <!-- 试题文档导入向导（P2：文档 → 解析预览 → 导入为暂存题） -->
          <el-dialog v-model="importVisible" title="从文档导入试题" width="1000px" :close-on-click-modal="false">
            <div class="question-toolbar">
              <el-select v-model="importDocId" placeholder="选择要解析的课程文档" clearable filterable style="width: 300px">
                <el-option v-for="d in documents" :key="d.doc_id" :label="d.file_name" :value="String(d.doc_id)" />
              </el-select>
              <span class="class-note">最多解析</span>
              <el-input-number v-model="importMax" :min="1" :max="500" controls-position="right" style="width: 120px" />
              <el-button type="primary" :loading="importLoading" :disabled="!importDocId" @click="runImportPreview">解析预览</el-button>
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
              @selection-change="onImportSelection"
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
                <template #default="{ row }"><span class="cell-empty">{{ importWarnText(row) || '—' }}</span></template>
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

            <el-collapse
              v-if="importReport && importReport.skipped && importReport.skipped.length"
              style="margin-top: 8px"
            >
              <el-collapse-item :title="`未导入 ${importReport.skipped.length} 题的拒绝原因`">
                <div v-for="(s, i) in importReport.skipped" :key="i" class="class-note">
                  第 {{ s.number || (s.index + 1) }} 题：{{ s.reason }}
                </div>
              </el-collapse-item>
            </el-collapse>

            <template #footer>
              <el-button @click="importVisible = false">关闭</el-button>
              <el-button :disabled="!importSelection.length" :loading="importLoading" @click="submitImport(false)">
                导入为暂存题（已选 {{ importSelection.length }}）
              </el-button>
              <el-button type="primary" :disabled="!importSelection.length" :loading="importLoading" @click="submitImport(true)">
                导入并直接启用
              </el-button>
            </template>
          </el-dialog>

          <!-- 题目收藏情况（哪些学生收藏了哪道题） -->
          <el-dialog v-model="questionFavVisible" title="题目收藏情况" width="660px">
            <el-table :data="questionFavs" v-loading="questionFavLoading" max-height="420">
              <el-table-column prop="student_name" label="学生" width="140" />
              <el-table-column prop="stem" label="题目" min-width="240" show-overflow-tooltip />
              <el-table-column prop="q_type_label" label="题型" width="90" />
              <el-table-column prop="created_at" label="收藏时间" width="150" />
              <template #empty><el-empty description="暂无学生收藏题目" :image-size="70" /></template>
            </el-table>
          </el-dialog>
        </template>
      </el-tab-pane>

      <!-- ============ Tab 6：主观题批改（Scope B：填空/解答提交不判分，教师批改后给分） ============ -->
      <el-tab-pane name="grading">
        <template #label>
          <span class="tab-label">
            <el-icon><EditPen /></el-icon>主观题批改
            <el-badge v-if="gradingSummary?.pending" :value="gradingSummary.pending" class="tab-badge" />
          </span>
        </template>

        <el-card v-if="!currentCourseId" class="page-card">
          <el-empty description="请先选择要批改的课程">
            <div class="doc-course-pick">
              <el-select v-model="currentCourseId" placeholder="选择课程" filterable clearable style="width: 260px" @change="onContextCourseChange">
                <el-option v-for="c in courses" :key="c.course_id" :label="c.course_name" :value="String(c.course_id)" />
              </el-select>
            </div>
          </el-empty>
        </el-card>

        <template v-else>
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
              <el-select v-model="gradingDocFilter" placeholder="全部学习资料" clearable style="width: 200px" @change="loadGrading">
                <el-option v-for="d in documents" :key="d.doc_id" :label="d.file_name" :value="String(d.doc_id)" />
              </el-select>
              <!-- G2：按学生筛选（选项来自批改台已加载的记录，无需额外请求） -->
              <el-select v-model="gradingStudentFilter" placeholder="全部学生" clearable filterable style="width: 160px" @change="loadGrading">
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
          <el-button type="primary" :disabled="!currentDocumentId" @click="goMonitorGraph">查看知识图谱</el-button>
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

    <!-- 新建课程对话框（课程中心改造：独立组件，含分类/院系/加入方式/是否公开） -->
    <CreateCourseDialog v-model="createCourseVisible" @created="onCourseCreated" />

    <!-- 课程设置（含加课码刷新与删除课程） -->
    <CourseSettingsDialog
      v-model="settingsVisible"
      :course="settingsCourse"
      @saved="reloadCoursesSilently"
      @deleted="onCourseDeleted"
    />

    <!-- 切换文档：复用学生端「切换资料」的同一个选择器（课程 + 文档），
         教师端不据此重设任何学习上下文，只用来换图谱/阅读器的作用域 -->
    <el-dialog v-model="docSwitchVisible" title="切换文档" width="560px" :close-on-click-modal="false">
      <CourseDocumentSelector
        :initial-course-id="currentCourseId"
        :initial-document-id="currentDocumentId"
        @confirm="applyDocSwitch"
        @cancel="docSwitchVisible = false"
      />
    </el-dialog>

    <!-- 邀请学生 / 协作教师 -->
    <InviteDialog v-model="inviteVisible" :course-id="inviteCourseId" />
  </div>
</template>

<script setup>
import { ref, watch, computed, onMounted, onBeforeUnmount, nextTick, reactive } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  UploadFilled, Upload, View, EditPen, Refresh, FullScreen, Plus, Connection, ArrowRight, Search, SuccessFilled,
  Notebook, Document, Delete, DataAnalysis, Clock, User, UserFilled, Back, Files, FolderOpened,
  Reading, Download, Promotion, Collection, Star, Aim, WarningFilled, CircleCheckFilled,
  DocumentChecked, MagicStick, Switch,
} from '@element-plus/icons-vue'
import { api } from '../api'
import { fetchDocumentBuffer } from '../utils/documentContent'
import { useAppStore } from '../stores/app'
import PageHeader from '../components/PageHeader.vue'
import GraphCanvas from '../components/GraphCanvas.vue'
import NodeDetailDrawer from '../components/NodeDetailDrawer.vue'
import MyCourseGrid from '../components/course/MyCourseGrid.vue'
import CreateCourseDialog from '../components/course/CreateCourseDialog.vue'
import CourseDocumentSelector from '../components/CourseDocumentSelector.vue'
import CourseSettingsDialog from '../components/course/CourseSettingsDialog.vue'
import CourseMembersPanel from '../components/course/CourseMembersPanel.vue'
import InviteDialog from '../components/course/InviteDialog.vue'
import { edgeTypeLabel, nodeTypeLabel, nodeColor } from '../utils/graphStyle'

const store = useAppStore()
const route = useRoute()
const router = useRouter()
// 课程中心改造：新增 members（学生管理）Tab，既有 5 个 Tab 全部保留
// 合并 PR #3：再并入合作者的 questions（题库管理）Tab

const TEACHER_TABS = ['courses', 'documents', 'members', 'preview', 'edit', 'monitor', 'questions', 'grading']

const activeTab = ref(TEACHER_TABS.includes(route.query.tab) ? route.query.tab : 'courses')

// 图谱管理：「查看图谱」与「编辑图谱」整合为同一 Tab，graphEditMode 控制就地切换编辑界面
const graphEditMode = ref(false)

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

// ===================== 解析/抽取进度自动刷新（驱动下方进度条实时显示） =====================
let docPollTimer = null
const DOC_POLL_INTERVAL = 3000
const DOC_POLL_TIMEOUT = 10 * 60 * 1000
let docPollStartedAt = 0

// 存在待处理/处理中的文档时才需要轮询（图谱生成进度查看）
const hasInFlightDoc = computed(() =>
  documents.value.some(
    (d) =>
      ['UPLOADED', 'PARSING'].includes(d.parse_status) ||
      ['PENDING', 'EXTRACTING'].includes(d.extract_status),
  ),
)

function stopDocPolling() {
  if (docPollTimer) {
    clearInterval(docPollTimer)
    docPollTimer = null
  }
}

function startDocPolling() {
  if (docPollTimer) return
  docPollStartedAt = Date.now()
  docPollTimer = setInterval(() => {
    // 离开文档页、无进行中任务或超过 10 分钟兜底时自动停止
    if (
      activeTab.value !== 'documents' ||
      !currentCourseId.value ||
      !hasInFlightDoc.value ||
      Date.now() - docPollStartedAt > DOC_POLL_TIMEOUT
    ) {
      stopDocPolling()
      return
    }
    pollDocuments()
  }, DOC_POLL_INTERVAL)
}

// 静默刷新文档列表（不触发表格 loading 闪烁），状态进入终态时给出提示
async function pollDocuments() {
  if (!currentCourseId.value) return
  try {
    const prev = new Map(documents.value.map((d) => [d.doc_id, `${d.parse_status}|${d.extract_status}`]))
    const list = await api.getDocuments(currentCourseId.value)
    // 保留上传中的本地占位行（后端在解析完成前不会返回该记录，避免进度条被轮询刷掉）
    const placeholders = documents.value.filter((d) => d.is_placeholder)
    documents.value = [...placeholders, ...list]
    for (const d of list) {
      const key = `${d.parse_status}|${d.extract_status}`
      if (prev.get(d.doc_id) && prev.get(d.doc_id) !== key) {
        if (d.extract_status === 'COMPLETED') {
          ElMessage.success(`「${d.file_name}」知识图谱构建完成`)
        } else if (d.parse_status === 'FAILED' || d.extract_status === 'FAILED') {
          ElMessage.error(`「${d.file_name}」处理失败，请检查文件后重试`)
        }
      }
    }
  } catch {
    /* 轮询失败静默，下一轮自动重试 */
  }
}

// 出现进行中任务且正停留在文档页时自动开启轮询；任务全部结束后停止
watch(hasInFlightDoc, (v) => {
  if (v && activeTab.value === 'documents') startDocPolling()
  else if (!v) stopDocPolling()
})

// 切回文档页时若有进行中任务则继续轮询，切走即停止
watch(activeTab, (tab) => {
  if (tab === 'documents') {
    if (hasInFlightDoc.value) startDocPolling()
  } else {
    stopDocPolling()
  }
})

async function doUpload() {
  if (!selectedFile.value) return
  if (!currentCourseId.value) {
    ElMessage.warning('请先选择课程')
    return
  }
  uploading.value = true
  uploadResult.value = null
  uploadError.value = ''

  // 后端为同步处理（解析+抽取耗时较长，且文档记录在完成后才入库），
  // 请求返回前列表不会有任何变化。先插入一行本地占位行，让下方进度条立即显示。
  const placeholderId = `local-${Date.now()}`
  const placeholder = {
    doc_id: placeholderId,
    file_name: selectedFile.value.name,
    file_type: (selectedFile.value.name.split('.').pop() || '').toUpperCase(),
    file_size: selectedFile.value.size,
    parse_status: 'PARSING',
    extract_status: 'PENDING',
    entity_count: null,
    relation_count: null,
    created_at: new Date().toISOString(),
    local_stage: 'PARSING',
    is_placeholder: true,
  }
  documents.value = [placeholder, ...documents.value]

  // 处理期间把占位行进度从「解析中」推进到「抽取中」，让进度条持续走动
  const stageTimer = setTimeout(() => {
    documents.value = documents.value.map((d) =>
      d.doc_id === placeholderId ? { ...d, local_stage: 'EXTRACTING' } : d,
    )
  }, 8000)

  try {
    const formData = new FormData()
    formData.append('file', selectedFile.value)
    // 上传到当前课程：只传 course_id，不再传 course_name / 自动建课
    const result = await api.uploadCourse(formData, currentCourseId.value)
    uploadResult.value = result
    ElMessage.success('知识图谱构建完成')
    // 用真实文档替换占位行；若仍有处理中任务则由轮询继续驱动进度
    documents.value = documents.value.filter((d) => d.doc_id !== placeholderId)
    await loadDocuments()
    if (hasInFlightDoc.value) startDocPolling()
  } catch (e) {
    documents.value = documents.value.filter((d) => d.doc_id !== placeholderId)
    uploadError.value = e.message || '上传失败'
    ElMessage.error(`上传失败：${e.message}`)
  } finally {
    clearTimeout(stageTimer)
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

async function leaveCourse(c) {
  const id = String(c.course_id)
  try {
    await ElMessageBox.confirm(
      `确定退出课程「${c.course_name}」吗？退出后将无法查看该课程内容。`,
      '退出课程',
      { type: 'warning', confirmButtonText: '退出', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  try {
    await store.leaveCourse(id)
    ElMessage.success('已退出课程')
    if (String(currentCourseId.value) === id) {
      currentCourseId.value = ''
      currentDocumentId.value = ''
    }
  } catch (e) {
    ElMessage.error(`退出失败：${e.message}`)
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
// 课程设置 / 邀请 弹窗（课程中心改造）
const settingsVisible = ref(false)
const settingsCourse = ref(null)
const inviteVisible = ref(false)
const inviteCourseId = ref('')

function openCreateCourse() {
  createCourseVisible.value = true
}

/** 新建课程成功：同步列表；若当前在文档页空状态创建，则直接进入该课程的文档管理 */
function onCourseCreated(created) {
  store.fetchCourses(true).catch(() => {})
  if (activeTab.value === 'documents' && created?.course_id != null) {
    router.replace({ path: '/teacher', query: { tab: 'documents', course_id: String(created.course_id) } })
  }
}

/** 课程被删除：若当前上下文正是它，清空后回到课程列表 */
function onCourseDeleted(course) {
  store.fetchCourses(true).catch(() => {})
  if (String(course?.course_id) === String(currentCourseId.value)) {
    currentCourseId.value = ''
    router.replace({ path: '/teacher', query: { tab: 'courses' } })
  }
}

/** 学生管理 Tab 的成员变动后静默刷新课程卡片（待审核角标要跟着变） */
function reloadCoursesSilently() {
  store.fetchCourses(true).catch(() => {})
}

function openSettings(course) {
  settingsCourse.value = course
  settingsVisible.value = true
}

/** 从顶部工具条的「邀请学生」进入：需要先有课程上下文 */
function openInviteForCurrent() {
  const cid = currentCourseId.value || store.courses[0]?.course_id
  if (!cid) {
    ElMessage.warning('请先创建或选择一门课程')
    return
  }
  const course = store.courses.find((c) => String(c.course_id) === String(cid))
  settingsCourse.value = course || null
  inviteCourseId.value = String(cid)
  inviteVisible.value = true
}

/** 跳到「学生管理」Tab 并选中该课程 */
function goMembers(course) {
  router.push({ path: '/teacher', query: { tab: 'members', course_id: String(course.course_id) } })
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
    { label: '已开始学习人数', value: d?.student_count ?? 0, color: '#4f6ef7' },
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
  graphEditMode.value = false
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
          { offset: 0, color: '#4f6ef7' },
          { offset: 1, color: '#8aa3f9' },
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
// 图谱管理：查看态 → 编辑态（就地切换，不换路由）；编辑态加载可编辑知识点列表
function enterGraphEdit() {
  graphEditMode.value = true
  if (currentCourseId.value) loadEditNodes()
}
// 编辑态 → 查看态：清空编辑选中；预览画布重新挂载时自动拉取最新图谱
function exitGraphEdit() {
  graphEditMode.value = false
  selectedNode.value = null
  prereqs.value = []
  prereqLoaded.value = false
}
/** 在线阅读：进入独立的文档阅读器整页（from=teacher 决定返回时回到课程管理） */
function readDocument(doc) {
  router.push({
    name: 'reader',
    params: { docId: String(doc.doc_id) },
    query: { course_id: String(doc.course_id), from: 'teacher' },
  })
}
/** 下载原文件：内容接口需要 JWT，地址栏直达会 401，因此取回字节后用 Blob 触发下载 */
async function downloadDocument(doc) {
  try {
    const buffer = await fetchDocumentBuffer(doc.doc_id)
    const url = URL.createObjectURL(new Blob([buffer]))
    const a = document.createElement('a')
    a.href = url
    a.download = doc.file_name || `document-${doc.doc_id}`
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 10000)
  } catch (e) {
    ElMessage.error(`下载失败：${e.message}`)
  }
}

/**
 * 图谱管理页「在线阅读」：进的是同一个阅读器整页，只是带 from=teacher-graph，
 * 让阅读器里的「返回」回到图谱管理（而不是被甩到课程文档列表）。
 * 课程 id 以当前上下文为准——同一课程的文档列表本来就按 course_id 取回，
 * 文档对象上的 course_id 只是冗余字段，缺了也不该让按钮失效。
 */
function readCurrentDocument() {
  const doc = currentDocument.value
  if (!doc) return
  router.push({
    name: 'reader',
    params: { docId: String(doc.doc_id) },
    query: {
      course_id: String(doc.course_id ?? currentCourseId.value),
      from: 'teacher-graph',
    },
  })
}

function monitorDocument(doc) {
  router.push({
    path: '/teacher',
    query: { tab: 'monitor', course_id: String(doc.course_id), document_id: String(doc.doc_id) },
  })
}

// 文档页内选择/切换/清空课程：同步路由，由路由守卫统一落地上下文并触发文档加载
function syncDocumentsRoute(id) {
  router
    .replace({
      path: '/teacher',
      query: { tab: 'documents', course_id: id ? String(id) : undefined },
    })
    .catch(() => {})
}

/**
 * 清掉「跟着 (课程, 文档) 走」的图谱页内部选中态。
 * 不清的话，换作用域后详情抽屉与前置知识面板还停在上一个文档的节点上——
 * 那些节点在新文档里可能根本不存在（节点按文档隔离），点进去会查到空数据。
 */
function resetContextSelection() {
  drawerVisible.value = false
  drawerNode.value = null
  selectedNode.value = null
  prereqs.value = []
  prereqLoaded.value = false
}

// ===================== 切换文档（图谱管理页内换 (课程, 文档) 作用域） =====================
const docSwitchVisible = ref(false)

/**
 * 应用选择器给出的 (课程, 文档)。
 *
 * 只改路由，状态同步全部交给既有的 route watcher：它已经负责把 query 落到
 * currentCourseId / currentDocumentId，并在课程变化时重载文档列表。
 * 这里额外只做它不管的一件事：清掉图谱页自己的选中项。
 *
 * 编辑模式下的「可编辑知识点列表」不在这里重载——路由 replace 后 ref 是异步更新的，
 * 此处同步调用 loadEditNodes() 读到的是切换前那篇文档。那件事交给下面的
 * watch([currentCourseId, currentDocumentId])。
 */
function applyDocSwitch({ courseId: cid, documentId: did }) {
  docSwitchVisible.value = false
  if (!cid || !did) return
  resetContextSelection()
  router
    .replace({
      path: '/teacher',
      query: { tab: 'preview', course_id: String(cid), document_id: String(did) },
    })
    .catch(() => {})
}

// 图谱管理/监测页内选择课程：清空已选文档并重置各页内部状态，避免残留上一个上下文的选中项
function onContextCourseChange(id) {
  currentDocumentId.value = ''
  resetContextSelection()
  router
    .replace({
      path: '/teacher',
      query: { tab: activeTab.value, course_id: id || undefined },
    })
    .catch(() => {})
  // 监测数据是课程级：选好课程即可加载；题库为课程级；图谱类页面等选好文档后由组件挂载时自加载
  if (activeTab.value === 'monitor' && id) loadMonitorData()
  if (activeTab.value === 'questions' && id) {
    questionDocFilter.value = ''
    loadQuestionsTab()
  }
}

// 图谱管理页内选择文档：重置内部状态并同步路由；编辑模式下的知识点列表由下面的 watch 重载
function onContextDocChange(id) {
  resetContextSelection()
  router
    .replace({
      path: '/teacher',
      query: { tab: activeTab.value, course_id: currentCourseId.value || undefined, document_id: id || undefined },
    })
    .catch(() => {})

  // 原先是 `if (activeTab.value === 'edit') loadEditNodes()`——'edit' 这个 Tab 已被
  // 并入 preview（见上面的 targetTab 改写），activeTab 永远不会等于 'edit'，是段死代码；
  // 而且即便成立也会读错文档（路由 replace 后 ref 异步才更新）。已由
  // watch([currentCourseId, currentDocumentId]) 统一接管。
  if (activeTab.value === 'grading') loadGrading()
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
    // 图谱管理整合：旧链接 tab=edit 并入 preview 并直接进入编辑模式
    const targetTab = tab === 'edit' ? 'preview' : tab
    if (tab === 'edit') graphEditMode.value = true
    if (!TEACHER_TABS.includes(targetTab)) {
      activeTab.value = 'courses'
      return
    }
    if (['preview', 'monitor'].includes(targetTab)) {
      // 缺少课程/文档时停留在当前页，由页内级联选择器引导，不再弹窗强制跳回
      currentCourseId.value = cid ? String(cid) : ''
      currentDocumentId.value = did ? String(did) : ''

    } else if (tab === 'documents' || tab === 'members' || tab === 'questions' || tab === 'grading') {

      // 这几个 Tab 只需要课程（不需要文档）：未指定课程时停留在页内选择器引导
      // questions 由 PR #3 并入，与 members 同理——不在此保留 currentCourseId 会永远停在空态
      currentCourseId.value = cid ? String(cid) : ''
      currentDocumentId.value = ''
    } else {
      currentCourseId.value = ''
      currentDocumentId.value = ''
    }
    activeTab.value = targetTab
  },
  { immediate: true }
)

// 课程上下文变化 → 加载文档列表
watch(currentCourseId, (cid) => {
  if (cid) loadDocuments()
  else documents.value = []
})

// (课程, 文档) 变了 → 编辑模式下的「可编辑知识点列表」跟着换。
// 列表按文档隔离（getGraphV1 带 document_id），不重载就会把上一篇文档的节点当成
// 当前文档的可编辑内容——不仅显示错的，改/删还会作用到错的节点上。
// 必须用 watch 而不是在切换处手动调用：router.replace 之后 ref 由 route watcher
// 异步更新，切换处同步调用 loadEditNodes() 读到的仍然是旧文档。
watch([currentCourseId, currentDocumentId], ([cid, did]) => {
  if (graphEditMode.value && cid && did) loadEditNodes()
})


/** 题型：前三类自动判分，后两类由教师批改（后端 MANUAL_GRADE_TYPES 的同口径前端副本） */

// 教师端也复用全局 AI 悬浮窗（App.vue 中挂载），它是从 store.learningContext 读取
// 「当前课程/文档」的。教师端的选中态是本页局部 ref，故在此同步过去，
// 否则悬浮窗会一直显示「未选择课程」，提问也退化成跨全部课程检索。
// immediate 必须加：深链进来时上面的路由 watcher 已在 setup 阶段（immediate）写好
// currentCourseId，本 watcher 若只在后续变化时触发，就会漏掉这个初始值。
watch([currentCourseId, currentDocumentId], ([cid, did]) => {
  store.setLearningContext({ courseId: cid || null, documentId: did || null })
}, { immediate: true })

// 深链「直接打开图谱」：仅当 URL 带 open=1（总览页点击课程卡片发起）时，
// 自动选中该课程首个可用文档并打开图谱。手动选择课程不会触发，保证仍可自由选择文档。
watch(
  () => route.query.open,
  async (open) => {
    if (open !== '1') return
    const cid = currentCourseId.value
    if (!cid) return
    if (!documents.value.length) await loadDocuments()
    const usable = documents.value.filter((d) => !d.is_placeholder && !d.local_stage)
    const first = usable.find((d) => d.extract_status === 'COMPLETED') || usable[0]
    if (first) {
      currentDocumentId.value = String(first.doc_id)
      router.replace({
        query: { tab: 'preview', course_id: String(cid), document_id: String(first.doc_id) },
      })
    }             
  },
  { immediate: true }
)

// ===================== 题库管理（Scope A：单选/多选/判断；答案与解析仅教师可见） =====================

const QUESTION_TYPES = [
  { value: 'SINGLE', label: '单选题' },
  { value: 'MULTI', label: '多选题' },
  { value: 'JUDGE', label: '判断题' },
  { value: 'FILL', label: '填空题' },
  { value: 'ESSAY', label: '解答题' },
]
const questionTypeLabel = (t) => QUESTION_TYPES.find((x) => x.value === t)?.label || t || ''

const questionList = ref([])
const questionsLoading = ref(false)
const questionTotal = ref(0)
const questionPage = ref(1)
const questionPageSize = ref(10)
const questionStats = ref(null)
const questionDocFilter = ref('')
const questionFilters = reactive({ q_type: '', keyword: '', is_active: '' })
/** 知识点题目覆盖率（无题知识点 / 悬空 kp_id；图谱不可用时 graph_available=false） */
const questionCoverage = ref(null)
const questionCoverageLoading = ref(false)
const danglingKpText = computed(() => {
  const list = questionCoverage.value?.dangling || []
  const names = list.slice(0, 5).map((d) => d.kp_id)
  return names.join('、') + (list.length > 5 ? ` 等 ${list.length} 个` : '')
})

const questionFormVisible = ref(false)
const questionFormLoading = ref(false)
const questionEditingId = ref(null)
const questionKpOptions = ref([])
const questionForm = reactive({
  q_type: 'SINGLE', stem: '', analysis: '', difficulty: 3, kp_id: '', document_id: '',
  options: [], singleAnswer: 'A', multiAnswer: [], judgeAnswer: 'true',
  // Scope B：填空题空位（key/label/hint/score/answer）与解答题参考答案
  blanks: [], essayAnswer: '',
})

const questionFavVisible = ref(false)
const questionFavLoading = ref(false)
const questionFavs = ref([])

/** 默认选项 A-D（教师可在表单内增删，最多 8 个） */
function emptyOptions() {
  return ['A', 'B', 'C', 'D'].map((k) => ({ key: k, text: '' }))
}

/** 关联知识点下拉数据：题库可能跨文档展示题目，因此聚合课程下全部文档的图谱节点，
 *  保证「知识点」列与新增题下拉都能显示名称而不是原始 kp_id（图谱不可用时降级为空） */
async function loadQuestionKpOptions() {
  if (!currentCourseId.value) {
    questionKpOptions.value = []
    return
  }
  // 文档列表尚未加载时先补齐（题库 Tab 可能是用户进入课程后的第一个 Tab）
  let docs = documents.value || []
  if (!docs.length) {
    try {
      docs = await api.getDocuments(currentCourseId.value)
      documents.value = docs
    } catch {
      docs = []
    }
  }
  if (!docs.length) {
    questionKpOptions.value = []
    return
  }
  try {
    const graphs = await Promise.all(
      docs.map((d) =>
        api
          .getGraphV1(currentCourseId.value, d.doc_id, { limit: 500 })
          .catch(() => ({ nodes: [] })),
      ),
    )
    const map = new Map()
    graphs.forEach((g) => {
      ;(g.nodes || []).forEach((n) => {
        if (n.id && !map.has(n.id)) map.set(n.id, { id: n.id, label: n.label })
      })
    })
    questionKpOptions.value = Array.from(map.values())
  } catch {
    questionKpOptions.value = []
  }
}

function kpNameById(id) {
  return questionKpOptions.value.find((n) => n.id === id)?.label
}

async function loadQuestions() {
  if (!currentCourseId.value) {
    questionList.value = []
    questionTotal.value = 0
    return
  }
  questionsLoading.value = true
  try {
    const data = await api.listQuestions({
      course_id: currentCourseId.value,
      document_id: questionDocFilter.value || undefined,
      q_type: questionFilters.q_type || undefined,
      keyword: questionFilters.keyword || undefined,
      is_active: questionFilters.is_active === '' ? undefined : questionFilters.is_active === '1',
      page: questionPage.value,
      page_size: questionPageSize.value,
    })
    questionList.value = data.items || []
    questionTotal.value = data.total || 0
  } catch (e) {
    questionList.value = []
    questionTotal.value = 0
    ElMessage.warning(`题库加载失败：${e.message}`)
  } finally {
    questionsLoading.value = false
  }
}

async function loadQuestionStats() {
  if (!currentCourseId.value) {
    questionStats.value = null
    return
  }
  try {
    questionStats.value = await api.getQuestionStats(currentCourseId.value)
  } catch {
    questionStats.value = null
  }
}

/** 知识点题目覆盖率：无题知识点清单 + 悬空 kp_id（图谱不可用时后端返回 graph_available=false） */
async function loadQuestionCoverage() {
  if (!currentCourseId.value) {
    questionCoverage.value = null
    return
  }
  questionCoverageLoading.value = true
  try {
    questionCoverage.value = await api.getQuestionCoverage(
      currentCourseId.value,
      questionDocFilter.value || undefined,
    )
  } catch (e) {
    questionCoverage.value = null
    ElMessage.warning(`覆盖率加载失败：${e.message}`)
  } finally {
    questionCoverageLoading.value = false
  }
}

// ---------- 知识点自动标注（Scope C / P3：字面匹配 + 向量召回 + 图谱扩展） ----------
const kpCandVisible = ref(false)
const kpCandLoading = ref(false)
const kpCandList = ref([])
const kpCandMeta = ref(null)

/** 候选来源与各层可用性（如实展示降级情况，避免"没结果却不知道为什么"） */
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

/** 单题自动标注：取候选（不写库，采纳后由「保存」落库） */
async function loadKpCandidates() {
  if (!questionEditingId.value) return
  kpCandVisible.value = true
  kpCandLoading.value = true
  try {
    const data = await api.getKpCandidates(questionEditingId.value, 5)
    kpCandList.value = data.candidates || []
    kpCandMeta.value = data.meta || null
  } catch (e) {
    kpCandList.value = []
    kpCandMeta.value = null
    ElMessage.warning(`自动标注失败：${e.message}`)
  } finally {
    kpCandLoading.value = false
  }
}

function adoptKpCandidate(row) {
  questionForm.kp_id = row.kp_id
  kpCandVisible.value = false
  ElMessage.success(`已填入知识点「${row.name}」，保存后生效`)
}

// ---------- 批量知识点自动标注 ----------
const autoLabelVisible = ref(false)
const autoLabelLoading = ref(false)
const autoLabelPreview = ref(null)
const autoLabelForm = reactive({
  document_id: '', only_missing: true, top_k: 3, apply_threshold: 0.6,
})

const autoLabelMetaText = computed(() => {
  const m = autoLabelPreview.value?.meta
  if (!m) return ''
  const parts = [
    `候选来源：${catalogSourceLabel(m.catalog_source)}（${m.catalog_size} 个知识点）`,
    m.graph_available ? '图谱可用' : '图谱不可用',
    m.embedding_configured ? '向量召回可用' : '未配置 EMBEDDING_API_KEY',
  ]
  if (m.reason) parts.push(m.reason)
  return parts.join(' · ')
})

function openAutoLabel() {
  autoLabelPreview.value = null
  autoLabelForm.document_id = questionDocFilter.value || ''
  autoLabelVisible.value = true
}

/** 批量标注：apply=false 仅预览；apply=true 写入「建议分 ≥ 阈值且非仅关系推断」的题 */
async function runAutoLabel(apply) {
  if (!currentCourseId.value) {
    ElMessage.warning('请先选择课程')
    return
  }
  autoLabelLoading.value = true
  try {
    const data = await api.autoLabelQuestions({
      course_id: currentCourseId.value,
      document_id: autoLabelForm.document_id || undefined,
      only_missing: autoLabelForm.only_missing,
      apply,
      top_k: autoLabelForm.top_k,
      apply_threshold: autoLabelForm.apply_threshold,
    })
    autoLabelPreview.value = data
    if (apply) {
      ElMessage.success(`已写入 ${data.applied} 道题（扫描 ${data.scanned} 道，有建议 ${data.with_candidates} 道）`)
      await loadQuestionsTab()
    } else {
      ElMessage.success(`预览完成：扫描 ${data.scanned} 道，有建议 ${data.with_candidates} 道`)
    }
  } catch (e) {
    ElMessage.error(`自动标注失败：${e.message}`)
  } finally {
    autoLabelLoading.value = false
  }
}

// ---------- 试题文档导入向导（P2 / Scope D：文档 → 预览 → 暂存） ----------
const importVisible = ref(false)
const importLoading = ref(false)
const importDocId = ref('')
const importMax = ref(200)
const importItems = ref([])
const importSelection = ref([])
const importStats = ref(null)
const importReport = ref(null)

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

function openImportWizard() {
  importVisible.value = true
  importItems.value = []
  importSelection.value = []
  importStats.value = null
  importReport.value = null
  importDocId.value = questionDocFilter.value || ''
}

/** 缺答案的题不允许勾选导入（主观题没有参考答案就无法批改；补完答案后状态会变、即可勾选） */
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

/** 行内补答案后重算状态：缺答案 → ANSWER_MISSING（不可勾选）；补完 → READY / NEEDS_REVIEW */
function recomputeImportState(row) {
  const others = (row.warnings || []).filter((w) => w !== 'answer_missing')
  const ok = hasImportAnswer(row)
  row.warnings = ok ? others : [...others, 'answer_missing']
  row.import_status = !ok ? 'ANSWER_MISSING' : (others.length ? 'NEEDS_REVIEW' : 'READY')
}

function importStatusLabel(status) {
  return { READY: '可导入', NEEDS_REVIEW: '待复核', ANSWER_MISSING: '缺答案' }[status] || status
}

function importWarnText(row) {
  const labels = {
    answer_missing: '缺答案', multi_type_needs_review: '多选需确认',
    judge_type_guessed: '判断题待确认', duplicate_stem: '题干重复',
    no_options_no_answer: '无选项无答案',
  }
  return (row.warnings || []).map((w) => labels[w] || w).join('、')
}

function formatImportAnswer(row) {
  const a = row.answer
  if (a === null || a === undefined || a === '') return '—'
  return Array.isArray(a) ? a.join('、') : String(a)
}

function onImportSelection(rows) {
  importSelection.value = rows || []
}

/** 解析预览：只读，不写库 */
async function runImportPreview() {
  if (!currentCourseId.value || !importDocId.value) return
  importLoading.value = true
  importReport.value = null
  try {
    const data = await api.previewImportQuestions({
      course_id: currentCourseId.value,
      document_id: importDocId.value,
      max_questions: importMax.value,
    })
    importItems.value = (data.items || []).map((it) => {
      if (it.q_type === 'MULTI' && !Array.isArray(it.answer)) it.answer = []
      if (it.q_type === 'FILL' && Array.isArray(it.options)) {
        it.options.forEach((b) => { if (b.answer === undefined) b.answer = '' })
      }
      return it
    })
    importStats.value = data.stats || null
    importSelection.value = []
    const importable = importItems.value.filter(importSelectable).length
    ElMessage.success(`解析完成：识别 ${data.stats.total} 题，可导入 ${importable} 题（缺答案的题可直接在表格里填写）`)
  } catch (e) {
    importItems.value = []
    importStats.value = null
    ElMessage.error(`解析失败：${e.message}`)
  } finally {
    importLoading.value = false
  }
}

/** 提交导入：默认入库为停用的暂存题；activate=true 则直接启用 */
async function submitImport(activate) {
  if (!importSelection.value.length) return
  importLoading.value = true
  try {
    const data = await api.commitImportQuestions({
      course_id: currentCourseId.value,
      document_id: importDocId.value,
      items: importSelection.value,
      activate,
    })
    importReport.value = data
    ElMessage.success(activate
      ? `已导入并启用 ${data.imported} 题`
      : `已导入 ${data.imported} 题为暂存题（默认停用）`)
    await loadQuestionsTab()
  } catch (e) {
    ElMessage.error(`导入失败：${e.message}`)
  } finally {
    importLoading.value = false
  }
}

// ---------- 主观题批改（Scope B：填空/解答提交不判分，教师批改后才给分） ----------
const gradingSummary = ref(null)
const gradingList = ref([])
const gradingLoading = ref(false)
const gradingTotal = ref(0)
const gradingPage = ref(1)
const gradingPageSize = ref(20)
const gradingDocFilter = ref('')
const gradingStatus = ref('PENDING')     // PENDING=待批改 / GRADED=已批改（复查与改判）
const gradingStudentFilter = ref('')     // 按学生筛选（后端 student_id，早前已支持）
const gradingStudentOptions = ref([])    // 批改台已加载记录中出现过的学生（避免筛选后选项丢失）
let gradingOptCourse = null              // 记录选项累积所属课程，切课即重置
const gradingSelection = ref([])
const gradingScore = reactive({})        // record_id -> 打分输入值（默认满分，教师可改）
const gradingBatchScore = ref(100)
const gradingBatchComment = ref('')

/** 待批改列表 + 进度汇总（进入批改 Tab / 刷新 / 批改后统一调用） */
async function loadGrading() {
  if (!currentCourseId.value) {
    gradingSummary.value = null
    gradingList.value = []
    gradingTotal.value = 0
    return
  }
  gradingLoading.value = true
  if (gradingOptCourse !== currentCourseId.value) {   // 切换课程后重建学生候选与筛选
    gradingOptCourse = currentCourseId.value
    gradingStudentOptions.value = []
    gradingStudentFilter.value = ''
  }
  try {
    const [summary, data] = await Promise.all([
      api.getGradingSummary(currentCourseId.value),
      api.getPendingGrades({
        course_id: currentCourseId.value,
        document_id: gradingDocFilter.value || undefined,
        student_id: gradingStudentFilter.value || undefined,
        status: gradingStatus.value,
        page: gradingPage.value,
        page_size: gradingPageSize.value,
      }),
    ])
    gradingSummary.value = summary
    gradingList.value = data.items || []
    gradingTotal.value = data.total || 0
    gradingList.value.forEach((it) => {
      // 待批改默认满分（教师只改需要调整的）；已批改预填当前分数（便于微调后改判）
      if (gradingStatus.value === 'GRADED') gradingScore[it.record_id] = Number(it.score ?? 0)
      else if (gradingScore[it.record_id] === undefined) gradingScore[it.record_id] = 100
      // 学生筛选下拉：累积本课程出现过的学生
      const sid = String(it.student_id ?? '')
      if (sid && !gradingStudentOptions.value.some((o) => o.value === sid)) {
        gradingStudentOptions.value.push({ value: sid, label: it.student_name || sid })
      }
    })
    gradingSelection.value = []
  } catch (e) {
    gradingSummary.value = null
    gradingList.value = []
    gradingTotal.value = 0
    ElMessage.warning(`批改数据加载失败：${e.message}`)
  } finally {
    gradingLoading.value = false
  }
}

function onGradingSelection(rows) {
  gradingSelection.value = (rows || []).map((r) => r.record_id)
}

/** 切换待批改/已批改视图：回到第一页并清空选择（避免跨视图误批量） */
function onGradingStatusChange() {
  gradingPage.value = 1
  gradingSelection.value = []
  loadGrading()
}

/** F3：从题库页「待批改」卡片直达主观题批改页（不依赖标签栏 tab 是否可见） */
function gotoGrading() {
  activeTab.value = 'grading'
  currentDocumentId.value = ''
  gradingPage.value = 1
  loadGrading()
}

/** 单题批改（就地更新分数与评语；后端按 >=60 记为答对，允许重批覆盖） */
async function submitGrade(row) {
  const score = Number(gradingScore[row.record_id])
  if (Number.isNaN(score) || score < 0 || score > 100) {
    ElMessage.warning('请填写 0-100 的分数')
    return
  }
  let comment = ''
  try {
    const { value } = await ElMessageBox.prompt(
      '可填写评语（可选，学生可见）',
      `批改 · ${row.student_name} · ${score} 分`,
      {
        confirmButtonText: '确认给分',
        cancelButtonText: '取消',
        inputPlaceholder: '评语（可选）',
        inputValue: '',
      },
    )
    comment = value || ''
  } catch {
    return                                  // 教师取消：不写库
  }
  try {
    await api.gradeAnswer(row.record_id, score, comment || undefined)
    ElMessage.success(`已批改：${row.student_name} ${score} 分`)
    await Promise.all([loadGrading(), loadQuestionStats()])
  } catch (e) {
    ElMessage.error(`批改失败：${e.message}`)
  }
}

/** 批量批改（同一分数与评语；客观题/越权记录由后端跳过并返回原因） */
async function batchGrade() {
  if (!gradingSelection.value.length) return
  const score = Number(gradingBatchScore.value)
  if (Number.isNaN(score) || score < 0 || score > 100) {
    ElMessage.warning('请填写 0-100 的分数')
    return
  }
  try {
    const data = await api.gradeAnswersBatch(
      gradingSelection.value, score, gradingBatchComment.value || undefined,
    )
    ElMessage.success(`批量批改完成：更新 ${data.updated} 条，跳过 ${(data.skipped || []).length} 条`)
    await Promise.all([loadGrading(), loadQuestionStats()])
  } catch (e) {
    ElMessage.error(`批量批改失败：${e.message}`)
  }
}

/** 覆盖率卡片里点击「无题知识点」→ 打开新增表单并预填该知识点（补题闭环） */
function createQuestionForKp(kp) {
  openQuestionForm(null)
  questionForm.kp_id = kp?.kp_id || ''
  if (kp?.document_id) questionForm.document_id = String(kp.document_id)
  if (!questionKpOptions.value.some((n) => String(n.id) === String(questionForm.kp_id))) {
    loadQuestionKpOptions()
  }
}

/** 进入题库 Tab / 切换筛选时统一刷新（列表 + 总览 + 覆盖率 + 知识点下拉） */
async function loadQuestionsTab() {
  questionPage.value = 1
  // 题库的「所属文档」下拉与知识点下拉都依赖文档列表；用户可能从未进过文档 Tab
  if (currentCourseId.value && !documents.value.length) loadDocuments()
  loadQuestionKpOptions()
  await Promise.all([loadQuestions(), loadQuestionStats(), loadQuestionCoverage()])
}

/** 打开新增（row=null）或编辑表单：回填题型/选项/答案/文档/知识点 */
function openQuestionForm(row) {
  questionEditingId.value = row ? row.question_id : null
  questionForm.q_type = row ? row.q_type : 'SINGLE'
  questionForm.stem = row ? row.stem : ''
  questionForm.analysis = row ? (row.analysis || '') : ''
  questionForm.difficulty = row ? (row.difficulty || 3) : 3
  questionForm.kp_id = row ? (row.kp_id || '') : ''
  questionForm.document_id = row
    ? (row.document_id ? String(row.document_id) : '')
    : (currentDocumentId.value || '')

  const isJudge = row && row.q_type === 'JUDGE'
  const isFill = row && row.q_type === 'FILL'
  const isEssay = row && row.q_type === 'ESSAY'

  // 选择题回填选项（不重排编号）；判断题/填空/解答不使用选项数组
  questionForm.options = (row && (row.q_type === 'SINGLE' || row.q_type === 'MULTI'))
    ? (Array.isArray(row.options)
        ? row.options.map((o) => ({ key: String(o.key), text: o.text }))
        : emptyOptions())
    : []

  // 填空题：回填空位（每空含参考答案与分值，教师视角才有 answer）
  questionForm.blanks = isFill && Array.isArray(row.options)
    ? row.options.map((b, i) => ({
        key: b.key ?? i + 1,
        label: b.label || `第${i + 1}空`,
        hint: b.hint || '',
        score: Number(b.score) || 0,
        answer: b.answer || '',
      }))
    : emptyBlanks()

  // 解答题：回填参考答案（存储为字符串）
  questionForm.essayAnswer = isEssay && typeof row.answer === 'string' ? row.answer : ''

  const ans = row ? row.answer : null
  questionForm.singleAnswer = row && row.q_type === 'SINGLE' ? String(ans) : (questionForm.options[0]?.key || 'A')
  questionForm.multiAnswer = row && row.q_type === 'MULTI' && Array.isArray(ans)
    ? ans.map((x) => String(x).toUpperCase())
    : []
  questionForm.judgeAnswer = row && row.q_type === 'JUDGE'
    ? (String(ans) === 'false' ? 'false' : 'true')
    : 'true'

  if (!questionForm.options.length && !isJudge && !isFill && !isEssay) {
    questionForm.options = emptyOptions()
  }
  questionFormVisible.value = true
  if (!questionKpOptions.value.length) loadQuestionKpOptions()
}

/** 添加选项：取 A-H 中第一个未使用的编号（不重排已有编号，避免答案键错位） */
function addQuestionOption() {
  const used = new Set(questionForm.options.map((o) => o.key))
  const next = 'ABCDEFGH'.split('').find((k) => !used.has(k))
  if (next) questionForm.options.push({ key: next, text: '' })
}

/** 删除选项：同步剔除该键在答案中的引用 */
function removeQuestionOption(idx) {
  if (questionForm.options.length <= 2) return
  const [removed] = questionForm.options.splice(idx, 1)
  questionForm.multiAnswer = questionForm.multiAnswer.filter((k) => k !== removed.key)
  if (questionForm.singleAnswer === removed.key) {
    questionForm.singleAnswer = questionForm.options[0]?.key || ''
  }
}

/** 填空题默认空位（1 个；最多 20 个，与后端 MAX_BLANKS 对齐） */
function emptyBlanks() {
  return [{ key: 1, label: '第1空', hint: '', score: 50, answer: '' }]
}

/** 添加空位：编号顺延（不重排已有编号，避免答案错位） */
function addQuestionBlank() {
  if (questionForm.blanks.length >= 20) return
  const nextKey = questionForm.blanks.reduce((m, b) => Math.max(m, Number(b.key) || 0), 0) + 1
  questionForm.blanks.push({ key: nextKey, label: `第${nextKey}空`, hint: '', score: 0, answer: '' })
}

/** 删除空位：至少保留 1 个 */
function removeQuestionBlank(idx) {
  if (questionForm.blanks.length <= 1) return
  questionForm.blanks.splice(idx, 1)
}

/** 批改台：从学生解答对象里取某个空位的作答（填空题学生答案形如 {"1": "浮点数"}） */
function blankAnswerOf(row, key) {
  const ans = row?.user_answer
  if (!ans) return ''
  if (Array.isArray(ans)) return ans[key - 1] ?? ''
  if (typeof ans === 'object') return ans[String(key)] ?? ans[key] ?? ''
  return String(ans)
}

/** 组装提交体：空选项被过滤；document_id 留空即「课程通用题」
 *  Scope B：填空题提交「空位定义」（每空带参考答案），解答题提交参考答案字符串 */
function buildQuestionPayload() {
  const base = {
    q_type: questionForm.q_type,
    stem: questionForm.stem,
    analysis: questionForm.analysis || null,
    difficulty: questionForm.difficulty,
    kp_id: questionForm.kp_id || null,
    document_id: questionForm.document_id || null,
  }
  if (questionForm.q_type === 'JUDGE') {
    return { ...base, options: [], answer: questionForm.judgeAnswer }
  }
  if (questionForm.q_type === 'FILL') {
    return {
      ...base,
      options: questionForm.blanks.map((b, i) => ({
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
  if (questionForm.q_type === 'ESSAY') {
    return { ...base, options: [], answer: (questionForm.essayAnswer || '').trim() }
  }
  const options = questionForm.options
    .filter((o) => (o.text || '').trim())
    .map((o) => ({ key: o.key, text: o.text.trim() }))
  return {
    ...base,
    options,
    answer: questionForm.q_type === 'MULTI' ? questionForm.multiAnswer : questionForm.singleAnswer,
  }
}

async function submitQuestionForm() {
  const payload = buildQuestionPayload()
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
  questionFormLoading.value = true
  try {
    if (questionEditingId.value) {
      await api.updateQuestion(questionEditingId.value, payload)
      ElMessage.success('题目已更新')
    } else {
      await api.createQuestion({ course_id: currentCourseId.value, ...payload })
      ElMessage.success('题目已新增')
    }
    questionFormVisible.value = false
    await loadQuestionsTab()
  } catch (e) {
    ElMessage.error(`保存失败：${e.message}`)
  } finally {
    questionFormLoading.value = false
  }
}

async function toggleQuestionActive(row) {
  try {
    await api.setQuestionActive(row.question_id, !row.is_active)
    ElMessage.success(row.is_active ? '题目已停用（移出出题池）' : '题目已启用')
    await loadQuestionsTab()
  } catch (e) {
    ElMessage.error(`操作失败：${e.message}`)
  }
}

async function removeQuestion(row) {
  try {
    await ElMessageBox.confirm(
      '删除后学生将无法再做该题；若已有作答记录，系统会自动改为「停用」以保护答题数据。是否继续？',
      '删除题目',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    const data = await api.deleteQuestion(row.question_id)
    ElMessage.success(data?.soft_deleted ? '该题已有作答记录，已停用（未物理删除）' : '题目已删除')
    await loadQuestionsTab()
  } catch (e) {
    ElMessage.error(`删除失败：${e.message}`)
  }
}

/** 题目收藏情况：哪些学生收藏了本课程的题 */
async function openQuestionFavorites() {
  questionFavVisible.value = true
  questionFavLoading.value = true
  try {
    const data = await api.getQuestionFavorites(currentCourseId.value)
    questionFavs.value = data.items || []
  } catch (e) {
    questionFavs.value = []
    ElMessage.warning(`收藏情况加载失败：${e.message}`)
  } finally {
    questionFavLoading.value = false
  }
}

// Tab 变化 → 按需加载数据
watch(activeTab, (tab) => {
  if (tab === 'courses') store.fetchCourses(true).catch(() => {})
  if (tab === 'documents' && currentCourseId.value) loadDocuments()
  if (tab === 'preview') {
    drawerVisible.value = false
    drawerNode.value = null
    // 图谱管理也要文档列表：上下文条要显示文档名、「在线阅读文档」入口要拿 doc_id。
    // 只靠 watch(currentCourseId) 加载是不够的——那条 watch 注册时 currentCourseId
    // 已被路由 watcher（immediate）写好，深链进来时它一次都不会触发。
    if (currentCourseId.value && !documents.value.length) loadDocuments()
    // 图谱管理：编辑模式下进入该页时主动拉取可编辑知识点列表
    if (graphEditMode.value && currentCourseId.value) loadEditNodes()
  }
  if (tab === 'monitor' && currentCourseId.value) loadMonitorData()
  if (tab === 'questions' && currentCourseId.value) loadQuestionsTab()
})

onMounted(() => {
  store.fetchCourses(true).catch(() => {})
  window.addEventListener('resize', handleMonitorResize)
  // 深链直达时 activeTab 初始即等于目标 tab，activeTab watcher 不会触发，需在此补一次加载
  if (activeTab.value === 'documents' && currentCourseId.value) loadDocuments()
  if (activeTab.value === 'preview' && currentCourseId.value && !documents.value.length) {
    loadDocuments() // 同上：上下文条 / 在线阅读入口需要文档列表
  }
  if (activeTab.value === 'monitor' && currentCourseId.value) loadMonitorData()
  if (activeTab.value === 'questions' && currentCourseId.value) loadQuestionsTab()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleMonitorResize)
  progressDistChart?.dispose()
  stopDocPolling()
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

// 图谱生成进度百分比：排队 5% → 解析中 30% → 已解析 50% → 抽取中 75% → 完成 100%
function docProgress(doc) {
  if (doc.parse_status === 'FAILED') return 30
  if (doc.extract_status === 'FAILED') return 75
  if (doc.extract_status === 'COMPLETED') return 100
  // 上传请求挂起期间的本地占位行：按阶段推进进度
  if (doc.local_stage === 'UPLOADING') return 15
  if (doc.local_stage === 'PARSING') return 30
  if (doc.local_stage === 'EXTRACTING') return 75
  if (doc.extract_status === 'EXTRACTING') return 75
  if (doc.parse_status === 'PARSED') return 50
  if (doc.parse_status === 'PARSING') return 30
  return 5
}

function docProgressStatus(doc) {
  if (doc.parse_status === 'FAILED' || doc.extract_status === 'FAILED') return 'exception'
  if (doc.extract_status === 'COMPLETED') return 'success'
  return ''
}

// 是否处于处理中（进度条条纹流动动画依据）
function isDocInFlight(doc) {
  return (
    ['UPLOADING', 'PARSING', 'EXTRACTING'].includes(doc.local_stage) ||
    ['UPLOADED', 'PARSING'].includes(doc.parse_status) ||
    ['PENDING', 'EXTRACTING'].includes(doc.extract_status)
  )
}
</script>

<style scoped>
/* 顶部标签栏已由左侧菜单接管：隐藏标签头，仅保留面板切换机制。 */
.main-view-tabs > :deep(.el-tabs__header) {
  display: none;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.stats-text {
  color: var(--color-text-secondary);
  font-size: var(--font-size-label);
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
  gap: var(--space-2);
  flex-wrap: wrap;
  margin-bottom: var(--space-3);
  padding: var(--space-2) var(--space-3);
  background: var(--color-bg-soft);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
}
.context-title {
  font-size: var(--font-size-section);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
}
/* 右侧动作组推到最右：点击它们是要离开当前视图，不该混在左侧的信息标签里 */
.ctx-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

/* ===== 文档列表 / 上传 ===== */
.upload-card {
  margin-bottom: var(--space-3);
}
.upload-result-alert {
  margin-top: var(--space-3);
}
.doc-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.doc-table :deep(.el-table__header th) {
  background: var(--color-bg-soft);
  color: var(--color-text-regular);
  font-weight: var(--font-weight-semibold);
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
  font-weight: var(--font-weight-semibold);
  font-size: var(--font-size-body);
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
  background: #eef1fe;
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
  gap: var(--space-2);
  flex-wrap: wrap;
  margin-bottom: var(--space-3);
}
.course-toolbar .toolbar-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-left: auto;
}
.course-grid-wrap {
  min-height: 200px;
}
.course-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: var(--space-4);
}
.course-card {
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  padding: var(--space-4);
  box-shadow: var(--shadow-card);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
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
  gap: var(--space-2);
}
.course-name {
  font-size: var(--font-size-section);
  font-weight: var(--font-weight-bold);
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}
.course-desc {
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  line-height: 1.5;
  min-height: 36px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.course-stats {
  display: flex;
  gap: var(--space-2);
}
.course-stat {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: var(--space-2);
  border-radius: var(--radius-md);
  background: var(--color-bg-soft);
  border: 1px solid var(--color-border-light);
}
.stat-num {
  font-size: 16px;
  font-weight: var(--font-weight-bold);
  color: var(--color-text-primary);
  font-family: var(--font-family-number);
}
.stat-label {
  font-size: 11px;
  color: var(--color-text-secondary);
}
.course-card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  flex-wrap: wrap;
  border-top: 1px solid var(--color-border-light);
  padding-top: 10px;
}
.course-updated {
  font-size: var(--font-size-caption);
  color: var(--color-text-muted);
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
}
.course-actions {
  display: flex;
  gap: var(--space-1);
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

/* ===== 知识点题目覆盖率 ===== */
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
  color: #4f6ef7;
}
.cell-empty {
  color: #c0c4cc;
}
/* 题库表单：选项输入 + 删除按钮同一行（右侧按钮不挤压输入框） */
.opt-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
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

/* ===== 文档页课程选择引导 ===== */
.doc-course-pick {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  flex-wrap: wrap;
}
.doc-course-pick-tip {
  margin: 12px 0 0;
  font-size: 12px;
  color: #909399;
}
.course-switcher {
  margin-left: auto;
  width: 200px;
}

/* 上下文条右侧操作区（学生管理 Tab 的「邀请学生」等） */
.context-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

/* ===== 响应式：三栏工作区窄屏堆叠 ===== */
@media (max-width: 768px) {
  .workspace :deep(.el-col) {
    margin-bottom: var(--space-3);
  }
}

/* ============================================================
   v2 视觉增强（靛蓝科技体系）——同优先级后置覆盖
   ============================================================ */

/* 上下文条：浅色渐变 + 胶囊 */
.context-bar {
  background: linear-gradient(120deg, rgba(91,141,239,.07), rgba(139,92,246,.06));
  border: 1px solid var(--brand-100);
  border-radius: var(--radius-lg);
  padding: 12px 16px;
}
.context-title { color: var(--brand-700); }

/* 文档表格表头 */
.doc-table :deep(.el-table__header th) {
  background: #f4f6fc !important;
  color: var(--text-secondary);
  letter-spacing: .3px;
}

/* 三栏工作区 */
.panel-card { border-radius: var(--radius-lg); overflow: hidden; }
.panel-header {
  font-size: 14px;
  color: var(--text-primary);
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border-light);
  margin-bottom: 4px;
}
.node-item { border-radius: var(--radius-sm); transition: all .16s; }
.node-item:hover { background: var(--brand-50); }
.node-item.active {
  background: linear-gradient(120deg, rgba(91,141,239,.12), rgba(139,92,246,.08));
  box-shadow: inset 3px 0 0 var(--brand-500);
}
.node-dot {
  box-shadow: 0 0 0 3px rgba(255,255,255,.9), 0 2px 6px -1px rgba(0,0,0,.25);
}
.detail-name { color: var(--text-primary); }
.prereq-list li:last-child { border-bottom: none; }

/* 课程管理卡（教师课程管理 Tab 内联卡，与 MyCourseGrid 风格对齐） */
.course-card { border-radius: var(--radius-lg); transition: transform .25s ease, box-shadow .25s ease; }
.course-card:hover { transform: translateY(-4px); box-shadow: var(--shadow-hover); border-color: var(--brand-200); }
.course-stat {
  background: var(--bg-soft);
  border-radius: var(--radius-sm);
  transition: background .2s;
}
.course-stat:hover { background: var(--brand-50); }
.stat-num { color: var(--brand-600); }

/* 教学监测 KPI */
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
.current-name { color: var(--brand-600); }

/* 题库/成员表格统一表头底色（全局 EP 表格已处理，这里保证 scoped 下一致） */
:deep(.el-table__header-wrapper th) { letter-spacing: .3px; }
.rec-tag { border-radius: 999px; }
.rec-list li:last-child { border-bottom: none; }
.result-card { border-left-color: var(--success); border-radius: var(--radius-md); }

/* 上传拖拽区 */
.upload-card :deep(.el-upload-dragger) {
  width: 100%;
  border-radius: var(--radius-lg);
  border: 1.5px dashed var(--brand-300);
  background:
    radial-gradient(60% 80% at 50% 0, rgba(91,141,239,.06), transparent 70%),
    #fbfcff;
  padding: 26px 20px;
  transition: border-color .2s, background .2s, box-shadow .2s;
}
.upload-card :deep(.el-upload-dragger:hover) {
  border-color: var(--brand-500);
  background:
    radial-gradient(60% 80% at 50% 0, rgba(91,141,239,.1), transparent 70%),
    var(--brand-50);
  box-shadow: 0 10px 26px -16px rgba(79,110,247,.5);
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
  box-shadow: 0 10px 22px -10px rgba(79,110,247,.7);
}
.upload-card :deep(.el-icon--upload svg) { color: #fff; }
.upload-card :deep(.el-upload__text) { color: var(--text-secondary); font-size: 13.5px; }
/* ===== Scope B：主观题（填空/解答）与批改台 ===== */
.class-kpi.highlight {
  border-color: rgba(245, 71, 93, .35);
  background: linear-gradient(135deg, rgba(245, 71, 93, .08), rgba(245, 71, 93, .02));
}
.class-kpi.highlight .class-kpi-value { color: #f5475d; }
.tab-badge { margin-left: 6px; }
.grade-blank-row { display: flex; gap: 8px; align-items: baseline; line-height: 1.7; font-size: 12.5px; }
.grade-blank-label { color: var(--text-secondary); flex: 0 0 auto; }
.grade-blank-user { color: var(--text-primary); font-weight: 600; flex: 0 0 auto; }
.grade-blank-ref { color: var(--text-secondary); }
.grade-essay-user { color: var(--text-primary); line-height: 1.6; margin-bottom: 4px; white-space: pre-wrap; }
/* Scope C：知识点自动标注候选 */
.kp-tag { margin-left: 6px; }
.kp-src { display: inline-block; margin-right: 8px; color: var(--text-secondary); font-size: 12.5px; }
</style>
