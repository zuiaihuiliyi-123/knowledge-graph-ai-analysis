/**
 * 课程状态展示口径（管理员端共用）。
 *
 * 课程状态是**两个正交维度**，务必分开理解和展示：
 *
 *   status（业务状态）              governance_status（治理状态）
 *   0 = 已关闭 / 1 = 开放           normal / hidden / archived
 *   由教师或管理员的「关闭/重开」设置   由平台治理动作（下架 / 归档）设置
 *   治理动作**不会**写这一列          **不会**写 status
 *
 * 学生是否可见 = 两者同时满足（业务开放 且 治理正常）。
 * 这与后端 core/sql_database.py 的 COURSE_VISIBLE_SQL / course_is_visible 是同一口径，
 * 后端在发现课程、申请加入、加课码、邀请落地、公开课元数据五处都按这个组合判定。
 *
 * 抽成公共模块的原因：这段判定原先在课程管理与课程治理两个页面各写了一遍，
 * 语义一旦调整就会漂移——所以放这里，只保留一份。
 */

export const GOVERNANCE_LABELS = {
  normal: '正常',
  hidden: '已下架',
  archived: '已归档',
}

const GOVERNANCE_TAG_TYPES = {
  normal: 'success',
  hidden: 'warning',
  archived: 'info',
}

/** 治理状态中文名（未知值按「正常」兜底，与后端默认值一致） */
export function govLabel(status) {
  return GOVERNANCE_LABELS[status] || GOVERNANCE_LABELS.normal
}

/** 治理状态对应的 el-tag type */
export function govTagType(status) {
  return GOVERNANCE_TAG_TYPES[status] || GOVERNANCE_TAG_TYPES.normal
}

/** 业务状态中文名（0=已关闭，其余按开放） */
export function bizLabel(status) {
  return status === 0 ? '已关闭' : '开放'
}

/** 业务状态对应的 el-tag type */
export function bizTagType(status) {
  return status === 0 ? 'danger' : 'success'
}

/**
 * 学生是否可见（可发现 / 可申请 / 可用加课码或邀请加入）。
 * 与后端 course_is_visible 保持一致：业务状态开放 **且** 治理状态正常。
 */
export function visibleToStudents(course) {
  if (!course) return false
  return course.status === 1 && (course.governance_status || 'normal') === 'normal'
}

/** 治理动作的启用条件（前端只做「避免发出必然失败的请求」，真正的校验在后端） */
export function governanceActionDisabled(action, course) {
  const gs = course?.governance_status || 'normal'
  const biz = course?.status === 0 ? 0 : 1
  switch (action) {
    case 'hide': return gs === 'hidden' || gs === 'archived'
    case 'restore': return gs !== 'hidden'
    case 'archive': return gs === 'archived'
    case 'unarchive': return gs !== 'archived'
    case 'close': return biz === 0 || gs !== 'normal'
    case 'reopen': return biz === 1 || gs !== 'normal'
    default: return true
  }
}
