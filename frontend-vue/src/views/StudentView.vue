<template>
  <div>
    <div class="page-header">
      <h2 class="page-title">学习空间</h2>
      <p class="page-desc">浏览课程知识图谱，智能问答与个性化学习路径推荐</p>
    </div>

    <el-tabs v-model="activeTab">
      <!-- ===================== Tab 1：图谱浏览 ===================== -->
      <el-tab-pane name="browse">
        <template #label><span class="tab-label"><el-icon><Compass /></el-icon>图谱浏览</span></template>
        <el-card class="page-card">
          <div class="toolbar">
            <CourseSelector v-model="browseCourseId" />
            <el-input
              v-model="searchText"
              placeholder="搜索知识点名称或描述…"
              clearable
              style="width: 260px"
            />
            <el-button :icon="Refresh" circle title="刷新" @click="refreshBrowse" />
            <span v-if="stats" class="stats-text">
              节点 {{ stats.visibleNodeCount }} / {{ stats.nodeCount }} · 关系 {{ stats.visibleEdgeCount }} / {{ stats.edgeCount }}
            </span>
          </div>
        </el-card>
        <el-card class="page-card graph-card">
          <GraphCanvas
            ref="browseGraphRef"
            :course-id="browseCourseId"
            :search-text="searchText"
            :mastered-kp-ids="masteredKpIds"
            :highlight-path="highlightPathNodes"
            focus-on-click
            progressive
            @node-click="onNodeClick"
            @stats="(s) => (stats = s)"
          />
        </el-card>
      </el-tab-pane>

      <!-- ===================== Tab 2：智能问答（证据链） ===================== -->
      <el-tab-pane name="qa">
        <template #label><span class="tab-label"><el-icon><ChatDotRound /></el-icon>智能问答</span></template>

        <el-row :gutter="16" class="qa-layout">
          <!-- 左栏：当前课程 + 相关知识点 -->
          <el-col :span="6">
            <el-card class="page-card qa-side">
              <template #header>
                <div class="qa-side-title"><el-icon><Collection /></el-icon>当前课程</div>
              </template>
              <CourseSelector v-model="qaCourseId" />

              <el-divider content-position="left">相关知识点</el-divider>
              <el-empty
                v-if="!relatedKps.length"
                description="提问后，此处展示答案引用的知识点"
                :image-size="60"
              />
              <div v-else class="kp-list">
                <div v-for="kp in relatedKps" :key="kp.kp_id || kp.name" class="kp-item" @click="jumpToKp(kp)">
                  <el-tag size="small" effect="plain" :type="categoryTagType(kp.category)">
                    {{ kp.category || '知识点' }}
                  </el-tag>
                  <span class="kp-name">{{ kp.name }}</span>
                  <el-icon class="kp-jump"><Right /></el-icon>
                </div>
              </div>
            </el-card>
          </el-col>

          <!-- 右栏：AI 学习助手 -->
          <el-col :span="18">
            <el-card class="qa-card qa-main">
              <div class="qa-toolbar">
                <span class="qa-title"><el-icon><MagicStick /></el-icon>AI 学习助手</span>
                <el-button size="small" @click="clearChat">清空对话</el-button>
              </div>

              <div ref="chatBoxRef" class="chat-box chat-scroll">
                <div v-if="!messages.length" class="chat-welcome">
                  <div class="welcome-icon"><el-icon :size="40" color="#409eff"><MagicStick /></el-icon></div>
                  <p>基于课程知识图谱的 AI 问答助手</p>
                  <p class="welcome-sub">例如：什么是线性表？栈和队列有什么区别？</p>
                </div>

                <div
                  v-for="(m, i) in messages"
                  :key="i"
                  class="msg-row"
                  :class="m.role === 'user' ? 'msg-user' : 'msg-ai'"
                >
                  <div class="msg-bubble">
                    <div class="msg-text">{{ m.content }}</div>
                    <!-- 证据链：回答正文 → 引用来源 → 相关知识点（点击可定位图谱） -->
                    <template v-if="m.sources && m.sources.length">
                      <div class="msg-sources">
                        <div class="sources-title">
                          <el-icon><Document /></el-icon> 参考来源 · 课程知识库
                        </div>
                        <div
                          v-for="(s, j) in m.sources"
                          :key="j"
                          class="source-card"
                          @click="jumpToKp(s)"
                        >
                          <div class="source-head">
                            <el-tag size="small" effect="plain" :type="categoryTagType(s.category)">
                              {{ s.category || '知识点' }}
                            </el-tag>
                            <span class="source-name">{{ s.name }}</span>
                            <el-icon class="source-jump"><Right /></el-icon>
                          </div>
                          <div v-if="s.description" class="source-desc">{{ s.description }}</div>
                        </div>
                      </div>
                    </template>
                  </div>
                </div>

                <div v-if="asking" class="msg-row msg-ai">
                  <div class="msg-bubble typing">
                    <span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>
                  </div>
                </div>
              </div>

              <div class="chat-input">
                <el-input
                  v-model="question"
                  placeholder="输入你的问题，回车发送"
                  :disabled="asking"
                  @keyup.enter="sendQuestion"
                />
                <el-button type="primary" :loading="asking" @click="sendQuestion">
                  发送
                </el-button>
              </div>
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>

      <!-- ===================== Tab 3：学习路径推荐 ===================== -->
      <el-tab-pane name="path">
        <template #label><span class="tab-label"><el-icon><Guide /></el-icon>学习路径推荐</span></template>

        <!-- P5：学习路径视觉层级（已掌握 → 当前 → 推荐 → 未学习） -->
        <el-card class="page-card path-hero">
          <template #header>
            <div class="path-hero-header">
              <span class="path-hero-title"><el-icon><Guide /></el-icon>学习路径</span>
              <CourseSelector v-model="pathCourseId" />
            </div>
          </template>

          <el-empty
            v-if="!pathCourseId"
            description="请先选择课程，查看你的学习路径"
            :image-size="80"
          />

          <template v-else>
            <!-- 学习进度摘要 -->
            <div class="path-summary">
              <div class="summary-left">
                <div class="summary-line">
                  已掌握 <b class="num">{{ pathProgress.mastered }}</b>
                  <span class="muted">/ {{ pathProgress.total }} 个知识点</span>
                </div>
                <div v-if="pathProgress.current" class="summary-suggestion">
                  当前建议：继续学习「<b>{{ pathProgress.current }}</b>」
                </div>
                <div
                  v-else-if="pathProgress.total && pathProgress.mastered >= pathProgress.total"
                  class="summary-suggestion success"
                >
                  <el-icon><CircleCheckFilled /></el-icon> 太棒了，本课程知识点已全部掌握
                </div>
                <div v-else class="summary-suggestion muted">
                  完成知识点标记后，这里会给出下一步学习建议
                </div>
              </div>
              <div class="summary-progress">
                <el-progress
                  :percentage="pathProgress.pct"
                  :stroke-width="14"
                  :text-inside="true"
                  color="#409eff"
                />
              </div>
            </div>

            <!-- 路径链 -->
            <div v-loading="pathDataLoading" class="path-chain-wrap">
              <el-empty
                v-if="!pathDataLoading && pathDataChecked && !pathChain.length"
                :description="pathProgress.total && pathProgress.mastered >= pathProgress.total ? '已掌握全部知识点' : '请先在「图谱浏览」中标记已掌握知识点，系统据此推荐学习路径'"
                :image-size="60"
              />
              <div v-else-if="pathChain.length" class="path-chain">
                <template v-for="(seg, i) in pathChain" :key="seg.node.id">
                  <div class="chain-node" :class="'state-' + seg.state">
                    <div class="chain-icon">{{ STATE_META[seg.state].glyph }}</div>
                    <div class="chain-content">
                      <div class="chain-head">
                        <span class="chain-name">{{ seg.node.label }}</span>
                        <el-tag size="small" effect="plain" :type="categoryTagType(seg.node.properties?.category)">
                          {{ seg.node.properties?.category || '知识点' }}
                        </el-tag>
                        <span class="state-label" :class="'state-label-' + seg.state">{{ STATE_META[seg.state].label }}</span>
                      </div>
                      <div v-if="seg.reason" class="chain-reason">原因：{{ seg.reason }}</div>
                      <div v-if="seg.state === 'recommended'" class="chain-status">当前状态：未掌握</div>
                    </div>
                  </div>
                  <div v-if="i < pathChain.length - 1" class="chain-link">
                    <el-icon><ArrowDown /></el-icon>
                    <span>前置知识</span>
                  </div>
                </template>
              </div>
            </div>

            <div class="path-actions">
              <el-button type="primary" size="large" :disabled="!pathProgress.current" @click="startLearning">
                开始学习
              </el-button>
            </div>
          </template>
        </el-card>

        <el-row :gutter="16">
          <!-- 推荐下一步 -->
          <el-col :span="12">
            <el-card class="page-card">
              <template #header>
                <b><el-icon class="icon-gap" color="#409eff"><Aim /></el-icon>推荐下一步学习内容</b>
              </template>
              <p class="tip">输入你已掌握的知识点（每行一个），系统根据图谱前置关系推荐可学习的知识点</p>
              <el-input
                v-model="masteredText"
                type="textarea"
                :rows="5"
                placeholder="Python基础&#10;数据结构绪论&#10;…"
              />
              <div class="btn-row">
                <el-button type="primary" :loading="recommendLoading" @click="doRecommend">
                  推荐下一步
                </el-button>
                <CourseSelector v-model="pathCourseId" />
              </div>

              <template v-if="recommendations.length">
                <el-divider content-position="left">推荐结果（{{ recommendations.length }}）</el-divider>
                <div v-for="r in recommendations" :key="r.name" class="rec-item">
                  <div class="rec-name">
                    <el-tag size="small" :type="categoryTagType(r.category)">{{ r.category }}</el-tag>
                    <b>{{ r.name }}</b>
                  </div>
                  <div class="rec-desc">{{ r.description }}</div>
                  <div class="rec-reason"><el-icon class="icon-gap" color="#e6a23c"><Opportunity /></el-icon>{{ r.reason }}</div>
                </div>
              </template>
              <el-empty
                v-else-if="recommendChecked && !recommendLoading"
                description="未找到可推荐的知识点（请确认已掌握知识点名称与图谱一致）"
                :image-size="60"
              />
            </el-card>
          </el-col>

          <!-- 目标路径 + 前置知识 -->
          <el-col :span="12">
            <el-card class="page-card">
              <template #header>
                <b><el-icon class="icon-gap" color="#409eff"><Guide /></el-icon>到达目标知识点的学习路径</b>
              </template>
              <p class="tip">输入目标知识点，系统自动生成从入门到该知识点的最短学习路径</p>
              <div class="btn-row">
                <el-input v-model="targetKnowledge" placeholder="目标知识点，如：二叉树的遍历" style="max-width: 320px" />
                <el-button type="primary" :loading="pathLoading" @click="doPathToTarget">
                  生成路径
                </el-button>
              </div>

              <template v-if="paths.length">
                <el-divider content-position="left">共 {{ paths.length }} 条路径</el-divider>
                <div v-for="(path, pi) in paths" :key="pi" class="path-chain">
                  <div class="path-index">路径 {{ pi + 1 }}</div>
                  <div class="path-steps">
                    <template v-for="(step, si) in path" :key="si">
                      <span class="path-step">
                        <el-tag size="small">{{ step.category || '知识点' }}</el-tag>
                        <span class="step-name">{{ step.name }}</span>
                      </span>
                      <span v-if="si < path.length - 1" class="path-arrow">→</span>
                    </template>
                  </div>
                </div>
              </template>
              <template v-else-if="pathFallback">
                <div class="rec-item">
                  <div class="rec-name">
                    <el-tag size="small" :type="categoryTagType(pathTargetNode?.category)">
                      {{ pathTargetNode?.category || '知识点' }}
                    </el-tag>
                    <b>{{ pathTargetNode?.name || targetKnowledge }}</b>
                  </div>
                  <div class="rec-desc">{{ pathTargetNode?.description }}</div>
                  <div class="rec-reason"><el-icon class="icon-gap" color="#e6a23c"><Opportunity /></el-icon>{{ pathReason }}</div>
                </div>
                <el-divider content-position="left">相关概念（{{ pathRelated.length }}）</el-divider>
                <div v-for="r in pathRelated" :key="r.name" class="rec-item">
                  <div class="rec-name">
                    <el-tag size="small" :type="categoryTagType(r.category)">{{ r.category }}</el-tag>
                    <b>{{ r.name }}</b>
                  </div>
                  <div class="rec-desc">{{ r.description }}</div>
                </div>
              </template>
              <el-empty
                v-else-if="pathChecked && !pathLoading"
                description="未找到该知识点（请确认名称与图谱一致）"
                :image-size="60"
              />
            </el-card>

            <el-card class="page-card">
              <template #header>
                <b><el-icon class="icon-gap" color="#409eff"><Search /></el-icon>前置知识查询</b>
              </template>
              <div class="btn-row">
                <el-input v-model="prereqName" placeholder="知识点名称" style="max-width: 320px" />
                <el-button :loading="prereqLoading" @click="doQueryPrereqs">查询</el-button>
              </div>
              <template v-if="prereqResult && prereqResult.count">
                <el-divider content-position="left">共 {{ prereqResult.count }} 个前置知识</el-divider>
                <div v-for="p in prereqResult.prerequisites" :key="p.name" class="rec-item">
                  <div class="rec-name">
                    <el-tag size="small" type="info">{{ p.reason || `${p.depth} 级前置` }}</el-tag>
                    <b>{{ p.name }}</b>
                  </div>
                  <div class="rec-desc">{{ p.description }}</div>
                </div>
              </template>
              <el-empty
                v-else-if="prereqResult && !prereqLoading"
                description="未查询到前置知识（请确认知识点名称准确）"
                :image-size="60"
              />
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>
    </el-tabs>

    <!-- 知识点详情抽屉（只读） -->
    <NodeDetailDrawer
      v-model="drawerVisible"
      :node="drawerNode"
      :course-id="browseCourseId"
      show-mastery
      :mastered="drawerMastered"
      :mastery-loading="masteryLoading"
      show-expand
      :expanded="drawerExpanded"
      :related="drawerRelated"
      @toggle-mastery="onToggleMastery"
      @expand-toggle="onExpandToggle"
      @jump-to="onJumpToNode"
    />
  </div>
