# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目背景

"服务外包大赛"（A10 赛题）参赛项目：基于 AIGC 的课程知识图谱智能构建与学习系统。核心链路：课程文档（PDF/TXT/DOCX/MD）→ LLM 抽取知识点与关系 → Neo4j 知识图谱 → 图谱可视化 + RAG 问答 + 学习路径推荐。8 周赛程，交付物除代码外还包括 Prompt 工程记录、知识抽取测试报告、问答测试集（20+ 题）、演示视频与 PPT。

- 更新版开发计划见会话中的《A10 赛题项目开发计划书》；仓库内 [docs/项目说明.md](docs/项目说明.md) 是较旧版本，以计划书为准
- README.md 与代码不同步：声称的 `backend/app/models/user.py`、`tests/` 等不存在；实际存在的 git 分析模块（见下）README 未提及
- 代码注释、文档、commit message 全部使用中文，新代码保持一致

## 常用命令

```bash
# 安装后端依赖（唯一依赖清单在 backend/requirements.txt）
cd backend && pip install -r requirements.txt

# 配置环境变量（模板 backend/.env.example：LLM_API_KEY / LLM_API_BASE / LLM_MODEL / NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD / UPLOAD_DIR）
cp backend/.env.example backend/.env   # 然后编辑填入真实值

# 启动后端（在 backend/ 目录下）
python -m uvicorn app.main:app --reload

# 启动前端（Streamlit 原型；从仓库根目录）
streamlit run frontend/app.py

# 健康检查
curl http://localhost:8000/health
```

- **没有测试框架、没有 tests/ 目录、没有 lint/格式化配置**——计划书第三阶段要求补测试集，但目前跑任何验证都靠手写临时脚本
- 无 CI 配置；无 Docker 配置
- 计划书规划前端迁移到 Vue3 + Vite + AntV G6，但仓库中尚无任何 Vue 代码，当前唯一前端是 Streamlit 原型

## 架构概览

**后端**（FastAPI，分层：`api` → `services` → `core`）：
- 唯一数据存储是 Neo4j，通过 [backend/app/core/database.py](backend/app/core/database.py) 的全局单例 `db` 访问；没有 SQL 库、没有 ORM。Neo4j 驱动的连接在模块导入时创建
- LLM 统一走 `openai` SDK 指向 DeepSeek 兼容端点（`LLM_API_BASE`，模型 `deepseek-chat`），三个消费方：
  1. `KnowledgeExtractor` — 长文本分块（中文 token 估算）后逐块抽取实体/关系 JSON，按名称去重合并
  2. `QAService` — 简化 RAG（见"已知问题"）
  3. `GitCommitAnalyzer` — 孤岛模块的一部分（见下）
- 数据模型：节点标签 `KnowledgeNode`，关系类型用**中文**（`前置知识`、`相关概念`、`包含`、`应用`）直接作为 Neo4j 关系类型；学习路径推荐（`PathRecommender`）依赖 `前置知识` 边做 BFS/前置检查
- 主流程：`api/courses.py` 的 upload 端点串起 解析 → 抽取 → 入图；`api/` 其余模块均为薄封装，业务逻辑全在 `services/`
- API 路由前缀是 `/api/...`（无版本号）；计划书写的规范是 `/api/v1/...`，两者尚未统一

**前端**：`frontend/app.py`（Streamlit）硬编码 `API_BASE = "http://localhost:8000"`，教师/学生双端界面，图谱可视化和学习路径按钮均为占位。

**孤岛模块**：`services/git_analyzer.py` + `services/git_kg_manager.py`（约占仓库代码量一半）实现 Git 仓库历史分析（git log 解析、LLM commit 语义分析、Neo4j 建模、版本恢复影响评估），但**没有任何 API 路由挂载、前端无界面、计划书未提及**。改这部分前先确认其去留。

## 已知问题与陷阱

改代码时注意以下现状，这些都是实测过代码得出的结论：

1. **同步阻塞代码跑在 async 端点里**：`openai` 同步客户端、Neo4j 同步驱动、文件读写全部直接在 `async def` 中调用，会阻塞事件循环。新增代码若做网络/IO，要么沿用现状保持一致，要么整体迁移到 `AsyncOpenAI`/`run_in_executor`（后者是更正确的方向）
2. **Cypher 注入风险**：`kg_manager.py` 用 f-string 把 LLM 输出的关系类型拼进 Cypher。同时 Neo4j **不支持参数化关系类型**（`[r:$rel_type]` 是语法错误），`delete_relationship` 传 `rel_type` 时必然失败
3. **节点 MERGE 只按 `name`**：不同课程的同名知识点会互相覆盖 `course_id` 属性，跨课程数据会串
4. **关系创建失败被静默吞掉**（`except: pass`），中文关系类型若不被 Neo4j 接受，整批关系会无声消失
5. **上传接口无安全校验**：`courses.py` 直接用 `file.filename` 拼保存路径（路径穿越风险）；`config.py` 的 `MAX_UPLOAD_SIZE` 定义了但从未执行
6. **RAG 检索是假的**：`qa_service.py` 用 `question[:20]` 做 `CONTAINS` 关键词匹配，匹配不到就随机补节点。requirements 里的 chromadb/langchain 尚未使用。计划书要求向量检索 + 引用来源，需要重写
7. **DeepSeek 没有 Embedding API**：RAG 向量化必须换供应商（计划书未覆盖此风险；推荐 SiliconFlow 的 `BAAI/bge-m3`，OpenAI 兼容，可复用现有客户端）
8. `api/auth.py` 是纯占位符（TODO JWT），任何"认证"相关功能都尚未实现
9. 根目录 `hello.py` 是遗留测试文件，无业务价值
