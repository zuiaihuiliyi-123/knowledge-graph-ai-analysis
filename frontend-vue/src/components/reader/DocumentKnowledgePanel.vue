<template>
  <aside class="dkp">
    <!-- 顶栏：模式切换 -->
    <div class="dkp-tabs">
      <button class="dkp-tab" :class="{ 'is-on': tab === 'kp' }" type="button" @click="tab = 'kp'">
        <el-icon><Collection /></el-icon>知识点
        <span v-if="nodes.length" class="dkp-tab-count">{{ nodes.length }}</span>
      </button>
      <!--
        AI 助手对师生都开放：它是阅读辅助（解释 / 总结 / 出题），不是学习记录，
        与「掌握 / 收藏 / 笔记」这类学生个人数据不同
      -->
      <button class="dkp-tab" :class="{ 'is-on': tab === 'ai' }" type="button" @click="tab = 'ai'">
        <el-icon><ChatDotRound /></el-icon>AI 助手
      </button>
      <button
        v-if="isStudent"
        class="dkp-tab"
        :class="{ 'is-on': tab === 'notes' }"
        type="button"
        @click="tab = 'notes'"
      >
        <el-icon><Notebook /></el-icon>笔记
        <span v-if="notes.length" class="dkp-tab-count">{{ notes.length }}</span>
      </button>
      <button class="dkp-close" type="button" title="收起面板" @click="emit('close')">
        <el-icon><DArrowRight /></el-icon>
      </button>
    </div>

    <!-- ============ 知识点 ============ -->
    <template v-if="tab === 'kp'">
      <div class="dkp-toolbar">
        <div class="dkp-filter">
          <el-icon class="dkp-filter-icon"><Search /></el-icon>
          <input v-model="filterText" class="dkp-filter-input" type="text" placeholder="筛选知识点" />
          <button v-if="filterText" class="dkp-filter-clear" type="button" @click="filterText = ''">
            <el-icon><Close /></el-icon>
          </button>
        </div>
        <button class="dkp-graph-btn" type="button" title="在知识图谱中查看本文档" @click="emit('open-graph')">
          <el-icon><Share /></el-icon>查看图谱
        </button>
      </div>

      <div v-loading="loading" class="dkp-body">
        <el-alert v-if="loadError" type="warning" :closable="false" class="dkp-alert">
          <template #title>知识点加载失败</template>
          <div class="dkp-alert-body">
            {{ loadError }}
            <el-button size="small" text type="primary" @click="loadKnowledge">重试</el-button>
          </div>
        </el-alert>

        <el-empty
          v-else-if="!loading && !nodes.length"
          description="本文档暂无知识点"
          :image-size="70"
        >
          <div class="dkp-empty-hint">可能知识抽取尚未完成或未识别到有效知识点</div>
        </el-empty>

        <template v-else>
          <!--
            第一组「当前阅读位置提到的」由正文文本实时匹配得出，不是预置的页码映射；
            第二组是文档全部知识点。两组共用同一套行渲染。
          -->
          <section v-for="g in groups" :key="g.key" class="dkp-section">
            <div class="dkp-section-title">
              <el-icon><component :is="g.icon" /></el-icon>
              {{ g.title }}
              <span class="dkp-section-note">{{ g.note }}</span>
            </div>
            <ul class="dkp-list">
              <li v-for="n in g.items" :key="g.key + '-' + n.id" class="dkp-item">
                <div class="dkp-row">
                  <div
                    class="dkp-row-head"
                    :class="{ 'is-open': expandedId === g.key + '::' + n.id }"
                    role="button"
                    tabindex="0"
                    @click="toggleExpand(g.key, n)"
                    @keydown.enter.prevent="toggleExpand(g.key, n)"
                    @keydown.space.prevent="toggleExpand(g.key, n)"
                  >
                    <span class="dkp-dot" :class="'cat-' + (n.category || 'other')" />
                    <span class="dkp-name">{{ n.label }}</span>
                    <span class="dkp-cat">{{ n.category }}</span>
                    <span v-if="mentionedIds.has(n.id)" class="dkp-flag is-here" title="当前阅读位置出现">当前</span>
                    <span v-if="masteredIds.has(n.id)" class="dkp-flag is-mastered" title="已掌握">✓</span>
                    <span v-if="favoriteIds.has(n.id)" class="dkp-flag is-fav" title="已收藏">★</span>
                  </div>

                  <div v-if="expandedId === g.key + '::' + n.id" class="dkp-detail">
                    <p class="dkp-desc">{{ n.description || '（该知识点暂无描述）' }}</p>
                    <div class="dkp-actions">
                      <button
                        v-if="canLocate"
                        class="dkp-act"
                        type="button"
                        title="在正文中找到这个词并跳过去"
                        @click="locate(n)"
                      >
                        <el-icon><Location /></el-icon>定位正文
                      </button>
                      <button
                        class="dkp-act"
                        type="button"
                        title="让 AI 结合正文讲解这个知识点"
                        @click="explainNode(n)"
                      >
                        <el-icon><MagicStick /></el-icon>AI 讲解
                      </button>
                      <button
                        v-if="isStudent"
                        class="dkp-act"
                        :class="{ 'is-on': masteredIds.has(n.id) }"
                        type="button"
                        @click="toggleMastery(n)"
                      >
                        <el-icon><Select /></el-icon>{{ masteredIds.has(n.id) ? '取消掌握' : '标记掌握' }}
                      </button>
                      <button
                        v-if="isStudent"
                        class="dkp-act"
                        :class="{ 'is-on': favoriteIds.has(n.id) }"
                        type="button"
                        @click="toggleFavorite(n)"
                      >
                        <el-icon><component :is="favoriteIds.has(n.id) ? StarFilled : Star" /></el-icon>
                        {{ favoriteIds.has(n.id) ? '取消收藏' : '收藏' }}
                      </button>
                    </div>
                  </div>
                </div>
              </li>
            </ul>
          </section>
        </template>
      </div>
    </template>

    <!-- ============ AI 助手 ============ -->
    <template v-else-if="tab === 'ai'">
      <div class="dkp-qa">
        <div ref="qaScrollEl" class="dkp-chat">
          <div v-if="!chat.length" class="dkp-chat-empty">
            <el-icon :size="22"><ChatDotRound /></el-icon>
            <p>用下面的快捷操作让 AI 结合本文档帮你读，也可以直接提问。<br />回答会给出依据的知识点来源。</p>
          </div>

          <div v-for="(m, i) in chat" :key="i" class="dkp-msg" :class="'is-' + m.role">
            <!-- 由阅读动作发起的提问：气泡只展示动作与原文片段，完整提示词不发回界面 -->
            <div v-if="m.display" class="dkp-msg-body is-action">
              <span class="dkp-act-tag">{{ m.display.label }}</span>
              <span v-if="m.display.preview" class="dkp-act-preview" :title="m.display.preview">
                {{ m.display.preview }}
              </span>
            </div>
            <div v-else class="dkp-msg-body">{{ m.text }}</div>
            <!-- 上下文被截断时明确告知：否则用户以为 AI 读的是全文 -->
            <div v-if="m.clipped" class="dkp-clip-note">
              本次仅发送正文前 {{ MAX_CONTEXT }} 字（当前内容共 {{ m.rawLen }} 字），超出部分 AI 未读到
            </div>

            <div v-if="m.sources?.length" class="dkp-msg-src">
              <span class="dkp-src-label">依据</span>
              <span v-for="s in m.sources" :key="s.kp_id" class="dkp-src-wrap">
                <button
                  class="dkp-src-chip"
                  type="button"
                  :title="s.description || s.name"
                  @click="locateByName(s.name)"
                >{{ s.name }}</button>
                <button
                  v-if="isStudent && s.kp_id"
                  class="dkp-src-fav"
                  type="button"
                  :class="{ 'is-on': favoriteIds.has(String(s.kp_id)) }"
                  :title="favoriteIds.has(String(s.kp_id)) ? '取消收藏' : '收藏这个知识点'"
                  @click="toggleFavorite({ id: String(s.kp_id) })"
                >
                  <el-icon><component :is="favoriteIds.has(String(s.kp_id)) ? StarFilled : Star" /></el-icon>
                </button>
              </span>
            </div>

            <!-- 回答级操作：加入笔记走本地笔记，收藏走既有收藏接口 -->
            <div v-if="m.role === 'ai' && !m.error && m.text" class="dkp-msg-acts">
              <button class="dkp-act" type="button" title="让 AI 就这段回答继续追问" @click="quoteQuestion(m.text)">
                <el-icon><ChatLineSquare /></el-icon>追问
              </button>
              <button
                v-if="isStudent"
                class="dkp-act"
                type="button"
                title="把这段回答存进本文档的笔记"
                @click="saveAnswerNote(m)"
              >
                <el-icon><CollectionTag /></el-icon>加入笔记
              </button>
            </div>
          </div>

          <div v-if="asking" class="dkp-msg is-ai">
            <div class="dkp-msg-body is-typing">正在结合本文档思考…</div>
          </div>
        </div>

        <!-- 快捷阅读动作：全部复用既有问答接口，只是把「读出来的上下文」拼进问题 -->
        <div class="dkp-quick">
          <button
            v-for="a in quickActions"
            :key="a.key"
            class="dkp-quick-btn"
            type="button"
            :disabled="asking || !hasContext"
            :title="a.title"
            @click="runAction(a.key)"
          >{{ a.label }}</button>
          <span v-if="!hasContext" class="dkp-quick-hint">正文尚未就绪</span>
        </div>

        <!-- 有选中内容时常驻一条入口，不必回到浮动工具条 -->
        <div v-if="selectionText" class="dkp-selection">
          <span class="dkp-selection-label">已选中</span>
          <span class="dkp-selection-text" :title="selectionText">{{ selectionText }}</span>
          <button class="dkp-selection-act" type="button" @click="runAction('explain', selectionText)">
            解释
          </button>
          <button class="dkp-selection-act" type="button" @click="runAction('selectionSummary', selectionText)">
            总结
          </button>
        </div>

        <div class="dkp-ask">
          <textarea
            v-model="question"
            class="dkp-ask-input"
            rows="2"
            placeholder="例如：二叉树的前序遍历是怎么做的？"
            @keydown.enter.exact.prevent="submitAsk"
          />
          <button class="dkp-ask-btn" type="button" :disabled="asking || !question.trim()" @click="submitAsk">
            {{ asking ? '思考中…' : '提问' }}
          </button>
        </div>
      </div>
    </template>

    <!-- ============ 学习笔记（学生） ============ -->
    <template v-else>
      <div class="dkp-toolbar">
        <div class="dkp-filter">
          <el-icon class="dkp-filter-icon"><Search /></el-icon>
          <input v-model="noteFilter" class="dkp-filter-input" type="text" placeholder="筛选笔记" />
          <button v-if="noteFilter" class="dkp-filter-clear" type="button" @click="noteFilter = ''">
            <el-icon><Close /></el-icon>
          </button>
        </div>
        <button class="dkp-graph-btn ghost" type="button" title="在笔记里手动记一条" @click="addManualNote">
          <el-icon><EditPen /></el-icon>记笔记
        </button>
      </div>

      <div class="dkp-body">
        <el-alert
          class="dkp-notes-local-tip"
          type="warning"
          :closable="false"
          show-icon
          title="笔记只保存在当前浏览器（本机），更换设备或清除缓存会丢失，建议定期导出。"
        >
          <template #default>
            <el-button link type="primary" size="small" @click="exportNotes">导出全部笔记</el-button>
          </template>
        </el-alert>
        <el-empty
          v-if="!filteredNotes.length"
          description="本文档还没有笔记"
          :image-size="70"
        >
          <div class="dkp-empty-hint">
            选中正文可以「加入笔记」，AI 回答也能一键存成笔记
          </div>
        </el-empty>

        <ul v-else class="dkp-notes">
          <li v-for="n in filteredNotes" :key="n.id" class="dkp-note">
            <div class="dkp-note-head">
              <span class="dkp-note-tag">{{ n.source }}</span>
              <span v-if="n.page" class="dkp-note-page">第 {{ n.page }} 页</span>
              <span class="dkp-note-time">{{ formatTime(n.createdAt) }}</span>
            </div>
            <p class="dkp-note-text">{{ n.text }}</p>
            <p v-if="n.quote" class="dkp-note-quote" :title="n.quote">原文：{{ n.quote }}</p>
            <div class="dkp-note-acts">
              <button
                v-if="canLocate && n.quote"
                class="dkp-act"
                type="button"
                title="在正文中找到这条笔记对应的原文"
                @click="emit('locate', n.quote)"
              >
                <el-icon><Location /></el-icon>定位原文
              </button>
              <button class="dkp-act" type="button" @click="copyNote(n)">
                <el-icon><DocumentCopy /></el-icon>复制
              </button>
              <button class="dkp-act danger" type="button" @click="removeNote(n)">
                <el-icon><Delete /></el-icon>删除
              </button>
            </div>
          </li>
        </ul>
      </div>
    </template>
  </aside>