</template>

<script setup>
import { ref, nextTick, watch, computed } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Refresh, Compass, ChatDotRound, Guide, Aim, Search, Document, Opportunity, MagicStick, ArrowDown, CircleCheckFilled, Right, Collection } from '@element-plus/icons-vue'
import { api } from '../api'
import GraphCanvas from '../components/GraphCanvas.vue'
import CourseSelector from '../components/CourseSelector.vue'
import NodeDetailDrawer from '../components/NodeDetailDrawer.vue'

const route = useRoute()

// 支持从菜单直接进入指定子功能（/student?tab=qa）
const activeTab = ref(route.query.tab || 'browse')
watch(
  () => route.query.tab,
  (tab) => {
    if (tab) activeTab.value = tab
  }
)

// ===================== 图谱浏览 =====================
const browseGraphRef = ref(null)
const browseCourseId = ref('')
const searchText = ref('')
const stats = ref(null)
const drawerVisible = ref(false)
const drawerNode = ref(null)
const drawerRelated = ref([])
const drawerExpanded = ref(false)

// ===================== 学习记录（掌握标记） =====================
const masteredKpIds = ref([])
const masteryLoading = ref(false)
const drawerMastered = computed(() =>
  drawerNode.value ? masteredKpIds.value.includes(String(drawerNode.value.id)) : false
)

