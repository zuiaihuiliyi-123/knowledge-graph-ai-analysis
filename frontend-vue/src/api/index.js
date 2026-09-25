import request from './request'

/**
 * 后端 API 封装
 * 教师端图谱编辑（新增/更新/删除节点、新增/删除关系）对齐后端 /api/v1/graph 编辑端点。
 */
export const api = {
  // ---- 用户认证（对齐后端 /api/auth，注意路由无 /v1 版本号） ----
  login: (data) => request.post('/api/auth/login', data),
  register: (data) => request.post('/api/auth/register', data),
  changePassword: (data) => request.post('/api/auth/change-password', data),
  /** 注销账号（软停用：置 is_active=0，需校验当前密码） */
  deactivateAccount: (data) => request.post('/api/auth/deactivate', data),

  // ---- 健康检查 ----
  health: () => request.get('/health'),

  // ---- 文档 / 课程 ----
  /** 上传文档到已有课程（对齐后端 POST /api/v1/documents/upload；LLM 抽取耗时较长，超时放宽到 10 分钟） */
  uploadCourse: (formData, courseId) => {
    if (courseId != null && courseId !== '') formData.append('course_id', courseId)
    return request.post('/api/v1/documents/upload', formData, { timeout: 600000 })
  },

  // ---- 文档管理（对齐后端 /api/v1/documents CRUD） ----
  /** 某课程文档列表 */
  getDocuments: (courseId) => request.get('/api/v1/documents', { params: { course_id: courseId } }),
  /** 文档详情 */
  getDocumentDetail: (docId) => request.get(`/api/v1/documents/${docId}`),
  /** 删除文档（Phase 5 后端仅删记录+文件，图谱级清理留待 Phase 8/9） */
  deleteDocument: (docId) => request.delete(`/api/v1/documents/${docId}`),

  // ---- 课程管理（对齐后端 /api/v1/courses CRUD） ----
  /** 课程列表（后端已收敛为「我可访问的课程」，teacher_id 传参不再能扩大范围） */
  listCourses: (params = {}) => request.get('/api/v1/courses', { params }),
  getCourse: (courseId) => request.get(`/api/v1/courses/${courseId}`),
  createCourse: (data) => request.post('/api/v1/courses', data),
  updateCourse: (courseId, data) => request.put(`/api/v1/courses/${courseId}`, data),
  deleteCourse: (courseId, confirm = true) =>
    request.delete(`/api/v1/courses/${courseId}`, { params: { confirm } }),

  // ---- 课程中心（我的课程 / 发现课程 / 加入课程） ----
  /** 我的课程：teacher=自己创建+协作的；student=已通过审核的 */
  getMyCourses: (params = {}) => request.get('/api/v1/courses/my', { params }),
  /** 发现课程：公开且启用中的课程，自动排除已加入/已申请的 */
  discoverCourses: (params = {}) => request.get('/api/v1/courses/discover', { params }),
  /** 用加课码加入（是否需要审核由课程加入方式决定） */
  joinByCode: (joinCode, reason = null) =>
    request.post('/api/v1/courses/join-by-code', { join_code: joinCode, reason }),
  /** 从发现课程申请加入公开课 */
  applyToCourse: (courseId, reason = null) =>
    request.post(`/api/v1/courses/${courseId}/apply`, { reason }),
  /** 查看加课码（仅该课程教师） */
  getJoinCode: (courseId) => request.get(`/api/v1/courses/${courseId}/join-code`),
  /** 刷新加课码（旧码立即失效） */
  refreshJoinCode: (courseId) => request.post(`/api/v1/courses/${courseId}/join-code/refresh`),
  /** 设置加入方式与是否公开 */
  setJoinMode: (courseId, data) => request.put(`/api/v1/courses/${courseId}/join-mode`, data),

  // ---- 课程成员管理（对齐后端 /api/v1/courses/{id}/members） ----
  /** 成员列表（status: pending/approved/rejected/removed） */
  getCourseMembers: (courseId, params = {}) =>
    request.get(`/api/v1/courses/${courseId}/members`, { params }),
  /** 成员统计（总人数/已通过/待审核/已移除/平均进度） */
  getMemberStats: (courseId) => request.get(`/api/v1/courses/${courseId}/members/stats`),
  /** 同意加入申请 */
  approveMember: (courseId, userId) =>
    request.post(`/api/v1/courses/${courseId}/members/${userId}/approve`),
  /** 拒绝加入申请（可填理由） */
  rejectMember: (courseId, userId, comment = null) =>
    request.post(`/api/v1/courses/${courseId}/members/${userId}/reject`, { comment }),
  /** 移除成员（软移除，保留其学习记录）；学生传自己的 userId 即「退出课程」 */
  removeMember: (courseId, userId, confirm = true) =>
    request.delete(`/api/v1/courses/${courseId}/members/${userId}`, { params: { confirm } }),

  // ---- 课程邀请 ----
  /** 生成邀请链接（role: student/teacher；expires_in_days 默认 7 天） */
  createInvite: (courseId, data = {}) =>
    request.post(`/api/v1/courses/${courseId}/invites`, data),
  /** 邀请列表（含折算过期后的 effective_status） */
  listInvites: (courseId) => request.get(`/api/v1/courses/${courseId}/invites`),
  /** 撤销邀请 */
  revokeInvite: (courseId, inviteId) =>
    request.delete(`/api/v1/courses/${courseId}/invites/${inviteId}`),
  /** 邀请落地页预览（未接受前先展示课程信息） */
  previewInvite: (token) => request.get(`/api/v1/invites/${encodeURIComponent(token)}`),
  /** 接受邀请（幂等） */
  acceptInvite: (token) => request.post('/api/v1/invites/accept', { token }),

  // ---- 个人中心（对齐后端 /api/v1/profile） ----
  /** 获取当前登录用户的资料（身份 + 个人资料） */
  getProfile: () => request.get('/api/v1/profile'),
  /** 更新资料（只传需要改的字段；传空串表示清空） */
  updateProfile: (data) => request.put('/api/v1/profile', data),
  /** 上传头像（jpg/jpeg/png/webp，≤2MB） */
  deleteAvatar: () => request.delete('/api/v1/profile/avatar'),
  uploadAvatar: (formData) =>
    request.post('/api/v1/profile/avatar', formData, { headers: { 'Content-Type': 'multipart/form-data' } }),

  // ---- 知识图谱（G6 格式） ----
  /** 图谱接口（Phase 8：按 course_id + document_id 隔离）：
   *  { nodes: [{id,label,type,description,properties}], edges: [...] } */
  getGraphV1: (courseId, documentId, params = {}) =>
    request.get(`/api/v1/graph/${courseId}`, {
      params: { document_id: documentId, ...params },
    }),

  // ---- 教师编辑（对齐后端 /api/v1/graph 编辑端点 6.3.3/6.3.4；Phase 8 全部带 document_id） ----
  createNode: (courseId, documentId, data) =>
    request.post(`/api/v1/graph/${courseId}/nodes`, data, { params: { document_id: documentId } }),
  updateNode: (courseId, documentId, nodeId, data) =>
    request.put(`/api/v1/graph/${courseId}/nodes/${nodeId}`, data, {
      params: { document_id: documentId },
    }),
  deleteNode: (courseId, documentId, nodeId) =>
    request.delete(`/api/v1/graph/${courseId}/nodes/${nodeId}`, {
      params: { document_id: documentId },
    }),
  createEdge: (courseId, documentId, data) =>
    request.post(`/api/v1/graph/${courseId}/edges`, data, { params: { document_id: documentId } }),
  deleteEdge: (courseId, documentId, edgeId) =>
    request.delete(`/api/v1/graph/${courseId}/edges/${edgeId}`, {
      params: { document_id: documentId },
    }),

  // ---- 图谱统计 ----
  getStats: () => request.get('/api/kg/stats'),
  getAllGraphs: () => request.get('/api/kg/all'),

  // ---- 数据总览 ----
  /** 全局统计：课程/用户/文档/知识点/关系 + 每课程概览 + 类别/关系分布 */
  getDashboardStats: () => request.get('/api/v1/dashboard/stats'),

  // ---- 教师教学监测 ----
  /** 查看自己课程下的学生学习进度（课程归属校验） */
  getTeacherStudentsProgress: (courseId) =>
    request.get('/api/v1/teacher/students/progress', { params: { course_id: courseId } }),

  // ---- 智能问答（Phase 8C：course_id + document_id） ----
  ask: (question, courseId, documentId) =>
    request.post('/api/v1/qa/ask', {
      question,
      course_id: courseId || null,
      document_id: documentId || null,
    }),

  // ---- 学习路径推荐（Phase 8B：course_id + document_id） ----
  recommendNext: (mastered, courseId, documentId) =>
    request.post('/api/v1/learning-path/recommend', {
      mastered,
      course_id: courseId || null,
      document_id: documentId || null,
    }),
  pathToTarget: (target, courseId, documentId) =>
    request.post('/api/v1/learning-path/path-to-target', {
      target,
      course_id: courseId || null,
      document_id: documentId || null,
    }),
  getPrerequisites: (name, courseId, documentId) =>
    request.get(`/api/v1/learning-path/prerequisites/${encodeURIComponent(name)}`, {
      params: { course_id: courseId || undefined, document_id: documentId || undefined },
    }),

  // ---- 学习记录（标记掌握 / 查询进度；Phase 8B：document_id） ----
  markMastered: (courseId, documentId, kpId, status = 'MASTERED', masteryLevel = 100) =>
    request.post('/api/v1/learning/mark', {
      course_id: courseId,
      document_id: documentId,
      kp_id: kpId,
      status,
      mastery_level: masteryLevel,
    }),
  unmarkMastered: (courseId, documentId, kpId) =>
    request.delete('/api/v1/learning/mark', {
      params: { course_id: courseId, document_id: documentId, kp_id: kpId },
    }),
  getProgress: (courseId, documentId) =>
    request.get('/api/v1/learning/progress', {
      params: { course_id: courseId || undefined, document_id: documentId || undefined },
    }),

  // ---- 收藏夹（学生个人知识点书签，独立于学习状态；Phase 8B：document_id） ----
  getFavorites: (courseId, documentId) =>
    request.get('/api/v1/favorites', {
      params: { course_id: courseId || undefined, document_id: documentId || undefined },
    }),
  addFavorite: (courseId, documentId, kpId) =>
    request.post('/api/v1/favorites', { course_id: courseId, document_id: documentId, kp_id: kpId }),
  removeFavorite: (courseId, documentId, kpId) =>
    request.delete(`/api/v1/favorites/${encodeURIComponent(kpId)}`, {
      params: { course_id: courseId, document_id: documentId },
    }),

  // ---- 题库管理（教师端；Scope B：单选/多选/判断自动判分 + 填空/解答由教师批改） ----
  /** 题库列表（含答案/解析/作答正确率/收藏数） */
  listQuestions: (params = {}) => request.get('/api/v1/questions', { params }),
  /** 题库总览：题量/题型分布/作答正确率/收藏数 */
  getQuestionStats: (courseId) =>
    request.get('/api/v1/questions/stats', { params: { course_id: courseId } }),
  /** 知识点题目覆盖率：无题知识点清单 + 悬空 kp_id（教师补题指引） */
  getQuestionCoverage: (courseId, documentId) =>
    request.get('/api/v1/questions/coverage', {
      params: { course_id: courseId, document_id: documentId || undefined },
    }),
  /** 题目收藏情况（哪些学生收藏了哪道题） */
  getQuestionFavorites: (courseId, questionId) =>
    request.get('/api/v1/questions/favorites', {
      params: { course_id: courseId, question_id: questionId || undefined },
    }),
  getQuestionDetail: (questionId) => request.get(`/api/v1/questions/${questionId}`),
  createQuestion: (data) => request.post('/api/v1/questions', data),
  /** 修改题目（未传字段沿用原值） */
  updateQuestion: (questionId, data) => request.put(`/api/v1/questions/${questionId}`, data),
  /** 删除题目（已被学生作答过则后端自动改为停用） */
  deleteQuestion: (questionId) => request.delete(`/api/v1/questions/${questionId}`),
  /** 启用/停用题目 */
  setQuestionActive: (questionId, isActive) =>
    request.patch(`/api/v1/questions/${questionId}/active`, { is_active: isActive }),
  /** 单题知识点候选（Scope C：字面匹配 + 向量召回 + 图谱扩展，融合打分） */
  getKpCandidates: (questionId, topK = 5) =>
    request.post(`/api/v1/questions/${questionId}/kp-candidates`, { top_k: topK }),
  /** 批量知识点自动标注（默认只出建议；apply=true 才写入达阈值的题） */
  autoLabelQuestions: (payload = {}) => request.post('/api/v1/questions/auto-label', payload),
  /** 从课程文档解析题目候选（预览，不写库）—— 试题文档导入 P2 */
  previewImportQuestions: (payload = {}) =>
    request.post('/api/v1/questions/import/preview', payload),
  /** 提交导入：入库为暂存题（activate=true 才直接启用） */
  commitImportQuestions: (payload = {}) =>
    request.post('/api/v1/questions/import/commit', payload),
  /** 导入批次明细 */
  getImportBatch: (batchId) => request.get(`/api/v1/questions/import/batch/${batchId}`),

  // ---- 主观题批改（教师端；Scope B：填空/解答提交只落库，教师批改后才给分） ----
  /** 待批改列表（只返回待批改的主观题作答，按提交先后排序） */
  getPendingGrades: (params = {}) => request.get('/api/v1/grading/pending', { params }),
  /** 批改进度汇总（待批改 / 已批改 / 主观题作答量 / 平均分） */
  getGradingSummary: (courseId) =>
    request.get('/api/v1/grading/summary', { params: { course_id: courseId } }),
  /** 单题批改（score 0~100，>=60 记为答对；允许重批覆盖） */
  gradeAnswer: (recordId, score, comment) =>
    request.post(`/api/v1/grading/${recordId}`, { score, comment }),
  /** 批量批改（同一分数与评语） */
  gradeAnswersBatch: (recordIds, score, comment) =>
    request.post('/api/v1/grading/batch', { record_ids: recordIds, score, comment }),

  // ---- 做题练习（学生端；出题接口不含答案，提交后才下发） ----
  /** 出题（course_id 必填；可指定文档/知识点/题型/数量） */
  getPracticeQuestions: (params = {}) => request.get('/api/v1/practice/questions', { params }),
  /** 智能推荐出题（按学情选卷；返回体含 reason/bucket_label，同样不含答案） */
  recommendQuestions: (payload = {}) => request.post('/api/v1/practice/recommend', payload),
  /** 提交作答：服务端判分并返回正确答案与解析 */
  submitAnswer: (questionId, userAnswer) =>
    request.post('/api/v1/practice/submit', { question_id: questionId, user_answer: userAnswer }),
  /** 我的答题记录（onlyWrong=true 仅错误记录） */
  getAnswerRecords: (params = {}) => request.get('/api/v1/practice/records', { params }),
  /** 错题本（每题最近一次错误 + 关联知识点） */
  getWrongBook: (params = {}) => request.get('/api/v1/practice/wrong-book', { params }),
  /** 我的练习统计 */
  getPracticeStats: (params = {}) => request.get('/api/v1/practice/stats', { params }),
  /** 我的题目收藏 */
  getQuestionFavList: (courseId) =>
    request.get('/api/v1/practice/favorites', { params: { course_id: courseId } }),
  favoriteQuestion: (courseId, questionId) =>
    request.post('/api/v1/practice/favorites', { course_id: courseId, question_id: questionId }),
  unfavoriteQuestion: (courseId, questionId) =>
    request.delete(`/api/v1/practice/favorites/${questionId}`, { params: { course_id: courseId } }),

  // ---- 管理员端（对齐后端 /api/v1/admin/*；后端每个端点都挂 require_admin） ----
  /** 工作台总览：平台计数 / 角色分布 / 治理概览 / 最近活动 */
  adminDashboard: (params = {}) => request.get('/api/v1/admin/dashboard', { params }),
  /** 筛选下拉可选项（教师列表 + 全平台课程） */
  adminOptions: () => request.get('/api/v1/admin/options'),

  /** 用户列表（keyword/role/status + 分页 + 排序） */
  adminListUsers: (params = {}) => request.get('/api/v1/admin/users', { params }),
  /** 用户详情（资料 + 统计 + 所在课程 + 相关操作记录；不含密码哈希） */
  adminGetUser: (userId) => request.get(`/api/v1/admin/users/${userId}`),
  /** 编辑用户资料（不含角色与状态） */
  adminUpdateUser: (userId, data) => request.put(`/api/v1/admin/users/${userId}`, data),
  adminEnableUser: (userId) => request.post(`/api/v1/admin/users/${userId}/enable`),
  adminDisableUser: (userId) => request.post(`/api/v1/admin/users/${userId}/disable`),
  adminSetUserRole: (userId, role) =>
    request.put(`/api/v1/admin/users/${userId}/role`, { role }),
  /** 重置密码：newPassword 省略时由后端生成随机初始密码，仅在响应里返回一次 */
  adminResetPassword: (userId, newPassword = null) =>
    request.post(`/api/v1/admin/users/${userId}/reset-password`,
      { new_password: newPassword }),
  adminDeleteUser: (userId) => request.delete(`/api/v1/admin/users/${userId}`),

  /** 全平台课程列表 */
  adminListCourses: (params = {}) => request.get('/api/v1/admin/courses', { params }),
  /** 课程详情（只读）：概览 + 成员 + 文档 + 图谱统计 */
  adminGetCourse: (courseId) => request.get(`/api/v1/admin/courses/${courseId}`),
  /** 课程治理：action = hide/restore/archive/unarchive/close/reopen */
  adminCourseGovernance: (courseId, action, note = null) =>
    request.post(`/api/v1/admin/courses/${courseId}/governance`, { action, note }),
  /** 转移课程负责人 */
  adminTransferCourse: (courseId, teacherId) =>
    request.post(`/api/v1/admin/courses/${courseId}/transfer`, { teacher_id: teacherId }),
  /** 删除课程（必须显式 confirm=true） */
  adminDeleteCourse: (courseId) =>
    request.delete(`/api/v1/admin/courses/${courseId}`, { params: { confirm: true } }),
  /** 课程治理工作台：需要管理员介入的课程分组 */
  adminGovernance: () => request.get('/api/v1/admin/governance'),

  /** 全平台文档资源列表 */
  adminListDocuments: (params = {}) => request.get('/api/v1/admin/resources/documents', { params }),
  /** 删除文档资源（与教师端同一套完整性清理） */
  adminDeleteDocument: (docId) => request.delete(`/api/v1/admin/resources/documents/${docId}`),
  /** 知识抽取任务监控 */
  adminExtractionTasks: (params = {}) =>
    request.get('/api/v1/admin/resources/extraction-tasks', { params }),

  /** 系统监控：组件真实状态 + 运行指标 + 库表行数 */
  adminSystem: () => request.get('/api/v1/admin/system'),
  /** 审计日志查询 */
  adminAuditLogs: (params = {}) => request.get('/api/v1/admin/audit-logs', { params }),
}

/**
 * 头像直链（用于 <img src>，不走 axios）。
 * 后端该端点刻意不鉴权：<img> 无法携带 Authorization 头，且头像本身是要展示在成员列表里的公开信息。
 * 未设置头像时后端返回 404，调用方应以 @error 回退为姓名首字母色块。
 */
export const avatarUrl = (userId) =>
  userId ? `/api/v1/profile/avatar/${userId}` : ''