</template>

<script setup>
/**
 * 阅读器右侧「学习助手」面板：知识点 / AI 助手 / 笔记。
 *
 * 知识点与正文的联动原则：只做**真实文本匹配**，不做伪造的页码映射。
 * - 「当前阅读位置提到的」= 用阅读器抛上来的正文（PDF 为当前页文本、TXT 为视口内行、
 *   DOCX 为可见段落）逐个匹配知识点名称，命中的才展示
 * - 「定位」= 调用阅读器的搜索能力跳到正文中第一次出现的位置，找不到就不显示该按钮
 * 后端目前没有「知识点 ↔ 页码」的数据，因此不存在精确到页的知识点映射，本面板不假装有。
 *
 * AI 阅读动作（总结 / 解释 / 出题 / 提取知识点）不再新建一套 AI 链路：
 * 把「读出来的正文」拼进问题，仍然调用既有的 POST /api/v1/qa/ask，
 * 因此引用来源、错误处理、超时策略与手动提问完全一致。
 * 笔记没有对应的后端接口，暂存本地（见 utils/studyNotes.js），不伪造业务数据源。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Aim, ChatDotRound, ChatLineSquare, Close, Collection, CollectionTag, DArrowRight, Delete,
  DocumentCopy, EditPen, Location, MagicStick, Notebook, Search, Select, Share, Star, StarFilled,
} from '@element-plus/icons-vue'
import { api } from '../../api'
import { addNote, deleteNote, listNotes } from '../../utils/studyNotes'
import { isStudentUser } from '../../utils/userRole'

const props = defineProps({
  courseId: { type: [String, Number], required: true },
  documentId: { type: [String, Number], required: true },
  /** 文档名，写进笔记便于回溯 */
  docName: { type: String, default: '' },
  /** 阅读器当前可见正文（由各 Viewer 提供） */
  visibleText: { type: String, default: '' },
  /** 阅读器是否支持正文定位（PDF / TXT 支持，DOCX 不支持） */
  canLocate: { type: Boolean, default: false },
  /** 当前选中的正文（供「解释选中内容」使用） */
  selectionText: { type: String, default: '' },
  /** 文档类型：决定快捷动作的措辞（PDF 有明确「页」，其它为当前所见） */
  kind: { type: String, default: 'none' },
})