async function loadProgress() {
  if (!browseCourseId.value) {
    masteredKpIds.value = []
    return
  }
  try {
    const res = await api.getProgress(browseCourseId.value)
    masteredKpIds.value = res.mastered_kp_ids || []
  } catch (e) {
    masteredKpIds.value = []
  }
}

async function onToggleMastery() {
  const node = drawerNode.value
  if (!node || !browseCourseId.value) return
  const kpId = String(node.id)
  const isMastered = masteredKpIds.value.includes(kpId)
  masteryLoading.value = true
  try {
    if (isMastered) {
      await api.unmarkMastered(browseCourseId.value, kpId)
      masteredKpIds.value = masteredKpIds.value.filter((id) => id !== kpId)
      ElMessage.success('已取消掌握标记')
    } else {
      await api.markMastered(browseCourseId.value, kpId)
      masteredKpIds.value = [...masteredKpIds.value, kpId]
      ElMessage.success('已标记为掌握')
    }
  } catch (e) {
    ElMessage.error(`操作失败：${e.message}`)
  } finally {
    masteryLoading.value = false
  }
}

function refreshBrowse() {
  browseGraphRef.value?.refresh()
}
function onNodeClick(node, info) {
  drawerNode.value = node
  drawerRelated.value = [...(info?.successors || []), ...(info?.related || [])]
  drawerExpanded.value = !!info?.expanded
  drawerVisible.value = true
}

