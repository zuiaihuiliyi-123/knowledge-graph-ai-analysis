"""
智能问答服务：基于知识图谱 + 真向量 RAG 检索

检索链路：问题 embedding → 与课程知识点向量做余弦相似度 → top_k 上下文 → LLM 生成。
未配置 embedding key 或向量检索失败时，自动退回关键词检索（保证功能可用）。

阶段 G（async 修复）：本模块的检索与 LLM 调用都是**同步阻塞**的
（openai 同步客户端 / Neo4j 同步驱动 / SQLite），故 `ask()` 把整段逻辑放进
`asyncio.to_thread` 执行，避免阻塞事件循环（见 `ask()` 的说明）。
"""
import asyncio
import logging
import math
from typing import List

from openai import OpenAI

from ..core.config import settings
from ..core.database import db
from .embedding import EmbeddingClient, KnowledgeEmbedder
from .vector_index import vector_index

_logger = logging.getLogger(__name__)


QA_SYSTEM_PROMPT = """你是一个课程学习助手。请基于提供的课程知识图谱内容回答学生的问题。

## 规则
1. 优先使用提供的知识内容回答问题
2. 如果知识库中没有相关信息，请诚实告知，不要编造
3. 回答要准确、简洁、易于理解
4. 如果合适，可以推荐相关的知识点供进一步学习

## 课程知识内容
{context}

请根据以上内容回答学生的问题。"""


def _coerce_course_id(course_id):
    """course_id 统一为整数（对齐 Neo4j 存储类型）；空值返回 None 表示不过滤"""
    if course_id is None or course_id == "":
        return None
    try:
        return int(course_id)
    except (TypeError, ValueError):
        return None


def _coerce_document_id(document_id):
    """document_id 统一为整数（对齐 Neo4j 存储类型）；空值返回 None 表示不过滤"""
    if document_id is None or document_id == "":
        return None
    try:
        return int(document_id)
    except (TypeError, ValueError):
        return None


