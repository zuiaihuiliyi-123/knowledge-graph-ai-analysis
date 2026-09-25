# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目背景

"服务外包大赛"（A10 赛题）参赛项目：基于 AIGC 的课程知识图谱智能构建与学习系统。核心链路：课程文档（PDF/TXT/DOCX/MD）→ LLM 抽取知识点与关系 → Neo4j 知识图谱 → 图谱可视化 + RAG 问答 + 学习路径推荐。8 周赛程，交付物除代码外还包括 Prompt 工程记录、知识抽取测试报告、问答测试集（20+ 题）、演示视频与 PPT。

- 更新版开发计划见会话中的《A10 赛题项目开发计划书》；仓库内 [docs/项目说明.md](docs/项目说明.md) 是较旧版本，以计划书为准
- `docs/` 下有 17 份专题文档（数据库设计、题库功能、协作与图谱共享、知识点自动标注设计、论文算法复现实验、评测运行简明教程、问题修复记录、交付说明…）。**改对应模块前先查**——多数设计决策与实测数据都记在里面
- README.md 已明显过时：仍以 Streamlit 描述前端（`frontend/app.py` **已不存在**），声称的 `backend/app/models/user.py` 不存在，根目录 `tests/` 是个空目录；而占仓库近半规模的 `frontend-vue/` 与 git 分析模块 README 完全未提
- 代码注释、文档、commit message 全部使用中文，新代码保持一致

## 常用命令

```bash
# 后端依赖（唯一依赖清单在 backend/requirements.txt）
cd backend && pip install -r requirements.txt

# 环境变量（模板 backend/.env.example：LLM_* / EMBEDDING_* / NEO4J_* / SQLITE_DB_PATH / UPLOAD_DIR / SECRET_KEY）
cp backend/.env.example backend/.env   # 然后编辑填入真实值

# 启动后端（在 backend/ 目录下）
python -m uvicorn app.main:app --reload

# 启动前端（在 frontend-vue/ 目录下；Vite dev server :5173，代理 /api 与 /health 到 :8000）
npm install && npm run dev

# 前端 e2e（在 frontend-vue/ 目录下，需后端与前端均已启动）
npx playwright test
npx playwright test tests/profile.spec.ts --project=chromium

# 健康检查
curl http://localhost:8000/health
```

**验证手段**——没有 pytest / jest，但有成体系的替代：

| 类型 | 位置 | 说明 |
|---|---|---|
| 端到端验证脚本 | `backend/test_*.py` | 自带断言、输出 `通过 N/M`，直接 `python test_xxx.py` 运行。多数会**真写 app.db 与 Neo4j 并自清** |
| 评测脚本 | `backend/eval_*.py` | 知识抽取准确率 / ANN 对比 / 布隆过滤器等，只读，结果落 `backend/eval_data/*.json` |
| 前端 e2e | `frontend-vue/tests/*.spec.ts` | Playwright，9 个 spec |

- ⚠️ **验证脚本直连线上库**：`app.db` 与 Neo4j 都是真实数据，脚本会真实建账号、建课、建节点。跑完务必核对是否复原（对照 `t_course` / `t_document` / `t_question` / `t_kp_embedding` 计数）
- **没有 lint / 格式化配置**，也没有 CI / Docker——"变量名写错、只在运行期才炸"这类问题不会被静态拦住
- `requirements.txt` 对版本**逐个精确锁定**，这不是洁癖：`starlette` 不锁会漂移到 1.x 导致启动崩溃（见文件内注释）。新增依赖请沿用锁定风格

## 架构概览

**数据存储是两套并存**（不是"唯一 Neo4j"）：

- **SQLite** — `backend/data/app.db`，经 [backend/app/core/sql_database.py](backend/app/core/sql_database.py) 的全局单例 `sql_db` 访问。无 ORM、手写 SQL。存用户、课程、课程成员、文档元数据、题库、作答、批改、邀请、个人资料，**以及全部向量**（`t_kp_embedding` / `t_question_embedding`）
- **Neo4j** — 经 [backend/app/core/database.py](backend/app/core/database.py) 的全局单例 `db` 访问，驱动连接在模块导入时创建。存知识图谱本体
- 两者**无事务保证**，级联删除必须两侧显式清理（见"已知问题"第 7 条）

**后端**（FastAPI，分层 `api` → `services` → `core`）：

- **两家 LLM 供应商**，都走 `openai` SDK 的 OpenAI 兼容接口：
  - DeepSeek（`LLM_API_BASE`，`deepseek-chat`）—— 抽取与生成。消费方 `KnowledgeExtractor`（分块抽取实体/关系 JSON）、`QAService`（问答生成）、`GitCommitAnalyzer`（孤岛模块）
  - SiliconFlow（`EMBEDDING_API_BASE`，`BAAI/bge-m3`）—— 向量化。**DeepSeek 没有 embedding 接口，这步无法用 DeepSeek**
