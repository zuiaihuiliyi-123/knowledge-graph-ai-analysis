# 方案核对报告 A —— 第4章 / 第10.4-10.5 / 第14章 / 第6.1 图模型

核对人：arch-verify（评审视角核对员）　核对日期：2026-11-09
待核对文档：`C:\Users\20472\Desktop\宁波大学A10五影会谈\作品材料\S6 项目详细方案（解决方案）\项目详细方案.pdf`（177 页）
全文提取：`.dsh_tmp/pdf_text.txt`（页内表格在 PDF 中是图片，pdfplumber 的 `extract_tables()` 只抓到页脚，故表 4.1/4.2/4.3/4.7 是用 `page.to_image()` 渲染成图片后逐页人工读出的，渲染件在 `.dsh_tmp/render/p35|p37|p40|p41.png`；正文页码与 PDF 页码一致，引用时写「PDF 第 N 页」，括号内是 `pdf_text.txt` 的行号）

---

## 0. 结论摘要

- 技术选型（4.2）、双存储分工（4.3）、图模型（4.4.2/6.1 的标签/关系/MERGE 键/索引）、统一响应与防泄题（4.5(2)(4)）**与代码基本一致**，可以照讲。
- **数量类陈述全部错了**：22 张表→实际 23 张；16 个 API 模块→实际 19 个；14.1 又写成「16 张表」自相矛盾；10.7 的 Playwright「9 个 spec」→实际 14 个。这三处是评委最容易当场打开代码数的地方。
- **10.5 的 41 秒是旧数字**：仓库最新实测 `backend/eval_data/perf_timing.json` 是 6.7–10.8 秒，且分块数是 1/2/2/4、temperature 是 0.15（方案写 3 个分块、temperature=0）。
- **两处技术描述方向写反/半真**：`kp_id` 说成「SQLite 生成、Neo4j 引用」实际相反；「避免 numpy 重依赖」实际 numpy 是显式依赖且是主检索路径（10.4 自己又写 numpy 加速，章内矛盾）。
- 10.4 的其余数字（83 条向量、5.55×、0.18ms/7ms、259.8ms/0.42ms、4.03s 降级）**均有出处**，见 §4 出处索引；其中 83 条是历史单文档口径，现在库里已是 551 条。

---

## 1. 逐项核对

### 1.1 4.2 技术选型（PDF 第 33–35 页；表 4.1 在第 35 页，`pdf_text.txt` L788-798）

| 方案陈述 | 代码实际 | 判定 |
|---|---|---|
| 前端 Vue 3 | `frontend-vue/package.json:30` `"vue": "^3.5.13"` | ✅ 一致 |
| 前端可视化 AntV G6 | `package.json:15` `"@antv/g6": "^5.0.0"` | ✅ 一致 |
| 前端图表 ECharts | `package.json:22` `"echarts": "^5.5.1"` | ✅ 一致（正文未点 Pinia/Element Plus/Vite，但表里没写错，且 `package.json:23/25/43` 确有 element-plus ^2.9.3、pinia ^3.0.1、vite ^7.0.0） |
| 后端 FastAPI | `backend/requirements.txt:2` `fastapi==0.115.6` | ✅ 一致（方案未写版本，安全） |
| JWT 鉴权 | `requirements.txt:42` `PyJWT==2.12.1`；`core/security.py` | ✅ 一致 |
| 关系型 SQLite / 图 Neo4j | `core/sql_database.py`（兼容入口）+ `app/db/`；`core/database.py`；`requirements.txt:16` `neo4j==5.27.0` | ✅ 一致 |
| LLM：DeepSeek `deepseek-chat` | `core/config.py:72-73` 默认 `https://api.deepseek.com/v1` / `deepseek-chat` | ✅ 一致 |
| 向量化：SiliconFlow `BAAI/bge-m3` | `core/config.py:83-84` 默认 `https://api.siliconflow.cn/v1` / `BAAI/bge-m3` | ✅ 一致 |
| 「向量检索使用纯 Python 余弦计算，避免 chromadb/numpy 等重依赖」 | **numpy 是显式依赖且是主路径**：`requirements.txt:35-36` `numpy==1.26.4/2.5.3`，注释 L30-34 明说「VectorIndex 的 numpy 矩阵后端」；`services/vector_index.py:36` `import numpy as _np`，纯 Python 只是回落；chromadb 则确实未启用（`requirements.txt:18-24` 全部注释掉） | ❌ **半真，需改** |
| 4.1 正文（第 33 页，L758-759）「知识点向量…以纯 Python 余弦相似度检索，避免引入重型向量库」 | 同上；且 10.4 自己写「numpy 0.18ms、纯 Python 回落 7ms」，两章互相矛盾 | ❌ 需改 |

