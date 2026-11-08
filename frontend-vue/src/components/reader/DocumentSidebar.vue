<template>
  <aside class="dsb">
    <!-- 切换 -->
    <div class="dsb-tabs">
      <button
        v-for="t in tabs"
        :key="t.key"
        class="dsb-tab"
        :class="{ 'is-on': tab === t.key }"
        type="button"
        @click="tab = t.key"
      >{{ t.label }}</button>
      <button class="dsb-close" type="button" title="收起侧栏" @click="emit('close')">
        <el-icon><DArrowLeft /></el-icon>
      </button>
    </div>

    <!-- ============ 目录 ============ -->
    <div v-if="tab === 'outline'" ref="scrollEl" class="dsb-body">
      <div v-if="!outline.length" class="dsb-empty">
        <el-icon :size="20"><Files /></el-icon>
        <p>{{ emptyOutlineText }}</p>
      </div>
      <ul v-else class="dsb-outline">
        <li v-for="it in outline" :key="it.id">
          <button
            class="dsb-outline-item"
            :class="{ 'is-active': it.id === activeOutlineId, ['depth-' + it.depth]: true }"
            type="button"
            :title="it.title"
            @click="emit('select', it)"
          >
            <span class="dsb-outline-text">{{ it.title }}</span>
            <span v-if="it.target?.page" class="dsb-outline-page">{{ it.target.page }}</span>
          </button>
        </li>
      </ul>
    </div>

    <!-- ============ 缩略图（仅 PDF） ============ -->
    <div v-else-if="tab === 'thumbs'" ref="scrollEl" class="dsb-body dsb-thumbs">
      <div
        v-for="p in pageCount"
        :key="p"
        :ref="(el) => setThumbEl(p, el)"
        class="dsb-thumb"
        :class="{ 'is-active': p === currentPage }"
        :data-page="p"
        @click="emit('select', { target: { page: p } })"
      >
        <canvas :ref="(el) => setThumbCanvas(p, el)" class="dsb-thumb-canvas" />
        <div class="dsb-thumb-no">{{ p }}</div>
      </div>
      <p v-if="!pageCount" class="dsb-empty">缩略图仅在 PDF 文档中可用</p>
    </div>

    <!-- ============ 文档信息 ============ -->
    <div v-else ref="scrollEl" class="dsb-body dsb-info">
      <div class="dsb-progress">
        <div class="dsb-progress-head">
          <span>阅读进度</span>
          <b>{{ progress.percent }}%</b>
        </div>
        <div class="dsb-progress-bar">
          <div class="dsb-progress-fill" :style="{ width: progress.percent + '%' }" />
        </div>
        <div class="dsb-progress-meta">
          <span v-if="progress.totalPages > 1">第 {{ progress.page }} / {{ progress.totalPages }} 页</span>
          <span v-else>{{ readingProgressLabel(progress.percent) }}</span>
          <button class="dsb-link" type="button" @click="emit('restart')">从头开始</button>
        </div>
        <p class="dsb-note">阅读进度仅保存在当前浏览器，换设备或清理浏览器数据后不会保留</p>
      </div>

      <dl class="dsb-dl">
        <div class="dsb-dl-row">
          <dt>文档名称</dt>
          <dd :title="doc.fileName">{{ doc.fileName || '—' }}</dd>
        </div>
        <div class="dsb-dl-row">
          <dt>所属课程</dt>
          <dd :title="courseName">{{ courseName || '—' }}</dd>
        </div>
        <div class="dsb-dl-row">
          <dt>文件类型</dt>
          <dd>{{ doc.fileType || '—' }}</dd>
        </div>
        <div class="dsb-dl-row">
          <dt>文件大小</dt>
          <dd>{{ doc.fileSizeText || '—' }}</dd>
        </div>
        <!-- 用「抽取结果」而非「知识点」：这是上传当时的统计，教师后续手动编辑图谱后
             实际节点数会变化，右侧面板显示的是图谱里的实时数据，两者口径不同 -->
        <div class="dsb-dl-row">
          <dt>抽取结果</dt>
          <dd>{{ doc.entityCount ?? 0 }} 个知识点 · {{ doc.relationCount ?? 0 }} 条关系</dd>
        </div>
        <div class="dsb-dl-row">
          <dt>上传时间</dt>
          <dd>{{ doc.createdAt || '—' }}</dd>
        </div>
      </dl>

      <p v-if="!canSearch" class="dsb-note">
        Word 文档由排版结果直接渲染，没有稳定的字符位置映射，因此暂不提供文内搜索。
      </p>
    </div>

    <!-- ============ 底部：纯文本阅读设置（仅 TXT / MD） ============ -->
    <div v-if="mode === 'text'" class="dsb-prefs">
      <div class="dsb-pref-row">
        <span class="dsb-pref-label">字号</span>
        <button class="dsb-pref-btn" type="button" :disabled="prefs.fontSize <= 12" @click="setPref('fontSize', prefs.fontSize - 1)">A−</button>
        <span class="dsb-pref-value">{{ prefs.fontSize }}px</span>
        <button class="dsb-pref-btn" type="button" :disabled="prefs.fontSize >= 26" @click="setPref('fontSize', prefs.fontSize + 1)">A+</button>
      </div>
      <div class="dsb-pref-row">
        <span class="dsb-pref-label">行距</span>
        <button
          v-for="opt in LINE_HEIGHTS"
          :key="opt.value"
          class="dsb-pref-btn wide"
          :class="{ 'is-on': Math.abs(prefs.lineHeight - opt.value) < 0.01 }"
          type="button"
          @click="setPref('lineHeight', opt.value)"
        >{{ opt.label }}</button>
      </div>
      <div class="dsb-pref-row">
        <span class="dsb-pref-label">宽度</span>
        <button
          v-for="opt in WIDTHS"
          :key="opt.value"
          class="dsb-pref-btn wide"
          :class="{ 'is-on': prefs.maxWidth === opt.value }"
          type="button"
          @click="setPref('maxWidth', opt.value)"
        >{{ opt.label }}</button>
      </div>
    </div>
  </aside>
