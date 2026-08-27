<template>
  <div>
    <div class="page-header">
      <h2 class="page-title">👨‍🎓 学习空间</h2>
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
              节点 {{ stats.nodeCount }} · 关系 {{ stats.edgeCount }}
            </span>
          </div>
        </el-card>
        <el-card class="page-card graph-card">
          <GraphCanvas
            ref="browseGraphRef"
            :course-id="browseCourseId"
            :search-text="searchText"
            @node-click="onNodeClick"
            @loaded="(s) => (stats = s)"
          />
        </el-card>
      </el-tab-pane>

      <!-- ===================== Tab 2：智能问答 ===================== -->
      <el-tab-pane name="qa">
        <template #label><span class="tab-label"><el-icon><ChatDotRound /></el-icon>智能问答</span></template>
        <el-card class="qa-card">
          <div class="qa-toolbar">
            <CourseSelector v-model="qaCourseId" />
            <el-button size="small" @click="clearChat">清空对话</el-button>
          </div>

          <div ref="chatBoxRef" class="chat-box chat-scroll">
            <div v-if="!messages.length" class="chat-welcome">
              <div class="welcome-icon">🤖</div>
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
                <template v-if="m.sources && m.sources.length">
                  <el-collapse class="msg-sources">
                    <el-collapse-item title="📖 参考来源" name="src">
                      <div v-for="(s, j) in m.sources" :key="j" class="source-line">
                        {{ s }}
                      </div>
                    </el-collapse-item>
                  </el-collapse>
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
      </el-tab-pane>

      <!-- ===================== Tab 3：学习路径推荐 ===================== -->
      <el-tab-pane name="path">
        <template #label><span class="tab-label"><el-icon><Guide /></el-icon>学习路径推荐</span></template>
        <el-row :gutter="16">
          <!-- 推荐下一步 -->
          <el-col :span="12">
            <el-card class="page-card">
              <template #header>
                <b>📌 推荐下一步学习内容</b>
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
                  <div class="rec-reason">💡 {{ r.reason }}</div>
                </div>
              </template>
              <el-empty
                v-else-if="recommendChecked && !recommendLoading"
                description="暂无推荐结果"
                :image-size="60"
              />
            </el-card>
          </el-col>

          <!-- 目标路径 + 前置知识 -->
          <el-col :span="12">
            <el-card class="page-card">
              <template #header>
                <b>🧭 到达目标知识点的学习路径</b>
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
              <el-empty
                v-else-if="pathChecked && !pathLoading"
                description="暂无路径（请确认目标知识点名称准确）"
                :image-size="60"
              />
            </el-card>

            <el-card class="page-card">
              <template #header>
                <b>🔎 前置知识查询</b>
              </template>
              <div class="btn-row">
                <el-input v-model="prereqName" placeholder="知识点名称" style="max-width: 320px" />
                <el-button :loading="prereqLoading" @click="doQueryPrereqs">查询</el-button>
              </div>
              <template v-if="prereqResult">
                <el-divider content-position="left">共 {{ prereqResult.count }} 个前置知识</el-divider>
                <div v-for="p in prereqResult.prerequisites" :key="p.name" class="rec-item">
                  <div class="rec-name">
                    <el-tag size="small" type="info">{{ p.depth }} 级前置</el-tag>
                    <b>{{ p.name }}</b>
                  </div>
                  <div class="rec-desc">{{ p.description }}</div>
                </div>
              </template>
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
    />
  </div>
</template>

<script setup>
import { ref, nextTick, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Refresh, Compass, ChatDotRound, Guide } from '@element-plus/icons-vue'
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

function refreshBrowse() {
  browseGraphRef.value?.refresh()
}
function onNodeClick(node) {
  drawerNode.value = node
  drawerVisible.value = true
}

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
      sources: res.sources || [],
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
    ElMessage.error(`推荐失败：${e.message}（注：后端该接口仍使用旧图模型，需迁移到 KnowledgePoint/PRECEDES）`)
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
    pathChecked.value = true
  } catch (e) {
    ElMessage.error(`生成失败：${e.message}（注：后端该接口仍使用旧图模型，需迁移到 KnowledgePoint/PRECEDES）`)
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
    ElMessage.error(`查询失败：${e.message}（注：后端该接口仍使用旧图模型，需迁移到 KnowledgePoint/PRECEDES）`)
  } finally {
    prereqLoading.value = false
  }
}

function categoryTagType(category) {
  const map = { 概念: 'primary', 定理: 'danger', 公式: 'warning', 方法: 'success' }
  return map[category] || 'info'
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
  max-width: 860px;
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
  margin-top: 8px;
  font-size: 12px;
}
.source-line {
  color: #909399;
  padding: 2px 0;
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
</style>
