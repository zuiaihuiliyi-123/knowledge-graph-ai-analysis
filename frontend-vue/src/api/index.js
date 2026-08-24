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

  // ---- 课程 ----
  /** 上传课程文档（LLM 抽取耗时较长，超时放宽到 10 分钟） */
  uploadCourse: (formData, courseName) =>
    request.post('/api/courses/upload', formData, {
      params: { course_name: courseName || undefined },
      timeout: 600000,
    }),
  /** 旧版图谱接口（ECharts 格式，节点以 name 为 id） */
  getCourseGraphLegacy: (courseId) => request.get(`/api/courses/${courseId}/graph`),

  // ---- 知识图谱（新版，G6 格式） ----
  /** 新版图谱接口：{ nodes: [{id,label,type,description,properties}], edges: [...] } */
  getGraphV1: (courseId, params = {}) =>
    request.get(`/api/v1/graph/${courseId}`, { params }),
  /** 教师编辑：更新节点属性（现有接口，后端标签为 KnowledgeNode 需修复） */
  updateNode: (courseId, name, properties) =>
    request.put(`/api/courses/${courseId}/graph/node`, null, {
      params: { name, properties: JSON.stringify(properties) },
    }),
  /** 教师编辑：删除节点（现有接口，后端标签为 KnowledgeNode 需修复） */
  deleteNode: (courseId, name) =>
    request.delete(`/api/courses/${courseId}/graph/node`, { params: { name } }),
  /** 教师编辑：新增节点（后端待补） */
  createNode: (courseId, body) =>
    request.post(`/api/v1/graph/${courseId}/node`, body),
  /** 教师编辑：新增关系（后端待补） */
  createEdge: (courseId, body) =>
    request.post(`/api/v1/graph/${courseId}/edge`, body),
  /** 教师编辑：删除关系（后端待补） */
  deleteEdge: (courseId, body) =>
    request.delete(`/api/v1/graph/${courseId}/edge`, { data: body }),

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