</template>

<script setup>
/**
 * 阅读器左侧栏：目录 / 缩略图 / 文档信息。
 * 目录对不同格式的来源不同（PDF 用内置大纲、TXT 从正文标题推导、DOCX 用标题样式），
 * 由阅读器页面统一扁平化后传入，本组件只负责展示与派发跳转意图。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { DArrowLeft, Files } from '@element-plus/icons-vue'
import { readingProgressLabel } from '../../utils/readingProgress'

const props = defineProps({
  mode: { type: String, default: 'none' }, // pdf | text | docx | none
  outline: { type: Array, default: () => [] },
  doc: { type: Object, default: () => ({}) },
  courseName: { type: String, default: '' },
  progress: { type: Object, default: () => ({ percent: 0, page: 1, totalPages: 0 }) },
  prefs: { type: Object, required: true },
  currentPage: { type: Number, default: 1 },
  /** 当前 Viewer 暴露的能力对象（缩略图渲染需要） */
  viewerApi: { type: Object, default: null },
  canSearch: { type: Boolean, default: true },
  /** Viewer 是否已经报告过目录。未报告前不能判定「没有目录」，否则会在加载间隙误判并跳走 */
  outlineReady: { type: Boolean, default: false },
  /** 当前文档标识：换文档后页码含义全变，缩略图缓存必须作废 */
  docKey: { type: [String, Number], default: '' },
})

const emit = defineEmits(['select', 'restart', 'update:prefs', 'close'])

const LINE_HEIGHTS = [
  { label: '紧凑', value: 1.6 },
  { label: '标准', value: 1.9 },
  { label: '宽松', value: 2.3 },
]
const WIDTHS = [
  { label: '窄', value: 620 },
  { label: '中', value: 760 },
  { label: '宽', value: 920 },
]

const tabs = computed(() => {
  const list = [{ key: 'outline', label: '目录' }]
  if (props.mode === 'pdf') list.push({ key: 'thumbs', label: '缩略图' })
  list.push({ key: 'info', label: '文档信息' })
  return list
})

const tab = ref('outline')
const scrollEl = ref(null)

// 切换模式（换文档）时回到目录页
watch(() => props.mode, () => { tab.value = 'outline' })
// 换文档后页码指向的是另一篇文档，已渲染的缩略图记录必须清空，
// 否则新文档的缩略图会因为「这几页渲染过」而被跳过，一直停在旧图上
watch(() => props.docKey, () => { renderedThumbs.clear() })
// 目录已就绪且确实为空时，自动切到「文档信息」，避免一进来对着空面板；
// 未就绪（还在解析正文 / 渲染 Word）时不动，否则会误判成「没有目录」
watch(
  () => [props.outlineReady, props.outline.length],
  ([ready, len]) => {
    if (!ready) return
    if (!len && tab.value === 'outline') tab.value = 'info'
    else if (len && tab.value === 'info') tab.value = 'outline'
  },
)

