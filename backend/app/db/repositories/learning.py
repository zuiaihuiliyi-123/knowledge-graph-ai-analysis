"""学习记录、知识点收藏与知识点文本缓存。"""

from ..connection import _now

class LearningMixin:
    """学习记录、知识点收藏与知识点文本缓存。"""


    # ---------- 学习记录 ----------

    def upsert_learning_record(self, user_id: int, course_id: int, document_id, kp_id: str,
                               status: str = "MASTERED", mastery_level: int = 100,
                               source: str = "MANUAL", last_learned_at: str = None,
                               kp_name: str = None) -> int:
        """
        写入学习记录；依赖 UNIQUE(user_id, course_id, kp_id) 冲突时更新，
        保证同一学生、同一课程、同一知识点仅一条记录。

        Phase 8B：document_id 作为业务作用域字段写入（kp_id 全局唯一，UNIQUE 无需改为四列；
        具体见规划文档「数据库约束」）。注：ON CONFLICT DO UPDATE 为 SQLite 语法，
        迁 MySQL 时改为 ON DUPLICATE KEY UPDATE。

        kp_name 是知识点名快照：重新抽取知识点会整体更换 kp_id，仅凭 kp_id 无法把
        旧掌握记录映射回新节点；写入时尽量带上名字（取不到时保留旧值）。
        """
        now = _now()
        sql = """
        INSERT INTO t_learning_record
            (user_id, course_id, document_id, kp_id, kp_name, status, mastery_level, source,
             last_learned_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, course_id, kp_id) DO UPDATE SET
            document_id = excluded.document_id,
            kp_name = COALESCE(excluded.kp_name, t_learning_record.kp_name),
            status = excluded.status,
            mastery_level = excluded.mastery_level,
            source = excluded.source,
            last_learned_at = excluded.last_learned_at,
            updated_at = excluded.updated_at
        """
        return self._execute(sql, (user_id, course_id, document_id, kp_id, kp_name, status,
                                   mastery_level, source, last_learned_at, now, now))


    def list_records_by_user_course(self, user_id: int, course_id: int, document_id=None) -> list:
        """查询某用户某文档的学习记录；document_id 为 None 时退化为课程级（教师监测汇总）"""
        if document_id is not None:
            return self._query(
                "SELECT * FROM t_learning_record WHERE user_id = ? AND course_id = ? "
                "AND document_id = ? ORDER BY record_id",
                (user_id, course_id, document_id),
            )
        return self._query(
            "SELECT * FROM t_learning_record WHERE user_id = ? AND course_id = ? "
            "ORDER BY record_id",
            (user_id, course_id),
        )


    def list_records_by_user(self, user_id: int) -> list:
        return self._query(
            "SELECT * FROM t_learning_record WHERE user_id = ? ORDER BY course_id, record_id",
            (user_id,),
        )


    def list_records_by_course(self, course_id: int) -> list:
        """查询某课程下全部学习记录（教师查看班级学习情况）"""
        return self._query(
            "SELECT * FROM t_learning_record WHERE course_id = ? ORDER BY user_id, record_id",
            (course_id,),
        )


    def delete_learning_record(self, user_id: int, course_id: int, document_id, kp_id: str) -> int:
        """删除某用户某文档某知识点的学习记录（用于取消掌握标记）"""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM t_learning_record "
                "WHERE user_id = ? AND course_id = ? AND document_id = ? AND kp_id = ?",
                (user_id, course_id, document_id, kp_id),
            )
            conn.commit()
            return cur.rowcount

    def delete_learning_records_by_name(self, user_id: int, course_id: int,
                                        document_id, kp_name: str) -> int:
        """按知识点名删除学习记录（撤销掌握时的兜底）。

        重新抽取知识点会更换 kp_id：前端按名字快照把旧记录显示为「已掌握」，
        学生点「取消掌握」时带来的却是新 kp_id，按 kp_id 删不到任何行。
        这里按同名补删，避免「取消失败却看起来成功」。
        """
        if not kp_name:
            return 0
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM t_learning_record "
                "WHERE user_id = ? AND course_id = ? AND document_id = ? AND kp_name = ?",
                (user_id, course_id, document_id, kp_name),
            )
            conn.commit()
            return cur.rowcount


    # ---------- 学生收藏（收藏 = 个人知识点书签，独立于学习状态） ----------

    def add_favorite(self, user_id: int, course_id: int, document_id, kp_id: str,
                     kp_name: str = None) -> bool:
        """新增收藏（INSERT OR IGNORE 幂等）；返回是否新插入（True=新增，False=已存在未重复）。

        Phase 8B：document_id 作为业务作用域字段写入（kp_id 全局唯一，UNIQUE 无需改为四列）。
        kp_name 是知识点名快照：重新抽取会整体更换 kp_id，仅凭 kp_id 无法找回原知识点。
        已存在的行也顺带补名字（旧数据可能没有快照），不改变幂等语义。
        """
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO t_student_favorite "
                "(user_id, course_id, document_id, kp_id, kp_name) VALUES (?, ?, ?, ?, ?)",
                (user_id, course_id, document_id, kp_id, kp_name),
            )
            if cur.rowcount == 0 and kp_name:
                conn.execute(
                    "UPDATE t_student_favorite SET kp_name = ? "
                    "WHERE user_id = ? AND course_id = ? AND kp_id = ? AND kp_name IS NULL",
                    (kp_name, user_id, course_id, kp_id),
                )
            conn.commit()
            return cur.rowcount > 0

    def update_favorite_kp_name(self, user_id: int, course_id: int, document_id,
                                kp_id: str, kp_name: str) -> int:
        """回填收藏的知识点名快照（旧数据升级用；幂等）"""
        if not kp_name:
            return 0
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE t_student_favorite SET kp_name = ? "
                "WHERE user_id = ? AND course_id = ? AND document_id = ? AND kp_id = ? "
                "AND (kp_name IS NULL OR kp_name = '')",
                (kp_name, user_id, course_id, document_id, kp_id),
            )
            conn.commit()
            return cur.rowcount


    def remove_favorite(self, user_id: int, course_id: int, document_id, kp_id: str) -> int:
        """取消收藏，返回删除条数"""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM t_student_favorite "
                "WHERE user_id = ? AND course_id = ? AND document_id = ? AND kp_id = ?",
                (user_id, course_id, document_id, kp_id),
            )
            conn.commit()
            return cur.rowcount


    def list_favorites_by_user_course(self, user_id: int, course_id: int, document_id=None) -> list:
        """查询某用户某文档的收藏（按收藏时间倒序）；document_id 为 None 时退化为课程级"""
        if document_id is not None:
            return self._query(
                "SELECT kp_id, kp_name, course_id, document_id, created_at "
                "FROM t_student_favorite "
                "WHERE user_id = ? AND course_id = ? AND document_id = ? ORDER BY id DESC",
                (user_id, course_id, document_id),
            )
        return self._query(
            "SELECT kp_id, kp_name, course_id, document_id, created_at "
            "FROM t_student_favorite "
            "WHERE user_id = ? AND course_id = ? ORDER BY id DESC",
            (user_id, course_id),
        )


    def list_favorites_by_user(self, user_id: int) -> list:
        """查询某用户全部课程的收藏（按课程 + 收藏时间倒序）"""
        return self._query(
            "SELECT kp_id, kp_name, course_id, document_id, created_at "
            "FROM t_student_favorite "
            "WHERE user_id = ? ORDER BY course_id, id DESC",
            (user_id,),
        )


    def count_favorites_by_course(self, course_id: int) -> dict:
        """按用户统计某课程的收藏数，返回 {user_id: count}（教师查看学生收藏情况）"""
        rows = self._query(
            "SELECT user_id, count(*) AS cnt FROM t_student_favorite "
            "WHERE course_id = ? GROUP BY user_id",
            (course_id,),
        )
        return {r["user_id"]: r["cnt"] for r in rows}


    def delete_learning_records_by_document(self, course_id: int, document_id) -> int:
        """删除文档全部学习记录，返回删除条数（Phase 8C 删除文档级清理）"""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM t_learning_record WHERE course_id = ? AND document_id = ?",
                (course_id, document_id),
            )
            conn.commit()
            return cur.rowcount


    def delete_favorites_by_document(self, course_id: int, document_id) -> int:
        """删除文档全部收藏，返回删除条数（Phase 8C 删除文档级清理）"""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM t_student_favorite WHERE course_id = ? AND document_id = ?",
                (course_id, document_id),
            )
            conn.commit()
            return cur.rowcount


    def delete_favorites_by_kp(self, course_id: int, document_id, kp_id: str) -> int:
        """删除某知识点的全部学生收藏，返回删除条数。

        教师删除单个知识点节点（kg_manager.delete_node）的级联收口：
        t_student_favorite.kp_id 是逻辑外键，节点从 Neo4j 删除后若不清，
        这些收藏就成了孤儿——前端「共收藏 N 个」计数照算，列表却因
        匹配不到图谱节点而少显示甚至不显示。
        """
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM t_student_favorite "
                "WHERE course_id = ? AND document_id = ? AND kp_id = ?",
                (course_id, document_id, kp_id),
            )
            conn.commit()
            return cur.rowcount


    def delete_learning_records_by_kp(self, course_id: int, document_id, kp_id: str) -> int:
        """删除某知识点的全部学生学习记录，返回删除条数。

        与 delete_favorites_by_kp 同一收口点：kp_id 失效后旧学习记录会让
        掌握数/学习进度统计虚高且对不上图谱节点，一并清理。
        """
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM t_learning_record "
                "WHERE course_id = ? AND document_id = ? AND kp_id = ?",
                (course_id, document_id, kp_id),
            )
            conn.commit()
            return cur.rowcount


    # ---------- Scope C：知识点文本缓存 + 题目向量（试题知识点自动标注） ----------

    def upsert_kp_text_batch(self, course_id: int, items: list) -> int:
        """批量写入/更新知识点文本缓存，返回写入条数。

        items: [{"kp_id", "name", "category", "description", "document_id"}]
        每次从图谱成功取到清单后调用（覆盖式更新），供图谱不可用时降级使用。
        """
        rows = [
            (course_id, it.get("kp_id"), it.get("document_id"), it.get("name") or "",
             it.get("category") or "", it.get("description") or "", _now())
            for it in items
            if it.get("kp_id")
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO t_kp_text (course_id, kp_id, document_id, name, category, "
                "description, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(course_id, kp_id) DO UPDATE SET "
                "document_id = excluded.document_id, name = excluded.name, "
                "category = excluded.category, description = excluded.description, "
                "updated_at = excluded.updated_at",
                rows,
            )
            conn.commit()
        return len(rows)


    def list_kp_text(self, course_id: int, document_id=None) -> list:
        """读取知识点文本缓存；document_id 传入时只取该文档（缓存无 document_id 的也一并返回）"""
        where, params = ["course_id = ?"], [course_id]
        if document_id is not None:
            where.append("(document_id = ? OR document_id IS NULL)")
            params.append(document_id)
        return self._query(
            f"SELECT kp_id, document_id, name, category, description FROM t_kp_text "
            f"WHERE {' AND '.join(where)} ORDER BY name",
            tuple(params),
        )


    def count_kp_text(self, course_id: int) -> int:
        return self._query_one(
            "SELECT count(*) AS cnt FROM t_kp_text WHERE course_id = ?", (course_id,),
        )["cnt"]