// P6：展开/收起当前节点的相关知识（局部展开模式）
function onExpandToggle() {
  if (!drawerNode.value || !browseGraphRef.value) return
  drawerExpanded.value = browseGraphRef.value.toggleExpand(String(drawerNode.value.id))
}

// P6：点击「相关知识」项 → 图谱聚焦并联动更新抽屉
function onJumpToNode(node) {
  if (!node || !browseGraphRef.value) return
  browseGraphRef.value.selectNode(String(node.id))
}

// 切换课程时重新加载该课程的学习进度
watch(browseCourseId, loadProgress)

// ===================== 智能问答 =====================
const qaCourseId = ref('')
const question = ref('')
const asking = ref(false)
const messages = ref([])
const chatBoxRef = ref(null)

async function sendQuestion() {
  const q = question.value.trim()
  if (!q || asking.value) return
  question.value = ''
  messages.value.push({ role: 'user', content: q })
  asking.value = true
  scrollChatToBottom()
  try {
    const res = await api.ask(q, qaCourseId.value)
    messages.value.push({
      role: 'ai',
      content: res.answer || '（无回答）',
      sources: (res.sources || []).map(normalizeSource),
    })
  } catch (e) {
    messages.value.push({ role: 'ai', content: `回答失败：${e.message}` })
  } finally {
    asking.value = false
    scrollChatToBottom()
  }
}