const emptyOutlineText = computed(() => {
  if (props.mode === 'pdf') return '该 PDF 没有内置书签目录，可切换到「缩略图」按页浏览'
  if (props.mode === 'text') return '未在正文中识别到章节标题'
  return '该文档没有可用的标题层级'
})

/** 目录当前项：取最后一个「起始页不超过当前页」的条目 */
const activeOutlineId = computed(() => {
  if (props.mode !== 'pdf') return ''
  let active = ''
  props.outline.forEach((it) => {
    if (it.target?.page && it.target.page <= props.currentPage) active = it.id
  })
  return active
})

// ---------------- 缩略图：进入视口才渲染 ----------------

const pageCount = computed(() => (props.mode === 'pdf' ? props.progress.totalPages || 0 : 0))
const thumbEls = new Map()
const thumbCanvas = new Map()
const renderedThumbs = new Set()
let observer = null
let rendering = false

function setThumbEl(p, el) {
  if (!el) {
    thumbEls.delete(p)
    return
  }
  thumbEls.set(p, el)
  observer?.observe(el)
}

function setThumbCanvas(p, el) {
  if (el) thumbCanvas.set(p, el)
  else thumbCanvas.delete(p)
}

function setupThumbObserver() {
  observer?.disconnect()
  observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return
        void queueThumb(Number(entry.target.dataset.page))
      })
    },
    { root: scrollEl.value, rootMargin: '200px 0px' },
  )
  thumbEls.forEach((el) => observer.observe(el))
}

/** 串行渲染，避免几百页同时解码把主线程堵住 */
async function queueThumb(p) {
  if (!p || renderedThumbs.has(p) || rendering) return
  const canvas = thumbCanvas.get(p)
  if (!canvas) return
  const api = props.viewerApi
  if (!api?.renderThumbnail) return
  rendering = true
  try {
    await api.renderThumbnail(p, canvas)
    renderedThumbs.add(p)
  } catch {
    // 缩略图失败保留空白占位，不影响阅读
  } finally {
    rendering = false
  }
}

watch([() => tab.value, pageCount, () => props.viewerApi], async () => {
  if (tab.value !== 'thumbs') {
    observer?.disconnect()
    return
  }
  await nextTick()
  setupThumbObserver()
  // 首屏可见的几张先渲染出来
  Array.from(thumbEls.keys()).slice(0, 6).forEach((p) => void queueThumb(p))
})

onBeforeUnmount(() => observer?.disconnect())

function setPref(key, value) {
  emit('update:prefs', { [key]: value })
}
</script>

<style scoped>
.dsb {
  width: 268px;
  flex-shrink: 0;
  background: #fff;
  border-right: 1px solid #e4e7ed;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.dsb-tabs {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 0 8px;
  height: 44px;
  border-bottom: 1px solid #f0f2f5;
  flex-shrink: 0;
}
.dsb-tab {
  height: 30px;
  padding: 0 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #606266;
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.dsb-tab:hover {
  background: #f5f7fa;
}
.dsb-tab.is-on {
  background: #eef1fe;
  color: #4f6ef7;
  font-weight: 600;
}
.dsb-close {
  margin-left: auto;
  border: none;
  background: transparent;
  color: #909399;
  cursor: pointer;
  display: inline-flex;
  padding: 4px;
  border-radius: 4px;
}
.dsb-close:hover {
  background: #f5f7fa;
  color: #4f6ef7;
}

.dsb-body {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
  padding: 8px 0 16px;
}

.dsb-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 36px 22px;
  color: #a8abb2;
  font-size: 12px;
  line-height: 1.7;
  text-align: center;
}

/* ---------- 目录 ---------- */
.dsb-outline {
  list-style: none;
  margin: 0;
  padding: 0 8px;
}
.dsb-outline-item {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 8px;
  border: none;
  border-radius: 5px;
  background: transparent;
  color: #606266;
  font-size: 13px;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}
