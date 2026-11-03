# frontend-vue —— Vue 3 前端

课程知识图谱系统的唯一前端（Vue 3.5 + Vite 7 + Pinia + Vue Router + Element Plus + AntV G6）。

> 项目整体介绍、后端启动、环境变量与评测方式见根目录 [README.md](../README.md)；
> 改代码前必读的架构约束与历史坑见 [CLAUDE.md](../CLAUDE.md)。本页只写本目录的事。

## 启动

```bash
cd frontend-vue
npm install
npm run dev        # http://localhost:5173
npm run build      # 生产构建，输出 dist/
```

开发服务器已把 `/api` 与 `/health` 代理到 `http://localhost:8000`（见 `vite.config.js`），无需额外配置跨域；需先启动后端。

## 源码组织

| 目录 | 职责 |
|---|---|
| `src/api/` | 接口封装；`request.js` 统一解包响应，`qaStream.js` / `sse.js` 处理流式问答 |
| `src/components/` | 按业务域分：`teacher/` `student/` `reader/` `course/` `admin/` |
| `src/composables/` | 组件配套逻辑（图谱编辑、题库、批改、监测、学习数据…） |
| `src/views/` | 页面级组件：教师端 / 学生端 / 课程中心 / 管理员端 / 登录注册 / 文档阅读 |
| `src/router/` | 路由与角色守卫 |
| `src/stores/` | Pinia 状态 |
| `src/utils/` | 纯函数工具（图谱样式、格式化、题型与判分、阅读进度…） |

## 校验

```bash
npm run lint          # ESLint（CI 只把 error 级当失败，no-unused-vars 暂为 warning）
npm run build         # 生产构建冒烟
npx playwright test   # 端到端测试，14 个 spec（需后端与前端均已启动）
```

CI 不跑 Playwright：它依赖真实后端、Neo4j 与 LLM Key，且会写入真实数据。