function scrollChatToBottom() {
  nextTick(() => {
    if (chatBoxRef.value) {
      chatBoxRef.value.scrollTop = chatBoxRef.value.scrollHeight
    }
  })
}

function clearChat() {
  messages.value = []
}

// 引用来源归一化：后端返回结构化对象；兼容旧字符串格式（[类别] 名称: 描述）
function normalizeSource(s) {
  if (typeof s !== 'string') return s || {}
  const m = s.match(/^\[(.+?)\]\s*(.+?)(?::\s*([\s\S]*))?$/)
  if (m) return { category: m[1], name: m[2].trim(), description: (m[3] || '').trim() }
  return { category: '', name: s, description: '' }
}

// 左栏「相关知识点」= 最近一条 AI 回答的引用来源
const relatedKps = computed(() => {
  for (let i = messages.value.length - 1; i >= 0; i--) {
    const m = messages.value[i]
    if (m.role === 'ai' && m.sources?.length) return m.sources
  }
  return []
})

// 点击引用/相关知识点 → 跳图谱浏览并高亮该知识点（原文/证据链字段将在 P6/P8 补充）
function jumpToKp(kp) {
  if (!kp || !kp.name) return
  browseCourseId.value = qaCourseId.value
  highlightPathNodes.value = [kp.name]
  activeTab.value = 'browse'
}

// ===================== 学习路径推荐 =====================
const pathCourseId = ref('')
const masteredText = ref('')
const recommendations = ref([])
const recommendLoading = ref(false)
const recommendChecked = ref(false)

const targetKnowledge = ref('')
const paths = ref([])
const pathLoading = ref(false)
const pathChecked = ref(false)
const pathFallback = ref(false)
const pathTargetNode = ref(null)
const pathRelated = ref([])
const pathReason = ref('')
const highlightPathNodes = ref([])

const prereqName = ref('')
const prereqResult = ref(null)
const prereqLoading = ref(false)

async function doRecommend() {
  const mastered = masteredText.value
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)
  recommendLoading.value = true
  try {
    const res = await api.recommendNext(mastered, pathCourseId.value)
    recommendations.value = res.recommendations || []
    recommendChecked.value = true
  } catch (e) {
    ElMessage.error(`推荐失败：${e.message}`)
  } finally {
    recommendLoading.value = false
  }
}