const emit = defineEmits(['close', 'open-graph', 'locate'])

const isStudent = computed(() => isStudentUser())

const tab = ref('kp')
const nodes = ref([])
const loading = ref(false)
const loadError = ref('')
const filterText = ref('')
const expandedId = ref('')
const masteredIds = ref(new Set())
const favoriteIds = ref(new Set())

// ---------------- 知识点加载 ----------------

async function loadKnowledge() {
  if (!props.courseId || !props.documentId) return
  loading.value = true
  loadError.value = ''
  try {
    const graph = await api.getGraphV1(props.courseId, props.documentId)
    nodes.value = (graph?.nodes || []).map((n) => ({
      id: String(n.id),
      label: n.label || '',
      category: n.properties?.category || '',
      description: n.description || '',
    }))
  } catch (e) {
    nodes.value = []
    loadError.value = e.message || '无法读取知识点'
  } finally {
    loading.value = false
  }
}

/** 这里只负责展示，学习状态仍走既有的学习记录 / 收藏接口，不另建状态源 */
async function loadLearningState() {
  if (!isStudent.value) return
  try {
    const [progress, favorites] = await Promise.all([
      api.getProgress(props.courseId, props.documentId),
      api.getFavorites(props.courseId, props.documentId),
    ])
    masteredIds.value = new Set((progress?.mastered_kp_ids || []).map(String))
    favoriteIds.value = new Set((favorites?.items || []).map((f) => String(f.kp_id)))
  } catch {
    // 学习状态读取失败不阻塞阅读，仅表现为图标未点亮
  }
}

