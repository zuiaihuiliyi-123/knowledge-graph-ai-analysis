"""知识点/题目向量与 BLOB 序列化。"""

import json
from ..connection import (_now, _vec_to_blob)

class EmbeddingsMixin:
    """知识点/题目向量与 BLOB 序列化。"""


    # ---------- 知识点向量（RAG 向量检索） ----------

    def upsert_kp_embedding(self, course_id: int, document_id, kp_id: str, embedding: list,
                            model: str = None) -> int:
        """写入/更新知识点向量（**双写**：`embedding` JSON 兼容 + `embedding_blob` float32 快读）

        L2 阶段 E：blob 列用于检索（体积 5.2×↓、免 JSON 解析）；JSON 列保留以兼容旧读取方。

        model：生成该向量的 embedding 模型名。写入方必须传当前配置值；
        留空表示未知（兼容旧调用方），`ensure_index` 会把 NULL 视为过期并重建。
        """
        return self._execute(
            "INSERT INTO t_kp_embedding "
            "(course_id, document_id, kp_id, embedding, embedding_blob, model, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(course_id, kp_id) DO UPDATE SET "
            "document_id = excluded.document_id, "
            "embedding = excluded.embedding, embedding_blob = excluded.embedding_blob, "
            "model = excluded.model, "
            "updated_at = excluded.updated_at",
            (course_id, document_id, kp_id, json.dumps(embedding),
             _vec_to_blob(embedding), model, _now()),
        )


    def get_embeddings_by_document(self, course_id: int, document_id) -> list:
        """返回文档全部知识点向量，[{kp_id, embedding(list[float])}]"""
        rows = self._query(
            "SELECT kp_id, embedding FROM t_kp_embedding WHERE course_id = ? AND document_id = ?",
            (course_id, document_id),
        )
        result = []
        for r in rows:
            try:
                vec = json.loads(r["embedding"])
            except (ValueError, TypeError):
                continue
            result.append({"kp_id": r["kp_id"], "embedding": vec})
        return result


    def get_embedding_kp_ids(self, course_id: int, document_id) -> set:
        """该文档已存向量的 kp_id 集合（向量索引新鲜度比对专用）。

        与 get_embeddings_by_document 的区别：不读 embedding 列。索引新鲜度只需要
        比对 kp_id 集合，若为此整表取回向量文本并反序列化，纯属浪费（实测 69 条
        向量下，反序列化占该步耗时的约 70%）。
        """
        rows = self._query(
            "SELECT kp_id FROM t_kp_embedding WHERE course_id = ? AND document_id = ?",
            (course_id, document_id),
        )
        return {r["kp_id"] for r in rows}


    def get_embedding_models(self, course_id: int, document_id=None) -> set:
        """该作用域内已存知识点向量分别由哪些模型生成（NULL 表示历史未知）。

        用于 `ensure_index` 的模型漂移判定：集合里只要出现非当前模型（含 NULL），
        就说明索引是旧模型产物，必须整篇重建，而不是按 kp_id 增量同步。
        """
        where, params = ["course_id = ?"], [course_id]
        if document_id is not None:
            where.append("document_id = ?")
            params.append(document_id)
        rows = self._query(
            f"SELECT DISTINCT model FROM t_kp_embedding WHERE {' AND '.join(where)}",
            tuple(params),
        )
        return {r["model"] for r in rows}


    def delete_kp_embeddings(self, course_id: int, document_id, kp_ids) -> int:
        """按 kp_id 精确删除向量行，返回删除条数（增量同步专用）。

        为什么需要它：`delete_embeddings_by_document` 是「整篇清空再重建」；
        教师只改/删一两个知识点、或融合 apply/revoke 只让少数节点进出时，
        整篇重建会重新调用一次外部 embedding API（69 条向量 ≈ 一次真实计费请求）。
        """
        ids = [str(k) for k in (kp_ids or []) if k]
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        sql = ("DELETE FROM t_kp_embedding "
               f"WHERE course_id = ? AND document_id = ? AND kp_id IN ({placeholders})")
        with self._connect() as c:
            cur = c.execute(sql, (course_id, document_id, *ids))
            c.commit()
            return cur.rowcount


    def delete_embeddings_by_document(self, course_id: int, document_id, conn=None) -> int:
        """删除文档全部知识点向量，返回删除条数。

        conn 传入时只用不提交，供融合的 apply/revoke 把「写入映射 + epoch+1 + 失效向量」
        组合进同一个事务 —— 否则向量删了、映射没写成，就会留下不一致状态。
        """
        sql = "DELETE FROM t_kp_embedding WHERE course_id = ? AND document_id = ?"
        params = (course_id, document_id)
        if conn is not None:
            return conn.execute(sql, params).rowcount
        with self._connect() as c:
            cur = c.execute(sql, params)
            c.commit()
            return cur.rowcount


    def get_embeddings_by_course(self, course_id: int) -> list:
        """返回该课程全部知识点向量（跨文档），[{kp_id, document_id, embedding(list)}]"""
        rows = self._query(
            "SELECT kp_id, document_id, embedding FROM t_kp_embedding WHERE course_id = ?",
            (course_id,),
        )
        for r in rows:
            r["embedding"] = self._loads_json(r["embedding"])
        return rows


    # ---------- L2 阶段 E：向量的 BLOB 快读通道（检索层用，免 JSON 解析） ----------

    def get_kp_embedding_blobs(self, course_id: int, document_id=None,
                               model: str = None) -> list:
        """取知识点向量的 **float32 原始字节**：`[{kp_id, document_id, blob}]`。

        优先读 `embedding_blob`；该列为空（历史行尚未回填）时回落解析 `embedding`(JSON)，
        保证**回填进度不影响读取正确性**。检索层直接 `np.frombuffer` 成矩阵，零解析开销。

        model 传入时只取该模型生成的行：换模型后旧行与新查询向量维度/语义空间都不同，
        混在一起会让检索结果不可信（维度不同还会让矩阵乘法直接报错）。
        """
        where, params = ["course_id = ?"], [course_id]
        if document_id is not None:
            where.append("document_id = ?")
            params.append(document_id)
        if model is not None:
            where.append("model = ?")
            params.append(model)
        rows = self._query(
            f"SELECT kp_id, document_id, embedding_blob, embedding FROM t_kp_embedding "
            f"WHERE {' AND '.join(where)}", tuple(params))
        return self._blob_rows(rows, key="kp_id", extra="document_id")


    def get_question_embedding_blobs(self, course_id: int, document_id=None,
                                     question_ids=None, model: str = None) -> list:
        """取题目向量的 float32 原始字节：`[{question_id, blob}]`（口径同 t_question_embedding）。

        `question_ids` 传入时只取这些题——召回 R5 只需「学生最近错题的向量」。
        model 传入时只取该模型生成的行（换模型后的旧缓存不参与检索）。
        """
        where, params = ["course_id = ?"], [course_id]
        if document_id is not None:
            where.append("document_id = ?")
            params.append(document_id)
        if model is not None:
            where.append("model = ?")
            params.append(model)
        ids = [int(q) for q in (question_ids or []) if q is not None]
        if ids:
            where.append(f"question_id IN ({', '.join('?' for _ in ids)})")
            params.extend(ids)
        rows = self._query(
            f"SELECT question_id, embedding_blob, embedding FROM t_question_embedding "
            f"WHERE {' AND '.join(where)}", tuple(params))
        return self._blob_rows(rows, key="question_id")


    def _blob_rows(self, rows: list, key: str, extra: str = None) -> list:
        """含 embedding_blob/embedding 的行 → `[{key, [extra], blob}]`；blob 缺失时回落 JSON。"""
        out = []
        for r in rows:
            blob = r["embedding_blob"]
            if blob is None:
                vec = self._loads_json(r["embedding"])
                if not vec:
                    continue
                blob = _vec_to_blob(vec)
            item = {key: r[key], "blob": blob}
            if extra:
                item[extra] = r[extra]
            out.append(item)
        return out


    def count_embeddings_by_course(self, course_id: int) -> int:
        """某课程全部知识点向量数量（供整课删除前统计与报告）"""
        return self._query_one(
            "SELECT count(*) AS cnt FROM t_kp_embedding WHERE course_id = ?", (course_id,),
        )["cnt"]


    def upsert_question_embedding(self, question_id: int, course_id: int, document_id,
                                  text_hash: str, embedding: list,
                                  model: str = None) -> int:
        """写入/更新题目向量（**双写**：`embedding` JSON 兼容 + `embedding_blob` float32 快读）

        model：生成该向量的 embedding 模型名；缓存命中逻辑会比对它，
        换模型后即使题目文本没变也必须重算。
        """
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO t_question_embedding "
                "(question_id, course_id, document_id, text_hash, embedding, embedding_blob, "
                " model, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(question_id) DO UPDATE SET "
                "course_id = excluded.course_id, document_id = excluded.document_id, "
                "text_hash = excluded.text_hash, embedding = excluded.embedding, "
                "embedding_blob = excluded.embedding_blob, model = excluded.model, "
                "updated_at = excluded.updated_at",
                (question_id, course_id, document_id, text_hash,
                 self._json_text(embedding), _vec_to_blob(embedding), model, _now()),
            )
            conn.commit()
        return 1


    def delete_question_embedding(self, question_id: int) -> int:
        """删除单题向量缓存，返回影响行数。

        为什么需要单独一个入口：`t_question_embedding` 带 `text_hash`，文本变了
        `ensure_question_vectors` 会自动重算；但 **`document_id` 变了哈希不变**，
        缓存行会永久停留在旧作用域——按新文档检索向量时查不到，而 ensure 也不会重写。
        因此「题目内容或归属发生变化」时必须显式删行。
        """
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM t_question_embedding WHERE question_id = ?", (question_id,))
            conn.commit()
            return cur.rowcount


    def get_question_embedding(self, question_id: int) -> dict:
        """取单题向量行（含 text_hash，供判断是否需要重算）"""
        row = self._query_one(
            "SELECT question_id, course_id, document_id, text_hash, embedding, model "
            "FROM t_question_embedding WHERE question_id = ?", (question_id,),
        )
        if row is None:
            return None
        row["embedding"] = self._loads_json(row["embedding"])
        return row
