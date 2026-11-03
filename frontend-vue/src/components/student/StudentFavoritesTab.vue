<template>
  <el-card class="page-card">
    <div class="toolbar">
      <el-input
        v-model="favSearch"
        placeholder="搜索收藏的知识点…"
        clearable
        style="width: 220px"
      />
      <el-select v-model="favCategory" placeholder="全部类型" clearable style="width: 140px">
        <el-option label="概念" value="概念" />
        <el-option label="定理" value="定理" />
        <el-option label="公式" value="公式" />
        <el-option label="方法" value="方法" />
      </el-select>
      <div class="fav-view-toggle">
        <button type="button" class="fav-view-btn" :class="{ active: favView === 'grid' }" @click="favView = 'grid'">卡片</button>
        <button type="button" class="fav-view-btn" :class="{ active: favView === 'list' }" @click="favView = 'list'">列表</button>
      </div>
    </div>

    <div v-if="ctxKey" class="fav-count">
      共收藏 <b class="num">{{ favoriteIds.length }}</b> 个知识点
      <span v-if="orphanCount > 0" class="fav-orphan-tip">
        （其中 {{ orphanCount }} 个已失效，可在下方取消收藏清理）
      </span>
    </div>

    <div v-loading="loading" class="fav-list">
      <el-empty
        v-if="!loading && !ctxKey"
        description="请先选择课程和学习资料，查看该文档的收藏"
        :image-size="80"
      />
      <el-empty
        v-else-if="!loading && loaded && favoriteIds.length === 0"
        :image-size="80"
      >
        <template #description>
          <p class="fav-empty-title">还没有收藏知识点</p>
          <p class="fav-empty-sub">在学习过程中收藏感兴趣或需要复习的知识点，它们会集中出现在这里。</p>
        </template>
        <el-button type="primary" @click="emit('go-browse')">去浏览知识图谱</el-button>
      </el-empty>
      <el-empty
        v-else-if="!loading && loaded && favoriteList.length === 0"
        description="没有符合条件的收藏"
        :image-size="80"
      />
      <div v-else-if="favoriteList.length && favView === 'list'" class="fav-rows">
        <div v-for="node in favoriteList" :key="node.id" class="fav-row" :class="{ 'is-orphan': node._orphan }">
          <el-icon class="fav-star"><StarFilled /></el-icon>
          <span class="fav-row-name">{{ node.label || '该知识点已不可用' }}</span>
          <el-tag size="small" effect="plain" :type="categoryTagType(node.properties?.category)">
            {{ node.properties?.category || (node._orphan ? '已失效' : '知识点') }}
          </el-tag>
          <span class="fav-row-desc">
            {{ node._orphan ? '已从图谱移除或合并，可取消收藏清理' : (node.description || '暂无描述') }}
          </span>
          <div class="fav-row-actions">
            <el-button v-if="!node._orphan" size="small" type="primary" plain @click="emit('view', node)">查看</el-button>
            <el-button size="small" type="danger" plain @click="emit('unfavorite', node.id)">取消收藏</el-button>
          </div>
        </div>
      </div>
      <div v-else-if="favoriteList.length" class="fav-grid">
        <div v-for="node in favoriteList" :key="node.id" class="fav-card" :class="{ 'is-orphan': node._orphan }">
          <div class="fav-card-head">
            <el-icon class="fav-star"><StarFilled /></el-icon>
            <span class="fav-name">{{ node.label || '该知识点已不可用' }}</span>
            <el-tag size="small" effect="plain" :type="categoryTagType(node.properties?.category)">
              {{ node.properties?.category || (node._orphan ? '已失效' : '知识点') }}
            </el-tag>
          </div>
          <div class="fav-desc">
            {{ node._orphan ? '已从图谱移除或合并，可取消收藏清理' : (node.description || '暂无描述') }}
          </div>
          <div class="fav-actions">
            <el-button v-if="!node._orphan" size="small" type="primary" plain @click="emit('view', node)">
              查看知识点
            </el-button>
            <el-button size="small" type="danger" plain @click="emit('unfavorite', node.id)">
              取消收藏
            </el-button>
          </div>
        </div>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { StarFilled } from '@element-plus/icons-vue'
import { categoryTagType } from '../../utils/graphStyle'

const props = defineProps({
  ctxKey: { type: String, default: '' },
  favoriteIds: { type: Array, default: () => [] },
  graphNodes: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  loaded: { type: Boolean, default: false },
})

const emit = defineEmits(['view', 'unfavorite', 'go-browse'])

const favSearch = ref('')
const favCategory = ref('')
const favView = ref(localStorage.getItem('fav-view') || 'grid')
watch(favView, (v) => {
  try { localStorage.setItem('fav-view', v) } catch { /* ignore */ }
})

// 收藏的 kp_id 在当前图谱里找不到节点时（节点被删 / 重新抽取换了 id / 被融合折叠），
// 不再静默丢弃——否则「共收藏 N 个」与列表条数对不上。给一个占位条目供清理。
const orphanCount = computed(() => {
  const nodeById = new Map(props.graphNodes.map((n) => [String(n.id), n]))
  return props.favoriteIds.filter((id) => !nodeById.has(String(id))).length
})