// ---------------- 正文匹配 ----------------

const mentionedIds = computed(() => {
  const text = (props.visibleText || '').toLowerCase()
  if (!text) return new Set()
  const hit = new Set()
  nodes.value.forEach((n) => {
    // 单字名称（如「树」）在正文里几乎必然误命中，不纳入位置匹配
    if (!n.label || n.label.length < 2) return
    if (text.includes(n.label.toLowerCase())) hit.add(n.id)
  })
  return hit
})

const mentioned = computed(() =>
  nodes.value
    .filter((n) => mentionedIds.value.has(n.id))
    // 名称更长的知识点更具体，排在前面（如「二叉搜索树」先于「二叉树」）
    .sort((a, b) => b.label.length - a.label.length)
    .slice(0, 20),
)

const filtered = computed(() => {
  const q = filterText.value.trim().toLowerCase()
  if (!q) return nodes.value
  return nodes.value.filter(
    (n) => n.label.toLowerCase().includes(q) || n.description.toLowerCase().includes(q),
  )
})

/** 面板展示分组；两组共用同一套行渲染，避免重复模板 */
const groups = computed(() => {
  const list = []
  if (mentioned.value.length && !filterText.value.trim()) {
    list.push({
      key: 'mentioned',
      icon: Aim,
      title: '当前阅读位置提到的',
      note: '按正文文本匹配',
      items: mentioned.value,
    })
  }
  list.push({
    key: 'all',
    icon: Collection,
    title: list.length ? '全部知识点' : '文档知识点',
    note: `${filtered.value.length} 个`,
    items: filtered.value,
  })
  return list
})

// 同一个知识点可能同时出现在「当前阅读位置提到的」和「全部知识点」两组里，
// 展开态必须带上分组前缀，否则点一处会两处同时展开
function toggleExpand(groupKey, node) {
  const key = `${groupKey}::${node.id}`
  expandedId.value = expandedId.value === key ? '' : key
}

// ---------------- 正文定位 ----------------

function locate(node) {
  emit('locate', node.label)
}

function locateByName(name) {
  if (!props.canLocate || !name) return
  tab.value = 'kp'
  emit('locate', name)
}

// ---------------- 学生动作 ----------------

async function toggleMastery(node) {
  const mastered = masteredIds.value.has(node.id)
  try {
    if (mastered) {
      await api.unmarkMastered(props.courseId, props.documentId, node.id)
      masteredIds.value.delete(node.id)
      masteredIds.value = new Set(masteredIds.value)
      ElMessage.success('已取消掌握标记')
    } else {
      await api.markMastered(props.courseId, props.documentId, node.id)
      masteredIds.value = new Set([...masteredIds.value, node.id])
      ElMessage.success('已标记为掌握')
    }
  } catch (e) {
    ElMessage.error(`操作失败：${e.message}`)
  }
}

async function toggleFavorite(node) {
  const favorited = favoriteIds.value.has(node.id)
  try {
    if (favorited) {
      await api.removeFavorite(props.courseId, props.documentId, node.id)
      favoriteIds.value.delete(node.id)
      favoriteIds.value = new Set(favoriteIds.value)
      ElMessage.success('已取消收藏')
    } else {
      await api.addFavorite(props.courseId, props.documentId, node.id)
      favoriteIds.value = new Set([...favoriteIds.value, node.id])
      ElMessage.success('已加入收藏夹')
    }
  } catch (e) {
    ElMessage.error(`操作失败：${e.message}`)
  }
}

// ---------------- AI 阅读助手 ----------------

const MAX_CONTEXT = 1500 // 单次送进问答的正文上限，避免提示词过长拖慢回答
const chat = ref([])
const question = ref('')
const asking = ref(false)
const qaScrollEl = ref(null)

/**
 * 阅读动作：措辞 + 提示词构造。
 * key 同时是「面板快捷操作」的 v-for key 与阅读器浮动菜单的调用参数，必须显式给出。
 */