- 图谱模型：节点标签 **`KnowledgePoint`**；MERGE 键 **`(course_id, document_id, name)`**（同课程不同文档的同名知识点互相独立）；`kp_id` 按 (course_id, uuid) 全局唯一，**不是** MERGE key
- 关系类型是**英文 Cypher 类型**（`PRECEDES` / `CONTAINS` / `RELATED_TO` / `APPLIES_TO`），中文名存在 `relation_type` 属性里（映射见 [database.py:12](backend/app/core/database.py#L12)）。`PathRecommender` 依赖 `PRECEDES` 边做 BFS
- 鉴权已实现（JWT + `get_current_user`，口令哈希在 [core/security.py](backend/app/core/security.py)）；权限校验走 [core/permissions.py](backend/app/core/permissions.py)，分「课程成员 / 协作教师 / 可访问课程」几档，模块越权检查优先复用它
- 主流程：`api/courses.py` 的 upload 端点串起 解析 → 抽取 → 入图；`api/` 其余模块多为薄封装，业务逻辑在 `services/`
- 路由前缀大部分已统一为 `/api/v1/...`，残留两个未迁移：`/api/auth`、`/api/kg`

**RAG 问答**：手写链路，**未使用任何 RAG 框架**（`langchain` / `chromadb` 在 requirements 里但零 import）。链路细节见下节。

**前端**：`frontend-vue/`（Vue3 + Vite + Pinia + Element Plus），约 2.3 万行，是当前唯一前端。Streamlit 原型已从仓库移除。

**孤岛模块**：`services/git_analyzer.py` + `services/git_kg_manager.py`（共 1016 行，约占后端 12.6k 行的 8%）实现 Git 仓库历史分析（git log 解析、LLM commit 语义分析、Neo4j 建模、版本恢复影响评估），但**没有任何 API 路由挂载、前端无界面、计划书未提及**。改这部分前先确认其去留。

## RAG 检索链路（改问答必读）

`QAService.ask_with_sources()`（[qa_service.py](backend/app/services/qa_service.py)）是唯一入口，返回 `{answer, sources}`——`sources` 就是本次喂给 LLM 的上下文，两者必然一致。**不要改成"先 ask() 再单独检索一遍取 sources"**，那会让同一次提问把整条链路跑两遍（含两次外部 embedding 调用）。

```
问题 → EmbeddingClient.embed([问题])            ← 外部 API 调用（SiliconFlow）
     → KnowledgeEmbedder.ensure_index(cid, did)  ← 新鲜度检查，并返回该文档全部向量
     → 纯 Python 余弦排序 top_k
     → 按 kp_id 回查 Neo4j 取节点元数据
     → 拼进 QA_SYSTEM_PROMPT → DeepSeek 生成
```

要点：

1. **`ensure_index` 是懒构建 + 懒重建**：每次问答都用 Neo4j 的当前 `kp_id` 集合与 SQLite 已存集合比对，不一致才重建。这是刻意的——"有向量就跳过"是错的，重新抽取会整体更换 `kp_id`，旧向量会指向不存在的节点，而表里"非空"会让索引永不重建
2. **新鲜度比对只读 `kp_id` 列**（`get_embedding_kp_ids`），不要退回 `get_embeddings_by_document`——后者会反序列化全部向量文本，实测占该步耗时 70%
3. **向量以 JSON 文本存 SQLite**，余弦是纯 Python 手写（刻意不引 numpy）。规模拐点约 **1000 条/文档**（实测 ~0.44ms/条）；当前最大文档 69 条、单次检索 ~20ms，远未到需要 ANN 的程度
4. **索引失效粒度是整个 `(course_id, document_id)`**：教师改一个知识点描述 → 该文档全部向量删除 → 下次问答全量重算（真实外部 API 调用）。`delete_embeddings_by_document` 的 7 个调用点即失效收口
5. **未配 `EMBEDDING_API_KEY` 或调用失败时会静默退回关键词检索**（Cypher `CONTAINS`）。降级本身是对的，但断网演示时会表现为"回答变差却不报错"——排查时先看 `qa_service` 的 warning 日志

## 已知问题与陷阱

改代码时注意以下现状，这些都是实测过代码得出的结论：

1. **同步阻塞代码跑在 async 端点里**：`openai` 同步客户端、Neo4j 同步驱动、文件读写全部直接在 `async def` 中调用，会阻塞事件循环。新增网络/IO 代码要么沿用现状，要么整体迁移到 `AsyncOpenAI` / `run_in_executor`（后者更正确）
2. **静默吞异常是本项目的顽固模式**：`app/services/` 下有 15 处裸 `except ...: pass`。排查"功能静默失效"（如向量检索整条不生效却伪装成"知识库为空"、降级无日志）时优先怀疑这里
3. **`t_kp_embedding` 主键是 `(course_id, kp_id)`，`document_id` 只是普通列**；作用域索引 `idx_kp_emb_scope` 建在 `_migrate` 里而**不是** `_SCHEMA_SQL`——旧库的 `document_id` 列由 `_migrate` 的 ALTER 补齐，顺序反了会因列不存在而报错。改 schema 时注意这个顺序约束
4. **`t_question_embedding` 是题目向量的懒构建缓存，常年为空属正常**：只有教师点「自动标注」才写；批量路径默认 `only_missing=True` 会跳过已挂知识点的题。**空表 ≠ 功能失效**——知识点标注的向量召回层查的是 `t_kp_embedding`，题目向量是现场 embed 的
5. **`langchain` / `langchain-community` / `chromadb` 是从未 import 的死依赖**。判定"要不要引入 RAG 框架"时的已知约束：当前规模（最大文档 69 条向量）用不上 ANN，且 `docs/论文算法复现实验.md` 里对比实验的价值恰恰建立在"检索是自己实现的"之上
6. **关系类型的 f-string 插值有白名单兜底**：`create_relationship` 把 `rel_type` 拼进 Cypher（Neo4j 不支持参数化关系类型），但先经 `VALID_RELATION_TYPES` 校验、非法回退 `RELATED_TO`。改这段时**不要移除白名单**。删除关系已改走 `elementId`，不再传 `rel_type`
7. **跨库级联清理容易漏**：删除课程/文档要同时清 Neo4j 图谱与 SQLite 的多张子表（向量、学习记录、收藏、作答、题目向量…）。`delete_questions_by_document` 是范例——子表按 `question_id` 子查询清理，且**必须排在 `DELETE FROM t_question` 之前**。新增子表时记得挂进对应级联点
