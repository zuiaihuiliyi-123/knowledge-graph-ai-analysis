<template>
  <header class="rtb">
    <div class="rtb-main">
      <!-- 左：返回 + 文档标识 -->
      <div class="rtb-left">
        <button class="rtb-back" type="button" @click="emit('back')">
          <el-icon><ArrowLeft /></el-icon>
          <span class="rtb-back-text">{{ backText }}</span>
        </button>

        <!-- 文档标题本身就是下拉入口：可以在阅读器内直接换同课程的其他文档 -->
        <DocumentSwitcher
          :title="title"
          :subtitle="subtitle"
          :file-type="fileType"
          :file-size="fileSize"
          :doc-id="docId"
          :documents="documents"
          @switch="(d) => emit('switch-document', d)"
        />
      </div>

      <!-- 中：搜索 -->
      <div class="rtb-center">
        <div class="rtb-search" :class="{ 'is-disabled': !searchable }">
          <el-icon class="rtb-search-icon"><Search /></el-icon>
          <input
            ref="searchInputEl"
            class="rtb-search-input"
            type="text"
            :value="searchValue"
            :disabled="!searchable"
            :placeholder="searchPlaceholder"
            @input="onInput"
            @keydown.enter.prevent="onSearchEnter"
            @keydown.esc="clearSearch"
          />
          <span v-if="searchStatus" class="rtb-search-status">{{ searchStatus }}</span>
          <template v-if="searchValue">
            <button class="rtb-icon-btn sm" type="button" title="上一个 (Shift+Enter / Shift+F3)" @click="emit('search-prev')">
              <el-icon><ArrowUp /></el-icon>
            </button>
            <button class="rtb-icon-btn sm" type="button" title="下一个 (Enter / F3)" @click="emit('search-next')">
              <el-icon><ArrowDown /></el-icon>
            </button>
            <button class="rtb-icon-btn sm" type="button" title="清除 (Esc)" @click="clearSearch">
              <el-icon><Close /></el-icon>
            </button>
          </template>
        </div>
      </div>

      <!-- 右：阅读控制 -->
      <div class="rtb-right">
        <!-- 分页 -->
        <div v-if="showPaging" class="rtb-group">
          <button class="rtb-icon-btn" type="button" title="上一页 (← / PageUp)" :disabled="page <= 1" @click="emit('prev-page')">
            <el-icon><ArrowLeft /></el-icon>
          </button>
          <input
            class="rtb-page-input"
            type="text"
            inputmode="numeric"
            :value="String(page)"
            title="输入页码后回车跳转"
            @keydown.enter="onJump($event)"
            @blur="onJump($event)"
          />
          <span class="rtb-page-total">/ {{ totalPages }}</span>
          <button
            class="rtb-icon-btn"
            type="button"
            title="下一页 (→ / PageDown)"
            :disabled="totalPages > 0 && page >= totalPages"
            @click="emit('next-page')"
          >
            <el-icon><ArrowRight /></el-icon>
          </button>
        </div>

        <!-- 缩放 -->
        <div v-if="showZoom" class="rtb-group">
          <button class="rtb-icon-btn" type="button" title="缩小 (-)" @click="emit('zoom-out')">
            <el-icon><ZoomOut /></el-icon>
          </button>
          <button class="rtb-zoom-label" type="button" title="恢复 100% (0)" @click="emit('zoom-reset')">
            {{ zoomLabel }}
          </button>
          <button class="rtb-icon-btn" type="button" title="放大 (+)" @click="emit('zoom-in')">
            <el-icon><ZoomIn /></el-icon>
          </button>
          <button
            class="rtb-text-btn"
            type="button"
            :class="{ 'is-on': zoomFit }"
            title="适应宽度"
            @click="emit('zoom-fit')"
          >
            适应宽度
          </button>
        </div>

        <div class="rtb-divider" />

        <button
          class="rtb-icon-btn"
          type="button"
          :class="{ 'is-on': sidebarOpen }"
          title="目录 / 缩略图"
          @click="emit('toggle-sidebar')"
        >
          <el-icon><List /></el-icon>
        </button>
        <button class="rtb-icon-btn" type="button" title="全屏" @click="emit('fullscreen')">
          <el-icon><FullScreen /></el-icon>
        </button>
        <button
          class="rtb-icon-btn"
          type="button"
          :disabled="downloading"
          :title="downloading ? '正在下载…' : '下载原文件'"
          @click="emit('download')"
        >
          <el-icon :class="{ spin: downloading }"><Download v-if="!downloading" /><Loading v-else /></el-icon>
        </button>
        <button
          class="rtb-icon-btn"
          type="button"
          :class="{ 'is-on': panelOpen }"
          title="学习助手（知识点 / AI 问答）"
          @click="emit('toggle-panel')"
        >
          <el-icon><Reading /></el-icon>
        </button>
      </div>
    </div>

    <!-- 阅读进度：细线 + 百分比，不遮挡正文；滚到底显示「已读完」，与文档列表口径一致 -->
    <div class="rtb-progress" :title="progressLabel">
      <div class="rtb-progress-fill" :style="{ width: percent + '%' }" />
      <span class="rtb-progress-text">{{ progressLabel }}</span>
    </div>
  </header>