const ACTIONS = {
  summary: {
    key: 'summary',
    label: '总结本页',
    title: '让 AI 总结当前阅读的内容',
    build: (ctx) => `请用简洁的要点总结下面这段课程内容，突出主要概念与结论：\n\n${ctx}`,
  },
  quiz: {
    key: 'quiz',
    label: '生成练习题',
    title: '根据当前阅读内容出 3 道练习题',
    build: (ctx) => `请根据下面的课程内容出 3 道练习题，每题给出答案与简要解析：\n\n${ctx}`,
  },
  keypoints: {
    key: 'keypoints',
    label: '提取知识点',
    title: '从当前阅读内容中提取关键知识点',
    build: (ctx) => `请从下面的课程内容中提取关键知识点，逐个列出并各用一句话说明：\n\n${ctx}`,
  },
  explain: {
    key: 'explain',
    label: '解释选中内容',
    title: '让 AI 解释选中的这段内容',
    build: (ctx) => `请解释下面这段课程内容，说明它的含义、关键要点，以及容易混淆的地方：\n\n${ctx}`,
  },
  selectionSummary: {
    key: 'selectionSummary',
    label: '总结选中内容',
    title: '让 AI 总结选中的这段内容',
    build: (ctx) => `请用简洁的要点总结下面这段课程内容：\n\n${ctx}`,
  },
  selectionQuiz: {
    key: 'selectionQuiz',
    label: '选中内容出题',
    title: '根据选中的内容出 3 道练习题',
    build: (ctx) => `请根据下面这段课程内容出 3 道练习题，附答案与简要解析：\n\n${ctx}`,
  },
  nodeExplain: {
    key: 'nodeExplain',
    label: '知识点 AI 讲解',
    title: '让 AI 结合本文档讲解该知识点',
    build: (ctx) => `请结合本文档内容讲解知识点「${ctx}」：它是什么、解决什么问题、和哪些概念容易混淆。`,
  },
}

/**
 * 无分页文档里「本页」没有明确所指，换成不漏掉语义的说法。
 * 按 kind 解析后的动作表同时供按钮与气泡使用，避免按钮写「总结当前内容」、
 * 气泡却显示「总结本页」这种不一致。
 */
const resolvedActions = computed(() => {
  if (props.kind === 'pdf') return ACTIONS
  return { ...ACTIONS, summary: { ...ACTIONS.summary, label: '总结当前内容' } }
})

const quickActions = computed(() => [
  resolvedActions.value.summary,
  resolvedActions.value.quiz,
  resolvedActions.value.keypoints,
])

const nodeExplainAction = ACTIONS.nodeExplain

const hasContext = computed(() => (props.visibleText || '').trim().length > 0)

function clipContext(text) {
  const value = String(text || '').trim()
  return value.length > MAX_CONTEXT ? `${value.slice(0, MAX_CONTEXT)}…` : value
}

/** 面板快捷动作：上下文取「当前可见正文」 */
function runAction(key, contextOverride) {
  const action = resolvedActions.value[key]
  if (!action) return
  const raw = String(contextOverride || props.visibleText || '')
  const context = clipContext(raw)
  if (!context) {
    ElMessage.info('正文还没准备好，稍后再试')
    return
  }
  void askWithContext({
    label: action.label,
    prompt: action.build(context),
    preview: context.slice(0, 60),
    clipped: raw.trim().length > MAX_CONTEXT,
    rawLen: raw.trim().length,
  })
}

/** 知识点行里的「AI 讲解」：以知识点名作为上下文 */
function explainNode(node) {
  void askWithContext({
    label: nodeExplainAction.label,
    prompt: nodeExplainAction.build(node.label),
    preview: node.label,
  })
}

/**
 * 由阅读器（含选中浮动菜单）发起一次 AI 阅读动作。
 * 面板持有对话状态，所以统一从这里进，避免出现第二份会话。
 */
async function askWithContext({ label, prompt, preview, clipped = false, rawLen = 0 }) {
  tab.value = 'ai'
  if (!prompt || asking.value) return
  chat.value.push({
    role: 'user',
    text: label,
    display: { label, preview },
    clipped,
    rawLen,
  })
  await scrollChat()
  asking.value = true
  try {
    const res = await api.ask(prompt, props.courseId, props.documentId)
    chat.value.push({
      role: 'ai',
      text: res?.answer || '（没有返回内容）',
      sources: res?.sources || [],
    })
  } catch (e) {
    chat.value.push({ role: 'ai', text: `提问失败：${e.message}`, error: true, sources: [] })
  } finally {
    asking.value = false
    await scrollChat()
  }
}

async function submitAsk() {
  const q = question.value.trim()
  if (!q || asking.value) return
  chat.value.push({ role: 'user', text: q })
  question.value = ''
  await scrollChat()
  asking.value = true
  try {
    const res = await api.ask(q, props.courseId, props.documentId)
    chat.value.push({
      role: 'ai',
      text: res?.answer || '（没有返回内容）',
      sources: res?.sources || [],
    })
  } catch (e) {
    chat.value.push({ role: 'ai', text: `提问失败：${e.message}`, error: true, sources: [] })
  } finally {
    asking.value = false
    await scrollChat()
  }
}

