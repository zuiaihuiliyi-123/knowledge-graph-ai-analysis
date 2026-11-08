<template>
  <div>
    <PageHeader title="学习空间" desc="浏览课程知识图谱，智能问答与个性化学习路径推荐" />

    <!-- 统一学习上下文条：当前课程 + 当前文档，所有学习 Tab 共享 -->
    <el-card class="page-card ctx-bar">
      <div class="ctx-left">
        <template v-if="currentCourseId && currentDocumentId">
          <span class="ctx-label">当前课程</span>
          <b class="ctx-name">{{ currentCourseName }}</b>
          <el-divider direction="vertical" />
          <span class="ctx-label">当前文档</span>
          <b class="ctx-name">{{ currentDocumentName }}</b>
        </template>
        <span v-else class="ctx-empty"><el-icon><Compass /></el-icon>请选择课程和学习资料</span>
      </div>
      <div class="ctx-actions">
        <el-button
          v-if="currentCourseId && currentDocumentId"
          type="primary"
          :icon="Reading"
          @click="readCurrentDocument"
        >在线阅读</el-button>
        <el-button type="primary" plain @click="openSelector">
          {{ currentCourseId && currentDocumentId ? '切换资料' : '选择资料' }}
        </el-button>
      </div>
    </el-card>

    <el-tabs v-model="activeTab" class="main-view-tabs" @tab-change="onTabChange">
      <!-- ===================== 学习总览 ===================== -->
      <el-tab-pane name="overview">
        <template #label><span class="tab-label"><el-icon><DataAnalysis /></el-icon>学习总览</span></template>
        <StudentOverviewTab
          :display-name="store.displayName"
          :ctx-key="ctxKey"
          :path-data-loading="pathDataLoading"
          :kpis="overviewKpis"
          :state-list="overviewStateList"
          :total="overviewTotal"
          :pct="overviewPct"
          :current-node="overviewCurrentNode"
          :recommended="overviewRecommended"
          :initial-course-id="currentCourseId"
          :initial-document-id="currentDocumentId"
          @confirm-context="applyContext"
          @continue="onOverviewContinue"
          @go-path="onOverviewGoPath"
          @view-name="onOverviewViewName"
        />
      </el-tab-pane>

      <!-- ===================== 课程文档 ===================== -->
      <el-tab-pane name="documents">
        <template #label><span class="tab-label"><el-icon><Document /></el-icon>课程文档</span></template>
        <StudentDocumentsTab
          :active="activeTab === 'documents'"
          :course-id="currentCourseId"
          :course-name="currentCourseName"
          @select-context="openSelector"
        />
      </el-tab-pane>

      <!-- ===================== 图谱浏览 ===================== -->
      <el-tab-pane name="browse">
        <template #label><span class="tab-label"><el-icon><Compass /></el-icon>图谱浏览</span></template>
        <StudentBrowseTab
          ref="browseTabRef"
          :course-id="currentCourseId"
          :document-id="currentDocumentId"
          :mastered-kp-ids="masteredKpIds"
          :highlight-path="highlightPathNodes"
          :current-kp-id="browseCurrentKpId"
          :recommended-kp-ids="browseRecommendedKpIds"
          @node-click="onNodeClick"
        />
      </el-tab-pane>

      <!-- ===================== 智能问答 ===================== -->
      <el-tab-pane name="qa">
        <template #label><span class="tab-label"><el-icon><ChatDotRound /></el-icon>智能问答</span></template>
        <StudentQaTab
          :course-id="currentCourseId"
          :document-id="currentDocumentId"
          :course-name="currentCourseName"
          :document-name="currentDocumentName"
          :ctx-key="ctxKey"
          :favorite-ids="favoriteIds"
          @jump-kp="jumpToKp"
          @toggle-favorite="toggleFavoriteSafe"
        />
      </el-tab-pane>

      <!-- ===================== 学习路径推荐 ===================== -->
      <el-tab-pane name="path">
        <template #label><span class="tab-label"><el-icon><Guide /></el-icon>学习路径推荐</span></template>
        <StudentPathTab
          :ctx-key="ctxKey"
          :course-id="currentCourseId"
          :document-id="currentDocumentId"
          :path-progress="pathProgress"
          :path-chain="pathChain"
          :path-data-loading="pathDataLoading"
          :path-data-checked="pathDataChecked"
          :current-node="currentKnowledgePoint"
          :overview-focus-list="overviewFocusList"
          :favorite-ids="favoriteIds"
          :knowledge-options="knowledgeOptions"
          :mastered-names="masteredNames"
          @locate="locateKnowledgePoint"
          @view-focus="onOverviewViewFocus"
          @toggle-favorite="toggleFavoriteSafe"
          @highlight-path="setPathHighlight"
          @go-browse="goBrowse"
        />
      </el-tab-pane>

      <!-- ===================== 收藏夹 ===================== -->
      <el-tab-pane name="favorites">
        <template #label><span class="tab-label"><el-icon><StarFilled /></el-icon>收藏夹</span></template>
        <StudentFavoritesTab
          :ctx-key="ctxKey"
          :document-name="currentDocumentName"
          :favorite-ids="favoriteIds"
          :graph-nodes="graphNodes"
          :loading="!!ctxKey && (!graphDataChecked || favoritesLoading)"
          :loaded="graphDataChecked && favoritesLoaded"
          @view="locateKnowledgePoint"
          @unfavorite="toggleFavoriteSafe"
          @go-browse="goBrowse"
        />
      </el-tab-pane>

      <!-- ===================== 做题练习 ===================== -->
      <el-tab-pane name="practice">
        <template #label><span class="tab-label"><el-icon><Collection /></el-icon>做题练习</span></template>
        <StudentPracticeTab
          :active="activeTab === 'practice'"
          :ctx-key="ctxKey"
          :course-id="currentCourseId"
          :document-id="currentDocumentId"
          :graph-nodes="graphNodes"
          @locate="locateKnowledgePoint"
          @go-selector="openSelector"
        />
      </el-tab-pane>
    </el-tabs>

    <!-- 课程 → 文档选择器 -->
    <el-dialog v-model="selectorVisible" title="选择课程与学习资料" width="560px" :close-on-click-modal="false">
      <CourseDocumentSelector
        :initial-course-id="currentCourseId"
        :initial-document-id="currentDocumentId"
        @confirm="applyContext"
        @cancel="selectorVisible = false"
      />
    </el-dialog>

    <!-- 知识点详情抽屉（只读 + 掌握/收藏/局部展开） -->
    <NodeDetailDrawer
      v-model="drawerVisible"
      :node="drawerNode"
      :course-id="currentCourseId"
      :document-id="currentDocumentId"
      show-mastery
      :mastered="drawerMastered"
      :mastery-loading="masteryLoading"
      show-favorite
      :favorited="drawerFavorited"
      :favorite-loading="favoriteLoading"
      show-expand
      :expanded="drawerExpanded"
      :related="drawerRelated"
      :predecessors="drawerPredecessors"
      :successors="drawerSuccessors"
      :state="drawerLearningState"
      @toggle-mastery="onToggleMastery"
      @toggle-favorite="onToggleFavorite"
      @expand-toggle="onExpandToggle"
      @jump-to="onJumpToNode"
    />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  ChatDotRound, Collection, Compass, DataAnalysis, Document, Guide, Reading, StarFilled,
} from '@element-plus/icons-vue'
import PageHeader from '../components/PageHeader.vue'
import CourseDocumentSelector from '../components/CourseDocumentSelector.vue'
import NodeDetailDrawer from '../components/NodeDetailDrawer.vue'
import StudentOverviewTab from '../components/student/StudentOverviewTab.vue'
import StudentDocumentsTab from '../components/student/StudentDocumentsTab.vue'
import StudentBrowseTab from '../components/student/StudentBrowseTab.vue'
import StudentQaTab from '../components/student/StudentQaTab.vue'
import StudentPathTab from '../components/student/StudentPathTab.vue'
import StudentFavoritesTab from '../components/student/StudentFavoritesTab.vue'
import StudentPracticeTab from '../components/student/StudentPracticeTab.vue'
import { useStudentLearningContext } from '../composables/student/useStudentLearningContext'
import { useStudentLearningData } from '../composables/student/useStudentLearningData'