def _cosine(a: List[float], b: List[float], norm_a: float = None) -> float:
    """余弦相似度（纯 Python 实现，避免引入 numpy 重依赖）

    norm_a：a 的 L2 范数。批量比较（如对一个查询向量排序整个索引）时 a 固定不变，
    传入可省掉每次都重算 a 的范数——1024 维下这是相当可观的一笔重复计算。
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) if norm_a is None else norm_a
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _format_node(node) -> str:
    """格式化知识点为上下文/来源文本"""
    ctx = f"[{node.get('category', '知识点')}] {node.get('name', '')}"
    if node.get('description'):
        ctx += f": {node.get('description')}"
    return ctx


class QAService:
    """智能问答服务"""

    def __init__(self):
        self.client = OpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_API_BASE,
        )
        self.embedder = EmbeddingClient()
        self.indexer = KnowledgeEmbedder()

    # ---------- 向量检索 ----------

    @staticmethod
    def _node_dict(n) -> dict:
        """Neo4j 节点 → 结构化引用（供前端"证据链"展示）"""
        return {
            "kp_id": n.get("kp_id"),
            "name": n.get("name", ""),
            "category": n.get("category", ""),
            "description": n.get("description", ""),
        }

    def _vector_search(self, question: str, course_id, document_id, top_k: int) -> List[dict]:
        """真向量检索：问题与文档知识点 embedding 做余弦相似度排序，返回结构化节点列表"""
        cid = _coerce_course_id(course_id)
        did = _coerce_document_id(document_id)
        if cid is None or did is None:
            return []  # 缺少文档作用域，退回关键词
        try:
            self.indexer.ensure_index(cid, did)
            q_vec = self.embedder.embed([question])[0]
        except Exception as e:
            # embedding 不可用（未配置 key / 网络异常等）退回关键词检索。
            # 这里必须留下日志：静默吞掉会让「向量检索整条失效」伪装成「知识库为空」，
            # 排查时无从下手。
            _logger.warning("向量检索不可用，本次退回关键词检索: %s", e, exc_info=True)
            return []

        # L2 阶段 E：检索改走 VectorIndex（numpy 矩阵 + 进程内缓存），
        # 不再每次「全量读库 + 逐条纯 Python 余弦」；无向量时返回空 → 上层退回关键词检索
        ranked = vector_index.kp_search(cid, did, q_vec, top_k)
        if not ranked:
            return []

        # 按 kp_id 回查节点元数据，拼接上下文（限定文档）
        kp_ids = [kp_id for kp_id, _ in ranked]
        nodes = {}
        recs = db.query(
            "MATCH (n:KnowledgePoint {course_id: $cid, document_id: $did}) "
            "WHERE n.kp_id IN $ids RETURN n",
            {"cid": cid, "did": did, "ids": kp_ids},
        )
        for rec in recs:
            n = rec.get("n")
            if n:
                nodes[n.get("kp_id")] = n

        return [self._node_dict(nodes[kp_id]) for kp_id, _ in ranked if kp_id in nodes]

    # ---------- 关键词检索（兜底） ----------

    def _keyword_search(self, question: str, course_id, document_id, top_k: int,
                        allowed_ids: List[int] = None) -> List[dict]:
        """关键词检索（向量不可用时的兜底），返回结构化节点列表。

        allowed_ids：调用方（当前用户）可访问的课程 id 列表。仅在「未指定 course_id」
        这条原本会全库扫描的分支上生效——把「不带课程参数」从全库泄漏收敛为
        「仅我的课程」。默认 None 表示不过滤，保证既有调用方行为逐字节不变。
        """
        cid = _coerce_course_id(course_id)
        did = _coerce_document_id(document_id)
        keyword = (question or "")[:20]

        # 未指定课程且用户没有任何可访问课程：直接返回空，避免退化成全库扫描
        if cid is None and allowed_ids is not None and len(allowed_ids) == 0:
            return []

        if cid is not None and did is not None:
            cypher = """
            MATCH (n:KnowledgePoint {course_id: $course_id, document_id: $document_id})
            WHERE n.name CONTAINS $keyword OR n.description CONTAINS $keyword
            RETURN n LIMIT $top_k
            """
            params = {"course_id": cid, "document_id": did, "keyword": keyword, "top_k": top_k}
        elif cid is not None:
            cypher = """
            MATCH (n:KnowledgePoint {course_id: $course_id})
            WHERE n.name CONTAINS $keyword OR n.description CONTAINS $keyword
            RETURN n LIMIT $top_k
            """
            params = {"course_id": cid, "keyword": keyword, "top_k": top_k}
        else:
            cypher = """
            MATCH (n:KnowledgePoint)
            WHERE (n.name CONTAINS $keyword OR n.description CONTAINS $keyword)
            """
            params = {"keyword": keyword, "top_k": top_k}
            if allowed_ids is not None:
                cypher += " AND n.course_id IN $allowed_course_ids\n"
                params["allowed_course_ids"] = list(allowed_ids)
            cypher += "RETURN n LIMIT $top_k"

        records = db.query(cypher, params)
        contexts = [self._node_dict(rec["n"]) for rec in records if rec.get("n")]

        # 结果不足 top_k 时，用同文档节点兜底补足
        if len(contexts) < top_k:
            existing_names = {c["name"] for c in contexts}
            if cid is not None and did is not None:
                records = db.query(
                    "MATCH (n:KnowledgePoint {course_id: $course_id, document_id: $document_id}) "
                    "RETURN n LIMIT $top_k",
                    {"course_id": cid, "document_id": did, "top_k": top_k},
                )
            elif cid is not None:
                records = db.query(
                    "MATCH (n:KnowledgePoint {course_id: $course_id}) RETURN n LIMIT $top_k",
                    {"course_id": cid, "top_k": top_k},
                )
            else:
                fallback_cypher = "MATCH (n:KnowledgePoint)"
                fallback_params = {"top_k": top_k}
                if allowed_ids is not None:
                    fallback_cypher += " WHERE n.course_id IN $allowed_course_ids"
                    fallback_params["allowed_course_ids"] = list(allowed_ids)
                fallback_cypher += " RETURN n LIMIT $top_k"
                records = db.query(fallback_cypher, fallback_params)
            for rec in records:
                n = rec.get("n")
                if not n or n.get("name") in existing_names:
                    continue
                contexts.append(self._node_dict(n))
                if len(contexts) >= top_k:
                    break
        return contexts

    def search_related_nodes(self, question: str, course_id=None, document_id=None,
                             top_k: int = 5, allowed_ids: List[int] = None) -> List[dict]:
        """检索相关知识（优先向量，失败退回关键词），返回结构化节点（含 kp_id/name/category/description）"""
        nodes = self._vector_search(question, course_id, document_id, top_k)
        if not nodes:
            nodes = self._keyword_search(question, course_id, document_id, top_k, allowed_ids)
        return nodes

    def search_related_knowledge(self, question: str, course_id=None, document_id=None,
                                 top_k: int = 5, allowed_ids: List[int] = None) -> List[str]:
        """检索相关知识，返回格式化字符串（供 LLM 上下文 / 向后兼容）"""
        return [_format_node(n) for n in self.search_related_nodes(
            question, course_id, document_id, top_k, allowed_ids)]

    async def ask(self, question: str, course_id=None, document_id=None,
                  allowed_ids: List[int] = None) -> str:
        """回答问题（RAG 模式）。

        阶段 G（async 修复）——**为什么必须放线程池**：

        这条链路整段都是同步阻塞的：
          · `search_related_knowledge` → embedder 的 HTTP 调用、`VectorIndex` 的 SQLite 读、
            Neo4j 同步驱动查询；
          · `client.chat.completions.create` 是 **openai 同步客户端**，`QA_TIMEOUT` 量级是秒。
        在 `async def` 里直接跑会**占住整个事件循环**：一个学生提问期间，
        同进程内**所有**其他请求（包括别人的提问）都只能排队 —— 并发下体验直接塌掉。

        故把整段同步逻辑交给 `asyncio.to_thread`。这与项目内既有做法一致
        （`knowledge_extractor._extract_single` / `relation_completion._call_llm` 都这样处理），
        且**不改动任何业务逻辑与异常处理**（原 try/except 原样保留在同步实现里）。
        """
        return await asyncio.to_thread(self._ask_blocking, question, course_id,
                                       document_id, allowed_ids)

    def _ask_blocking(self, question: str, course_id=None, document_id=None,
                      allowed_ids: List[int] = None) -> str:
        """`ask()` 的同步实现体。**运行在线程池中，禁止在此使用 asyncio API。**"""
        # 1. 检索相关知识（向量优先，文档作用域）
        contexts = self.search_related_knowledge(question, course_id, document_id,
                                                 allowed_ids=allowed_ids)
        context_text = "\n".join(contexts) if contexts else "暂无相关课程知识"

        # 2. 调用 LLM 生成回答
        try:
            response = self.client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": QA_SYSTEM_PROMPT.format(context=context_text)},
                    {"role": "user", "content": question},
                ],
                temperature=0.3,
                max_tokens=1024,
                timeout=settings.QA_TIMEOUT,
            )
            answer = response.choices[0].message.content.strip()
        except Exception as e:
            answer = f"抱歉，问答服务暂时不可用：{str(e)}"
        return {"answer": answer, "sources": sources}

    async def ask(self, question: str, course_id=None, document_id=None,
                  allowed_ids: List[int] = None) -> str:
        """回答问题（RAG 模式），仅返回答案文本；需要引用来源请用 ask_with_sources"""
        result = await self.ask_with_sources(question, course_id, document_id, allowed_ids)
        return result["answer"]