/** 「追问」：把 AI 的回答塞回输入框，学生改一改就能接着问 */
function quoteQuestion(answer) {
  const snippet = String(answer || '').slice(0, 120)
  question.value = question.value
    ? `${question.value}\n${snippet}`
    : `关于「${snippet}」还想再问：`
  nextTick(() => {
    qaScrollEl.value?.scrollTo?.({ top: qaScrollEl.value.scrollHeight })
  })
}

async function scrollChat() {
  await nextTick()
  const el = qaScrollEl.value
  if (el) el.scrollTop = el.scrollHeight
}

// ---------------- 学习笔记（本地） ----------------

const notes = ref([])
const noteFilter = ref('')

function loadNotes() {
  notes.value = listNotes(props.documentId)
}

const filteredNotes = computed(() => {
  const q = noteFilter.value.trim().toLowerCase()
  if (!q) return notes.value
  return notes.value.filter(
    (n) => (n.text || '').toLowerCase().includes(q) || (n.quote || '').toLowerCase().includes(q),
  )
})

function formatTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function persistNote(payload) {
  const note = addNote({
    docId: props.documentId,
    courseId: props.courseId,
    docName: props.docName,
    ...payload,
  })
  if (note) {
    loadNotes()
    ElMessage.success('已加入笔记')
  }
  return note
}

function saveAnswerNote(message) {
  persistNote({ text: message.text, source: 'AI 回答', quote: '' })
}

function addManualNote() {
  ElMessageBox.prompt('写点什么，随时可以回来查看（保存在本机）', '记笔记', {
    confirmButtonText: '保存',
    cancelButtonText: '取消',
    inputType: 'textarea',
    inputPlaceholder: '例如：这节的公式推导要再推一遍',
  })
    .then(({ value }) => {
      if (value && value.trim()) persistNote({ text: value, source: '手动摘录' })
    })
    .catch(() => {}) // 取消不需要提示
}

async function copyNote(note) {
  try {
    await navigator.clipboard.writeText(note.text)
    ElMessage.success('已复制')
  } catch {
    ElMessage.warning('当前浏览器不允许自动复制，请手动选中')
  }
}