async function doPathToTarget() {
  if (!targetKnowledge.value.trim()) {
    ElMessage.warning('请输入目标知识点')
    return
  }
  pathLoading.value = true
  try {
    const res = await api.pathToTarget(targetKnowledge.value.trim(), pathCourseId.value)
    paths.value = res.paths || []
    pathFallback.value = !!res.fallback
    pathTargetNode.value = res.target_node || null
    pathRelated.value = res.related || []
    pathReason.value = res.reason || ''
    pathChecked.value = true
    // 有真实路径时，高亮到图谱（对齐课程 + 切到浏览 Tab）
    if (res.paths && res.paths.length && pathCourseId.value) {
      highlightPathNodes.value = res.paths[0].map((s) => s.name)
      browseCourseId.value = pathCourseId.value
      activeTab.value = 'browse'
      ElMessage.success('已生成路径，正在图谱中高亮')
    } else {
      highlightPathNodes.value = []
    }
  } catch (e) {
    ElMessage.error(`生成失败：${e.message}`)
  } finally {
    pathLoading.value = false
  }
}

async function doQueryPrereqs() {
  if (!prereqName.value.trim()) {
    ElMessage.warning('请输入知识点名称')
    return
  }
  prereqLoading.value = true
  prereqResult.value = null
  try {
    prereqResult.value = await api.getPrerequisites(prereqName.value.trim(), pathCourseId.value)
  } catch (e) {
    ElMessage.error(`查询失败：${e.message}`)
  } finally {
    prereqLoading.value = false
  }
}

function categoryTagType(category) {
  const map = { 概念: 'primary', 定理: 'danger', 公式: 'warning', 方法: 'success' }
  return map[category] || 'info'
}

// ===================== 学习路径视觉层级（P5：已掌握/当前/推荐/未学习） =====================
const pathMasteredIds = ref([])
const pathGraphNodes = ref([])
const pathGraphEdges = ref([])
const pathRecs = ref([])
const pathDataLoading = ref(false)
const pathDataChecked = ref(false)

const STATE_META = {
  mastered: { label: '已掌握', glyph: '✓' },
  current: { label: '当前学习', glyph: '●' },
  recommended: { label: '推荐学习', glyph: '★' },
  unlearned: { label: '未学习', glyph: '○' },
}

async function loadPathData() {
  if (!pathCourseId.value) {
    pathMasteredIds.value = []
    pathGraphNodes.value = []
    pathGraphEdges.value = []
    pathRecs.value = []
    pathDataChecked.value = false
    return
  }
  pathDataLoading.value = true
  try {
    const [graph, progress] = await Promise.all([
      api.getGraphV1(pathCourseId.value, { limit: 800 }),
      api.getProgress(pathCourseId.value),
    ])
    pathGraphNodes.value = graph.nodes || []
    pathGraphEdges.value = graph.edges || []
    pathMasteredIds.value = progress.mastered_kp_ids || []

    // 已掌握 kp_id → 名称（recommendNext 入参需名称）
    const byId = new Map(pathGraphNodes.value.map((n) => [String(n.id), n]))
    const masteredNames = pathMasteredIds.value
      .map((id) => byId.get(String(id))?.label)
      .filter(Boolean)
    const res = await api.recommendNext(masteredNames, pathCourseId.value)
    pathRecs.value = res.recommendations || []
  } catch (e) {
    pathRecs.value = []
  } finally {
    pathDataLoading.value = false
    pathDataChecked.value = true
  }
}

watch(pathCourseId, loadPathData)

const pathProgress = computed(() => {
  const total = pathGraphNodes.value.length
  const mastered = pathMasteredIds.value.length
  const pct = total ? Math.round((mastered / total) * 100) : 0
  return { total, mastered, pct, current: pathRecs.value[0]?.name || '' }
})

