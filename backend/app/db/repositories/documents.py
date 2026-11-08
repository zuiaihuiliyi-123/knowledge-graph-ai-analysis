"""文档元数据、处理任务与统计。"""

from ..connection import _now

class DocumentsMixin:
    """文档元数据、处理任务与统计。"""


    # ---------- 文档 ----------

    def create_document(self, course_id: int, uploader_id: int, file_name: str,
                        file_type: str, file_size: int,
                        file_sha256: str = None, file_path: str = None) -> int:
        return self._execute(
            "INSERT INTO t_document "
            "(course_id, uploader_id, file_name, file_type, file_size, file_sha256, file_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (course_id, uploader_id, file_name, file_type, file_size, file_sha256, file_path),
        )


    def update_document(self, doc_id: int, **fields) -> None:
        """
        更新文档表字段（字段白名单防注入），自动刷新 updated_at。
        示例：update_document(doc_id, parse_status='PARSED', entity_count=12)

        抽取起止时刻在此**收口**：只要本次写了 extract_status，就在同一句 UPDATE 里
        顺带打戳，调用方无需（也无法）自己传这两个字段：
          - 置 EXTRACTING：记 extract_started_at，并清空上一轮的 extract_finished_at
          - 置 COMPLETED / FAILED：仅当 extract_started_at 非空时记 extract_finished_at
            （在解析阶段就失败、从未开始抽取的任务保持 NULL，前端显示「—」）
        之所以放在这里而不是 document_service，是因为后者有多条失败返回路径，
        每新增一条都要记得补一次打戳；收口后不存在「漏写」这种失败方式。
        """
        allowed = {"parse_status", "extract_status", "graph_status", "error_message",
                   "chunk_count", "entity_count", "relation_count", "vector_collection",
                   "file_path"}
        sets, params = [], []
        for key, val in fields.items():
            if key not in allowed:
                continue
            sets.append(f"{key} = ?")
            params.append(val)
        if not sets:
            return
        status = fields.get("extract_status")
        if status == "EXTRACTING":
            sets.append("extract_started_at = ?")
            params.append(_now())
            sets.append("extract_finished_at = NULL")
        elif status in ("COMPLETED", "FAILED"):
            # 判空放在 SQL 表达式里，省掉一次额外读；未开始抽取的任务不会被写上假结束时刻
            sets.append("extract_finished_at = CASE WHEN extract_started_at IS NOT NULL "
                        "THEN ? ELSE extract_finished_at END")
            params.append(_now())
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(doc_id)
        self._execute(f"UPDATE t_document SET {', '.join(sets)} WHERE doc_id = ?", tuple(params))


    def get_document(self, doc_id: int) -> dict:
        return self._query_one("SELECT * FROM t_document WHERE doc_id = ?", (doc_id,))


    # ---------- 文档处理任务（阶段 4：断点续跑） ----------

    # 允许由调用方写入的字段（attempts / 时间戳由本方法自己维护，防注入）
    TASK_FIELDS = ("stage", "status", "artifacts", "heartbeat_at", "error_message")


    def upsert_document_task(self, doc_id: int, course_id: int, *,
                             bump_attempts: bool = False, **fields) -> None:
        """写入/更新「这篇文档当前的处理任务」。

        `bump_attempts=True` 表示「这一轮是新的尝试」，attempts +1；
        其余字段（stage/status/artifacts/error_message）按传入值覆盖。
        表是「当前任务」而非流水：同一文档永远只有一行。
        """
        allowed = {k: v for k, v in fields.items() if k in self.TASK_FIELDS}
        cols = ["doc_id", "course_id", "attempts", *allowed]
        params = [int(doc_id), int(course_id), 1 if bump_attempts else 0,
                  *[allowed[k] for k in allowed]]
        sets = [f"{k} = excluded.{k}" for k in allowed]
        if bump_attempts:
            sets.append("attempts = t_document_task.attempts + 1")
        sets.append("updated_at = ?")
        params.append(_now())
        placeholders = ", ".join("?" * len(cols))
        self._execute(
            f"INSERT INTO t_document_task ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(doc_id) DO UPDATE SET {', '.join(sets)}",
            tuple(params),
        )


    def get_document_task(self, doc_id: int) -> dict:
        return self._query_one("SELECT * FROM t_document_task WHERE doc_id = ?", (doc_id,))


    def delete_document_task(self, doc_id: int) -> None:
        self._execute("DELETE FROM t_document_task WHERE doc_id = ?", (int(doc_id),))


    def list_running_tasks(self, stale_before: str = None) -> list:
        """仍在 RUNNING 的任务；传 `stale_before` 只取心跳早于该时刻的（排查卡死）。"""
        if stale_before:
            return self._query(
                "SELECT * FROM t_document_task WHERE status = 'RUNNING' "
                "AND (heartbeat_at IS NULL OR heartbeat_at < ?) ORDER BY doc_id",
                (stale_before,),
            )
        return self._query(
            "SELECT * FROM t_document_task WHERE status = 'RUNNING' ORDER BY doc_id")


    def list_documents_by_course(self, course_id: int) -> list:
        return self._query(
            "SELECT * FROM t_document WHERE course_id = ? ORDER BY doc_id", (course_id,),
        )


    def get_document_by_sha256(self, course_id: int, file_sha256: str,
                               usable_only: bool = False) -> dict:
        """按内容哈希查同课程里最近一次上传的相同内容文档（上传去重）。

        去重范围刻意限定在同一课程内：不同课程的同名文件是两份独立教学资料，
        各自建图，符合「不跨课程融合」的约束。

        usable_only=True 时排除已判失败的文档（parse / extract / graph 任一
        FAILED）。失败文档不能挡住教师重新上传同一文件——否则「重试」只能改文件名，
        而旧实现正是让一条 FAILED 记录永久占住这个哈希。
        """
        if not file_sha256:
            return None
        where = ["course_id = ?", "file_sha256 = ?"]
        if usable_only:
            where.append("COALESCE(parse_status, '') <> 'FAILED'")
            where.append("COALESCE(extract_status, '') <> 'FAILED'")
            where.append("COALESCE(graph_status, '') <> 'FAILED'")
        return self._query_one(
            f"SELECT * FROM t_document WHERE {' AND '.join(where)} "
            f"ORDER BY doc_id DESC LIMIT 1",
            (course_id, file_sha256),
        )


    def list_all_documents(self) -> list:
        """全部文档记录（按课程 + 文档号排序）。

        供跨课程的批量维护脚本使用（如 scripts/rebuild_missing_graphs.py
        扫描「记录说抽取完成、Neo4j 里却没有节点」的文档）。
        """
        return self._query("SELECT * FROM t_document ORDER BY course_id, doc_id")


    def count_documents_by_course(self, course_id: int) -> int:
        return self._query_one(
            "SELECT count(*) AS cnt FROM t_document WHERE course_id = ?", (course_id,),
        )["cnt"]


    def count_documents_grouped(self, course_ids: list = None) -> dict:
        """按课程统计文档数，返回 {course_id: count}。

        course_ids 给定时只统计这些课程（课程列表按当前页 ID 过滤，避免全表 GROUP BY）。
        None / 空列表的区别与 member_counts_by_course 一致：None=全部，[]=无结果。
        """
        if course_ids is not None and not course_ids:
            return {}
        if course_ids:
            rows = self._query(
                f"SELECT course_id, count(*) AS cnt FROM t_document "
                f"WHERE course_id IN ({','.join('?' * len(course_ids))}) "
                f"GROUP BY course_id",
                tuple(course_ids),
            )
        else:
            rows = self._query(
                "SELECT course_id, count(*) AS cnt FROM t_document GROUP BY course_id")
        return {r["course_id"]: r["cnt"] for r in rows}


    def delete_document(self, doc_id: int) -> int:
        """删除单个文档记录（仅删除 t_document 行；图谱/向量/学习/收藏清理由上层 Phase 8/9 完成）"""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM t_document WHERE doc_id = ?", (doc_id,))
            conn.commit()
            return cur.rowcount