</template>

<script setup>
import { computed, ref } from 'vue'
import {
  ArrowLeft, ArrowRight, ArrowUp, ArrowDown, Close, Download, FullScreen,
  List, Loading, Reading, Search, ZoomIn, ZoomOut,
} from '@element-plus/icons-vue'
import DocumentSwitcher from './DocumentSwitcher.vue'
import { readingProgressLabel } from '../../utils/readingProgress'

const props = defineProps({
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' },
  fileType: { type: String, default: '' },
  fileSize: { type: String, default: '' },
  /** 当前文档 id 与同课程文档列表（供顶部下拉切换） */
  docId: { type: [String, Number], default: '' },
  documents: { type: Array, default: () => [] },
  backText: { type: String, default: '返回课程' },
  percent: { type: Number, default: 0 },

  page: { type: Number, default: 1 },
  totalPages: { type: Number, default: 0 },
  showPaging: { type: Boolean, default: false },

  zoomLabel: { type: String, default: '100%' },
  zoomFit: { type: Boolean, default: false },
  showZoom: { type: Boolean, default: false },

  searchable: { type: Boolean, default: true },
  searchPlaceholder: { type: String, default: '在文档中搜索' },
  searchStatus: { type: String, default: '' },
  /** 受控搜索词：由页面持有，知识点「定位正文」也要能直接写入 */
  searchValue: { type: String, default: '' },

  sidebarOpen: { type: Boolean, default: true },
  panelOpen: { type: Boolean, default: true },
  /** 下载原文件进行中（按钮显示加载并禁用，避免重复点击） */
  downloading: { type: Boolean, default: false },
})

const emit = defineEmits([
  'back', 'download', 'fullscreen', 'toggle-sidebar', 'toggle-panel', 'switch-document',
  'prev-page', 'next-page', 'jump-page', 'zoom-in', 'zoom-out', 'zoom-reset', 'zoom-fit',
  'search', 'search-next', 'search-prev', 'update:searchValue',
])

const searchInputEl = ref(null)
const searchable = computed(() => props.searchable)
const progressLabel = computed(() => readingProgressLabel(props.percent))

// 只在用户输入时触发搜索；页面程序化写入搜索词（如知识点定位）时由页面自己发起，
// 避免同一次定位触发两遍全文扫描
function onInput(event) {
  const value = event.target.value
  emit('update:searchValue', value)
  emit('search', value)
}

function onSearchEnter() {
  if (props.searchValue) emit('search-next')
}

function clearSearch() {
  emit('update:searchValue', '')
  emit('search', '')
}

function onJump(event) {
  const raw = Number.parseInt(event.target.value, 10)
  if (Number.isFinite(raw)) emit('jump-page', raw)
  // 无论是否合法都回填当前页，避免输入框残留非法字符串
  event.target.value = String(props.page)
}

// 供父组件调用：Ctrl+F 聚焦搜索框（阅读器的常见预期行为）
defineExpose({
  focusSearch: () => searchInputEl.value?.focus(),
})
</script>

<style scoped>
.rtb {
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  flex-shrink: 0;
  position: relative;
  z-index: 20;
}

.rtb-main {
  display: flex;
  align-items: center;
  gap: 16px;
  height: 56px;
  padding: 0 14px;
}

/* ---------- 左 ---------- */
.rtb-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  flex: 0 1 auto;
}

