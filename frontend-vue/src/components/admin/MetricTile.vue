<script setup>
/**
 * 管理端指标卡（工作台 / 资源管理 / 系统监控共用）。
 *
 * value 传 null 表示"未检测/无数据"，显示为 —，而不是 0 ——
 * 把"没测到"显示成 0 会让人误以为该指标确实是零。
 */
defineProps({
  label: { type: String, default: '' },
  value: { type: [Number, String], default: null },
  icon: { type: [Object, Function], default: null },
  /** blue / violet / green / amber / red / slate */
  tone: { type: String, default: 'blue' },
  hint: { type: String, default: '' },
})
</script>

<template>
  <div class="metric-tile" :class="`tone-${tone}`">
    <div v-if="icon" class="mt-icon"><el-icon :size="18"><component :is="icon" /></el-icon></div>
    <div class="mt-body">
      <div class="mt-value">{{ value === null || value === undefined ? '—' : value }}</div>
      <div class="mt-label">{{ label }}</div>
      <div v-if="hint" class="mt-hint">{{ hint }}</div>
    </div>
  </div>
</template>

<style scoped>
.metric-tile {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  border-radius: var(--radius-lg);
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  box-shadow: var(--shadow-card);
  transition: box-shadow .22s ease, transform .22s ease;
}
.metric-tile:hover {
  box-shadow: var(--shadow-hover);
  transform: translateY(-2px);
}
.mt-icon {
  width: 38px;
  height: 38px;
  border-radius: 11px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  color: #fff;
}
.mt-body { min-width: 0; flex: 1; }
.mt-value {
  font-family: var(--font-family-number);
  font-size: 22px;
  font-weight: 700;
  line-height: 1.15;
  color: var(--text-primary);
}
.mt-label {
  margin-top: 2px;
  font-size: 12.5px;
  color: var(--color-text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.mt-hint {
  margin-top: 3px;
  font-size: 11.5px;
  color: var(--color-text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tone-blue .mt-icon { background: linear-gradient(135deg, #5b8def, #6a5cf6); box-shadow: 0 6px 14px -6px rgba(91,141,239,.7); }
.tone-violet .mt-icon { background: linear-gradient(135deg, #8b5cf6, #b06cf6); box-shadow: 0 6px 14px -6px rgba(139,92,246,.7); }
.tone-green .mt-icon { background: linear-gradient(135deg, #18b87a, #35d29a); box-shadow: 0 6px 14px -6px rgba(24,184,122,.7); }
.tone-amber .mt-icon { background: linear-gradient(135deg, #f5a623, #f4794d); box-shadow: 0 6px 14px -6px rgba(245,166,35,.7); }
.tone-red .mt-icon { background: linear-gradient(135deg, #f5475d, #f4794d); box-shadow: 0 6px 14px -6px rgba(245,71,93,.7); }
.tone-slate .mt-icon { background: linear-gradient(135deg, #8590a8, #64708c); box-shadow: 0 6px 14px -6px rgba(133,144,168,.7); }
</style>
