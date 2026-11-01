<template>
  <div>
    <!-- P5：学习路径视觉层级（已掌握 → 当前 → 推荐 → 未学习） -->
    <el-card class="page-card path-hero">
      <template #header>
        <div class="path-hero-header">
          <span class="path-hero-title"><el-icon><Guide /></el-icon>学习路径</span>
        </div>
      </template>

      <el-empty
        v-if="!ctxKey"
        description="请先选择课程和学习资料，查看你的学习路径"
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
              color="linear-gradient(90deg,#5b8def,#8b5cf6)"
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
              <div
                class="chain-node"
                :class="'state-' + seg.state"
                title="点击在图谱中定位并查看详情"
                @click="emit('locate', seg.node)"
              >
                <div class="chain-icon">{{ STATE_META[seg.state].glyph }}</div>
                <div class="chain-content">
                  <div class="chain-head">
                    <span class="chain-name">{{ seg.node.label }}</span>
                    <el-tag size="small" effect="plain" :type="categoryTagType(seg.node.properties?.category)">
                      {{ seg.node.properties?.category || '知识点' }}
                    </el-tag>
                    <span class="state-label" :class="'state-label-' + seg.state">{{ STATE_META[seg.state].label }}</span>
                    <el-button
                      v-if="ctxKey && seg.node.id != null"
                      size="small"
                      text
                      class="chain-fav"
                      :title="isFav(seg.node.id) ? '取消收藏' : '收藏'"
                      :type="isFav(seg.node.id) ? 'warning' : 'info'"
                      @click.stop="emit('toggle-favorite', seg.node.id)"
                    >
                      <el-icon><StarFilled v-if="isFav(seg.node.id)" /><Star v-else /></el-icon>
                    </el-button>
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

    <!-- 建议重点学习：与学习路径/推荐同归学习空间 -->
    <el-card class="page-card ov-card">
      <template #header>
        <div class="ov-card-title"><el-icon><Aim /></el-icon>建议重点学习</div>
      </template>
      <div v-if="overviewFocusList.length" class="ov-focus-list">
        <div
          v-for="f in overviewFocusList"
          :key="f.kpId || f.name"
          class="ov-focus-item"
          @click="emit('view-focus', f)"
        >
          <div class="ov-focus-head">
            <span class="ov-focus-name">{{ f.name }}</span>
            <el-tag size="small" effect="plain" :type="categoryTagType(f.category)">{{ f.category || '知识点' }}</el-tag>
            <el-button size="small" text type="primary" class="ov-focus-view" @click.stop="emit('view-focus', f)">查看</el-button>
          </div>
          <div v-if="f.reason" class="ov-focus-reason">{{ f.reason }}</div>
        </div>
      </div>
      <el-empty v-else description="暂无推荐，请先在课程中标记已掌握知识点" :image-size="60" />
    </el-card>

    <el-row :gutter="16">
      <!-- 推荐下一步 -->
      <el-col :xs="24" :sm="12">
        <PathRecommendPanel
          :ctx-key="ctxKey"
          :course-id="courseId"
          :document-id="documentId"
        />
      </el-col>

      <!-- 目标路径 + 前置知识 -->
      <el-col :xs="24" :sm="12">
        <PathTargetPanel
          :ctx-key="ctxKey"
          :course-id="courseId"
          :document-id="documentId"
          @highlight-path="emit('highlight-path', $event)"
          @go-browse="emit('go-browse')"
        />
        <PathPrereqPanel
          :ctx-key="ctxKey"
          :course-id="courseId"
          :document-id="documentId"
        />
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ElMessage } from 'element-plus'
import {
  Aim, ArrowDown, CircleCheckFilled, Guide, Star, StarFilled,
} from '@element-plus/icons-vue'
import { categoryTagType } from '../../utils/graphStyle'
import PathRecommendPanel from './PathRecommendPanel.vue'
import PathTargetPanel from './PathTargetPanel.vue'
import PathPrereqPanel from './PathPrereqPanel.vue'

const props = defineProps({
  ctxKey: { type: String, default: '' },
  courseId: { type: [String, Number], default: '' },
  documentId: { type: [String, Number], default: '' },
  pathProgress: { type: Object, default: () => ({ total: 0, mastered: 0, pct: 0, current: '' }) },
  pathChain: { type: Array, default: () => [] },
  pathDataLoading: { type: Boolean, default: false },
  pathDataChecked: { type: Boolean, default: false },
  currentNode: { type: Object, default: null },
  overviewFocusList: { type: Array, default: () => [] },
  favoriteIds: { type: Array, default: () => [] },
})

const emit = defineEmits(['locate', 'view-focus', 'toggle-favorite', 'highlight-path', 'go-browse'])

const STATE_META = {
  mastered: { label: '已掌握', glyph: '✓' },
  current: { label: '当前学习', glyph: '●' },
  recommended: { label: '推荐学习', glyph: '★' },
  unlearned: { label: '未学习', glyph: '○' },
}

function isFav(kpId) {
  return props.favoriteIds.includes(String(kpId))
}

// ---------------- 页面级交互 ----------------
// 三个查询面板已拆分到 PathRecommendPanel / PathTargetPanel / PathPrereqPanel，
// 它们各自持有请求状态并在 ctxKey 变化时取消在途请求 + 清空结果；
// 这里只保留与本页「路径链 / 建议重点学习」相关的事件。
function startLearning() {
  if (!props.currentNode) return
  emit('locate', props.currentNode)
  ElMessage.success(`开始学习「${props.currentNode.label}」`)
}
</script>

<style scoped>
/* 三个查询面板（推荐 / 目标路径 / 前置知识）的共用样式已抽到 src/styles/path-panels.css */