const favoriteList = computed(() => {
  const nodeById = new Map(props.graphNodes.map((n) => [String(n.id), n]))
  const kw = favSearch.value.trim().toLowerCase()
  const cat = favCategory.value
  return props.favoriteIds
    .map((id) => {
      const node = nodeById.get(String(id))
      if (node) return { ...node, _orphan: false }
      // 孤儿收藏：kp_id 在当前图谱找不到节点。保留占位条目，让计数与列表对得上，
      // 并提供「取消收藏」入口把失效记录清掉。
      return {
        id: String(id),
        label: '',
        description: '',
        properties: { category: '' },
        _orphan: true,
      }
    })
    .filter((n) => !cat || (n.properties?.category || '') === cat)
    .filter((n) => {
      if (!kw) return true
      // 有关键词搜索时，孤儿条目（无名称/描述）不命中，自然不显示
      if (n._orphan) return false
      return String(n.label).toLowerCase().includes(kw)
        || String(n.description || '').toLowerCase().includes(kw)
    })
})
</script>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

/* ===== 收藏夹 ===== */
.fav-count {
  margin: var(--space-3) 0 var(--space-2);
  font-size: var(--font-size-body);
  color: var(--color-text-regular);
}
.fav-count .num {
  font-size: 18px;
  font-weight: var(--font-weight-bold);
  color: var(--color-primary);
  margin: 0 2px;
}
.fav-orphan-tip {
  margin-left: var(--space-1);
  font-size: var(--font-size-caption);
  color: var(--color-warning);
}
.fav-list { min-height: 220px; }
.fav-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: var(--space-4);
}
.fav-card {
  display: flex;
  flex-direction: column;
  padding: var(--space-4) var(--space-4);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  background: var(--color-bg-surface);
  box-shadow: var(--shadow-card);
  transition: box-shadow 0.15s, border-color 0.15s;
}
.fav-card:hover {
  border-color: var(--el-color-primary-light-8);
  box-shadow: var(--shadow-hover);
}
.fav-card-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.fav-star {
  color: var(--color-warning);
  font-size: 16px;
  flex-shrink: 0;
}
.fav-name {
  flex: 1;
  min-width: 0;
  font-size: var(--font-size-section);
  font-weight: var(--font-weight-bold);
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fav-desc {
  margin-top: var(--space-2);
  font-size: var(--font-size-label);
  color: var(--color-text-regular);
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.fav-actions {
  display: flex;
  gap: var(--space-2);
  margin-top: var(--space-3);
}
.fav-view-toggle {
  display: inline-flex;
  margin-left: auto;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  overflow: hidden;
}
.fav-view-btn {
  border: none;
  background: transparent;
  padding: 7px 16px;
  font-size: var(--font-size-label);
  color: var(--color-text-regular);
  cursor: pointer;
  transition: background .18s ease, color .18s ease;
}
.fav-view-btn + .fav-view-btn { border-left: 1px solid var(--color-border-light); }
.fav-view-btn.active { background: var(--color-primary); color: #fff; }
.fav-view-btn:hover:not(.active) { background: rgba(91, 141, 239, .08); }
.fav-rows {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  overflow: hidden;
  background: var(--color-bg-surface);
}
.fav-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: 14px var(--space-4);
  border-bottom: 1px solid var(--color-border-light);
  transition: background .18s ease;
}
.fav-row:last-child { border-bottom: none; }
.fav-row:hover { background: rgba(91, 141, 239, .05); }
.fav-row-name {
  font-weight: var(--font-weight-bold);
  color: var(--color-text-primary);
  min-width: 140px;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fav-row-desc {
  flex: 1;
  min-width: 0;
  font-size: var(--font-size-label);
  color: var(--color-text-regular);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fav-row-actions { display: flex; gap: var(--space-2); flex-shrink: 0; }
/* 孤儿收藏（kp_id 在当前图谱已失效）：虚线边 + 灰名，与正常收藏区分 */
.fav-row.is-orphan .fav-row-name,
.fav-card.is-orphan .fav-name { color: var(--color-text-secondary); }
.fav-row.is-orphan { background: rgba(245, 108, 108, .04); }
.fav-card.is-orphan { border-style: dashed; opacity: .92; }
.fav-empty-title {
  margin: 0 0 var(--space-1);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-primary);
}
.fav-empty-sub {
  margin: 0;
  font-size: var(--font-size-caption);
  color: var(--color-text-secondary);
  line-height: 1.6;
}

/* v2 视觉增强 */
.fav-card {
  border-radius: var(--radius-md);
  transition: transform .22s, box-shadow .22s, border-color .22s;
}
.fav-card:hover {
  transform: translateY(-3px);
  border-color: var(--brand-200);
}
.fav-actions { gap: 8px; }
.fav-actions .el-button {
  flex: 0 0 auto;
  padding-left: 12px;
  padding-right: 12px;
  margin-left: 0;
}
</style>