.rtb-back {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 30px;
  padding: 0 10px;
  border: 1px solid #dcdfe6;
  border-radius: 6px;
  background: #fff;
  color: #606266;
  font-size: 13px;
  cursor: pointer;
  white-space: nowrap;
  transition: color 0.15s, border-color 0.15s, background 0.15s;
}
.rtb-back:hover {
  color: #4f6ef7;
  border-color: #d4ddfc;
  background: #eef1fe;
}

/* 标题与文档切换下拉在 DocumentSwitcher 内部，此处只保留返回按钮 */

/* ---------- 中：搜索 ---------- */
.rtb-center {
  flex: 1 1 auto;
  display: flex;
  justify-content: center;
  min-width: 0;
}
.rtb-search {
  display: flex;
  align-items: center;
  gap: 2px;
  width: 100%;
  max-width: 340px;
  height: 32px;
  padding: 0 4px 0 9px;
  border: 1px solid #dcdfe6;
  border-radius: 6px;
  background: #fafbfc;
  transition: border-color 0.15s, background 0.15s;
}
.rtb-search:focus-within {
  border-color: #4f6ef7;
  background: #fff;
}
.rtb-search.is-disabled {
  opacity: 0.55;
}
.rtb-search-icon {
  color: #a8abb2;
  font-size: 14px;
  flex-shrink: 0;
}
.rtb-search-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font-size: 13px;
  color: #303133;
  font-family: inherit;
}
.rtb-search-input::placeholder {
  color: #c0c4cc;
}
.rtb-search-status {
  font-size: 12px;
  color: #909399;
  padding: 0 4px;
  white-space: nowrap;
  flex-shrink: 0;
}

/* ---------- 右 ---------- */
.rtb-right {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
}
.rtb-group {
  display: flex;
  align-items: center;
  gap: 1px;
  height: 32px;
  padding: 0 3px;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  background: #fafbfc;
}
.rtb-divider {
  width: 1px;
  height: 20px;
  background: #e4e7ed;
  margin: 0 2px;
}

.rtb-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 5px;
  background: transparent;
  color: #606266;
  font-size: 15px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.rtb-icon-btn:hover:not(:disabled) {
  background: #eef1fe;
  color: #4f6ef7;
}
.rtb-icon-btn .spin {
  animation: rtb-spin 0.9s linear infinite;
}
@keyframes rtb-spin {
  to { transform: rotate(360deg); }
}
.rtb-icon-btn:disabled {
  color: #c0c4cc;
  cursor: not-allowed;
}
.rtb-icon-btn.sm {
  width: 22px;
  height: 22px;
  font-size: 13px;
}
.rtb-icon-btn.is-on {
  background: #eef1fe;
  color: #4f6ef7;
}

.rtb-page-input {
  width: 38px;
  height: 26px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  text-align: center;
  font-size: 13px;
  color: #303133;
  font-family: inherit;
  outline: none;
}
.rtb-page-input:focus {
  border-color: #4f6ef7;
}
.rtb-page-total {
  font-size: 12px;
  color: #909399;
  padding-right: 4px;
  white-space: nowrap;
}

.rtb-zoom-label {
  min-width: 48px;
  height: 26px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: #606266;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
}
.rtb-zoom-label:hover {
  background: #eef1fe;
  color: #4f6ef7;
}

.rtb-text-btn {
  height: 26px;
  padding: 0 8px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: #606266;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
}
.rtb-text-btn:hover {
  background: #eef1fe;
  color: #4f6ef7;
}
.rtb-text-btn.is-on {
  background: #eef1fe;
  color: #4f6ef7;
  font-weight: 600;
}

/* ---------- 进度条 ---------- */
.rtb-progress {
  position: relative;
  height: 3px;
  background: #f0f2f5;
}
.rtb-progress-fill {
  height: 100%;
  background: #4f6ef7;
  transition: width 0.2s ease;
}
.rtb-progress-text {
  position: absolute;
  right: 12px;
  top: 4px;
  font-size: 11px;
  color: #a8abb2;
  pointer-events: none;
}

/* ---------- 响应式：窄屏收起次要控件，优先保住搜索与阅读区 ---------- */
@media (max-width: 1180px) {
  .rtb-back-text { display: none; }
  .rtb-search { max-width: 240px; }
}
@media (max-width: 1000px) {
  .rtb-text-btn { display: none; }
}
@media (max-width: 820px) {
  .rtb-main { gap: 8px; padding: 0 10px; }
  .rtb-center { display: none; }
  .rtb-zoom-label { min-width: 40px; }
}
</style>
