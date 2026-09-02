"""
智能问答服务：基于知识图谱 + 真向量 RAG 检索

检索链路：问题 embedding → 与课程知识点向量做余弦相似度 → top_k 上下文 → LLM 生成。
未配置 embedding key 或向量检索失败时，自动退回关键词检索（保证功能可用）。
"""
import math
from typing import List

from openai import OpenAI

from ..core.config import settings
from ..core.database import db
from ..core.sql_database import sql_db
from .embedding import EmbeddingClient, KnowledgeEmbedder


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


def _cosine(a: List[float], b: List[float]) -> float:
    """余弦相似度（纯 Python 实现，避免引入 numpy 重依赖）"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
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

    def _vector_search(self, question: str, course_id, top_k: int) -> List[dict]:
        """真向量检索：问题与课程知识点 embedding 做余弦相似度排序，返回结构化节点列表"""
        cid = _coerce_course_id(course_id)
        if cid is None:
            return []  # 跨课程向量检索暂不支持，退回关键词
        try:
            self.indexer.ensure_index(cid)
            rows = sql_db.get_embeddings_by_course(cid)
            if not rows:
                return []
            q_vec = self.embedder.embed([question])[0]
        except Exception:
            return []  # embedding 不可用（未配置 key / 网络异常等），退回关键词

        ranked = sorted(rows, key=lambda r: -_cosine(q_vec, r["embedding"]))[:top_k]

        # 按 kp_id 回查节点元数据，拼接上下文
        kp_ids = [r["kp_id"] for r in ranked]
        nodes = {}
        recs = db.query(
            "MATCH (n:KnowledgePoint {course_id: $cid}) WHERE n.kp_id IN $ids RETURN n",
            {"cid": cid, "ids": kp_ids},
        )
        for rec in recs:
            n = rec.get("n")
            if n:
                nodes[n.get("kp_id")] = n

        return [self._node_dict(nodes[r["kp_id"]]) for r in ranked if r["kp_id"] in nodes]

    # ---------- 关键词检索（兜底） ----------

    def _keyword_search(self, question: str, course_id, top_k: int) -> List[dict]:
        """关键词检索（向量不可用时的兜底），返回结构化节点列表"""
        cid = _coerce_course_id(course_id)
        keyword = (question or "")[:20]

        if cid is not None:
            cypher = """
            MATCH (n:KnowledgePoint {course_id: $course_id})
            WHERE n.name CONTAINS $keyword OR n.description CONTAINS $keyword
            RETURN n LIMIT $top_k
            """
            params = {"course_id": cid, "keyword": keyword, "top_k": top_k}
        else:
            cypher = """
            MATCH (n:KnowledgePoint)
            WHERE n.name CONTAINS $keyword OR n.description CONTAINS $keyword
            RETURN n LIMIT $top_k
            """
            params = {"keyword": keyword, "top_k": top_k}

        records = db.query(cypher, params)
        contexts = [self._node_dict(rec["n"]) for rec in records if rec.get("n")]

        # 结果不足 top_k 时，用同课程节点兜底补足
        if len(contexts) < top_k:
            existing_names = {c["name"] for c in contexts}
            if cid is not None:
                records = db.query(
                    "MATCH (n:KnowledgePoint {course_id: $course_id}) RETURN n LIMIT $top_k",
                    {"course_id": cid, "top_k": top_k},
                )
            else:
                records = db.query(
                    "MATCH (n:KnowledgePoint) RETURN n LIMIT $top_k", {"top_k": top_k},
                )
            for rec in records:
                n = rec.get("n")
                if not n or n.get("name") in existing_names:
                    continue
                contexts.append(self._node_dict(n))
                if len(contexts) >= top_k:
                    break
        return contexts

    def search_related_nodes(self, question: str, course_id=None, top_k: int = 5) -> List[dict]:
        """检索相关知识（优先向量，失败退回关键词），返回结构化节点（含 kp_id/name/category/description）"""
        nodes = self._vector_search(question, course_id, top_k)
        if not nodes:
            nodes = self._keyword_search(question, course_id, top_k)
        return nodes

    def search_related_knowledge(self, question: str, course_id=None, top_k: int = 5) -> List[str]:
        """检索相关知识，返回格式化字符串（供 LLM 上下文 / 向后兼容）"""
        return [_format_node(n) for n in self.search_related_nodes(question, course_id, top_k)]

    async def ask(self, question: str, course_id=None) -> str:
        """回答问题（RAG 模式）"""
        # 1. 检索相关知识（向量优先）
        contexts = self.search_related_knowledge(question, course_id)
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
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"抱歉，问答服务暂时不可用：{str(e)}"
