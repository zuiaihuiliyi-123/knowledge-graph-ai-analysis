/**
 * 图谱样式常量：节点类别与关系类型的中英文映射、颜色方案
 * 与后端 core/database.py 的 RELATION_TYPE_LABELS 保持一致
 */

// 节点类别（后端 type 字段为英文，properties.category 为中文）
export const NODE_TYPES = {
  concept: { label: '概念', color: '#409EFF' },
  theorem: { label: '定理', color: '#F56C6C' },
  formula: { label: '公式', color: '#E6A23C' },
  method: { label: '方法', color: '#67C23A' },
}

// 关系类型英文 -> 中文
export const EDGE_TYPE_LABELS = {
  PRECEDES: '前置知识',
  CONTAINS: '包含',
  RELATED_TO: '相关概念',
  APPLIES_TO: '应用',
}

export function nodeColor(type) {
  return NODE_TYPES[type]?.color || '#909399'
}

export function nodeTypeLabel(type) {
  return NODE_TYPES[type]?.label || type || '其他'
}

export function edgeTypeLabel(type, fallbackLabel) {
  return fallbackLabel || EDGE_TYPE_LABELS[type] || type || '相关'
}