const {
  store,
  router,
  currentCourseId,
  currentDocumentId,
  ctxKey,
  currentCourseName,
  currentDocumentName,
  activeTab,
  selectorVisible,
  openSelector,
  applyContext,
  onTabChange,
  initFromRoute,
} = useStudentLearningContext()

const {
  graphNodes,
  pathDataLoading,
  pathDataChecked,
  graphDataChecked,
  masteryLoading,
  masteredKpIds,
  favoritesLoading,
  favoritesLoaded,
  favoriteLoading,
  favoriteIds,
  drawerVisible,
  drawerNode,
  drawerRelated,
  drawerPredecessors,
  drawerSuccessors,
  drawerExpanded,
  knowledgeOptions,
  masteredNames,
  drawerMastered,
  drawerFavorited,
  drawerLearningState,
  currentKnowledgePoint,
  browseCurrentKpId,
  browseRecommendedKpIds,
  pathProgress,
  pathChain,
  overviewTotal,
  overviewPct,
  overviewCurrentNode,
  overviewRecommended,
  overviewFocusList,
  overviewKpis,
  overviewStateList,
  toggleFavoriteSafe,
  onToggleFavorite,
  onToggleMastery,
  onNodeClick,
  neighborsOf,
} = useStudentLearningData({ currentCourseId, currentDocumentId, ctxKey })