/* ===== P5 学习路径视觉层级 ===== */
.path-hero-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}
.path-hero-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: var(--font-weight-semibold);
}
.path-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-6);
  flex-wrap: wrap;
  padding: var(--space-4) var(--space-4);
  background: var(--color-bg-soft);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  margin-bottom: var(--space-4);
}
.summary-left {
  flex: 1;
  min-width: 0;
}
.summary-line {
  font-size: var(--font-size-body);
  color: var(--color-text-regular);
}
.summary-line .num {
  font-size: 22px;
  font-weight: var(--font-weight-bold);
  color: var(--color-primary);
  margin: 0 2px;
}
.summary-line .muted {
  color: var(--color-text-secondary);
  font-size: var(--font-size-label);
}
.summary-suggestion {
  margin-top: 6px;
  font-size: var(--font-size-label);
  color: var(--color-primary);
  font-weight: var(--font-weight-semibold);
}
.summary-suggestion.success { color: var(--color-success); }
.summary-suggestion.muted {
  color: var(--color-text-secondary);
  font-weight: var(--font-weight-regular);
}
.summary-progress {
  width: 220px;
  flex-shrink: 0;
}
.path-chain-wrap { min-height: 120px; }
.path-chain {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: var(--space-2) 0;
}
.chain-node {
  display: flex;
  gap: var(--space-3);
  width: 100%;
  max-width: 560px;
  padding: var(--space-3) var(--space-4);
  background: var(--color-bg-surface);
  border: 1px solid var(--color-border);
  border-left: 4px solid var(--color-text-muted);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-card);
  cursor: pointer;
  transition: transform 0.15s, box-shadow 0.15s;
}
.chain-node:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-hover);
}
.chain-node.state-mastered { border-left-color: var(--color-success); }
.chain-node.state-current {
  border-left-color: var(--color-primary);
  background: var(--color-bg-hover);
  border-color: var(--el-color-primary-light-8);
  box-shadow: 0 4px 16px rgba(64, 158, 255, 0.15);
}
.chain-node.state-recommended { border-left-color: var(--color-warning); }
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
  font-weight: var(--font-weight-bold);
  flex-shrink: 0;
}
.state-mastered .chain-icon {
  background: var(--el-color-success-light-9);
  color: var(--color-success);
}
.state-current .chain-icon {
  background: var(--color-primary);
  color: var(--text-inverse);
}
.state-recommended .chain-icon {
  background: var(--el-color-warning-light-9);
  color: var(--color-warning);
}
.state-unlearned .chain-icon {
  background: var(--el-fill-color-light);
  color: var(--color-text-muted);
}
.chain-content { flex: 1; min-width: 0; }
.chain-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.chain-name {
  font-size: var(--font-size-section);
  font-weight: var(--font-weight-bold);
  color: var(--color-text-primary);
}
.state-label {
  font-size: var(--font-size-caption);
  padding: 1px 8px;
  border-radius: var(--radius-lg);
  font-weight: var(--font-weight-semibold);
}
.state-label-mastered {
  color: var(--color-success);
  background: var(--el-color-success-light-9);
}
.state-label-current {
  color: var(--color-primary);
  background: var(--color-primary-light);
}
.state-label-recommended {
  color: var(--color-warning);
  background: var(--el-color-warning-light-9);
}
.state-label-unlearned {
  color: var(--color-text-secondary);
  background: var(--el-color-info-light-9);
}
.chain-reason {
  margin-top: 6px;
  font-size: var(--font-size-label);
  color: var(--color-text-regular);
}
.chain-status {
  margin-top: 4px;
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
}
.chain-link {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  color: var(--color-text-muted);
  font-size: var(--font-size-caption);
  padding: var(--space-1) 0;
}
.path-actions {
  display: flex;
  justify-content: center;
  margin-top: var(--space-4);
}

/* 建议重点学习 */
.ov-focus-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.ov-focus-item {
  padding: 10px var(--space-3);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.ov-focus-item:hover {
  background: var(--color-bg-hover);
  border-color: var(--el-color-primary-light-8);
}
.ov-focus-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.ov-focus-name {
  flex: 1;
  min-width: 0;
  font-size: var(--font-size-body);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ov-focus-view { flex-shrink: 0; padding: 2px; }
.ov-focus-reason {
  margin-top: 6px;
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  line-height: 1.5;
}

/* v2 视觉增强 */
.path-summary {
  background: linear-gradient(120deg, rgba(91, 141, 239, .07), rgba(34, 192, 138, .05));
  border-color: var(--brand-100);
}
.summary-line .num { color: var(--brand-600); }
.chain-node {
  border-left-width: 4px;
  border-radius: var(--radius-md);
  transition: transform .2s ease, box-shadow .2s ease;
}
.chain-node.state-current {
  background: linear-gradient(120deg, rgba(91, 141, 239, .1), rgba(139, 92, 246, .06));
  border-color: var(--brand-200);
  box-shadow: 0 8px 22px -12px rgba(79, 110, 247, .45);
}
.state-current .chain-icon {
  background: var(--gradient-brand) !important;
  color: #fff !important;
  box-shadow: 0 6px 14px -6px rgba(79, 110, 247, .7);
}
.state-mastered .chain-icon { box-shadow: inset 0 1px 0 rgba(255, 255, 255, .5); }
.state-recommended .chain-icon { box-shadow: inset 0 1px 0 rgba(255, 255, 255, .5); }
.chain-link {
  flex-direction: column;
  gap: 1px;
  padding: 2px 0;
  color: var(--brand-400);
}
.chain-link .el-icon { transform: rotate(180deg); }
.ov-focus-item:hover {
  background: var(--brand-50);
  border-color: var(--brand-200);
}
.chain-fav {
  flex-shrink: 0;
  margin-left: auto;
  padding: 2px;
}
</style>
