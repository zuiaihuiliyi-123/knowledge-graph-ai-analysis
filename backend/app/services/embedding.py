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
from .vector_index import vector_index

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

    @staticmethod
    def _fused_sources(course_id, document_id) -> set:
        """已被文档级融合折掉的源 kp_id 集合。

        **三处过滤必须完全一致**（这里 / `_load_kp_records` / 它的 t_kp_text 回落分支）：
        只改其中一处会让「新鲜度基准集合」与「实际写入集合」不相等，
        于是 `ensure_index` 每次都判定索引过期、**每次问答都全量重建索引**
        （配了 embedding key 时等于每次提问多一次外部 API 调用）。
        """
        from .fusion.fusion_map import active_fused_sources
        return active_fused_sources(course_id, document_id)   # tolerant：失败即降级为不过滤

    @classmethod
    def _current_kp_ids(cls, course_id, document_id) -> set:
        """该文档当前在 Neo4j 里的 kp_id 集合（向量索引的新鲜度基准）"""
        recs = db.query(
            "MATCH (n:KnowledgePoint {course_id: $cid, document_id: $did}) "
            "RETURN n.kp_id AS kp_id",
            {"cid": course_id, "did": document_id},
        )
        ids = {r["kp_id"] for r in recs if r.get("kp_id")}
        return ids - cls._fused_sources(course_id, document_id)

    def ensure_index(self, course_id, document_id) -> list:
        """确保文档知识点向量与图谱一致，返回该文档的全部向量行 [{kp_id, embedding}]。

        「有向量就跳过」是错的：重新抽取会让 kp_id 全部更换，教师增删改节点也会
        改变知识点集合，此时旧向量全部指向已不存在的节点 —— 向量检索会一直返回空，
        而因为表里「非空」，索引永远不会重建。故改为比对 kp_id 集合是否一致。

        **阶段 5' 起改为增量同步**（`sync_index`）：集合差异只补缺失、只删过期，
        不再「整篇清空 + 全量重算」。改一个知识点/做一次融合不再触发整篇文档的
        embedding API 调用（这是本链路唯一的真实外部计费点）。

        返回值即调用方检索所需的那份向量：比对阶段只读 kp_id 列（不反序列化向量），
        同步后整取一次返回。旧实现返回条数、由调用方再查一遍，等于每问一次
        多解析一遍全部向量文本。
        """
        if course_id is None or document_id is None:
            return []
        current = self._current_kp_ids(course_id, document_id)
        if not current:
            return []  # 图谱为空（节点尚未抽取），交给关键词检索兜底

        stored = sql_db.get_embedding_kp_ids(course_id, document_id)
        # 融合状态变过（apply / revoke / 整批撤销）时也要核对一次：
        # epoch 是权威且顺序无关的信号，不依赖图库可用性。
        if stored != current or self._fusion_epoch_changed(course_id, document_id):
            self.sync_index(course_id, document_id, current=current, stored=stored)
        return sql_db.get_embeddings_by_document(course_id, document_id)

    def sync_index(self, course_id, document_id, current=None, stored=None) -> dict:
        """增量同步向量索引：只删过期、只补缺失，返回 `{added, removed, kept}`。

        与 `build_index`（全量重建）的分工：
        - **重新抽取**：kp_id 全换，走 `build_index` 逻辑更简单、也不会漏；
        - **教师改一个知识点 / 融合 apply-revoke**：kp_id 集合只变少数几个 → 走这里。

        注意：**描述文本变化不会改变 kp_id 集合**，本案靠编辑处显式
        `delete_embeddings_by_document` 失效（7 个调用点即失效收口），
        本方法不负责文本级比对——否则每次问答都要把全部文本重新哈希一遍。
        """
        if not self.embedding.available:
            return {"added": 0, "removed": 0, "kept": 0}

        current = self._current_kp_ids(course_id, document_id) if current is None else set(current)
        stored = (sql_db.get_embedding_kp_ids(course_id, document_id)
                  if stored is None else set(stored))

        to_remove = stored - current
        to_add = current - stored

        if to_remove:
            sql_db.delete_kp_embeddings(course_id, document_id, sorted(to_remove))
            vector_index.invalidate(kind="kp", course_id=course_id)

        added = 0
        if to_add:
            recs = [r for r in self._load_kp_records(course_id, document_id)
                    if r.get("kp_id") in to_add]
            if recs:
                texts = [
                    f"{r.get('category') or '概念'}：{r.get('name') or ''}。{r.get('description') or ''}"
                    for r in recs
                ]
                vecs = self.embedding.embed(texts)
                for r, vec in zip(recs, vecs):
                    sql_db.upsert_kp_embedding(course_id, document_id, r.get("kp_id"), vec)
                added = len(recs)
                vector_index.invalidate(kind="kp", course_id=course_id)

        # 即使本次没有增删（集合碰巧一致、只是 epoch 领先），也要记下 epoch，
        # 否则 `ensure_index` 会每次问答都判定「epoch 变了」而反复同步。
        scope = sql_db.fusion_scope_get(course_id, document_id)
        if scope:
            sql_db.fusion_scope_mark_indexed(course_id, document_id, scope.get("epoch") or 0)

        return {"added": added, "removed": len(to_remove), "kept": len(stored & current)}

    @staticmethod
    def _fusion_epoch_changed(course_id, document_id) -> bool:
        """融合 epoch 是否领先于「上次建索引时的 epoch」。

        文档从未融合过（无 scope 行）时为 False —— 既避免误判，
        也**不会**为未融合的文档凭空写一行 scope 记录。
        """
        scope = sql_db.fusion_scope_get(course_id, document_id)
        if not scope:
            return False
        return scope.get("indexed_epoch") != scope.get("epoch")

    def build_index(self, course_id, document_id) -> int:
        """为文档全部知识点生成并持久化 embedding；无 key 或无知识点时返回 0。

        L2 阶段 E：知识点清单**优先取图谱**；图谱不可用、或该文档在图中查不到节点时
        **回落 `t_kp_text` 缓存**（缓存在每次图谱读取成功时覆盖写入，字段同源）。
        这让「图库宕机 + 已缓存过知识点」的环境仍能建立向量索引——此前该函数在图库不可用时
        **永远返回 0**，向量路整体失效（这正是本机 course 65 一条 kp 向量都没有的原因）。
        """
        if not self.embedding.available:
            return 0
        recs = self._load_kp_records(course_id, document_id)
        # 重建前先清空该文档的旧向量：kp_id 已变的过期行若留着，
        # 上面的 kp_id 集合比对会永远判定「不一致」，每次提问都重复重建
        sql_db.delete_embeddings_by_document(course_id, document_id)
        if not recs:
            return 0
        texts = [
            f"{r.get('category') or '概念'}：{r.get('name') or ''}。{r.get('description') or ''}"
            for r in recs
        ]
        vecs = self.embedding.embed(texts)
        for r, vec in zip(recs, vecs):
            sql_db.upsert_kp_embedding(course_id, document_id, r.get("kp_id"), vec)
        # 记下「本次索引是按哪个融合 epoch 建的」，供 ensure_index 判新鲜度。
        # 只在文档已有 scope 行时写 —— 未融合过的文档不该因为建索引而凭空多出一行。
        scope = sql_db.fusion_scope_get(course_id, document_id)
        if scope:
            sql_db.fusion_scope_mark_indexed(course_id, document_id, scope.get("epoch") or 0)
        # L2 阶段 E：写完失效检索层缓存（同进程立即生效；跨进程由 VECTOR_CACHE_TTL 收敛）
        vector_index.invalidate(kind="kp", course_id=course_id)
        return len(recs)

    @classmethod
    def _load_kp_records(cls, course_id, document_id) -> list:
        """知识点清单：图谱优先 → 回落 `t_kp_text` 缓存（含 name / category / description）。

        **两条路径都扣除已被融合折掉的源节点**，否则问答仍会把「BFS」和
        「广度优先搜索」当成两个知识点重复召回，与图谱上看到的对不上。
        """
        fused = cls._fused_sources(course_id, document_id)
        doc_filter = ", document_id: $did" if document_id is not None else ""
        params = {"cid": course_id}
        if document_id is not None:
            params["did"] = document_id
        try:
            recs = db.query(
                "MATCH (n:KnowledgePoint {course_id: $cid" + doc_filter + "}) "
                "RETURN n.kp_id AS kp_id, n.name AS name, n.category AS category, "
                "n.description AS description", params)
            if recs:
                return [r for r in recs if r.get("kp_id") not in fused]
        except Exception:
            pass                                   # 图库不可用 → 静默回落缓存
        try:
            rows = sql_db.list_kp_text(course_id, document_id=document_id)
        except Exception:
            return []
        return [{"kp_id": r["kp_id"], "name": r.get("name"), "category": r.get("category"),
                 "description": r.get("description")}
                for r in rows if r.get("kp_id") not in fused]
