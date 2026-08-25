import request from './request'

/**
 * 后端 API 封装
 * 注意：教师端"新增节点/新增关系/删除关系"三个接口后端尚未实现，
 * 前端按约定好的 REST 风格调用 /api/v1/graph/...，后端补齐后即可直接工作
 * （见根目录 README 与开发报告的"后端待补接口"章节）。
 */
export const api = {
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

  // ---- 教师编辑（后端图谱节点/关系编辑接口 6.3.3/6.3.4 尚未实现，前端暂屏蔽） ----
  updateNode: () => Promise.reject(new Error('节点编辑功能开发中')),
  deleteNode: () => Promise.reject(new Error('节点删除功能开发中')),
  createNode: () => Promise.reject(new Error('新增知识点功能开发中')),
  createEdge: () => Promise.reject(new Error('新增关系功能开发中')),
  deleteEdge: () => Promise.reject(new Error('删除关系功能开发中')),

  // ---- 图谱统计 ----
  getStats: () => request.get('/api/kg/stats'),
  getAllGraphs: () => request.get('/api/kg/all'),

  // ---- 智能问答 ----
  ask: (question, courseId) =>
    request.post('/api/qa/ask', { question, course_id: courseId || null }),

  // ---- 学习路径推荐 ----
  recommendNext: (mastered, courseId) =>
    request.post('/api/learning-path/recommend', {
      mastered,
      course_id: courseId || null,
    }),
  pathToTarget: (target, courseId) =>
    request.post('/api/learning-path/path-to-target', {
      target,
      course_id: courseId || null,
    }),
  getPrerequisites: (name, courseId) =>
    request.get(`/api/learning-path/prerequisites/${encodeURIComponent(name)}`, {
      params: { course_id: courseId || undefined },
    }),
}