// 基于图谱 PRECEDES 边 + 学习状态，构建「已掌握 → 当前 → 推荐 → 未学习」展示链
const pathChain = computed(() => {
  const nodes = pathGraphNodes.value
  if (!nodes.length) return []

  const byId = new Map(nodes.map((n) => [String(n.id), n]))
  const byName = new Map(nodes.map((n) => [n.label, n]))
  const masteredSet = new Set(pathMasteredIds.value.map(String))

  const succ = new Map() // name -> [name]（PRECEDES：source 是 target 的前置）
  const pred = new Map()
  for (const e of pathGraphEdges.value) {
    if (e.type !== 'PRECEDES') continue
    const s = byId.get(String(e.source))?.label
    const t = byId.get(String(e.target))?.label
    if (!s || !t || s === t) continue
    if (!succ.has(s)) succ.set(s, [])
    succ.get(s).push(t)
    if (!pred.has(t)) pred.set(t, [])
    pred.get(t).push(s)
  }

  const current = pathRecs.value[0]
  if (!current) return []

  const segs = []
  const used = new Set()

  // 1) 已掌握：current 的直接前置（且已掌握）
  for (const pname of pred.get(current.name) || []) {
    const node = byName.get(pname)
    if (node && masteredSet.has(String(node.id)) && !used.has(node.id)) {
      used.add(node.id)
      segs.push({ node, state: 'mastered', reason: '' })
    }
  }

  // 2) 当前学习
  const curNode = byName.get(current.name)
  if (curNode && !used.has(curNode.id)) {
    used.add(curNode.id)
    segs.push({ node: curNode, state: 'current', reason: current.reason || '' })
  }

  // 3) 推荐学习：current 的第一个后继
  const nexts = succ.get(current.name) || []
  const firstNext = nexts[0]
  if (firstNext) {
    const rNode = byName.get(firstNext)
    if (rNode && !used.has(rNode.id)) {
      used.add(rNode.id)
      segs.push({ node: rNode, state: 'recommended', reason: `前置知识「${current.name}」掌握后即可学习` })
    }
    // 4) 未学习：推荐节点的后继（再进一步）
    const next2 = succ.get(firstNext) || []
    for (const n2name of next2) {
      const n2 = byName.get(n2name)
      if (n2 && !used.has(n2.id)) {
        used.add(n2.id)
        segs.push({ node: n2, state: 'unlearned', reason: '' })
        break
      }
    }
  }

  return segs
})

function startLearning() {
  const current = pathRecs.value[0]
  if (!current) return
  browseCourseId.value = pathCourseId.value
  highlightPathNodes.value = [current.name]
  activeTab.value = 'browse'
  ElMessage.success(`开始学习「${current.name}」`)
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

/* 问答 */
.qa-card {
  max-width: 100%;
}
.qa-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.chat-box {
  height: 460px;
  padding: 12px;
  background: #fafbfc;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
}
.chat-welcome {
  text-align: center;
  color: #909399;
  margin-top: 120px;
}
.welcome-icon {
  font-size: 44px;
}
.welcome-sub {
  font-size: 13px;
}
.msg-row {
  display: flex;
  margin-bottom: 12px;
}
.msg-user {
  justify-content: flex-end;
}
.msg-ai {
  justify-content: flex-start;
}
.msg-bubble {
  max-width: 78%;
  padding: 10px 14px;
  border-radius: 10px;
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.msg-user .msg-bubble {
  background: #409eff;
  color: #fff;
}
.msg-ai .msg-bubble {
  background: #fff;
  border: 1px solid #e4e7ed;
}
.msg-sources {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed #e4e7ed;
}
.sources-title {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: #909399;
  margin-bottom: 8px;
}
.source-card {
  padding: 8px 10px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  margin-bottom: 6px;
  background: #fafbfc;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.source-card:hover {
  background: #f0f7ff;
  border-color: #d9ecff;
}
.source-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.source-name {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.source-jump {
  color: #c0c4cc;
  flex-shrink: 0;
}
.source-card:hover .source-jump {
  color: #409eff;
}
.source-desc {
  margin-top: 6px;
  font-size: 12px;
  color: #909399;
  line-height: 1.5;
}
.typing {
  display: flex;
  gap: 5px;
  align-items: center;
  padding: 14px 16px;
}
.typing-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #c0c4cc;
  animation: blink 1.2s infinite;
}
.typing-dot:nth-child(2) {
  animation-delay: 0.2s;
}
.typing-dot:nth-child(3) {
  animation-delay: 0.4s;
}
@keyframes blink {
  0%,
  100% {
    opacity: 0.25;
  }
  50% {
    opacity: 1;
  }
}
.chat-input {
  display: flex;
  gap: 10px;
  margin-top: 12px;
}
.qa-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}
.qa-side-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  font-size: 14px;
  color: #303133;
}
.qa-side :deep(.course-selector) {
  flex-wrap: wrap;
  width: 100%;
}
.qa-side :deep(.course-selector .el-select) {
  width: 100% !important;
}
.kp-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.kp-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid #f0f2f5;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.kp-item:hover {
  background: #f0f7ff;
  border-color: #d9ecff;
}
.kp-name {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.kp-jump {
  color: #c0c4cc;
  flex-shrink: 0;
}
.kp-item:hover .kp-jump {
  color: #409eff;
}

/* 路径推荐 */
.tip {
  color: #909399;
  font-size: 13px;
  margin: 0 0 10px;
}
.btn-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 10px;
  flex-wrap: wrap;
}
.rec-item {
  padding: 10px 0;
  border-bottom: 1px dashed #e4e7ed;
}
.rec-name {
  display: flex;
  align-items: center;
  gap: 8px;
}
.rec-desc {
  color: #606266;
  font-size: 13px;
  margin: 6px 0 4px;
}
.rec-reason {
  color: #909399;
  font-size: 12px;
}
.path-chain {
  margin-bottom: 14px;
  padding: 10px;
  background: #fafbfc;
  border-radius: 8px;
}
.path-index {
  font-size: 12px;
  color: #909399;
  margin-bottom: 8px;
}
.path-steps {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}
.path-step {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 4px 8px;
}
.step-name {
  font-size: 13px;
  font-weight: 600;
}
.path-arrow {
  color: #67c23a;
  font-weight: 700;
}
.icon-gap {
  vertical-align: -2px;
  margin-right: 4px;
}