const browseTabRef = ref(null)
const highlightPathNodes = ref([])

/** 路径查询结果 → 图谱高亮（路径 Tab 只发事件，不直接改页面状态） */
function setPathHighlight(names) {
  highlightPathNodes.value = Array.isArray(names) ? names : []
}

function goBrowse() {
  activeTab.value = 'browse'
}

/** 当前上下文里直接开始阅读（上下文条按钮） */
function readCurrentDocument() {
  if (!currentCourseId.value || !currentDocumentId.value) return
  router.push({
    name: 'reader',
    params: { docId: String(currentDocumentId.value) },
    query: { course_id: String(currentCourseId.value), from: 'student' },
  })
}

// 点击引用/相关知识点 → 跳图谱浏览并高亮 + 打开详情
function jumpToKp(kp) {
  if (!kp || !kp.name) return
  activeTab.value = 'browse'
  drawerNode.value = {
    id: kp.kp_id ?? kp.name,
    label: kp.name,
    description: kp.description || '',
    properties: { category: kp.category || '', is_manual: false, confidence: null },
  }
  drawerRelated.value = []
  drawerPredecessors.value = []
  drawerSuccessors.value = []
  drawerExpanded.value = false
  drawerVisible.value = true
  highlightPathNodes.value = [String(kp.kp_id ?? kp.name)]
}

// 统一的「定位知识点」：切到图谱 → 打开详情 → 图谱高亮 + 居中（路径/总览/收藏/练习复用）
function locateKnowledgePoint(node) {
  if (!node || node.id == null) return
  activeTab.value = 'browse'
  drawerNode.value = node
  const nb = neighborsOf(node.id)
  drawerSuccessors.value = nb.successors
  drawerRelated.value = nb.related
  drawerPredecessors.value = nb.predecessors
  drawerExpanded.value = browseTabRef.value?.isExpanded(String(node.id)) ?? false
  drawerVisible.value = true
  highlightPathNodes.value = [String(node.id)]
}

function onExpandToggle() {
  if (!drawerNode.value || !browseTabRef.value) return
  drawerExpanded.value = browseTabRef.value.toggleExpand(String(drawerNode.value.id))
}

function onJumpToNode(node) {
  if (!node || !browseTabRef.value) return
  browseTabRef.value.selectNode(String(node.id))
}

// ===================== 学习总览动作 =====================
function onOverviewContinue() {
  if (!overviewCurrentNode.value) return
  locateKnowledgePoint(overviewCurrentNode.value)
  ElMessage.success(`继续学习「${overviewCurrentNode.value.label}」`)
}

function onOverviewGoPath() {
  activeTab.value = 'path'
}

function onOverviewViewName(name) {
  const node = graphNodes.value.find((n) => n.label === name)
  if (node) locateKnowledgePoint(node)
}

function onOverviewViewFocus(item) {
  const node = graphNodes.value.find((n) => String(n.id) === String(item?.kpId))
  if (node) locateKnowledgePoint(node)
}

onMounted(() => {
  initFromRoute()
})
</script>

<style scoped>
/* 顶部标签栏已由左侧菜单接管：隐藏标签头，仅保留面板切换机制。
   > 子选择器限定最外层 tabs，练习页内嵌的 practiceSubTab 标签头不受影响。 */
.main-view-tabs > :deep(.el-tabs__header) {
  display: none;
}

/* ===== 统一学习上下文条 ===== */
.ctx-bar {
  margin-bottom: var(--space-4);
}
.ctx-bar :deep(.el-card__body) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
}
.ctx-left {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  min-width: 0;
}
.ctx-label {
  color: var(--color-text-secondary);
  font-size: var(--font-size-label);
}
.ctx-name {
  font-size: var(--font-size-body);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
  max-width: 360px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ctx-empty {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--color-text-secondary);
  font-size: var(--font-size-body);
}
.ctx-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}

/* v2 视觉增强：浅色渐变底 + 状态胶囊 */
.ctx-bar :deep(.el-card__body) {
  background: linear-gradient(120deg, rgba(91, 141, 239, .07), rgba(139, 92, 246, .06));
  border-radius: var(--radius-lg);
  padding: 14px 18px;
}
.ctx-label {
  background: rgba(255, 255, 255, .7);
  padding: 2px 9px;
  border-radius: 999px;
  font-size: 12px;
  border: 1px solid var(--border-light);
}
.ctx-name { color: var(--brand-700); }
.ctx-empty { font-weight: 500; }
</style>
