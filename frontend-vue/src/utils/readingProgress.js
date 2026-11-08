/**
 * 阅读进度与阅读偏好（第一阶段：localStorage）。
 *
 * 为什么先用 localStorage：后端 t_document 只有文档元数据，没有「按用户记录阅读位置」的表。
 * 新增该表需要动 DDL、迁移、权限与教师监测等多处业务，收益与风险不成比例。
 * 本模块把读写集中在一处，将来要迁到后端时，只需把下面几个函数换成 API 调用，
 * 阅读器组件无需改动（这是刻意的收敛点）。
 *
 * 存储结构：kg_reader_progress_v1 = { [userId]: { [docId]: 进度记录 } }
 * 按用户隔离，同一台机器上的多个账号互不干扰。
 */
const PROGRESS_KEY = 'kg_reader_progress_v1'
const PREFS_KEY = 'kg_reader_prefs_v1'

/** 进度记录字段（对齐「建议后端结构」：document_id / user_id / progress / last_page / updated_at） */
function emptyRecord(docId) {
  return {
    docId: String(docId),
    courseId: null,
    docName: '',
    percent: 0, // 阅读百分比（0-100，整数）
    page: 1, // 当前页（TXT/DOCX 等无分页文档为 1）
    totalPages: 0,
    scrollTop: 0, // 文档内滚动偏移，用于恢复无分页文档的位置
    scale: null, // PDF 缩放倍数（记录用户习惯，再次打开沿用）
    updatedAt: null,
  }
}

/** 当前用户命名空间：登录用户 id 优先，退化到用户名，未登录时用 guest */
function currentUserKey() {
  try {
    const user = JSON.parse(localStorage.getItem('kg_user') || 'null')
    return String(user?.user_id || user?.username || 'guest')
  } catch {
    return 'guest'
  }
}

function readJson(key) {
  try {
    const raw = localStorage.getItem(key)
    return raw ? JSON.parse(raw) : {}
  } catch {
    // 存储被禁用或内容损坏：退化为「无进度」，不影响阅读本身
    return {}
  }
}

function writeJson(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // 配额写满 / 隐私模式：静默忽略，阅读功能不受影响
  }
}

function nowIso() {
  return new Date().toISOString()
}

// ---------- 阅读进度 ----------

/**
 * 进度文案：滚到底时各浏览器算出的百分比可能是 99 而不是 100，
 * 列表里标「已读完」、阅读器里却写「已读 99%」会让用户以为还没读完。
 * 统一以 99 为「读完」阈值，两处共用这一份判断。
 */
export function readingProgressLabel(percent) {
  const p = Math.max(0, Math.min(100, Math.round(Number(percent) || 0)))
  return p >= 99 ? '已读完' : `已读 ${p}%`
}

/** 读取某文档的阅读进度；从未读过返回 null */
export function getReadingProgress(docId) {
  if (docId == null) return null
  const bucket = readJson(PROGRESS_KEY)[currentUserKey()]
  return bucket?.[String(docId)] || null
}

/**
 * 保存阅读进度（按字段合并，未传的字段保留原值）。
 * 返回写入后的完整记录，便于调用方直接使用。
 */
export function saveReadingProgress(docId, patch = {}) {
  if (docId == null) return null
  const all = readJson(PROGRESS_KEY)
  const userKey = currentUserKey()
  const bucket = all[userKey] || {}
  const merged = { ...emptyRecord(docId), ...(bucket[String(docId)] || {}), ...patch }
  merged.docId = String(docId)
  merged.updatedAt = nowIso()
  merged.percent = Math.max(0, Math.min(100, Math.round(Number(merged.percent) || 0)))
  bucket[String(docId)] = merged
  all[userKey] = bucket
  writeJson(PROGRESS_KEY, all)
  return merged
}

/** 清除某文档的阅读进度（「从头开始阅读」） */
export function clearReadingProgress(docId) {
  const all = readJson(PROGRESS_KEY)
  const userKey = currentUserKey()
  const bucket = all[userKey]
  if (!bucket) return
  delete bucket[String(docId)]
  all[userKey] = bucket
  writeJson(PROGRESS_KEY, all)
}

/** 最近阅读列表（按更新时间倒序），供「继续阅读」等入口使用 */
export function listReadingProgress(limit = 10) {
  const bucket = readJson(PROGRESS_KEY)[currentUserKey()] || {}
  return Object.values(bucket)
    .filter((r) => r && r.docId)
    .sort((a, b) => String(b.updatedAt || '').localeCompare(String(a.updatedAt || '')))
    .slice(0, limit)
}

// ---------- 阅读偏好（正文字号 / 行距 / 阅读宽度） ----------

// sidebarOpen / panelOpen 也放在这里：侧栏开合属于「阅读器状态」，
// 用户收起过一次之后，下次打开任何文档都应保持同样的布局
const DEFAULT_PREFS = {
  fontSize: 16,
  lineHeight: 1.9,
  maxWidth: 760,
  pdfScaleMode: 'fit-width',
  sidebarOpen: true,
  panelOpen: true,
}

export function getReaderPrefs() {
  return { ...DEFAULT_PREFS, ...readJson(PREFS_KEY) }
}

export function saveReaderPrefs(patch = {}) {
  const merged = { ...getReaderPrefs(), ...patch }
  writeJson(PREFS_KEY, merged)
  return merged
}
