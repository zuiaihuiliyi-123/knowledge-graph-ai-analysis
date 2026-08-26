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
  /** 上传课程文档（对齐后端 POST /api/v1/documents/upload；LLM 抽取耗时较长，超时放宽到 10 分钟） */
  uploadCourse: (formData, courseName) => {
    if (courseName) formData.append('course_name', courseName)
    return request.post('/api/v1/documents/upload', formData, { timeout: 600000 })
  },

  // ---- 课程管理（对齐后端 /api/v1/courses CRUD） ----
  listCourses: (params = {}) => request.get('/api/v1/courses', { params }),
  getCourse: (courseId) => request.get(`/api/v1/courses/${courseId}`),
  createCourse: (data) => request.post('/api/v1/courses', data),
  updateCourse: (courseId, data) => request.put(`/api/v1/courses/${courseId}`, data),
  deleteCourse: (courseId, confirm = true) =>
    request.delete(`/api/v1/courses/${courseId}`, { params: { confirm } }),

  // ---- 知识图谱（G6 格式） ----
  /** 图谱接口：{ nodes: [{id,label,type,description,properties}], edges: [...] } */
  getGraphV1: (courseId, params = {}) =>
    request.get(`/api/v1/graph/${courseId}`, { params }),

  // ---- 教师编辑（对齐后端 /api/v1/graph 编辑端点 6.3.3/6.3.4） ----
  createNode: (courseId, data) => request.post(`/api/v1/graph/${courseId}/nodes`, data),
  updateNode: (courseId, nodeId, data) =>
    request.put(`/api/v1/graph/${courseId}/nodes/${nodeId}`, data),
  deleteNode: (courseId, nodeId) => request.delete(`/api/v1/graph/${courseId}/nodes/${nodeId}`),
  createEdge: (courseId, data) => request.post(`/api/v1/graph/${courseId}/edges`, data),
  deleteEdge: (courseId, edgeId) => request.delete(`/api/v1/graph/${courseId}/edges/${edgeId}`),

  // ---- 图谱统计 ----
  getStats: () => request.get('/api/kg/stats'),
  getAllGraphs: () => request.get('/api/kg/all'),

  // ---- 智能问答 ----
  ask: (question, courseId) =>
    request.post('/api/v1/qa/ask', { question, course_id: courseId || null }),

  // ---- 学习路径推荐 ----
  recommendNext: (mastered, courseId) =>
    request.post('/api/v1/learning-path/recommend', {
      mastered,
      course_id: courseId || null,
    }),
  pathToTarget: (target, courseId) =>
    request.post('/api/v1/learning-path/path-to-target', {
      target,
      course_id: courseId || null,
    }),
  getPrerequisites: (name, courseId) =>
    request.get(`/api/v1/learning-path/prerequisites/${encodeURIComponent(name)}`, {
      params: { course_id: courseId || undefined },
    }),
}
