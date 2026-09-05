import request from './request'

/**
 * 后端 API 封装
 * 教师端图谱编辑（新增/更新/删除节点、新增/删除关系）对齐后端 /api/v1/graph 编辑端点。
 */
export const api = {
  // ---- 用户认证（对齐后端 /api/auth，注意路由无 /v1 版本号） ----
  login: (data) => request.post('/api/auth/login', data),
  register: (data) => request.post('/api/auth/register', data),

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
  listCourses: (params = {}) => request.get('/api/v1/courses', { params }),
  getCourse: (courseId) => request.get(`/api/v1/courses/${courseId}`),
  createCourse: (data) => request.post('/api/v1/courses', data),
  updateCourse: (courseId, data) => request.put(`/api/v1/courses/${courseId}`, data),
  deleteCourse: (courseId, confirm = true) =>
    request.delete(`/api/v1/courses/${courseId}`, { params: { confirm } }),

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
}
