<template>
  <div ref="rootEl" class="dsw">
    <button
      class="dsw-trigger"
      type="button"
      :class="{ 'is-open': open }"
      :title="title"
      @click="toggle"
    >
      <span class="dsw-trigger-text">
        <span class="dsw-title">{{ title || '加载中…' }}</span>
        <span class="dsw-meta">
          <span v-if="fileType" class="dsw-badge">{{ fileType }}</span>
          <span v-if="subtitle" class="dsw-sub" :title="subtitle">{{ subtitle }}</span>
          <span v-if="fileSize" class="dsw-sub">{{ fileSize }}</span>
        </span>
      </span>
      <el-icon class="dsw-caret" :class="{ 'is-open': open }"><ArrowDown /></el-icon>
    </button>

    <!-- 当前课程的文档列表：切换后仍在本课程内，权限口径与课程文档列表一致 -->
    <div v-if="open" class="dsw-panel" role="listbox">
      <div class="dsw-panel-head">
        <span class="dsw-panel-title">课程文档</span>
        <span class="dsw-panel-count">共 {{ documents.length }} 个</span>
      </div>
      <div class="dsw-panel-body">
        <button
          v-for="d in documents"
          :key="d.doc_id"
          class="dsw-item"
          :class="{ 'is-current': String(d.doc_id) === String(docId) }"
          type="button"
          role="option"
          :aria-selected="String(d.doc_id) === String(docId)"
          @click="pick(d)"
        >
          <span class="dsw-item-main">
            <span class="dsw-item-name">{{ d.file_name }}</span>
            <span class="dsw-item-sub">
              <span class="dsw-item-type">{{ d.file_type }}</span>
              <span v-if="sizeText(d.file_size)">{{ sizeText(d.file_size) }}</span>
              <span v-if="progressOf(d)" class="dsw-item-progress" :class="{ 'is-done': progressOf(d) >= 99 }">
                {{ readingProgressLabel(progressOf(d)) }}
              </span>
            </span>
          </span>
          <el-icon v-if="String(d.doc_id) === String(docId)" class="dsw-item-check"><Select /></el-icon>
        </button>
        <p v-if="!documents.length" class="dsw-panel-empty">当前课程没有其他文档</p>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * 顶栏「当前文档」下拉：在阅读器内直接切换同课程的其他文档。
 *
 * 只做导航，不做内容加载 —— 切换后由阅读器页面按新的 docId 重新走一遍既有流程，
 * 因此权限、课程归属、阅读进度隔离全部沿用原有逻辑，这里不引入第二套状态。
 */
import { onBeforeUnmount, ref, watch } from 'vue'
import { ArrowDown, Select } from '@element-plus/icons-vue'
import { getReadingProgress, readingProgressLabel } from '../../utils/readingProgress'

const props = defineProps({
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' },
  fileType: { type: String, default: '' },
  fileSize: { type: String, default: '' },
  /** 当前文档 id（字符串） */
  docId: { type: [String, Number], default: '' },
  /** 当前课程的文档列表（与课程文档列表同源） */
  documents: { type: Array, default: () => [] },
})

const emit = defineEmits(['switch'])

const rootEl = ref(null)
const open = ref(false)

function toggle() {
  open.value = !open.value
}

function close() {
  open.value = false
}

function pick(doc) {
  close()
  if (String(doc.doc_id) === String(props.docId)) return
  emit('switch', doc)
}

function sizeText(bytes) {
  const n = Number(bytes)
  if (!n) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

/** 每篇文档的阅读进度来自本地记录，读到哪一目了然，方便挑一篇继续 */
function progressOf(doc) {
  const record = getReadingProgress(doc.doc_id)
  return record?.percent > 0 ? record.percent : 0
}

function onDocMouseDown(e) {
  if (!rootEl.value?.contains(e.target)) close()
}

function onKeydown(e) {
  if (e.key === 'Escape') close()
}

watch(open, (v) => {
  if (v) {
    document.addEventListener('mousedown', onDocMouseDown, true)
    document.addEventListener('keydown', onKeydown)
  } else {
    document.removeEventListener('mousedown', onDocMouseDown, true)
    document.removeEventListener('keydown', onKeydown)
  }
})

// 换了文档就收起面板，避免面板还开着指向旧的当前位置
watch(() => props.docId, close)

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocMouseDown, true)
  document.removeEventListener('keydown', onKeydown)
})
</script>

<style scoped>
.dsw {
  position: relative;
  min-width: 0;
}

.dsw-trigger {
  display: flex;
  align-items: center;
  gap: 6px;
  max-width: 360px;
  padding: 3px 8px 3px 7px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.dsw-trigger:hover,
.dsw-trigger.is-open {
  background: #f5f7fa;
  border-color: #e4e7ed;
}
.dsw-trigger-text {
  min-width: 0;
}
.dsw-title {
  display: block;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  line-height: 1.3;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.dsw-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 1px;
  font-size: 12px;
  color: #909399;
  white-space: nowrap;
  overflow: hidden;
}
.dsw-badge {
  padding: 0 5px;
  border-radius: 3px;
  background: #eef1fe;
  color: #4f6ef7;
  font-size: 11px;
  font-weight: 600;
  line-height: 15px;
}
.dsw-sub {
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 180px;
}
.dsw-caret {
  font-size: 12px;
  color: #a8abb2;
  flex-shrink: 0;
  transition: transform 0.15s;
}
.dsw-caret.is-open {
  transform: rotate(180deg);
}

/* ---------- 下拉面板 ---------- */
.dsw-panel {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  z-index: 30;
  width: 380px;
  max-width: 78vw;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
  overflow: hidden;
}
.dsw-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 9px 12px;
  border-bottom: 1px solid #f0f2f5;
  font-size: 12px;
  color: #606266;
}
.dsw-panel-title {
  font-weight: 600;
}
.dsw-panel-count {
  color: #a8abb2;
}
.dsw-panel-body {
  max-height: 52vh;
  overflow-y: auto;
  padding: 4px;
}
.dsw-panel-empty {
  margin: 0;
  padding: 20px 12px;
  text-align: center;
  font-size: 12px;
  color: #a8abb2;
}

.dsw-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 8px;
  border: none;
  border-radius: 6px;
  background: transparent;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}
.dsw-item:hover {
  background: #f5f7fa;
}
.dsw-item.is-current {
  background: #eef1fe;
}
.dsw-item-main {
  flex: 1;
  min-width: 0;
}
.dsw-item-name {
  display: block;
  font-size: 13px;
  color: #303133;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.dsw-item.is-current .dsw-item-name {
  color: #4f6ef7;
  font-weight: 600;
}
.dsw-item-sub {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 2px;
  font-size: 11px;
  color: #909399;
}
.dsw-item-type {
  padding: 0 4px;
  border-radius: 3px;
  background: #f0f2f5;
  line-height: 15px;
}
.dsw-item-progress {
  color: #4f6ef7;
}
.dsw-item-progress.is-done {
  color: #67c23a;
}
.dsw-item-check {
  color: #4f6ef7;
  font-size: 14px;
  flex-shrink: 0;
}

@media (max-width: 1180px) {
  .dsw-trigger { max-width: 240px; }
  .dsw-sub { max-width: 110px; }
}
@media (max-width: 1000px) {
  .dsw-meta { display: none; }
  .dsw-trigger { max-width: 170px; }
}
</style>