补充：`requirements.txt:19-21` 确有「chromadb 0.5.23 依赖 chroma-hnswlib 在 Windows+Python 3.12 无预编译 wheel」的注释，方案「该问题开发期已实际遇到并记录，详见 requirements.txt 注释」**属实**，可保留（但现在是 Python 3.13 环境，`connection` 用 2.5.3，讲稿别把 3.12 说成当前版本）。

### 1.2 4.3 双存储设计（PDF 第 34–36 页，L799-828）

| 方案陈述 | 代码实际 | 判定 |
|---|---|---|
| SQLite 承载用户/课程/文档/学习记录/题库 + 全部向量（t_kp_embedding） | `app/db/schema.py` 建表；向量两列 `embedding`(JSON) + `embedding_blob`(float32 BLOB) | ✅ 一致 |
| Neo4j 只存 KnowledgePoint 与四类关系 | `core/database.py:26-34,141` | ✅ 一致 |
| course_id 三端统一为整数、kp_id 逻辑外键 | `schema.py` 各表 course_id INTEGER；`database.py:12` 关系类型白名单 | ✅ 一致 |
| 「连接管理、建表与幂等迁移封装于 core/sql_database.py」 | **已重构**：`core/sql_database.py` 现在只有 49 行、是兼容 re-export（L1-9 自述「Phase 1 起真实实现位于 app/db/」），建表在 `app/db/schema.py`（36KB，`_SCHEMA_SQL`），迁移在 `app/db/migrations.py`（33KB，`_migrate()` 在 L30） | ❌ 指向失效，需改路径 |
| 扫描脚本 `scripts/scan_consistency.py` | 实际在 `backend/scripts/scan_consistency.py`（另有 `check_consistency.py`） | ⚠️ 路径需补 `backend/` |
| 知识点向量 1024 维、float32 BLOB 为主 + JSON 回落 | 库内实取：`dim=1024`；551 条向量 JSON 12,528,505 字符 / BLOB 2,256,896 字节（**5.55×**，与 10.4 完全对上） | ✅ 一致 |

### 1.3 4.4.1 「SQLite 关系型表（22 张）」（PDF 第 35–37 页；表 4.2/4.3 在第 37 页）

方案表 4.2（16 张）+ 表 4.3（6 张）= 22 张，表名与本报告核对到的实际表逐一对上，**但漏了一张**：