/* ===== P5 学习路径视觉层级 ===== */
.path-hero-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.path-hero-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}
.path-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 14px 16px;
  background: #fafbfc;
  border: 1px solid #f0f2f5;
  border-radius: 10px;
  margin-bottom: 16px;
}
.summary-left {
  flex: 1;
  min-width: 0;
}
.summary-line {
  font-size: 14px;
  color: #606266;
}
.summary-line .num {
  font-size: 22px;
  font-weight: 700;
  color: #409eff;
  margin: 0 2px;
}
.summary-line .muted {
  color: #909399;
  font-size: 13px;
}
.summary-suggestion {
  margin-top: 6px;
  font-size: 13px;
  color: #409eff;
  font-weight: 600;
}
.summary-suggestion.success {
  color: #67c23a;
}
.summary-suggestion.muted {
  color: #909399;
  font-weight: 400;
}
.summary-progress {
  width: 220px;
  flex-shrink: 0;
}
.path-chain-wrap {
  min-height: 120px;
}
.path-chain {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 8px 0;
}
.chain-node {
  display: flex;
  gap: 12px;
  width: 100%;
  max-width: 560px;
  padding: 12px 16px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-left: 4px solid #c0c4cc;
  border-radius: 10px;
  box-shadow: var(--shadow-card);
}
.chain-node.state-mastered {
  border-left-color: #67c23a;
}
.chain-node.state-current {
  border-left-color: #409eff;
  background: #f0f7ff;
  border-color: #d9ecff;
  box-shadow: 0 4px 16px rgba(64, 158, 255, 0.15);
}
.chain-node.state-recommended {
  border-left-color: #e6a23c;
}
.chain-node.state-unlearned {
  border-left-color: #dcdfe6;
  opacity: 0.72;
}
.chain-icon {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  font-weight: 700;
  flex-shrink: 0;
}
.state-mastered .chain-icon {
  background: #f0f9eb;
  color: #67c23a;
}
.state-current .chain-icon {
  background: #409eff;
  color: #fff;
}
.state-recommended .chain-icon {
  background: #fdf6ec;
  color: #e6a23c;
}
.state-unlearned .chain-icon {
  background: #f5f7fa;
  color: #c0c4cc;
}
.chain-content {
  flex: 1;
  min-width: 0;
}
.chain-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.chain-name {
  font-size: 15px;
  font-weight: 700;
  color: #303133;
}
.state-label {
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 10px;
  font-weight: 600;
}
.state-label-mastered {
  color: #67c23a;
  background: #f0f9eb;
}
.state-label-current {
  color: #409eff;
  background: #ecf5ff;
}
.state-label-recommended {
  color: #e6a23c;
  background: #fdf6ec;
}
.state-label-unlearned {
  color: #909399;
  background: #f4f4f5;
}
.chain-reason {
  margin-top: 6px;
  font-size: 13px;
  color: #606266;
}
.chain-status {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
}
.chain-link {
  display: flex;
  align-items: center;
  gap: 4px;
  color: #c0c4cc;
  font-size: 12px;
  padding: 4px 0;
}
.path-actions {
  display: flex;
  justify-content: center;
  margin-top: 16px;
}
</style>
