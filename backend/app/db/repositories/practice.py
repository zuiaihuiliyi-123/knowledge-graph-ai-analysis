"""答题记录、判分与题目收藏。"""

from ..schema import (ANSWER_GRADE_STATUS, MANUAL_GRADE_TYPES)
from ..connection import _now

class PracticeMixin:
    """答题记录、判分与题目收藏。"""

    def list_practice_questions(self, course_id: int, document_id=None, kp_id: str = None,
                                kp_ids=None,
                                q_type: str = None, limit: int = 10,
                                exclude_ids=None, auto_grade_only: bool = True) -> list:
        """出题查询：仅取启用题目，随机排序；document_id 传入时含「该文档题 + 课程通用题」。

        auto_grade_only=True（默认）：主观题（FILL/ESSAY）不进随机出题池——它们要教师批改、
        作答耗时长，混进随机卷会拉垮体验；只有显式指定 q_type 时才取（"不硬插入"策略）。
        """
        where, params = ["course_id = ?", "is_active = 1"], [course_id]
        if document_id is not None:
            where.append("(document_id = ? OR document_id IS NULL)")
            params.append(document_id)
        # 知识点过滤（关联表口径；本查询 FROM 无别名，qualifier 传表名）
        self._kp_filter(where, params, kp_id, kp_ids, qualifier="t_question")
        if q_type:
            where.append("q_type = ?")
            params.append(q_type)
        elif auto_grade_only:
            marks = ", ".join("?" for _ in MANUAL_GRADE_TYPES)
            where.append(f"q_type NOT IN ({marks})")
            params.extend(MANUAL_GRADE_TYPES)
        if exclude_ids:
            placeholders = ",".join("?" for _ in exclude_ids)
            where.append(f"question_id NOT IN ({placeholders})")
            params.extend(list(exclude_ids))
        params.append(limit)
        return self._query(
            f"SELECT * FROM t_question WHERE {' AND '.join(where)} "
            f"ORDER BY RANDOM() LIMIT ?",
            tuple(params),
        )


    # ---------- 题库：学生答题记录 ----------

    def add_answer_record(self, user_id: int, course_id: int, document_id, question_id: int,
                          user_answer, is_correct: bool, score: float = 0,
                          grade_source: str = "AUTO", grade_status: str = "GRADED") -> int:
        """追加一条答题记录（不覆盖历史，同一题可多次作答）。

        grade_status 默认 'GRADED'（客观题提交即判分，与历史行为一致）；
        主观题（FILL/ESSAY）由上层显式传 'PENDING'——提交只落库、不判分，等待教师批改。
        """
        if grade_status not in ANSWER_GRADE_STATUS:
            grade_status = "GRADED"
        return self._execute(
            "INSERT INTO t_answer_record "
            "(user_id, course_id, document_id, question_id, user_answer, is_correct, "
            " score, grade_source, grade_status, answered_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, course_id, document_id, question_id, self._json_text(user_answer),
             1 if is_correct else 0, score, grade_source, grade_status, _now()),
        )


    def list_answer_records(self, user_id: int, course_id: int = None, document_id=None,
                            only_wrong: bool = False, include_pending: bool = True) -> list:
        """查询答题记录（时间倒序）；only_wrong=True 仅返回答错的记录（错题本原始数据）。

        include_pending=False 时排除「待批改」的主观题记录——错题本、正确率、掌握度
        都必须用这个口径，否则"交了但老师还没批"会被当成答错。
        """
        where, params = ["user_id = ?"], [user_id]
        if course_id is not None:
            where.append("course_id = ?")
            params.append(course_id)
        if document_id is not None:
            where.append("document_id = ?")
            params.append(document_id)
        if only_wrong:
            where.append("is_correct = 0")
        if not include_pending:
            where.append("grade_status = 'GRADED'")
        return self._query(
            f"SELECT * FROM t_answer_record WHERE {' AND '.join(where)} ORDER BY record_id DESC",
            tuple(params),
        )


    def list_answer_records_by_course(self, course_id: int) -> list:
        """某课程全部答题记录（教师端统计学生练习情况）"""
        return self._query(
            "SELECT * FROM t_answer_record WHERE course_id = ? ORDER BY user_id, record_id",
            (course_id,),
        )


    def count_answers_by_course(self, course_id: int) -> int:
        return self._query_one(
            "SELECT count(*) AS cnt FROM t_answer_record WHERE course_id = ?", (course_id,),
        )["cnt"]


    def count_answers_by_question(self, question_id: int) -> int:
        """某题被作答次数（题目是否可物理删除的判据：已作答过则只允许软删）"""
        return self._query_one(
            "SELECT count(*) AS cnt FROM t_answer_record WHERE question_id = ?", (question_id,),
        )["cnt"]


    def get_answer_record(self, record_id: int) -> dict:
        """取单条作答记录（批改前校验归属与题型用）"""
        return self._query_one(
            "SELECT * FROM t_answer_record WHERE record_id = ?", (record_id,)
        )


    # ---------- 题库：主观题批改（Scope B，就地更新 t_answer_record） ----------

    def grade_answer_record(self, record_id: int, score: float, is_correct: bool,
                            graded_by: int, comment: str = None) -> int:
        """教师批改单条作答：就地更新（分数/对错/评语/批改痕迹），返回影响行数。

        - grade_status 置 GRADED、grade_source 置 TEACHER：与客观题（AUTO）可区分；
        - 允许重批（覆盖旧分与旧评语）——这是「就地更新」方案的既定语义（不留痕）。
        """
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE t_answer_record SET score = ?, is_correct = ?, grade_status = 'GRADED', "
                "grade_source = 'TEACHER', graded_by = ?, graded_at = ?, comment = ? "
                "WHERE record_id = ?",
                (score, 1 if is_correct else 0, graded_by, _now(), comment, record_id),
            )
            conn.commit()
            return cur.rowcount


    def grade_answer_records_batch(self, record_ids: list, score: float, is_correct: bool,
                                   graded_by: int, comment: str = None) -> int:
        """批量批改（同一分数与评语），返回实际更新条数；空列表返回 0"""
        if not record_ids:
            return 0
        marks = ", ".join("?" for _ in record_ids)
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE t_answer_record SET score = ?, is_correct = ?, grade_status = 'GRADED', "
                f"grade_source = 'TEACHER', graded_by = ?, graded_at = ?, comment = ? "
                f"WHERE record_id IN ({marks})",
                (score, 1 if is_correct else 0, graded_by, _now(), comment) + tuple(record_ids),
            )
            conn.commit()
            return cur.rowcount


    def list_pending_answer_records(self, course_id: int, document_id=None, kp_id: str = None,
                                    kp_ids=None,
                                    student_id: int = None, limit: int = 50,
                                    offset: int = 0, status: str = "PENDING") -> list:
        """批改台列表（教师视角，联表带出题面 / 参考答案 / 学生信息）。

        - status='PENDING'（默认）：只取待批改，排序按 record_id 升序 = 先交先批；
        - status='GRADED'：取已批改（**限定主观题**，客观题由系统判分不属于人工批改台），
          排序按 graded_at 倒序 = 最近批改在前，供教师复查与改判；
        - status='ALL'：不过滤状态（仍按 record_id 升序）。
        document_id 口径与题目列表一致（含课程通用题）。
        参考答案随该接口下发给教师，学生端绝不可复用本方法。
        """
        where, params = ["r.course_id = ?"], [course_id]
        if status == "GRADED":
            where.append("r.grade_status = 'GRADED'")
            where.append(f"q.q_type IN ({', '.join('?' for _ in MANUAL_GRADE_TYPES)})")
            params.extend(MANUAL_GRADE_TYPES)
        elif status == "ALL":
            pass
        else:
            where.append("r.grade_status = 'PENDING'")
        if document_id is not None:
            where.append("(r.document_id = ? OR r.document_id IS NULL)")
            params.append(document_id)
        self._kp_filter(where, params, kp_id, kp_ids)
        if student_id is not None:
            where.append("r.user_id = ?")
            params.append(student_id)
        order_by = "r.graded_at DESC, r.record_id DESC" if status == "GRADED" else "r.record_id ASC"
        params.extend([limit, offset])
        return self._query(
            f"""
            SELECT r.*, q.stem AS stem, q.q_type AS q_type, q.kp_id AS kp_id,
                   q.options AS question_options,
                   q.answer AS reference_answer, q.analysis AS analysis,
                   q.difficulty AS difficulty, q.document_id AS question_document_id,
                   COALESCE(u.display_name, u.username, '') AS student_name,
                   u.username AS student_username
            FROM t_answer_record r
            JOIN t_question q ON q.question_id = r.question_id
            LEFT JOIN t_user u ON u.user_id = r.user_id
            WHERE {' AND '.join(where)}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
            """,
            tuple(params),
        )


    def count_pending_answer_records(self, course_id: int, document_id=None, kp_id: str = None,
                                     kp_ids=None,
                                     student_id: int = None, status: str = "PENDING") -> int:
        """批改台条数（与 list_pending_answer_records 同一口径，含 status 筛选）"""
        where, params = ["r.course_id = ?"], [course_id]
        if status == "GRADED":
            where.append("r.grade_status = 'GRADED'")
            where.append(f"q.q_type IN ({', '.join('?' for _ in MANUAL_GRADE_TYPES)})")
            params.extend(MANUAL_GRADE_TYPES)
        elif status == "ALL":
            pass
        else:
            where.append("r.grade_status = 'PENDING'")
        if document_id is not None:
            where.append("(r.document_id = ? OR r.document_id IS NULL)")
            params.append(document_id)
        self._kp_filter(where, params, kp_id, kp_ids)
        if student_id is not None:
            where.append("r.user_id = ?")
            params.append(student_id)
        return self._query_one(
            f"SELECT count(*) AS cnt FROM t_answer_record r "
            f"JOIN t_question q ON q.question_id = r.question_id "
            f"WHERE {' AND '.join(where)}",
            tuple(params),
        )["cnt"]


    def answer_grading_summary(self, course_id: int) -> dict:
        """批改进度汇总：{pending, graded, manual_total, auto_total, avg_score}

        - manual_total = 主观题（FILL/ESSAY）作答总数，auto_total = 客观题作答总数；
        - avg_score 只统计已批改记录：未批改记录的 0 分是占位值，不能拉低平均分。
        """
        rows = self._query(
            """
            SELECT q.q_type AS q_type, r.grade_status AS grade_status,
                   count(*) AS cnt, AVG(r.score) AS avg_score
            FROM t_answer_record r
            JOIN t_question q ON q.question_id = r.question_id
            WHERE r.course_id = ?
            GROUP BY q.q_type, r.grade_status
            """,
            (course_id,),
        )
        summary = {"pending": 0, "graded": 0, "manual_total": 0, "auto_total": 0,
                   "avg_score": 0.0}
        weighted, scored = 0.0, 0
        for r in rows:
            if r["grade_status"] == "PENDING":
                summary["pending"] += r["cnt"]
            else:
                summary["graded"] += r["cnt"]
                if r["avg_score"] is not None:
                    weighted += r["avg_score"] * r["cnt"]
                    scored += r["cnt"]
            if r["q_type"] in MANUAL_GRADE_TYPES:
                summary["manual_total"] += r["cnt"]
            else:
                summary["auto_total"] += r["cnt"]
        summary["avg_score"] = round(weighted / scored, 1) if scored else 0.0
        return summary


    def count_answers_grouped_by_course(self) -> dict:
        """按课程统计答题总数，返回 {course_id: count}"""
        rows = self._query(
            "SELECT course_id, count(*) AS cnt FROM t_answer_record GROUP BY course_id",
        )
        return {r["course_id"]: r["cnt"] for r in rows}


    # ---------- 题库：学生题目收藏（独立于知识点收藏 t_student_favorite） ----------

    def add_question_favorite(self, user_id: int, course_id: int, question_id: int) -> bool:
        """新增题目收藏（INSERT OR IGNORE 幂等）；返回是否新插入（True=新增，False=已存在）"""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO t_question_favorite (user_id, course_id, question_id) "
                "VALUES (?, ?, ?)",
                (user_id, course_id, question_id),
            )
            conn.commit()
            return cur.rowcount > 0


    def remove_question_favorite(self, user_id: int, course_id: int, question_id: int) -> int:
        """取消题目收藏，返回删除条数"""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM t_question_favorite "
                "WHERE user_id = ? AND course_id = ? AND question_id = ?",
                (user_id, course_id, question_id),
            )
            conn.commit()
            return cur.rowcount


    def list_question_favorites(self, user_id: int, course_id: int) -> list:
        """查询某用户某课程的题目收藏（按收藏时间倒序）"""
        return self._query(
            "SELECT question_id, course_id, created_at FROM t_question_favorite "
            "WHERE user_id = ? AND course_id = ? ORDER BY id DESC",
            (user_id, course_id),
        )


    def list_question_favorite_ids(self, user_id: int, course_id: int) -> set:
        """某用户某课程已收藏的 question_id 集合（出题时回填 is_favorited 标记）"""
        rows = self._query(
            "SELECT question_id FROM t_question_favorite WHERE user_id = ? AND course_id = ?",
            (user_id, course_id),
        )
        return {r["question_id"] for r in rows}


    def count_question_favorites_grouped(self, course_id: int) -> dict:
        """按题统计某课程的收藏数，返回 {question_id: count}（教师端「题目收藏情况」）"""
        rows = self._query(
            "SELECT question_id, count(*) AS cnt FROM t_question_favorite "
            "WHERE course_id = ? GROUP BY question_id",
            (course_id,),
        )
        return {r["question_id"]: r["cnt"] for r in rows}


    def list_question_favorite_users(self, course_id: int, question_id: int = None) -> list:
        """某课程（或某题）的收藏明细：谁收藏了哪道题，[{user_id, question_id, created_at}]"""
        if question_id is not None:
            return self._query(
                "SELECT user_id, question_id, created_at FROM t_question_favorite "
                "WHERE course_id = ? AND question_id = ? ORDER BY id DESC",
                (course_id, question_id),
            )
        return self._query(
            "SELECT user_id, question_id, created_at FROM t_question_favorite "
            "WHERE course_id = ? ORDER BY id DESC",
            (course_id,),
        )