async function removeNote(note) {
  try {
    await ElMessageBox.confirm('删除这条笔记？', '删除笔记', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return // 用户取消
  }
  deleteNote(props.documentId, note.id)
  loadNotes()
  ElMessage.success('已删除')
}

/** 导出本文档全部笔记为纯文本（笔记仅存本机，导出是防丢失的兜底手段） */
function exportNotes() {
  const list = listNotes(props.documentId)
  if (!list.length) {
    ElMessage.info('本文档还没有笔记')
    return
  }
  const blocks = list.map((n) => {
    const head = [n.source, n.page ? `第 ${n.page} 页` : '', formatTime(n.createdAt)]
      .filter(Boolean).join(' · ')
    const quote = n.quote ? `\n原文：${n.quote}` : ''
    return `【${head || '笔记'}】\n${n.text}${quote}`
  })
  const blob = new Blob([blocks.join('\n\n---\n\n')], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `笔记-${props.docName || props.documentId || '文档'}.txt`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
  ElMessage.success('笔记已导出')
}

/** 供阅读器调用：选中一段文字后直接存成笔记 */
function saveSelectionNote(text) {
  const body = String(text || '').trim()
  if (!body) return
  persistNote({ text: body, quote: body, source: '选中摘录' })
}

onMounted(() => {
  loadKnowledge()
  loadLearningState()
  loadNotes()
})

watch(() => [props.courseId, props.documentId], () => {
  expandedId.value = ''
  filterText.value = ''
  chat.value = []
  noteFilter.value = ''
  loadKnowledge()
  loadLearningState()
  loadNotes()
})

// 切换到笔记页时刷新一次，保证在别的入口（如选中菜单）存的笔记能立刻看到
watch(tab, (value) => {
  if (value === 'notes') loadNotes()
})

// hasChat 供阅读器在切换文档前确认：切文档会清空当前文档的学习助手对话
const hasChat = computed(() => chat.value.length > 0)

defineExpose({ runAction, askWithContext, saveSelectionNote, hasChat })
</script>

<style scoped>
.dkp {
  width: 330px;
  flex-shrink: 0;
  background: #fff;
  border-left: 1px solid #e4e7ed;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

/* ---------- 顶部切换 ---------- */
.dkp-tabs {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 0 8px;
  height: 44px;
  border-bottom: 1px solid #f0f2f5;
  flex-shrink: 0;
}
.dkp-tab {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 30px;
  padding: 0 9px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #606266;
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.dkp-tab:hover {
  background: #f5f7fa;
}
.dkp-tab.is-on {
  background: #eef1fe;
  color: #4f6ef7;
  font-weight: 600;
}
.dkp-tab-count {
  font-size: 11px;
  color: #909399;
  background: #f0f2f5;
  border-radius: 8px;
  padding: 0 5px;
  line-height: 15px;
}
.dkp-tab.is-on .dkp-tab-count {
  background: #dde4fc;
  color: #4f6ef7;
}
.dkp-close {
  margin-left: auto;
  border: none;
  background: transparent;
  color: #909399;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  padding: 4px;
  border-radius: 4px;
}
.dkp-close:hover {
  background: #f5f7fa;
  color: #4f6ef7;
}

/* ---------- 工具行 ---------- */
.dkp-toolbar {
  display: flex;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid #f0f2f5;
  flex-shrink: 0;
}
.dkp-filter {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 5px;
  height: 30px;
  padding: 0 8px;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  background: #fafbfc;
}
.dkp-filter:focus-within {
  border-color: #4f6ef7;
  background: #fff;
}
.dkp-filter-icon {
  color: #a8abb2;
  font-size: 13px;
}
.dkp-filter-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font-size: 13px;
  font-family: inherit;
  color: #303133;
}
.dkp-filter-clear {
  border: none;
  background: transparent;
  color: #c0c4cc;
  cursor: pointer;
  padding: 0;
  display: inline-flex;
}
.dkp-filter-clear:hover {
  color: #909399;
}
.dkp-graph-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 30px;
  padding: 0 10px;
  border: 1px solid #d4ddfc;
  border-radius: 6px;
  background: #eef1fe;
  color: #4f6ef7;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
}
.dkp-graph-btn:hover {
  background: #dde4fc;
}
.dkp-graph-btn.ghost {
  background: #fff;
  border-color: #e4e7ed;
  color: #606266;
}
.dkp-graph-btn.ghost:hover {
  border-color: #d4ddfc;
  color: #4f6ef7;
  background: #eef1fe;
}

/* ---------- 列表 ---------- */
.dkp-body {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
  padding-bottom: 24px;
}
.dkp-alert {
  margin: 10px 12px;
}
.dkp-alert-body {
  display: flex;
  align-items: center;
  gap: 6px;
}
.dkp-empty-hint {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}

.dkp-section {
  padding-top: 6px;
}
.dkp-section-title {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 8px 12px 6px;
  font-size: 12px;
  font-weight: 600;
  color: #606266;
}
.dkp-section-note {
  margin-left: auto;
  font-weight: 400;
  color: #a8abb2;
  font-size: 11px;
}

.dkp-list {
  list-style: none;
  margin: 0;
  padding: 0 8px;
}
.dkp-item {
  margin-bottom: 2px;
}

.dkp-row-head {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 7px 8px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.12s;
}
.dkp-row-head:hover {
  background: #f5f7fa;
}
.dkp-row-head.is-open {
  background: #f4f6fe;
}
.dkp-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #909399;
  flex-shrink: 0;
}
/* 与图谱页面的类别配色保持一致 */
.dkp-dot.cat-概念 { background: #4f6ef7; }
.dkp-dot.cat-定理 { background: #e6a23c; }
.dkp-dot.cat-公式 { background: #67c23a; }
.dkp-dot.cat-方法 { background: #a06cd5; }

.dkp-name {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dkp-cat {
  font-size: 11px;
  color: #a8abb2;
  flex-shrink: 0;
}
.dkp-flag {
  font-size: 10px;
  line-height: 15px;
  padding: 0 4px;
  border-radius: 3px;
  flex-shrink: 0;
}
.dkp-flag.is-mastered {
  background: #f0f9eb;
  color: #67c23a;
}
.dkp-flag.is-fav {
  background: #fdf6ec;
  color: #e6a23c;
}
.dkp-flag.is-here {
  background: #eef1fe;
  color: #4f6ef7;
}

.dkp-detail {
  padding: 2px 10px 10px 23px;
}
.dkp-desc {
  margin: 0 0 8px;
  font-size: 12px;
  line-height: 1.7;
  color: #606266;
}
.dkp-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.dkp-act {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  height: 24px;
  padding: 0 8px;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  background: #fff;
  color: #606266;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
}
.dkp-act:hover {
  border-color: #d4ddfc;
  color: #4f6ef7;
  background: #eef1fe;
}
.dkp-act.is-on {
  border-color: #d4ddfc;
  color: #4f6ef7;
  background: #eef1fe;
}
.dkp-act.danger:hover {
  border-color: #fde2e2;
  color: #f56c6c;
  background: #fef0f0;
}

/* ---------- AI 助手 ---------- */
.dkp-qa {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.dkp-chat {
  flex: 1;
  overflow-y: auto;
  padding: 14px 12px;
  min-height: 0;
}
.dkp-chat-empty {
  text-align: center;
  color: #a8abb2;
  padding: 36px 16px;
}
.dkp-chat-empty p {
  font-size: 12px;
  line-height: 1.8;
  margin: 8px 0 0;
}
.dkp-msg {
  margin-bottom: 12px;
  display: flex;
  flex-direction: column;
}
.dkp-msg.is-user {
  align-items: flex-end;
}
.dkp-msg-body {
  max-width: 92%;
  padding: 8px 11px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}
.dkp-msg.is-user .dkp-msg-body {
  background: #4f6ef7;
  color: #fff;
}
.dkp-msg.is-ai .dkp-msg-body {
  background: #f5f7fa;
  color: #303133;
  border: 1px solid #f0f2f5;
}
/* 阅读动作气泡：动作标签 + 原文片段，看起来像一条「带着上下文的提问」 */
.dkp-msg.is-user .dkp-msg-body.is-action {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 3px;
  background: #eef1fe;
  color: #3f5ce0;
  border: 1px solid #dde4fc;
}
.dkp-act-tag {
  font-size: 12px;
  font-weight: 600;
}
.dkp-act-preview {
  max-width: 100%;
  font-size: 11px;
  line-height: 1.6;
  color: #8aa3f9;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.dkp-msg-body.is-typing {
  color: #909399;
  font-size: 12px;
}

.dkp-clip-note {
  margin-top: 4px;
  max-width: 92%;
  font-size: 11px;
  line-height: 1.5;
  color: var(--el-color-warning, #e6a23c);
}

.dkp-msg-src {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin-top: 6px;
  max-width: 92%;
}
.dkp-src-label {
  font-size: 11px;
  color: #a8abb2;
}
.dkp-src-wrap {
  display: inline-flex;
  align-items: center;
  border: 1px solid #e4e7ed;
  border-radius: 3px;
  background: #fff;
  overflow: hidden;
}
.dkp-src-chip {
  font-size: 11px;
  padding: 1px 6px;
  border: none;
  background: transparent;
  color: #606266;
  cursor: pointer;
  font-family: inherit;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dkp-src-wrap:hover {
  border-color: #d4ddfc;
}
.dkp-src-chip:hover {
  color: #4f6ef7;
}
.dkp-src-fav {
  display: inline-flex;
  align-items: center;
  padding: 1px 4px;
  border: none;
  border-left: 1px solid #f0f2f5;
  background: transparent;
  color: #c0c4cc;
  font-size: 11px;
  cursor: pointer;
}
.dkp-src-fav:hover,
.dkp-src-fav.is-on {
  color: #e6a23c;
}

.dkp-msg-acts {
  display: flex;
  gap: 6px;
  margin-top: 6px;
}

/* 快捷阅读动作 */
.dkp-quick {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 8px 12px 0;
  flex-shrink: 0;
}
.dkp-quick-btn {
  height: 26px;
  padding: 0 10px;
  border: 1px solid #e4e7ed;
  border-radius: 13px;
  background: #fff;
  color: #606266;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
}
.dkp-quick-btn:hover:not(:disabled) {
  border-color: #d4ddfc;
  color: #4f6ef7;
  background: #eef1fe;
}
.dkp-quick-btn:disabled {
  color: #c0c4cc;
  cursor: not-allowed;
}
.dkp-quick-hint {
  font-size: 11px;
  color: #c0c4cc;
  align-self: center;
}

/* 选中内容入口 */
.dkp-selection {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 8px 12px 0;
  padding: 6px 8px;
  border: 1px solid #dde4fc;
  border-radius: 6px;
  background: #f4f6fe;
  flex-shrink: 0;
}
.dkp-selection-label {
  font-size: 11px;
  color: #8aa3f9;
  flex-shrink: 0;
}
.dkp-selection-text {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: #606266;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dkp-selection-act {
  border: none;
  background: transparent;
  color: #4f6ef7;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  padding: 0 3px;
  flex-shrink: 0;
}
.dkp-selection-act:hover {
  text-decoration: underline;
}

.dkp-ask {
  border-top: 1px solid #f0f2f5;
  margin-top: 10px;
  padding: 10px 12px 12px;
  flex-shrink: 0;
}
.dkp-ask-input {
  width: 100%;
  box-sizing: border-box;
  resize: none;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 7px 9px;
  font-size: 13px;
  line-height: 1.6;
  font-family: inherit;
  color: #303133;
  outline: none;
}
.dkp-ask-input:focus {
  border-color: #4f6ef7;
}
.dkp-ask-btn {
  width: 100%;
  margin-top: 7px;
  height: 30px;
  border: none;
  border-radius: 6px;
  background: #4f6ef7;
  color: #fff;
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.dkp-ask-btn:hover:not(:disabled) {
  background: #7d96fa;
}
.dkp-ask-btn:disabled {
  background: #d4ddfc;
  cursor: not-allowed;
}

.dkp-notes-local-tip {
  margin-bottom: 10px;
}

/* ---------- 笔记 ---------- */
.dkp-notes {
  list-style: none;
  margin: 0;
  padding: 4px 10px;
}
.dkp-note {
  padding: 9px 10px;
  margin-bottom: 8px;
  border: 1px solid #f0f2f5;
  border-radius: 6px;
  background: #fcfcfd;
}
.dkp-note-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 5px;
}
.dkp-note-tag {
  font-size: 10px;
  line-height: 15px;
  padding: 0 5px;
  border-radius: 3px;
  background: #eef1fe;
  color: #4f6ef7;
}
.dkp-note-page {
  font-size: 11px;
  color: #a8abb2;
}
.dkp-note-time {
  margin-left: auto;
  font-size: 11px;
  color: #c0c4cc;
}
.dkp-note-text {
  margin: 0;
  font-size: 12px;
  line-height: 1.75;
  color: #303133;
  white-space: pre-wrap;
  word-break: break-word;
  /* 长笔记只展示前几行，展开留给专门的笔记页 */
  display: -webkit-box;
  -webkit-line-clamp: 6;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.dkp-note-quote {
  margin: 6px 0 0;
  padding-left: 7px;
  border-left: 2px solid #e4e7ed;
  font-size: 11px;
  line-height: 1.6;
  color: #909399;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.dkp-note-acts {
  display: flex;
  gap: 6px;
  margin-top: 8px;
}

@media (max-width: 900px) {
  .dkp {
    width: 100%;
    border-left: none;
  }
}
</style>
