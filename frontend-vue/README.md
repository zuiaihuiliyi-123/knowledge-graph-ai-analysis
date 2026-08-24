# 课程知识图谱系统 - Vue3 前端

基于 Vue 3 + Vite + Element Plus + AntV G6 的前端（对齐《A10 赛题项目开发计划书》第二阶段）。

## 启动

```bash
cd frontend-vue
npm install
npm run dev        # 开发服务器 http://localhost:5173
npm run build      # 生产构建（输出 dist/）
```

开发模式下 `/api` 与 `/health` 已代理到 `http://localhost:8000`（见 `vite.config.js`），无需额外配置 CORS。
需先启动后端：`cd backend && python -m uvicorn app.main:app --reload`

## 功能一览

**教师端**（`/teacher`）
- 📤 文档上传：上传 PDF/TXT/DOCX/MD → 调用后端 LLM 提取 → 展示实体/关系统计与原始 JSON
- 🔍 图谱预览：G6 力导向图渲染，缩放/拖拽/点选查看详情，支持知识点搜索高亮
- ✏️ 编辑图谱：点击节点编辑名称/类别/描述、删除节点；新增知识点、新增关系（下拉选择源/目标 + 关系类型）、点击边删除关系

**学生端**（`/student`）
- 🗺️ 图谱浏览：G6 图谱浏览 + 搜索高亮 + 节点详情（含前置知识查询）
- 💬 智能问答：聊天式 RAG 问答，展示参考来源
- 🎯 学习路径推荐：已掌握知识点 → 推荐下一步；目标知识点 → 学习路径；前置知识查询

## 与后端的接口约定

接口封装见 `src/api/index.js`。后端响应存在两种风格（统一包装 `{code,data}` 与裸返回），`src/api/request.js` 拦截器自动兼容解包。

### 后端待补接口（教师端编辑功能依赖）

前端已按如下 REST 约定调用，后端实现后即可直接工作：

| 方法 | 路径 | 请求体 | 说明 |
|------|------|--------|------|
| POST | `/api/v1/graph/{course_id}/node` | `{name, category, description}` | 新增知识点（category 为中文：概念/定理/公式/方法） |
| POST | `/api/v1/graph/{course_id}/edge` | `{source, target, type}` | 新增关系（type 为英文：PRECEDES/CONTAINS/RELATED_TO/APPLIES_TO） |
| DELETE | `/api/v1/graph/{course_id}/edge` | `{source, target, type}` | 删除关系 |

### 后端已知问题（前端已做降级提示）

1. `PUT/DELETE /api/courses/{course_id}/graph/node` 查询的节点标签是 `KnowledgeNode`，而新数据标签是 `KnowledgePoint`，导致教师端"保存修改/删除节点"对现有数据无效
2. `/api/learning-path/*` 三个接口仍使用旧图模型（`KnowledgeNode` + 中文关系类型 `前置知识`），与当前 `KnowledgePoint` + `PRECEDES` 数据不匹配，学习路径推荐对新数据无结果
3. 暂无"课程列表"接口，前端用 localStorage 记录上传过的课程（`src/stores/app.js`），补齐 `GET /api/courses` 后可替换

## 目录结构

```
src/
├── api/            # 后端接口封装（request.js 统一响应解包 + index.js 接口清单）
├── components/     # GraphCanvas（G6 图谱）/ CourseSelector / NodeDetailDrawer
├── stores/         # Pinia：角色、后端状态、课程注册表
├── utils/          # 图谱样式常量（节点类别/关系类型映射，与后端对齐）
├── views/          # TeacherView（教师端三页签）/ StudentView（学生端三页签）
├── router/         # 路由（tab 子功能通过 query 参数直达）
├── App.vue         # 整体布局：侧边栏 + 角色切换 + 后端健康指示
└── main.js         # 入口（Element Plus 中文 locale）
```
