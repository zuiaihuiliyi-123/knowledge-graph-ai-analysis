"""题库、导入批次与题目-知识点关联。"""

import json
import uuid
from ..schema import (MANUAL_GRADE_TYPES, QUESTION_SOURCE)
from ..connection import _now

class QuestionsMixin:
    """题库、导入批次与题目-知识点关联。"""


    @staticmethod
    def _loads_json(value):
        """把库内 JSON 文本解析回对象（解析失败返回空列表）"""
        if isinstance(value, (list, dict)):
            return value
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return []


    # ---------- Scope D：试题文档导入（批次） ----------

    def create_import_batch(self, course_id: int, document_id, file_name: str,
                            source: str = "RULE", total: int = 0,
                            created_by: int = None, meta: dict = None) -> str:
        """创建导入批次，返回 batch_id（形如 imp_xxxxxxxxxxxx）"""
        batch_id = f"imp_{uuid.uuid4().hex[:12]}"
        self._execute(
            "INSERT INTO t_question_import_batch "
            "(batch_id, course_id, document_id, file_name, source, total, created_by, meta) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (batch_id, course_id, document_id, file_name, source, total, created_by,
             json.dumps(meta or {}, ensure_ascii=False)),
        )
        return batch_id


    def get_import_batch(self, batch_id: str) -> dict:
        return self._query_one(
            "SELECT * FROM t_question_import_batch WHERE batch_id = ?", (batch_id,)
        )


    def update_import_batch(self, batch_id: str, **fields) -> int:
        """更新批次（白名单字段）"""
        allowed = {"imported", "needs_review", "status", "total", "meta", "source"}
        sets, params = [], []
        for key, val in fields.items():
            if key not in allowed:
                continue
            if key == "meta" and not isinstance(val, str):
                val = json.dumps(val, ensure_ascii=False)
            sets.append(f"{key} = ?")
            params.append(val)
        if not sets:
            return 0
        params.append(batch_id)
        return self._execute(
            f"UPDATE t_question_import_batch SET {', '.join(sets)} WHERE batch_id = ?",
            tuple(params),
        )


    def list_import_batches(self, course_id: int, document_id=None, limit: int = 20) -> list:
        where, params = ["course_id = ?"], [course_id]
        if document_id is not None:
            where.append("document_id = ?")
            params.append(document_id)
        params.append(limit)
        return self._query(
            f"SELECT * FROM t_question_import_batch WHERE {' AND '.join(where)} "
            f"ORDER BY created_at DESC, batch_id DESC LIMIT ?",
            tuple(params),
        )


    def list_questions_by_batch(self, batch_id: str, page: int = 1, page_size: int = 200):
        """批次内已入库的题目（分页），返回 (total, rows)"""
        total = self._query_one(
            "SELECT count(*) AS cnt FROM t_question WHERE import_batch_id = ?", (batch_id,),
        )["cnt"]
        rows = self._query(
            "SELECT * FROM t_question WHERE import_batch_id = ? ORDER BY question_id LIMIT ? OFFSET ?",
            (batch_id, page_size, (page - 1) * page_size),
        )
        return total, rows


    def count_questions_by_import_status(self, course_id: int) -> dict:
        """课件/课程级：按导入状态统计（教师端"待复核导入题"角标）"""
        rows = self._query(
            "SELECT import_status, count(*) AS cnt FROM t_question "
            "WHERE course_id = ? AND import_batch_id IS NOT NULL GROUP BY import_status",
            (course_id,),
        )
        return {r["import_status"] or "READY": r["cnt"] for r in rows}


    @staticmethod
    def _json_text(value):
        """把 options/answer 一律序列化为 JSON 文本（None 原样返回）。

        为什么「一律 dumps」而不是「字符串原样返回」：
        - 单选答案 "A" 这类字符串不是合法 JSON，原样存库后回读 json.loads 会失败；
        - 判断题答案 "true" 恰好是合法 JSON，原样存库后回读会变成布尔 True；
        两种题型行为不一致（单选永远判错）。统一 json.dumps 后 _loads 可无损还原：
        "A" -> '"A"' -> "A"，"true" -> '"true"' -> "true"。
        """
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False)


    def create_question(self, course_id: int, document_id, kp_id, q_type: str,
                        stem: str, options, answer, analysis: str = None,
                        difficulty: int = 3, created_by: int = None,
                        source: str = "MANUAL", is_active: int = 1,
                        import_batch_id: str = None,
                        import_status: str = None,
                        kp_ids=None, kp_primary=None, kp_scores=None) -> int:
        """新增题目，返回 question_id（options/answer 自动序列化为 JSON 文本）。

        Scope D：导入的题目一律 is_active=0 进「暂存区」，并带上 import_batch_id /
        import_status（READY / NEEDS_REVIEW / ANSWER_MISSING），教师复核后才启用。

        L2 一题多 KP：`kp_ids` 是该题的完整知识点列表（`kp_id` 保留为「主知识点」兼容参数）。
        未传 kp_ids 时退回单值 kp_id，既有调用方行为不变；两种写法都会经 set_question_kps
        写关联表并把主知识点投影回 t_question.kp_id（投影与关联表必须一致）。
        """
        qid = self._execute(
            "INSERT INTO t_question "
            "(course_id, document_id, kp_id, q_type, stem, options, answer, analysis, "
            " difficulty, source, created_by, is_active, import_batch_id, import_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (course_id, document_id, kp_id, q_type, stem,
             self._json_text(options), self._json_text(answer),
             analysis, difficulty, source, created_by, 1 if is_active else 0,
             import_batch_id, import_status or "READY"),
        )
        # 只要给了 kp_ids（含空列表）或 kp_id，就走统一写入口——空列表也要走，
        # 否则「显式传 kp_ids=[] 但同时传了 kp_id」会留下投影与关联表不一致的行。
        if kp_ids is not None or kp_id:
            linked = list(kp_ids) if kp_ids is not None else [kp_id]
            self.set_question_kps(qid, linked, primary=kp_primary or kp_id,
                                  source=source, scores=kp_scores)
        return qid


    def get_question(self, question_id: int) -> dict:
        return self._query_one("SELECT * FROM t_question WHERE question_id = ?", (question_id,))


    def update_question(self, question_id: int, **fields) -> None:
        """更新题目字段（白名单，None 跳过表示不修改；options/answer 自动序列化），刷新 updated_at

        L2 一题多 KP：`kp_id` **不再**直接写列，而是转交 set_question_kps()——关联表是权威、
        `t_question.kp_id` 是其冗余投影，两者必须同事务更新。这样既有调用方（教师端编辑、
        kp_labeler 自动标注）无需改动即自动保持一致。

        知识点字段两种写法（互斥，`kp_ids` 优先）：
        - `kp_ids=[...]`（+ 可选 `kp_primary` / `kp_scores` / `kp_source`）：**全量替换**为该组知识点；
        - `kp_id="kp_x"`：单值语义，等价于 `kp_ids=["kp_x"]`；`kp_id=""` 表示解除全部关联。

        两者都不传 = 该题知识点**完全不动**。这点很关键：教师只改难度/解析时若误传 kp_id，
        会把多 KP 题塌缩成单 KP（question_service 已按此口径收口，只在 payload 显式含 kp 键时才传）。
        """
        # 知识点字段先摘出来，避免混进下面的白名单 UPDATE
        kp_ids = fields.pop("kp_ids", None)
        kp_primary = fields.pop("kp_primary", None)
        kp_scores = fields.pop("kp_scores", None)
        kp_source = fields.pop("kp_source", None)
        kp_single = fields.pop("kp_id", None)

        allowed = {"document_id", "q_type", "stem", "options", "answer",
                   "analysis", "difficulty", "source", "is_active"}
        sets, params = [], []
        for key, val in fields.items():
            if key not in allowed or val is None:
                continue
            if key in ("options", "answer"):
                val = self._json_text(val)
            sets.append(f"{key} = ?")
            params.append(val)
        if sets:
            sets.append("updated_at = ?")
            params.append(_now())
            params.append(question_id)
            self._execute(f"UPDATE t_question SET {', '.join(sets)} WHERE question_id = ?", tuple(params))

        # 知识点关联：关联表为权威，t_question.kp_id 由其投影（见 set_question_kps）
        if kp_ids is not None:
            self.set_question_kps(question_id, kp_ids, primary=kp_primary,
                                  source=kp_source or "MANUAL", scores=kp_scores)
        elif kp_single is not None:
            single = str(kp_single).strip()
            self.set_question_kps(question_id, [single] if single else [],
                                  primary=single, source=kp_source or "MANUAL")


    def set_question_document(self, question_id: int, document_id) -> int:
        """把题目挂到指定文档（document_id=None 表示改为「课程通用题」）。

        单独提供该方法是必要的：update_question 对 None 的语义是「不修改」，
        无法表达「清空 document_id」，而「改为课程通用题」是教师端的真实编辑动作。
        """
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE t_question SET document_id = ?, updated_at = ? WHERE question_id = ?",
                (document_id, _now(), question_id),
            )
            conn.commit()
            return cur.rowcount


    # ---------- L2：一题多知识点（关联表为权威，t_question.kp_id 为冗余投影） ----------

    def set_question_kps(self, question_id: int, kp_ids, primary=None,
                         source: str = "MANUAL", scores: dict = None,
                         sources: dict = None) -> int:
        """全量替换某题的「知识点关联」，并把主知识点同步投影到 t_question.kp_id。

        这是 L2 一题多 KP 的**唯一写入口**：`t_question_kp` 是权威数据，
        `t_question.kp_id` 只是它的冗余投影——两者由本方法**同事务**更新，
        因此既有读侧（`question["kp_id"]`、前端展示、统计口径）行为完全不变。

        参数：
        - `kp_ids`：知识点列表；空列表 / None 表示「解除全部关联」；
        - `primary`：主知识点，必须是 kp_ids 之一；None 或不属于列表时取首项；
        - `source`：MANUAL / AI / IMPORT（非法值回退 MANUAL，与 QUESTION_SOURCE 同口径）；
        - `scores`：`{kp_id: score}` 可选，写入关联表（kp_labeler 的标注置信度）；
        - `sources`：`{kp_id: source}` 可选，**按 kp 覆盖**来源标记。用途：合并式写入时
          保留既有行的原始来源（如教师手工挂的仍是 MANUAL），只把新增行标记为 AI；
          缺省则整批使用 `source`。非法值回退到 `source`。

        返回：写入的关联行数。

        为什么单独一个方法、而不是塞进 update_question：
        1) update_question 对 None 的语义是「不修改」，无法表达「清空全部知识点」；
        2) 「一题最多一个主知识点」是跨两张表的约束（由 uq_qkp_primary 在 DB 层兜底），
           必须在一个事务里完成「删除旧关联 + 重建新关联 + 同步投影」；
        3) 教师端穿梭框提交的就是**完整集合**，全量替换语义最清晰，无需做增量 diff。

        注意：本方法对「多 KP 题」是全量覆盖。调用方若只想改别的字段（如难度），
        **不要**传 kp 参数——否则会把多 KP 塌缩成单个（question_service 已按此口径收口）。
        """
        # 归一化：去空、去重、保序（保序让 primary 缺省取首项时符合教师排列意图）
        cleaned, seen = [], set()
        for kp in (kp_ids or []):
            kp = str(kp).strip() if kp is not None else ""
            if kp and kp not in seen:
                seen.add(kp)
                cleaned.append(kp)
        prim = str(primary).strip() if primary else ""
        if prim not in seen:
            prim = cleaned[0] if cleaned else ""
        if source not in QUESTION_SOURCE:
            source = "MANUAL"
        scores = scores or {}
        sources = sources or {}

        with self._connect() as conn:
            conn.execute("DELETE FROM t_question_kp WHERE question_id = ?", (question_id,))
            for kp in cleaned:
                # 来源标记支持按 kp 覆盖：合并式写入时用于保留既有行的原始来源
                row_source = sources.get(kp) or source
                if row_source not in QUESTION_SOURCE:
                    row_source = source
                conn.execute(
                    "INSERT INTO t_question_kp "
                    "(question_id, kp_id, score, source, is_primary) VALUES (?, ?, ?, ?, ?)",
                    (question_id, kp, scores.get(kp), row_source, 1 if kp == prim else 0),
                )
            # 冗余投影：空值统一落 NULL（与 create_question 既有口径一致；
            # 全库「未挂知识点」只有 NULL 一种形态，见 question_id=373 的历史行）
            conn.execute(
                "UPDATE t_question SET kp_id = ?, updated_at = ? WHERE question_id = ?",
                (prim or None, _now(), question_id),
            )
            conn.commit()
            return len(cleaned)


    def get_question_kps(self, question_id: int) -> list:
        """取某题的全部知识点关联，主知识点排首位：`[{kp_id, score, source, is_primary}]`"""
        return self._query(
            "SELECT kp_id, score, source, is_primary FROM t_question_kp "
            "WHERE question_id = ? ORDER BY is_primary DESC, kp_id",
            (question_id,),
        )


    def get_question_kps_map(self, question_ids) -> dict:
        """批量取多题的知识点关联：`{question_id: [kp_id, ...]}`（主知识点排首位）。

        批量版存在的意义：掌握度归因（决策 1a）需要按题展开多个 kp，
        逐题查询会退化成 N+1 次 SQL（大批量标注/评测时尤其明显）。
        """
        ids = [q for q in (question_ids or []) if q is not None]
        if not ids:
            return {}
        marks = ", ".join("?" for _ in ids)
        rows = self._query(
            f"SELECT question_id, kp_id FROM t_question_kp "
            f"WHERE question_id IN ({marks}) ORDER BY question_id, is_primary DESC, kp_id",
            tuple(ids),
        )
        out = {}
        for r in rows:
            out.setdefault(r["question_id"], []).append(r["kp_id"])
        return out


    def set_question_active(self, question_id: int, is_active: bool) -> int:
        """启用/停用题目（软删：保留学生答题记录，仅从出题池移除），返回受影响行数"""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE t_question SET is_active = ?, updated_at = ? WHERE question_id = ?",
                (1 if is_active else 0, _now(), question_id),
            )
            conn.commit()
            return cur.rowcount


    def delete_question(self, question_id: int) -> int:
        """物理删除题目及其知识点关联/向量/收藏/答题记录（先删子表再删主表，满足外键顺序）"""
        with self._connect() as conn:
            conn.execute("DELETE FROM t_question_favorite WHERE question_id = ?", (question_id,))
            conn.execute("DELETE FROM t_answer_record WHERE question_id = ?", (question_id,))
            # L2：t_question_kp 对 t_question 有物理外键，且 _connect() 已开启 foreign_keys=ON，
            # 不先删关联行会导致紧接着的删题语句直接抛 FK 约束错误。
            conn.execute("DELETE FROM t_question_kp WHERE question_id = ?", (question_id,))
            # L2 阶段 E：题目向量**无外键** → 不删也不报错，只会静默留下孤儿向量
            # （表现为向量索引能检索到已删除的题号）。必须显式清理。
            conn.execute("DELETE FROM t_question_embedding WHERE question_id = ?", (question_id,))
            cur = conn.execute("DELETE FROM t_question WHERE question_id = ?", (question_id,))
            conn.commit()
            return cur.rowcount


    # ---------- 题库：题目查询（管理列表 / 出题池） ----------

    @staticmethod
    def _kp_filter(where: list, params: list, kp_id=None, kp_ids=None,
                   qualifier: str = "q") -> bool:
        """把「按知识点过滤」统一编译为 EXISTS 子查询（关联表口径），追加到 where / params。

        语义：**「该题涉及该知识点」**——既命中主知识点，也命中次要知识点。
        单值 `kp_id` 与多值 `kp_ids` 等价（kp_ids 优先）；返回是否真的追加了条件。

        为什么统一走 t_question_kp 而不是 t_question.kp_id：
        - 旧口径只认主知识点，一题多挂后「次要知识点上的题」会被整批漏掉；
        - 存量数据当前 126/126 题为单知识点，故本次切换在现有数据上**无可观测差异**。
        集中成一个 helper 的原因：这条语义有 4 个调用点（题库列表 / 出题池 / 批改台列表 /
        批改台计数），抄 4 遍必然漂移。
        """
        ids = [str(k).strip() for k in (kp_ids or []) if k and str(k).strip()]
        if not ids and kp_id:
            single = str(kp_id).strip()
            if single:
                ids = [single]
        if not ids:
            return False
        marks = ", ".join("?" for _ in ids)
        where.append(
            f"EXISTS (SELECT 1 FROM t_question_kp qk "
            f"WHERE qk.question_id = {qualifier}.question_id AND qk.kp_id IN ({marks}))"
        )
        params.extend(ids)
        return True


    def list_questions(self, course_id: int, document_id=None, kp_id: str = None,
                       kp_ids=None,
                       q_type: str = None, keyword: str = None, is_active=None,
                       page: int = 1, page_size: int = 10,
                       include_course_level: bool = True,
                       auto_grade_only: bool = False):
        """分页查询题目（LEFT JOIN 取创建人姓名），返回 (total, rows)。

        作用域规则：传入 document_id 时默认同时包含「该文档题目」与「课程通用题
        （document_id IS NULL，即题目挂课程不挂具体文档）」；include_course_level=False
        时退化为精确匹配该文档（用于文档级清理/统计）。

        auto_grade_only=True 时排除主观题（FILL/ESSAY）——推荐器候选池用该口径实现
        "自动组卷不硬插入主观题"；教师端题库列表保持 False（要能看到主观题）。

        注意：WHERE 条件里的列一律带 `q.` 前缀——`t_user` 也有 `is_active` 列，
        裸写会报 `ambiguous column name: is_active`（教师端「启用状态」筛选曾因此报错）。
        """
        where, params = ["q.course_id = ?"], [course_id]
        if document_id is not None:
            if include_course_level:
                where.append("(q.document_id = ? OR q.document_id IS NULL)")
            else:
                where.append("q.document_id = ?")
            params.append(document_id)
        # 知识点过滤（关联表口径，见 _kp_filter）
        self._kp_filter(where, params, kp_id, kp_ids)
        if q_type:
            where.append("q.q_type = ?")
            params.append(q_type)
        elif auto_grade_only:
            marks = ", ".join("?" for _ in MANUAL_GRADE_TYPES)
            where.append(f"q.q_type NOT IN ({marks})")
            params.extend(MANUAL_GRADE_TYPES)
        if keyword:
            where.append("q.stem LIKE ?")
            params.append(f"%{keyword}%")
        if is_active is not None:
            where.append("q.is_active = ?")
            params.append(1 if is_active else 0)
        where_sql = "WHERE " + " AND ".join(where)

        total = self._query_one(
            f"SELECT count(*) AS cnt FROM t_question q {where_sql}", tuple(params),
        )["cnt"]
        rows = self._query(
            f"""
            SELECT q.*, COALESCE(u.display_name, u.username, '') AS creator_name
            FROM t_question q LEFT JOIN t_user u ON q.created_by = u.user_id
            {where_sql}
            ORDER BY q.question_id DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params + [page_size, (page - 1) * page_size]),
        )
        return total, rows


    def count_questions_by_course(self, course_id: int) -> int:
        return self._query_one(
            "SELECT count(*) AS cnt FROM t_question WHERE course_id = ?", (course_id,),
        )["cnt"]


    def count_questions_by_document(self, course_id: int, document_id) -> int:
        """该文档精确挂载的题目数（不含课程通用题），供文档删除报告使用"""
        return self._query_one(
            "SELECT count(*) AS cnt FROM t_question WHERE course_id = ? AND document_id = ?",
            (course_id, document_id),
        )["cnt"]


    def count_questions_grouped_by_kp(self, course_id: int, document_id=None,
                                      include_course_level: bool = True,
                                      only_active: bool = False):
        """按知识点统计题目数，返回 (grouped, unlinked)。

        - grouped：{kp_id: 题目数}。**决策 2a：一题多挂时每个 kp 各计 1 题**——
          某 kp 的计数既含「以它为主知识点」的题，也含「仅作为次要知识点」的题
          （旧口径只数 t_question.kp_id，会漏掉次要知识点上的题数）；
        - unlinked：**关联表中无任何行**的题目数（口径由「kp_id 为空」改为关联表口径）。

        L2 改造点：数据源从 t_question.kp_id 单列改为 t_question_kp 关联表。
        作用域口径与 list_questions 一致：document_id 传入时默认含课程通用题
        （document_id IS NULL）；only_active=True 时只数启用中的题（覆盖率报表用）。
        """
        where, params = ["q.course_id = ?"], [course_id]
        if document_id is not None:
            if include_course_level:
                where.append("(q.document_id = ? OR q.document_id IS NULL)")
            else:
                where.append("q.document_id = ?")
            params.append(document_id)
        if only_active:
            where.append("q.is_active = 1")
        where_sql = " AND ".join(where)

        rows = self._query(
            f"SELECT qk.kp_id AS kp_id, count(*) AS c "
            f"FROM t_question_kp qk JOIN t_question q ON q.question_id = qk.question_id "
            f"WHERE {where_sql} GROUP BY qk.kp_id",
            tuple(params),
        )
        grouped = {r["kp_id"]: r["c"] for r in rows if r["kp_id"]}
        unlinked = self._query_one(
            f"SELECT count(*) AS cnt FROM t_question q WHERE {where_sql} "
            f"AND NOT EXISTS (SELECT 1 FROM t_question_kp k WHERE k.question_id = q.question_id)",
            tuple(params),
        )["cnt"]
        return grouped, unlinked


    def question_answer_stats(self, course_id: int, graded_only: bool = True) -> dict:
        """按题统计作答人次与正确数，返回 {question_id: {"attempts": n, "correct": n}}。

        graded_only=True（默认）只统计已判分的记录：主观题提交后处于 PENDING 且
        is_correct=0，若不排除会把"未批改"误算成"答错"，污染推荐器的区分度信号与正确率。
        """
        sql = ("SELECT question_id, count(*) AS attempts, sum(is_correct) AS correct "
               "FROM t_answer_record WHERE course_id = ?")
        if graded_only:
            sql += " AND grade_status = 'GRADED'"
        sql += " GROUP BY question_id"
        rows = self._query(sql, (course_id,))
        if not rows:
            return {}
        return {
            r["question_id"]: {"attempts": r["attempts"], "correct": r["correct"] or 0}
            for r in rows
        }


    # ---------- 题库：级联清理（防孤儿数据，顺序敏感：子表 -> 主表） ----------

    def delete_questions_by_course(self, course_id: int) -> int:
        """删除课程全部题目及其收藏/答题记录，返回删除的题目数（整课删除时调用）"""
        with self._connect() as conn:
            count = conn.execute(
                "SELECT count(*) AS cnt FROM t_question WHERE course_id = ?", (course_id,),
            ).fetchone()["cnt"]
            conn.execute(
                "DELETE FROM t_question_favorite WHERE question_id IN "
                "(SELECT question_id FROM t_question WHERE course_id = ?)", (course_id,),
            )
            conn.execute(
                "DELETE FROM t_answer_record WHERE question_id IN "
                "(SELECT question_id FROM t_question WHERE course_id = ?)", (course_id,),
            )
            # L2：知识点关联有物理外键指向 t_question，必须先删（否则删题会撞 FK 约束）
            conn.execute(
                "DELETE FROM t_question_kp WHERE question_id IN "
                "(SELECT question_id FROM t_question WHERE course_id = ?)", (course_id,),
            )
            # L2 阶段 E：题目向量无外键 → 不删会静默留下孤儿向量
            conn.execute(
                "DELETE FROM t_question_embedding WHERE question_id IN "
                "(SELECT question_id FROM t_question WHERE course_id = ?)", (course_id,),
            )
            conn.execute("DELETE FROM t_question WHERE course_id = ?", (course_id,))
            conn.commit()
            return count


    def delete_questions_by_document(self, course_id: int, document_id) -> int:
        """删除某文档精确挂载的题目（课程通用题 document_id IS NULL 刻意保留），返回题目数。

        防御性处理：若题目在「已被作答之后」才被改挂到别的文档，其答题记录的 document_id
        可能与题目当前 document_id 不一致，故这里先按 question_id 子查询清子表，再删题目，
        避免触发 t_answer_record / t_question_favorite 的外键约束。

        题目向量表（t_question_embedding）同属「按 question_id 挂在题目上」的子表，且无外键、
        不参与级联，必须在这里一并清理——漏掉会留下指向已删题目的孤儿向量。清理必须在
        DELETE FROM t_question 之前，否则子查询已取不到 question_id。
        """
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM t_question_favorite WHERE question_id IN "
                "(SELECT question_id FROM t_question WHERE course_id = ? AND document_id = ?)",
                (course_id, document_id),
            )
            conn.execute(
                "DELETE FROM t_answer_record WHERE question_id IN "
                "(SELECT question_id FROM t_question WHERE course_id = ? AND document_id = ?)",
                (course_id, document_id),
            )
            # L2：知识点关联同理（物理外键指向 t_question）
            conn.execute(
                "DELETE FROM t_question_kp WHERE question_id IN "
                "(SELECT question_id FROM t_question WHERE course_id = ? AND document_id = ?)",
                (course_id, document_id),
            )
            # L2 阶段 E：题目向量同理（无外键 → 不删会静默留孤儿）
            conn.execute(
                "DELETE FROM t_question_embedding WHERE question_id IN "
                "(SELECT question_id FROM t_question WHERE course_id = ? AND document_id = ?)",
                (course_id, document_id),
            )
            cur = conn.execute(
                "DELETE FROM t_question WHERE course_id = ? AND document_id = ?",
                (course_id, document_id),
            )
            conn.commit()
            return cur.rowcount


    def delete_import_batches_by_document(self, course_id: int, document_id) -> int:
        """删除某文档的题目导入批次记录，返回条数。

        `t_question_import_batch.document_id` 无物理外键、不在任何自动级联里；
        文档删除后这些批次会变成指向不存在文档的孤儿，前端「导入历史」仍能翻到，
        点进去却找不到题目。课程级删除已按 course_id 清理（courses.delete_course），
        这里补齐文档级入口。
        """
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM t_question_import_batch WHERE course_id = ? AND document_id = ?",
                (course_id, document_id),
            )
            conn.commit()
            return cur.rowcount


    def delete_question_favorites_by_document(self, course_id: int, document_id) -> int:
        """删除某文档题目的收藏记录（须先于题目删除调用），返回条数"""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM t_question_favorite WHERE question_id IN "
                "(SELECT question_id FROM t_question WHERE course_id = ? AND document_id = ?)",
                (course_id, document_id),
            )
            conn.commit()
            return cur.rowcount


    def delete_answers_by_document(self, course_id: int, document_id) -> int:
        """删除某文档的答题记录，返回条数"""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM t_answer_record WHERE course_id = ? AND document_id = ?",
                (course_id, document_id),
            )
            conn.commit()
            return cur.rowcount
