"""
Embedding 客户端与知识点向量索引（RAG 向量检索）

DeepSeek 无 embedding 接口，向量化改用 OpenAI 兼容的 SiliconFlow BAAI/bge-m3。
embedding 持久化到 SQLite 的 t_kp_embedding 表；相似度用纯 Python 余弦计算，
避免引入 numpy/chromadb 等重依赖（竞赛场景知识点规模小，纯 Python 足够）。
"""
from openai import OpenAI

from ..core.config import settings
from ..core.database import db
from ..core.sql_database import sql_db

# 单次 embedding 调用的最大条数（避免超出服务端单请求限制）
_EMBED_BATCH_SIZE = 32


class EmbeddingClient:
    """OpenAI 兼容 embedding 客户端；未配置 key 时 available=False"""

    def __init__(self):
        self.client = None
        if settings.EMBEDDING_API_KEY:
            self.client = OpenAI(
                api_key=settings.EMBEDDING_API_KEY,
                base_url=settings.EMBEDDING_API_BASE,
            )

    @property
    def available(self) -> bool:
        return self.client is not None

    def embed(self, texts: list) -> list:
        """批量嵌入文本，返回 list[list[float]]（与输入顺序一致）"""
        if self.client is None:
            raise RuntimeError("未配置 EMBEDDING_API_KEY，无法进行向量检索")
        if not texts:
            return []
        texts = [t if t and t.strip() else " " for t in texts]
        vectors = []
        for i in range(0, len(texts), _EMBED_BATCH_SIZE):
            chunk = texts[i:i + _EMBED_BATCH_SIZE]
            resp = self.client.embeddings.create(model=settings.EMBEDDING_MODEL, input=chunk)
            ordered = sorted(resp.data, key=lambda d: d.index)
            vectors.extend([d.embedding for d in ordered])
        return vectors


class KnowledgeEmbedder:
    """为课程知识点构建/维护向量索引（懒构建 + 编辑失效后重建）"""

    def __init__(self):
        self.embedding = EmbeddingClient()

    def ensure_index(self, course_id) -> int:
        """确保课程知识点向量已构建；缺失时现场构建，返回向量条数"""
        if course_id is None:
            return 0
        existing = sql_db.get_embeddings_by_course(course_id)
        if existing:
            return len(existing)
        return self.build_index(course_id)

    def build_index(self, course_id) -> int:
        """为课程全部知识点生成并持久化 embedding；无 key 或无节点时返回 0"""
        if not self.embedding.available:
            return 0
        recs = db.query(
            "MATCH (n:KnowledgePoint {course_id: $cid}) "
            "RETURN n.kp_id AS kp_id, n.name AS name, n.category AS category, "
            "n.description AS description",
            {"cid": course_id},
        )
        if not recs:
            return 0
        texts = [
            f"{r.get('category') or '概念'}：{r.get('name') or ''}。{r.get('description') or ''}"
            for r in recs
        ]
        vecs = self.embedding.embed(texts)
        for r, vec in zip(recs, vecs):
            sql_db.upsert_kp_embedding(course_id, r.get("kp_id"), vec)
        return len(recs)
