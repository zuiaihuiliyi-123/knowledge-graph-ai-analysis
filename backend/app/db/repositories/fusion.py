"""文档级知识融合的批次/候选/映射/冲突/作用域。"""

from ..connection import _now

class FusionMixin:
    """文档级知识融合的批次/候选/映射/冲突/作用域。"""


    # ---------- 文档级知识融合（Fusion） ----------
    #
    # 事务约定：所有写方法都接受 `conn=None`。传 None 时自建连接并提交（单条操作）；
    # 传连接时**只用不提交**，由调用方在同一个事务里组合「bump epoch + 写映射 + 失效向量」，
    # 保证 apply / revoke 要么整体成功要么整体回滚 —— 这是「不留半完成状态」的实现基础。

    def fusion_scope_get(self, course_id: int, document_id) -> dict:
        """读取某文档的融合 epoch 状态；无记录返回 None"""
        return self._query_one(
            "SELECT course_id, document_id, epoch, indexed_epoch, updated_at "
            "FROM t_kp_fusion_scope WHERE course_id = ? AND document_id = ?",
            (course_id, document_id),
        )


    def fusion_scope_bump_epoch(self, course_id: int, document_id, conn=None) -> int:
        """融合状态变更时 +1，返回新 epoch。

        INSERT...ON CONFLICT 一步到位：首次调用即建行（epoch 从 1 开始）。
        """
        sql = (
            "INSERT INTO t_kp_fusion_scope (course_id, document_id, epoch, updated_at) "
            "VALUES (?, ?, 1, ?) "
            "ON CONFLICT(course_id, document_id) DO UPDATE SET "
            "epoch = epoch + 1, updated_at = excluded.updated_at"
        )
        params = (course_id, document_id, _now())
        if conn is not None:
            conn.execute(sql, params)
        else:
            with self._connect() as c:
                c.execute(sql, params)
                c.commit()
        row = self.fusion_scope_get(course_id, document_id)
        return row["epoch"] if row else 0


    def fusion_scope_mark_indexed(self, course_id: int, document_id, epoch: int) -> None:
        """记录「该文档的向量索引已按此 epoch 建好」，供 ensure_index 判新鲜度"""
        self._execute(
            "INSERT INTO t_kp_fusion_scope (course_id, document_id, epoch, indexed_epoch, "
            "updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(course_id, document_id) DO UPDATE SET "
            "indexed_epoch = excluded.indexed_epoch, updated_at = excluded.updated_at",
            (course_id, document_id, epoch, epoch, _now()),
        )


    def list_fusion_map(self, course_id: int, document_id, status: str = None) -> list:
        """列出某文档的融合映射；status 传入时按状态过滤（读图只用 'ACTIVE'）"""
        where, params = ["course_id = ?", "document_id = ?"], [course_id, document_id]
        if status:
            where.append("status = ?")
            params.append(status)
        return self._query(
            f"SELECT * FROM t_kp_fusion_map WHERE {' AND '.join(where)} "
            f"ORDER BY fusion_id",
            tuple(params),
        )


    def list_active_fusion_map(self, course_id: int, document_id) -> list:
        """读图折叠专用的 ACTIVE 映射。**不要**在这里吞异常 —— 降级策略由调用方决定
        （读图 tolerant、管理操作 fail-closed，见 services/fusion/fusion_map.py）。"""
        return self.list_fusion_map(course_id, document_id, status="ACTIVE")


    def insert_fusion_map(self, course_id: int, document_id, source_kp_id: str,
                          target_kp_id: str, source_name: str = None, target_name: str = None,
                          source_category: str = None, target_category: str = None,
                          source_description: str = None, score: float = None,
                          decision_source: str = None, reason: str = None,
                          run_id: str = None, candidate_id: int = None,
                          operator_id: int = None, operator_name: str = None,
                          conn=None) -> int:
        """写入一条融合映射，返回 fusion_id；该源已有 **ACTIVE** 映射时不覆盖，返回 0。

        UNIQUE(course_id, document_id, source_kp_id) + `DO UPDATE ... WHERE status='REVOKED'`
        同时满足两件事：
          - 同一源重复 apply 是幂等的（ACTIVE 行不匹配 WHERE，零副作用）；
          - **撤销之后重新应用必须能生效** —— 若只写 DO NOTHING，新插入会撞上那行
            REVOKED 的旧记录、rowcount=0，映射没被复活却仍返回成功，
            表现为"点了应用但图谱没变"（这是实测踩到过的真实缺陷）。
            复活时沿用原 fusion_id，run_id/操作人/快照更新为本次的，
            操作历史由 t_kp_fusion_run 与审计日志保留。

        注意不能依赖 `lastrowid`：UPDATE 路径下它不会更新，必须回查 fusion_id。
        """
        sql = (
            "INSERT INTO t_kp_fusion_map (course_id, document_id, source_kp_id, target_kp_id, "
            "source_name, target_name, source_category, target_category, source_description, "
            "score, decision_source, reason, run_id, candidate_id, operator_id, operator_name, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(course_id, document_id, source_kp_id) DO UPDATE SET "
            " target_kp_id = excluded.target_kp_id, "
            " source_name = excluded.source_name, target_name = excluded.target_name, "
            " source_category = excluded.source_category, "
            " target_category = excluded.target_category, "
            " source_description = excluded.source_description, "
            " score = excluded.score, decision_source = excluded.decision_source, "
            " reason = excluded.reason, run_id = excluded.run_id, "
            " candidate_id = excluded.candidate_id, operator_id = excluded.operator_id, "
            " operator_name = excluded.operator_name, "
            " status = 'ACTIVE', revoked_at = NULL, revoked_by = NULL "
            "WHERE t_kp_fusion_map.status = 'REVOKED'"
        )
        params = (course_id, document_id, source_kp_id, target_kp_id, source_name, target_name,
                  source_category, target_category, source_description, score, decision_source,
                  reason, run_id, candidate_id, operator_id, operator_name, _now())
        lookup = ("SELECT fusion_id FROM t_kp_fusion_map "
                  "WHERE course_id = ? AND document_id = ? AND source_kp_id = ? AND status = 'ACTIVE'")
        lookup_params = (course_id, document_id, source_kp_id)

        def _run(c):
            cur = c.execute(sql, params)
            if not cur.rowcount:
                return 0
            row = c.execute(lookup, lookup_params).fetchone()
            return dict(row)["fusion_id"] if row else 0

        if conn is not None:
            return _run(conn)
        with self._connect() as c:
            fid = _run(c)
            c.commit()
            return fid


    def revoke_fusion_map(self, course_id: int, document_id, fusion_id: int,
                          revoked_by: int = None, conn=None) -> int:
        """撤销单条融合：**只把 status 改成 REVOKED，不删行**（历史映射与快照全部保留）。

        幂等：重复撤销返回 0（已 REVOKED 的不再匹配 status='ACTIVE'）。
        返回受影响行数。
        """
        sql = ("UPDATE t_kp_fusion_map SET status = 'REVOKED', revoked_at = ?, revoked_by = ? "
               "WHERE fusion_id = ? AND course_id = ? AND document_id = ? AND status = 'ACTIVE'")
        params = (_now(), revoked_by, fusion_id, course_id, document_id)

        def _run(c):
            return c.execute(sql, params).rowcount

        if conn is not None:
            return _run(conn)
        with self._connect() as c:
            n = _run(c)
            c.commit()
            return n


    def revoke_fusion_run(self, course_id: int, document_id, run_id: str,
                          revoked_by: int = None, conn=None) -> int:
        """整批撤销：该 run 下所有 ACTIVE 映射置 REVOKED，并把 run 标成 REVOKED。"""
        sql_map = ("UPDATE t_kp_fusion_map SET status = 'REVOKED', revoked_at = ?, revoked_by = ? "
                   "WHERE run_id = ? AND course_id = ? AND document_id = ? AND status = 'ACTIVE'")
        sql_run = ("UPDATE t_kp_fusion_run SET status = 'REVOKED', revoked_at = ?, revoked_by = ? "
                   "WHERE run_id = ? AND course_id = ? AND document_id = ? AND status = 'COMPLETED'")
        params = (_now(), revoked_by, run_id, course_id, document_id)

        def _run(c):
            n = c.execute(sql_map, params).rowcount
            c.execute(sql_run, params)
            return n

        if conn is not None:
            return _run(conn)
        with self._connect() as c:
            n = _run(c)
            c.commit()
            return n


    # 知识融合的 5 张表。文档级 / 课程级删除路径与计数共用同一份清单——
    # 历史上就是因为课程级删除各写各的，漏掉了这 5 张表（详见 delete_fusion_by_course）。
    _FUSION_TABLES = (
        "t_kp_fusion_run", "t_kp_fusion_candidate", "t_kp_fusion_map",
        "t_kp_fusion_conflict", "t_kp_fusion_scope",
    )


    def delete_fusion_by_document(self, course_id: int, document_id, conn=None) -> dict:
        """文档删除时的级联清理：该文档的 run / candidate / map / conflict / scope 全清。

        与 delete_questions_by_document 同一先例 —— 挂在 _delete_document_cascade 里，
        必须在 DELETE FROM t_document **之前**执行。
        """
        tables = self._FUSION_TABLES
        counts = {}

        def _run(c):
            for t in tables:
                counts[t] = c.execute(
                    f"DELETE FROM {t} WHERE course_id = ? AND document_id = ?",
                    (course_id, document_id),
                ).rowcount
            return counts

        if conn is not None:
            return _run(conn)
        with self._connect() as c:
            out = _run(c)
            c.commit()
            return out


    def delete_fusion_by_course(self, course_id: int, conn=None) -> dict:
        """课程删除时的级联清理：**该课程名下全部文档**的融合数据全清。

        与 delete_fusion_by_document 同一先例，区别只在不带 document_id 条件——
        删课程会带走它名下的所有文档，文档级清理点在此不适用，必须另走这条路。

        为什么必须清：这 5 张表**都没设物理外键**（与 t_kp_embedding / t_kp_text 同一
        先例，设了 FK 会让删文档的手工级联直接报错），所以漏清**不会报错**，只会静默
        堆积指向已删课程的孤儿行。文档级路径早已接上，课程级路径曾长期缺失。

        必须在 DELETE FROM t_course **之前**调用。
        """
        tables = self._FUSION_TABLES
        counts = {}

        def _run(c):
            for t in tables:
                counts[t] = c.execute(
                    f"DELETE FROM {t} WHERE course_id = ?", (course_id,)
                ).rowcount
            return counts

        if conn is not None:
            return _run(conn)
        with self._connect() as c:
            out = _run(c)
            c.commit()
            return out


    def count_fusion_by_course(self, course_id: int) -> dict:
        """该课程名下全部文档的融合行数（逐表）。

        与 delete_fusion_by_course 配对：delete_course 的返回值是**文档数**（int），
        不携带分表计数，故删除报告需在删之前单独取一次——与
        count_embeddings_by_course / count_questions_by_course 同一用法。
        """
        out = {}
        for t in self._FUSION_TABLES:
            row = self._query_one(
                f"SELECT count(*) AS cnt FROM {t} WHERE course_id = ?", (course_id,)
            )
            out[t] = row["cnt"] if row else 0
        return out


    def count_fusion_map(self, course_id: int, document_id, status: str = None) -> int:
        where, params = ["course_id = ?", "document_id = ?"], [course_id, document_id]
        if status:
            where.append("status = ?")
            params.append(status)
        row = self._query_one(
            f"SELECT count(*) AS cnt FROM t_kp_fusion_map WHERE {' AND '.join(where)}",
            tuple(params),
        )
        return row["cnt"] if row else 0


    def fused_source_kp_ids(self, course_id: int, document_id) -> set:
        """该文档当前 ACTIVE 映射里的全部**源** kp_id。

        供 RAG 侧扣除被融合节点（embedding._current_kp_ids / _load_kp_records）
        与关键词检索折叠复用。失败**不吞异常**，由调用方按 tolerant/strict 决定。
        """
        rows = self._query(
            "SELECT source_kp_id FROM t_kp_fusion_map "
            "WHERE course_id = ? AND document_id = ? AND status = 'ACTIVE'",
            (course_id, document_id),
        )
        return {r["source_kp_id"] for r in rows if r.get("source_kp_id")}


    # ---------- 融合批次（run） ----------

    def insert_fusion_run(self, run_id: str, course_id: int, document_id, mode: str,
                          config_json: str = None, operator_id: int = None,
                          operator_name: str = None, conn=None) -> str:
        sql = ("INSERT INTO t_kp_fusion_run (run_id, course_id, document_id, mode, status, "
               "config_json, operator_id, operator_name) VALUES (?, ?, ?, ?, 'RUNNING', ?, ?, ?)")
        params = (run_id, course_id, document_id, mode, config_json, operator_id, operator_name)
        if conn is not None:
            conn.execute(sql, params)
        else:
            with self._connect() as c:
                c.execute(sql, params)
                c.commit()
        return run_id


    def finish_fusion_run(self, run_id: str, status: str, total_candidates: int = 0,
                          n_same: int = 0, n_different: int = 0, n_uncertain: int = 0,
                          n_applied: int = 0, error_message: str = None, conn=None) -> int:
        sql = ("UPDATE t_kp_fusion_run SET status = ?, total_candidates = ?, n_same = ?, "
               "n_different = ?, n_uncertain = ?, n_applied = ?, error_message = ?, "
               "finished_at = ? WHERE run_id = ?")
        params = (status, total_candidates, n_same, n_different, n_uncertain, n_applied,
                  error_message, _now(), run_id)
        if conn is not None:
            return conn.execute(sql, params).rowcount
        with self._connect() as c:
            n = c.execute(sql, params).rowcount
            c.commit()
            return n


    def get_fusion_run(self, run_id: str) -> dict:
        return self._query_one("SELECT * FROM t_kp_fusion_run WHERE run_id = ?", (run_id,))


    def list_fusion_runs(self, course_id: int, document_id, page: int = 1,
                         page_size: int = 20) -> tuple:
        total = self._query_one(
            "SELECT count(*) AS c FROM t_kp_fusion_run WHERE course_id = ? AND document_id = ?",
            (course_id, document_id),
        )["c"]
        rows = self._query(
            "SELECT * FROM t_kp_fusion_run WHERE course_id = ? AND document_id = ? "
            "ORDER BY started_at DESC, run_id DESC LIMIT ? OFFSET ?",
            (course_id, document_id, page_size, max(0, (page - 1) * page_size)),
        )
        return total, rows


    # ---------- 融合候选 ----------

    def upsert_fusion_candidates(self, course_id: int, document_id, items: list,
                                 run_id: str = None, conn=None) -> int:
        """批量写入/更新候选对，返回处理条数。

        **幂等的关键**：`ON CONFLICT ... DO UPDATE` 只更新分数与理由，
        **永不触碰 `status`** —— 所以上一轮被驳回（REJECTED）或暂缓（DEFERRED）的候选
        不会在下一轮扫描里悄悄复活成 PENDING，人工审核结论得以保留。
        decision / reason 也只在仍是 PENDING 时才随扫描刷新。
        """
        if not items:
            return 0
        sql = (
            "INSERT INTO t_kp_fusion_candidate "
            "(course_id, document_id, source_kp_id, target_kp_id, source_name, target_name, "
            " source_category, target_category, score, score_detail, decision, decision_source, "
            " reason, status, last_run_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?) "
            "ON CONFLICT(course_id, document_id, source_kp_id, target_kp_id) DO UPDATE SET "
            " source_name = excluded.source_name, target_name = excluded.target_name, "
            " source_category = excluded.source_category, "
            " target_category = excluded.target_category, "
            " score = excluded.score, score_detail = excluded.score_detail, "
            " decision = CASE WHEN t_kp_fusion_candidate.status = 'PENDING' "
            "                 THEN excluded.decision ELSE t_kp_fusion_candidate.decision END, "
            " decision_source = CASE WHEN t_kp_fusion_candidate.status = 'PENDING' "
            "                 THEN excluded.decision_source "
            "                 ELSE t_kp_fusion_candidate.decision_source END, "
            " reason = CASE WHEN t_kp_fusion_candidate.status = 'PENDING' "
            "               THEN excluded.reason ELSE t_kp_fusion_candidate.reason END, "
            " last_run_id = excluded.last_run_id, updated_at = excluded.updated_at"
        )
        now = _now()
        rows = [
            (course_id, document_id, it["source_kp_id"], it["target_kp_id"],
             it.get("source_name"), it.get("target_name"),
             it.get("source_category"), it.get("target_category"),
             it.get("score") or 0.0, it.get("score_detail"), it.get("decision") or "UNCERTAIN",
             it.get("decision_source") or "RULE", it.get("reason"), run_id, now, now)
            for it in items
        ]
        if conn is not None:
            conn.executemany(sql, rows)
            return len(rows)
        with self._connect() as c:
            c.executemany(sql, rows)
            c.commit()
            return len(rows)


    def list_fusion_candidates(self, course_id: int, document_id, decision: str = None,
                               status: str = None, keyword: str = None, page: int = 1,
                               page_size: int = 20) -> tuple:
        where = ["course_id = ?", "document_id = ?"]
        params = [course_id, document_id]
        if decision:
            where.append("decision = ?")
            params.append(decision)
        if status:
            where.append("status = ?")
            params.append(status)
        if keyword:
            where.append("(source_name LIKE ? OR target_name LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        cond = " AND ".join(where)
        total = self._query_one(
            f"SELECT count(*) AS c FROM t_kp_fusion_candidate WHERE {cond}", tuple(params))["c"]
        rows = self._query(
            f"SELECT * FROM t_kp_fusion_candidate WHERE {cond} "
            f"ORDER BY score DESC, candidate_id LIMIT ? OFFSET ?",
            tuple(params + [page_size, max(0, (page - 1) * page_size)]),
        )
        return total, rows


    def get_fusion_candidate(self, candidate_id: int) -> dict:
        return self._query_one(
            "SELECT * FROM t_kp_fusion_candidate WHERE candidate_id = ?", (candidate_id,))


    def set_fusion_candidate_status(self, candidate_id: int, status: str,
                                    reviewed_by: int = None, comment: str = None,
                                    conn=None) -> int:
        """人工审核：通过（ACCEPTED）/ 驳回（REJECTED）/ 暂缓（DEFERRED）。

        审核结论一经写入就**不会被后续扫描覆盖**（见 upsert_fusion_candidates），
        因此驳回过的候选不会无限重现。
        """
        sql = ("UPDATE t_kp_fusion_candidate SET status = ?, reviewed_by = ?, "
               "reviewed_at = ?, review_comment = ?, updated_at = ? WHERE candidate_id = ?")
        params = (status, reviewed_by, _now(), comment, _now(), candidate_id)
        if conn is not None:
            return conn.execute(sql, params).rowcount
        with self._connect() as c:
            n = c.execute(sql, params).rowcount
            c.commit()
            return n


    def count_fusion_candidates(self, course_id: int, document_id, **filters) -> dict:
        """按 decision / status 汇总计数（管理端统计卡用），全部限定在文档范围内"""
        where = ["course_id = ?", "document_id = ?"]
        params = [course_id, document_id]
        for k in ("decision", "status"):
            if filters.get(k):
                where.append(f"{k} = ?")
                params.append(filters[k])
        cond = " AND ".join(where)
        total = self._query_one(
            f"SELECT count(*) AS c FROM t_kp_fusion_candidate WHERE {cond}", tuple(params))["c"]
        by_decision = {
            r["decision"]: r["c"] for r in self._query(
                f"SELECT decision, count(*) AS c FROM t_kp_fusion_candidate WHERE {cond} "
                f"GROUP BY decision", tuple(params))
        }
        by_status = {
            r["status"]: r["c"] for r in self._query(
                f"SELECT status, count(*) AS c FROM t_kp_fusion_candidate WHERE {cond} "
                f"GROUP BY status", tuple(params))
        }
        return {"total": total, "by_decision": by_decision, "by_status": by_status}


    # ---------- 冲突记录 ----------

    def insert_fusion_conflict(self, course_id: int, document_id, conflict_type: str,
                               source_kp_id: str = None, target_kp_id: str = None,
                               detail: str = None, run_id: str = None, conn=None) -> int:
        sql = ("INSERT INTO t_kp_fusion_conflict (course_id, document_id, conflict_type, "
               "source_kp_id, target_kp_id, detail, run_id) VALUES (?, ?, ?, ?, ?, ?, ?)")
        params = (course_id, document_id, conflict_type, source_kp_id, target_kp_id,
                  detail, run_id)
        if conn is not None:
            return conn.execute(sql, params).lastrowid
        with self._connect() as c:
            cid = c.execute(sql, params).lastrowid
            c.commit()
            return cid


    def list_fusion_conflicts(self, course_id: int, document_id, status: str = None,
                             page: int = 1, page_size: int = 20) -> tuple:
        where = ["course_id = ?", "document_id = ?"]
        params = [course_id, document_id]
        if status:
            where.append("status = ?")
            params.append(status)
        cond = " AND ".join(where)
        total = self._query_one(
            f"SELECT count(*) AS c FROM t_kp_fusion_conflict WHERE {cond}", tuple(params))["c"]
        rows = self._query(
            f"SELECT * FROM t_kp_fusion_conflict WHERE {cond} "
            f"ORDER BY conflict_id DESC LIMIT ? OFFSET ?",
            tuple(params + [page_size, max(0, (page - 1) * page_size)]),
        )
        return total, rows