.dsb-outline-item:hover {
  background: #f5f7fa;
  color: #4f6ef7;
}
.dsb-outline-item.is-active {
  background: #eef1fe;
  color: #4f6ef7;
  font-weight: 600;
}
.dsb-outline-item.depth-1 { padding-left: 22px; }
.dsb-outline-item.depth-2 { padding-left: 36px; }
.dsb-outline-item.depth-3 { padding-left: 50px; }
.dsb-outline-item.depth-4 { padding-left: 64px; }
.dsb-outline-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dsb-outline-page {
  font-size: 11px;
  color: #a8abb2;
  flex-shrink: 0;
}

/* ---------- 缩略图 ---------- */
.dsb-thumbs {
  padding: 12px 10px 24px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}
.dsb-thumb {
  cursor: pointer;
  border: 1px solid #e4e7ed;
  border-radius: 3px;
  overflow: hidden;
  background: #fff;
  transition: border-color 0.15s, box-shadow 0.15s;
  max-width: 100%;
}
.dsb-thumb:hover {
  border-color: #b3c2fb;
}
.dsb-thumb.is-active {
  border-color: #4f6ef7;
  box-shadow: 0 0 0 2px rgba(64, 158, 255, 0.18);
}
.dsb-thumb-canvas {
  display: block;
  max-width: 100%;
}
.dsb-thumb-no {
  font-size: 11px;
  color: #909399;
  text-align: center;
  padding: 2px 0 3px;
  background: #fafbfc;
}

/* ---------- 信息 ---------- */
.dsb-info {
  padding: 14px 14px 24px;
}
.dsb-progress-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  font-size: 12px;
  color: #606266;
}
.dsb-progress-head b {
  font-size: 16px;
  color: #4f6ef7;
}
.dsb-progress-bar {
  height: 6px;
  border-radius: 3px;
  background: #f0f2f5;
  margin: 7px 0 6px;
  overflow: hidden;
}
.dsb-progress-fill {
  height: 100%;
  background: #4f6ef7;
  border-radius: 3px;
  transition: width 0.2s ease;
}
.dsb-progress-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: #909399;
}
.dsb-note {
  margin: 6px 0 0;
  font-size: 11px;
  line-height: 1.5;
  color: #b1b3b8;
}
.dsb-link {
  border: none;
  background: transparent;
  color: #4f6ef7;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  padding: 0;
}
.dsb-link:hover {
  text-decoration: underline;
}

.dsb-dl {
  margin: 18px 0 0;
}
.dsb-dl-row {
  display: flex;
  gap: 10px;
  padding: 6px 0;
  border-bottom: 1px dashed #f0f2f5;
  font-size: 12px;
}
.dsb-dl-row dt {
  width: 60px;
  flex-shrink: 0;
  color: #909399;
}
.dsb-dl-row dd {
  flex: 1;
  min-width: 0;
  margin: 0;
  color: #303133;
  word-break: break-all;
}

.dsb-note {
  margin: 16px 0 0;
  padding: 8px 10px;
  background: #fdf6ec;
  border-radius: 4px;
  color: #b88230;
  font-size: 12px;
  line-height: 1.7;
}

/* ---------- 阅读设置 ---------- */
.dsb-prefs {
  border-top: 1px solid #f0f2f5;
  padding: 10px 12px 12px;
  flex-shrink: 0;
  background: #fafbfc;
}
.dsb-pref-row {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 6px;
}
.dsb-pref-row:last-child {
  margin-bottom: 0;
}
.dsb-pref-label {
  width: 30px;
  font-size: 12px;
  color: #909399;
  flex-shrink: 0;
}
.dsb-pref-btn {
  height: 24px;
  min-width: 30px;
  padding: 0 6px;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  background: #fff;
  color: #606266;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
}
.dsb-pref-btn:hover:not(:disabled) {
  border-color: #d4ddfc;
  color: #4f6ef7;
}
.dsb-pref-btn:disabled {
  color: #c0c4cc;
  cursor: not-allowed;
}
.dsb-pref-btn.is-on {
  background: #eef1fe;
  border-color: #d4ddfc;
  color: #4f6ef7;
  font-weight: 600;
}
.dsb-pref-btn.wide {
  flex: 1;
}
.dsb-pref-value {
  min-width: 34px;
  text-align: center;
  font-size: 12px;
  color: #606266;
}
</style>
