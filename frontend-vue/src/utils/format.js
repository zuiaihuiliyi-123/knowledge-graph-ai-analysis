/** 通用格式化工具（教师端与学生端共用，避免两套近似实现继续漂移） */

export function formatTime(value) {
  return value ? String(value).slice(0, 16) : '—'
}

/**
 * 把后端时间字符串格式化为 "YYYY-MM-DD HH:mm"（本地时区）。
 *
 * 为什么不能继续用 String(t).slice(0, 16)：SQLite 存的是
 * "2026-11-03 08:00:00" 或 ISO "2026-11-03T08:00:00Z"，直接切片会把
 * "T" 留在界面上，且不做时区换算。
 *
 * 兼容：SQLite 本地时间字符串 / ISO 8601（带 Z 或 +08:00）/ Date / 时间戳。
 */
export function formatDateTime(value, fallback = '—') {
  if (value === null || value === undefined || value === '') return fallback
  let raw = value
  if (typeof raw === 'string') {
    raw = raw.trim()
    // "YYYY-MM-DD HH:mm:ss" 不是标准 ISO，Safari 等浏览器会解析成 Invalid Date；
    // 加 T 后按**本地时间**解析（与后端 datetime('now','localtime') 语义一致）。
    if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(raw)) raw = raw.replace(' ', 'T')
  }
  const d = raw instanceof Date ? raw : new Date(raw)
  if (Number.isNaN(d.getTime())) {
    // 无法解析时至少去掉 T，不把原始 ISO 直接暴露给用户
    return String(value).replace('T', ' ').slice(0, 16) || fallback
  }
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 文件大小格式化：B / KB / MB / GB（教师端与学生端共用） */
export function formatFileSize(bytes, fallback = '—') {
  const n = Number(bytes)
  if (!Number.isFinite(n) || n < 0) return fallback
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`
}

/**
 * 文件大小。
 * 教师文档表：0 显示 0 B；学生文档列表：0/空显示 —。
 */
export function formatBytes(bytes, { kbDigits = 1, zeroAsDash = false } = {}) {
  if (bytes == null || (zeroAsDash && !bytes)) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(kbDigits)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