- 代码 `app/db/schema.py` 里 `CREATE TABLE IF NOT EXISTS` 共 **23** 条（L100/131/152/168/211/236/248/272/300/323/360/388/406/421/437/460/487/515/543/582/613/641/**657**），第 23 张是 **`t_document_task`（L657）**。
- 线上库实证（`backend/data/app.db`，`select name from sqlite_master where type='table'`）返回 **23 张**，且 `t_document_task` 有 **6 行**在用（异步上传/抽取任务状态、断点恢复，见 `services/task_state.py`）。
- 判定：❌ **「22 张」过时，应为 23 张，表 4.2 需补 `t_document_task` 一行**。
- 其余关键设计约定与代码一致：全表 `t_` 前缀、自增主键、时间存本地时间字符串（`db/connection.py:39 _now()`）、新增列走 `_migrate()` 的 `ALTER TABLE`（`migrations.py:30,50,61,118,143,175`）、选项/答案 JSON 入库（`schema.py:349` 注释）、`t_question_kp` 主知识点唯一性由部分唯一索引兜底（`migrations.py:255`）。✅
- 表 4.2 里 `t_kp_text` 现库为 **0 行**（懒构建缓存），答辩若被问「这张表有什么用」要能答「图库不可用时检索回落，按需写入」。

### 1.4 4.4.2 Neo4j 图模型 + 6.1 图模型（PDF 第 38–40 页；6.1 在第 56–59 页）

| 方案陈述 | 代码实际 | 判定 |
|---|---|---|
| 节点标签 `KnowledgePoint` | `core/database.py:141` `MERGE (kp:KnowledgePoint ...)` | ✅ |
| kp_id 唯一约束 + `(course_id,name)`、`(course_id,document_id)`、`(course_id,document_id,name)`、PRE-CEDES 的 course_id 索引由 `init_schema()` 幂等创建 | `core/database.py:102-117`（`kp_id_unique`、`kp_course_name`、`kp_course_category`、`kp_course_doc`、`kp_course_doc_name`、`precedes_course`） | ✅（方案没提 `kp_course_category`，不算错，可补） |
| 四类关系 PRECEDES/CONTAINS/RELATED_TO/APPLIES_TO + 中文标签「前置知识/包含/相关概念/应用」 | `core/database.py:26-34` `RELATION_TYPE_LABELS` / `VALID_RELATION_TYPES` | ✅ |
| 关系类型白名单校验后才拼进 Cypher | `database.py:166-168` 非法回退 `RELATED_TO` | ✅ |
| 节点 confidence 缺省 0.9、is_manual=false；关系 confidence 缺省 0.8 | `services/kg_manager.py:44`（0.9/false）、`kg_manager.py:73-85`（0.8/false） | ✅ |
| MERGE 键 `(course_id,document_id,name)`，同文档同名合并 | `database.py:136-141`、批量路径 `L200-225` | ✅ |
| Neo4j 2026.x 用 `NOT EXISTS { MATCH ... }` 形式 | `services/path_recommender.py:182,251,299` 均为 `WHERE NOT EXISTS { ... }` | ✅ |
| 6.1(6)（第 58–59 页，L1270-1275）「**知识点元数据先写入 SQLite 生成全局唯一的 kp_id，随后 Neo4j 节点以同名属性携带该 kp_id 入图**」 | **方向反了**：`kp_id` 只由 Neo4j 建点侧生成——`core/database.py:42 generate_kp_id()`，调用点仅 `L139`、`L214`（均在 `MERGE KnowledgePoint` 前）；SQLite 侧没有任何生成/写 kp 元数据的路径，只在学习记录/收藏/题目里存 `kp_id` 引用 | ❌ **必须改** |
| 6.1(2)「kp_id 格式 kp_{course_id}_{uuid}」 | `database.py:42` 生成函数（与 4.4.2 第 38 页说法一致） | ✅ |

### 1.5 4.5 API 接口设计（PDF 第 40–42 页；表 4.7 在第 41 页）

| 方案陈述 | 代码实际 | 判定 |
|---|---|---|
| 「后端共 **16 个** API 模块」 | `app/main.py:195-218` 实际 `include_router` **19** 次：auth、courses、knowledge_graph、qa、learning_path、learning、graph、documents、dashboard、favorites、teacher、course_members、invites、profile、questions、practice、grading、admin、admin_fusion | ❌ 数量错 |
| 表 4.7 共 15 行 | 缺 **teacher（/api/v1/teacher）、admin（/api/v1/admin）、admin_fusion（/api/v1/admin/fusion）** 三个已挂载模块 | ❌ 漏列 |
| 表 4.7 知识图谱前缀 `/api/v1/graph、/api/v1/kg` | **`/api/kg` 没有 v1**：`app/api/knowledge_graph.py:17` `prefix="/api/kg"`；graph 为 `graph.py:20` `/api/v1/graph` | ❌ 必须改 |
| 「认证模块前缀 /api/auth，其余模块统一前缀 /api/v1」 | 除 `/api/auth`（`auth.py:25`）外还有遗留 `/api/kg`（同上），并非全部 /api/v1 | ❌ 措辞需限定 |
| 统一响应 `{code,message,data,timestamp}`，code=0 成功，错误码在 `core/codes.py` | `core/response.py:12-19` 完全一致 | ✅ |
| 防泄题白名单投影（只下发题干/选项/难度，不含 answer/analysis） | `api/practice.py:14,89`；`services/question_service.py:11,161`（填空题每空答案也不下发） | ✅ |
| 权限按 `core/permissions.py` 分档、学生端按 course_id 过滤 | `core/permissions.py` 存在；`main.py` 路由均挂 `Depends(get_current_user)` | ✅（未逐端点核对，属 B 组范围） |

### 1.6 10.4 存储与并发测试（PDF 第 104–106 页，L2386-2410）

| 方案陈述 | 实际出处 / 复核 | 判定 |
|---|---|---|
| 修复后 journal_mode=wal、`busy_timeout=15000ms` | `app/db/connection.py:22-23`（`JOURNAL_MODE` 默认 WAL、`BUSY_TIMEOUT_MS` 默认 15000）；线上 `PRAGMA journal_mode` 实测 = `wal` | ✅ 一致 |
| `test_sqlite_wal.py` **13/13 通过**（临时副本、不碰线上库） | 脚本存在（262 行，`checks` 机制）；**本轮复跑失败**：`sqlite3.OperationalError: unable to open database file`（`test_sqlite_wal.py:158` `tempfile.mkdtemp()` → `%TEMP%`，本会话文件沙箱不允许写该目录，报错点在 `app/db/connection.py:130`）。属**环境限制**不是代码缺陷，但答辩材料里「13/13」最好附最近一次实跑输出 | ⚠️ 待复跑取证 |
| 83 条向量 JSON 1,888,331 字符 → BLOB 339,968 字节（5.55×） | 出处 `docs/题库与推荐系统设计.md:760`；口径是**单一文档 83 条**的历史测量。现库 15 个文档共 551 条：JSON 12,528,505 / BLOB 2,256,896 = **5.55×**（比例完全一致，可放心讲；但「83 条」写死在答辩 PPT 里容易被问「现在多少」→ 答 551 条、仍 5.55×） | ✅ 有出处，口径宜注明 |
| numpy 0.18–0.24ms、纯 Python 7.2–8.3ms（35–40×）；N=5000 时纯 Python 259.8ms、numpy 0.42ms（620×），据此暂不引入 HNSW | 出处 `docs/题库与推荐系统设计.md:767`（259.8/0.42）；`docs/题库与推荐系统设计.md:396`（纯 Python→numpy 矩阵+缓存；N=59 实测 35~40×）；代码侧 `vector_index.py`（numpy 主 + 进程内 TTL 缓存 + 纯 Python 回落） | ✅ 有出处（本轮未复跑基准） |
| 问答 async 修复：`asyncio.to_thread`，`test_qa_async.py` 12/12 | `services/qa_service.py:416` `return await asyncio.to_thread(self._ask_blocking, ...)`；`qa_service.py:476` 流式路径同样 to_thread；脚本存在（8KB） | ✅ 一致（12/12 未复跑） |
| `test_vector_index.py` 24/24 | 脚本存在（9.9KB，checks 机制）；未复跑 | ⚠️ 未复跑 |

### 1.7 10.5 性能测试（PDF 第 105–106 页，L2411-2423）

| 方案陈述 | 实际数据 | 判定 |
|---|---|---|
| 10.5(1)《Hello 算法》第 7 章树「约 1.2 万 tokens，**切 3 个分块**，实测上传→解析→抽取全流程 **41 秒**」，由「分块 6000 tokens、并发 4 路、**temperature=0**」保证 | 仓库最新实测 `backend/eval_data/perf_timing.json`（generated_at 2026-10-08）：4 份文档 ×3 次，`total_mean_s` = **10.811 / 7.471 / 6.742 / 7.912 秒**，最坏单次 11.374s，`chunks` = **1 / 2 / 4 / 2**，`params.temperature` = **0.15**（与 `services/knowledge_extractor.py:581` 默认 0.15 一致；评测配置 `backend/eval_config.py:29` 才是 temperature=0）。41 秒仅见于 `docs/项目说明_v2.md:31`，且 `docs/需求规格说明书.md:1140` 自标「单次测量；文档长度/模型/网络影响大；无 P95；待复测」 | ❌ **必须改**（旧数字 + 分块数 + temperature 三处都不对） |
| 10.5(2) 建 40 条知识点向量 9.1s；自动标注 121 题 22.5s（4 次 embedding 批量调用，batch=32）；标注质量评测 0.47s | 出处 `docs/题库与推荐系统设计.md:818` | ✅ 有出处（121 题与现库 `t_question` 101 行不同，属历史批次口径，建议写「当时 121 题」） |
| 10.5(3) 单次图库连接失败 4.03s；连续 3 次 4.03s→0.000s→0.000s；Recall@K 评测 >300s→7.0s；三脚本 1.2s/1.7s/5.5s | 出处 `docs/题库与推荐系统设计.md:714-721`；代码侧有连接超时 2.0s + 失败熔断 30s 的实现依据（`core/database.py` 连接超时、`main.py:233-236` 注释说明"单次请求会到 4 秒"） | ✅ 有出处 |
| 10.7（第 105–106 页）「前端另有 Playwright e2e 共 **9 个** spec」 | 实际 `frontend-vue/tests/*.spec.ts` 共 **14** 个（admin×3、auth-register、course-center、example、merged-question-bank、permissions、profile、qa-rag、qa-stream、refactor-smoke、regression、write-ops） | ❌ 数量错 |

### 1.8 第 14 章 总结与展望（PDF 第 173–175 页，L3335-3400）

| 方案陈述 | 实际 | 判定 |
|---|---|---|
| 14.1(1) 关系主指标 F1 0.1614→**0.8312**（P=0.7841，R=0.8889），CONTAINS F1 0.939 | 出处 `backend/eval_data/eval_report_第7章_树_v15.json:123`（mean 0.8312）；`eval_data/评测报告_第7章_树_v15.md:31`（0.7841 / 0.8889 / 0.8312） | ✅ 有出处 |
| 14.1(2) 21 题课内 20/20、课外拒答 1/1；召回层 Recall@K **0.9867** 过 0.95 安全阀 | 0.9867 出处 `docs/题库与推荐系统设计.md:701,730,789`；但 `backend/eval_data/eval_report_question_recall.json:6` 的 `recall_at_k_mean`=**1.0**、`target`=0.95。属不同实验口径（bias/信号开关），**建议在表里注明口径**，否则和 JSON 对不上 | ⚠️ 口径需注明 |
| 14.1(3)「后端 **16 个 API 模块**（含 /api/v1/admin 治理接口）」 | 实际 **19** 个（`main.py:195-218`） | ❌ |
| 14.1(3)「SQLite **16 张表** 与 Neo4j 双存储」 | 实际 **23** 张（`app/db/schema.py` 23 条 CREATE TABLE；线上 app.db 23 张）。与第 4 章的「22 张」**又互相矛盾** | ❌ 双重错 |
| 14.1(3)「文档解析与抽取全流程实测 **41 秒**」 | 同 1.7：`perf_timing.json` 实测 6.7–10.8 秒 | ❌ 旧数字 |
| 14.1(3)「numpy 矩阵化检索加速 **3540 倍**」（PDF 排版丢了波浪号，原意应为 35~40 倍） | 10.4 写 35–40×（N=59）、620×（N=5000） | ⚠️ 排版风险：评委可能读成「3540 倍」，建议写「35–40 倍（N=59），N=5000 时约 620 倍」 |
| 14.1(3) 三端（教师/学生/管理员） | `app/db/schema.py:9` `USER_ROLES = ("admin","teacher","student")`；`frontend-vue/src/views/admin/` 8 个视图（AdminUsers/Courses/Governance/Resources/System/AuditLogs/Fusion/Dashboard） | ✅ 一致 |
| 14.2(2)「错题本、**批改**等模块的第一版边界（**不支持填空/简答**、无批量导入与 AI 出题）」 | 批改已上线且支持主观题：`services/grading_service.py:5`（FILL/ESSAY 提交只落库 PENDING 待批改）、`:55` FILL 逐空对照、`:75`；`app/db/schema.py:32-33` `MANUAL_GRADE_TYPES=("FILL","ESSAY")`；`api/grading.py:24` 已挂载；4.5 表 4.7 自己也写「主观题教师批改」；9.x/11.x 有批改界面 | ❌ **必须改**（自相矛盾，评委可当场翻 4.5 对照） |
| 14.2(3) 问答链路已 `asyncio.to_thread` 迁移；Git 仓库分析模块（约千行）挂载形态待决策 | `qa_service.py:416,476` ✅；`services/git_analyzer.py` + `git_kg_manager.py` 共 1016 行、无路由挂载 ✅（方案已诚实披露，保持即可） | ✅ |

---

## 2. 必须改清单（按优先级，可直接改稿）

| # | 页码/节 | 方案原文（要点） | 代码/数据实际 | 建议改法 |
|---|---|---|---|---|
| 1 | P173 14.1(3) | 「SQLite **16 张表**」 | 实际 **23** 张（`app/db/schema.py` 23 条 CREATE TABLE；线上 app.db 23 张），且与第4章「22 张」自相矛盾 | 统一改为「23 张表（含 t_document_task）」 |
| 2 | P34-36 4.3(1)/4.4.1 + 表4.2 | 「SQLite 共 **22 张表**」，表4.2 16 张 + 表4.3 6 张 | 实际 23 张，漏 `t_document_task`（`schema.py:657`，库内 6 行在用，服务 `services/task_state.py`） | 全文 22→23，表4.2「文档」领域补一行 `t_document_task`（异步任务状态/断点恢复） |
| 3 | P33、P40、P173 4.1/4.5(1)/14.1(3) | 「后端共 **16 个** API/路由模块」 | 实际 **19** 个（`main.py:195-218`include_router 19 次） | 改 19，并补 teacher/admin/admin_fusion |
| 4 | P41 表4.7 | 知识图谱前缀「`/api/v1/graph`、**`/api/v1/kg`**」；表4.7 只有 15 行 | 实际 `/api/kg` 无 v1（`api/knowledge_graph.py:17`）；表缺 teacher(/api/v1/teacher)、admin(/api/v1/admin)、admin_fusion(/api/v1/admin/fusion) | 改为「/api/v1/graph 与遗留 /api/kg（计划统一 /api/v1）」，表4.7 补 3 行 |
| 5 | P34 4.2 原则 + P33 4.1 | 「向量检索用**纯 Python** 余弦，避免 chromadb/**numpy** 等重依赖」；10.4 又写 numpy 加速 35–40× | numpy 是显式依赖且是**主路径**（`requirements.txt:35-36`；`services/vector_index.py:36`），纯 Python 仅回落；chromadb 才确实未启用（`requirements.txt:18-24` 注释） | 改「**numpy 矩阵化检索 + 纯 Python 兜底**；仅回避 chromadb（Win+Py3.12 无 wheel，见 requirements 注释）」，删「避免 numpy」 |
| 6 | P34 4.3(1) | 「连接管理、建表与幂等迁移封装于 **core/sql_database.py**」 | 该文件已是 49 行兼容 shim（`core/sql_database.py:1-9` 自述真实实现在 app/db/）；建表 `app/db/schema.py`，迁移 `app/db/migrations.py:30 _migrate()` | 改「`core/sql_database.py` 提供兼容入口，实现在 `app/db/`（schema.py 建表 / migrations.py 幂等迁移）」 |
| 7 | P58-59 6.1(6) | 「知识点元数据**先写入 SQLite 生成** kp_id，随后 Neo4j 携带引用」 | 方向相反：kp_id 只在 Neo4j 建点侧 `generate_kp_id` 生成（`core/database.py:42,139,214`），SQLite 只存引用 | 改「**Neo4j 建点时生成 kp_id**，SQLite 各表以逻辑外键引用」 |
| 8 | P174 14.2(2) | 「错题本、**批改**等模块第一版边界（**不支持填空/简答**…）」 | 批改已上线支持主观题 FILL/ESSAY（`grading_service.py:5,55,75`；`schema.py:32-33`；`api/grading.py:24`；4.5 表4.7 也写「主观题教师批改」） | 删「批改不支持填空/简答」，改为「错题本、AI 出题为后续迭代」 |
| 9 | P105-106 10.5(1) | 「约1.2万tokens、**切3个分块**、实测 **41 秒**、**temperature=0**」 | `eval_data/perf_timing.json`：4 份文档×3 次 mean **6.742–10.811s**（最坏 11.374s），chunks **1/2/4/2**，temperature **0.15**（`knowledge_extractor.py:581`；评测配置才 0，`eval_config.py:29`） | 改「实测 **6.7–10.8 秒**（4 份真实文档×3 次，最坏 11.4s，`eval_data/perf_timing.json`）、chunk 6000 tokens、并发 4 路、temperature 0.15」，删「41 秒/3 分块/0」 |
| 10 | P105-106 10.7 | 「Playwright e2e 共 **9** 个 spec」 | 实际 **14** 个（`frontend-vue/tests/*.spec.ts`） | 改 14 个 |
| 11 | P174 14.1(3) | 「numpy 矩阵化检索加速**3540倍**」（PDF 波浪号丢失） | 10.4 为 35–40×（N=59）、620×（N=5000） | 写「**35–40 倍（N=59），N=5000 时约 620 倍**」，避免误读 |
| 12 | P31 4.1 | 前端展示层「按角色划分为**教师端与学生端**」 | 三端：admin/teacher/student（`schema.py:9`；`views/admin/` 8 个视图；14.1 与 11.4 都写三端） | 改「教师端、学生端与管理员端三端」 |
| 13 | P34 4.3(3) | 一致性扫描脚本 `scripts/scan_consistency.py` | 实际 `backend/scripts/scan_consistency.py` | 补全路径 |
| 14 | P104 10.4(1) | 「test_sqlite_wal.py **13/13** 通过」 | 脚本存在，但本轮复跑在沙箱环境报 `unable to open database file`（`%TEMP%` 不可写，`test_sqlite_wal.py:158` + `connection.py:130`），**13/13 未能现场复现**；`test_vector_index.py 24/24`、`test_qa_async.py 12/12` 同样未复跑 | 答辩前在正常环境各跑一遍，把「通过 N/M + 日期」写进 PPT 备注；不要把通过数当口头承诺 |
| 15 | P173 14.1(2) | 「召回层 Recall@K **0.9867** 通过 0.95 安全阀」 | 出处 `docs/题库与推荐系统设计.md:701`，但 `eval_data/eval_report_question_recall.json:6` 为 **1.0** | 注明口径（如「bias 口径 1.0000；混合口径 0.9867」），避免与 JSON 对不上 |

---

## 3. 可以照讲、不需改的（给答辩吃定心丸）

1. 4.2 表技术栈逐项都能在代码里点到（Vue3/Element Plus/Pinia/Vite/AntV G6 v5/ECharts/FastAPI/JWT/SQLite/Neo4j/DeepSeek `deepseek-chat`/SiliconFlow `BAAI/bge-m3`），`@antv/g6` 确实是 v5（不是旧 v4 文档）。
2. 图模型叙述（KnowledgePoint + 四类关系 + 中文标签 + `(course_id,document_id,name)` MERGE + kp_id 唯一约束与 4 个复合索引 + 关系类型白名单 + NOT EXISTS 写法）在 `core/database.py:26-34,102-117,136-141,166-168` 与 `kg_manager.py:44,73-85` 全部落地，6.2 的 AntV G6 交互（`GraphCanvas.vue`）也在。
3. 统一响应 `{code,message,data,timestamp}`（`core/response.py:12-19`）与防泄题白名单（`question_service.py:11,161`；`practice.py:14,89`）完全属实，是加分项。
4. 10.4 的 5.55×、numpy 加速、4.03s 降级、0.18ms/7ms 等数字都有文档出处（见 §4），且 5.55× 用现库 551 条复算仍是 5.55×——这条最稳。
5. 14.1 的抽取 F1 0.8312 / P 0.7841 / R 0.8889 有实验 JSON 与报告双出处，可以放心讲。

---

## 4. 数字出处索引（评委追问时直接翻）

| 数字 | 出处 |
|---|---|
| 5.55×（JSON 1,888,331 字符 → BLOB 339,968 字节，83 条） | `docs/题库与推荐系统设计.md:760`；现库复算 551 条：12,528,505 字符 → 2,256,896 字节，同为 5.55× |
| numpy 0.42ms vs 纯 Python 259.8ms（N=5000，620×） | `docs/题库与推荐系统设计.md:767` |
| 35–40×（N=59）、numpy 矩阵+进程内缓存 | `docs/题库与推荐系统设计.md:396` |
| 建 40 条向量 9.1s / 标注 121 题 22.5s（batch=32） | `docs/题库与推荐系统设计.md:818` |
| 图库单次失败 4.03s、熔断后 0.000s、Recall@K 评测 300s→7.0s | `docs/题库与推荐系统设计.md:714-721` |
| 解析+抽取耗时（最新） | `backend/eval_data/perf_timing.json`：mean 10.811/7.471/6.742/7.912s，chunks 1/2/4/2，temperature 0.15，chunk_max_tokens 6000，max_concurrency 4 |
| 抽取 F1 0.8312 / P 0.7841 / R 0.8889 | `backend/eval_data/eval_report_第7章_树_v15.json:123`；`backend/eval_data/评测报告_第7章_树_v15.md:31` |
| Recall@K 0.9867（混合口径） | `docs/题库与推荐系统设计.md:701,730,789`；`eval_data/eval_report_question_recall.json:6` 的 mean=1.0（bias 口径），口径需注明 |

---

## 5. 取证方法与限制（供 Lead 复核）

- 表 4.1/4.2/4.3/4.7 在 PDF 中是位图，文本层没有内容；用 `pdfplumber page.to_image(resolution=160)` 渲染为 `.dsh_tmp/render/p35.png`、`p37.png`、`p40.png`、`p41.png` 后逐页读表（页脚页码 = PDF 页码，已与正文交叉验证）。**其余表格（表4.4/4.5/4.6/10.x）本轮未逐格读图**，仅核对正文结论。
- 表数量用双证：静态 `grep CREATE TABLE app/db/schema.py`（23 条）+ 线上 `backend/data/app.db` `sqlite_master`（23 张，`t_document_task` 6 行）。
- 路由以 `app/main.py:195-218` 的 `include_router` 为准，前缀取自各 `api/*.py` 的 `APIRouter(prefix=...)`。
- 版本以 `frontend-vue/package.json`、`backend/requirements.txt` 为准。
- 性能数字未做基准复跑（需 LLM/Neo4j/网络），只做「方案数字 vs 仓库记录」对账；唯一复跑的 `test_sqlite_wal.py` 因 `%TEMP%` 不可写失败（沙箱环境限制，非代码缺陷）。
- 未修改任何源码、文档、PDF；本报告外只产生 `.dsh_tmp/render/*.png`（表格截图取证用）与两个临时取样脚本（已删除）。
