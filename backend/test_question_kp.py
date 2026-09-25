"""
一题多知识点（L2 阶段 A：关联表 + 双写 + 读侧切换）——端到端验证

覆盖范围：
  1. 表结构与索引：t_question_kp 存在；uq_qkp_primary 部分唯一索引存在（一题最多一个主知识点）
  2. 迁移幂等与一致性：全库 t_question.kp_id 与 is_primary 行一一对应，无漂移
  3. DAO set_question_kps：单值 / 多值 / 去重 / 主 kp 兜底（primary 不在列表时取首项）
  4. DB 层约束：直接插第二个 is_primary=1 → 被 uq_qkp_primary 拒绝（不靠应用层自觉）
  5. **只改其他字段不塌缩多 KP**（关键回归：旧实现无条件传 kp_id，会把多 KP 压成单 KP）
  6. 清空语义：kp_ids=[] 与 kp_id="" 均等效为「解除全部关联」，投影落 NULL
  7. 按知识点过滤：list_questions(kp_id=次要知识点) 能命中；kp_ids 多值过滤
  8. 覆盖率统计口径（决策 2a）：一题多挂 → 每个 kp 各计 1 题；unlinked 改用关联表口径
  9. 级联清理：删题 / 按文档删题 / 删课程 都不留孤儿关联行
 10. 服务层：多知识点保存（图谱可用时校验真 kp；不可用时放行并 kp_checked=False）
 11. 上限：单题知识点超 MAX_KP_PER_QUESTION → 拒绝

依赖：**不依赖 Neo4j**（DAO 层用假 kp_id，本就不过图谱校验）；
     服务层会探测图谱——可用则取真实 kp 做多值保存，不可用则验证降级路径。
运行方式（backend 目录下）：
    python test_question_kp.py
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.sql_database import sql_db
from app.services.question_service import MAX_KP_PER_QUESTION, QuestionService

TEST_COURSE_MARK = "__question_kp_test__"
# DAO 层测试用的假 kp_id：DAO 不做图谱校验，用假的即可（同时避免依赖 Neo4j 在线）
KP_A, KP_B, KP_C = "kp_test_a", "kp_test_b", "kp_test_c"


def _cleanup_existing_test_course():
    """清掉上一次失败运行遗留的测试课程（幂等）"""
    for c in sql_db.list_courses():
        if c["course_name"] == TEST_COURSE_MARK:
            sql_db.delete_course(c["course_id"])


def _make_question(course_id, doc_id, teacher, kp_id=None, stem="kp-test"):
    """直接经 sql_db 建题（绕开图谱校验，专测关联表行为），返回 question_id"""
    return sql_db.create_question(
        course_id=course_id, document_id=doc_id, kp_id=kp_id, q_type="SINGLE",
        stem=stem, options=[{"key": "A", "text": "甲"}, {"key": "B", "text": "乙"}],
        answer="A", analysis="一题多知识点测试用题（脚本自动清理）", difficulty=3,
        created_by=teacher, source="MANUAL",
    )


def _primary_of(question_id):
    rows = sql_db._query(
        "SELECT kp_id FROM t_question_kp WHERE question_id = ? AND is_primary = 1",
        (question_id,),
    )
    return rows[0]["kp_id"] if rows else None


def _kp_ids_of(question_id):
    return [r["kp_id"] for r in sql_db._query(
        "SELECT kp_id FROM t_question_kp WHERE question_id = ? ORDER BY kp_id", (question_id,))]


def _payload_multi(kp_ids, kp_primary=None, stem="服务层-多知识点"):
    """服务层校验用的单选题目（带 kp_ids 数组）"""
    return {
        "kp_ids": kp_ids, "kp_primary": kp_primary, "q_type": "SINGLE", "stem": stem,
        "options": [{"key": "A", "text": "甲"}, {"key": "B", "text": "乙"}],
        "answer": "A", "analysis": "服务层多知识点测试（脚本自动清理）", "difficulty": 3,
    }


def main():
    checks = []

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    created_course = None
    try:
        _cleanup_existing_test_course()
        teacher = sql_db.ensure_default_teacher()
        course_id = sql_db.create_course(TEST_COURSE_MARK, teacher)
        created_course = course_id
        doc_id = sql_db.create_document(course_id, teacher, "kp测试文档.txt", "TXT", 10)
        print(f"测试课程 course_id={course_id}，文档 doc_id={doc_id}\n")

        # ---------- 1. 表结构与索引 ----------
        print("=" * 70)
        print("1. 表结构与索引")
        print("=" * 70)
        objs = {(r["type"], r["name"]) for r in sql_db._query(
            "SELECT type, name FROM sqlite_master WHERE tbl_name = 't_question_kp'")}
        check("表 t_question_kp 存在", ("table", "t_question_kp") in objs)
        check("索引 idx_qkp_kp 存在（按 kp 反查）", ("index", "idx_qkp_kp") in objs)
        check("部分唯一索引 uq_qkp_primary 存在（一题最多一个主知识点）",
              ("index", "uq_qkp_primary") in objs)
        idx = sql_db._query_one("SELECT sql FROM sqlite_master WHERE name = 'uq_qkp_primary'")
        check("uq_qkp_primary 确为部分唯一索引（含 WHERE is_primary = 1）",
              bool(idx) and "WHERE is_primary = 1" in (idx["sql"] or ""),
              str(idx["sql"]) if idx else "缺失")

        # ---------- 2. 迁移幂等与全库一致性 ----------
        print("=" * 70)
        print("2. 迁移幂等与全库一致性")
        print("=" * 70)
        drift = sql_db._query_one(
            "SELECT count(*) AS c FROM t_question q WHERE "
            "  COALESCE(q.kp_id, '') <> COALESCE("
            "    (SELECT k.kp_id FROM t_question_kp k "
            "     WHERE k.question_id = q.question_id AND k.is_primary = 1), '')"
        )["c"]
        check("全库 t_question.kp_id 与 is_primary 行一致（0 处漂移）", drift == 0, f"漂移 {drift} 处")
        orphan = sql_db._query_one(
            "SELECT count(*) AS c FROM t_question_kp k "
            "LEFT JOIN t_question q ON q.question_id = k.question_id WHERE q.question_id IS NULL"
        )["c"]
        check("无孤儿关联行", orphan == 0, f"孤儿 {orphan} 行")
        before = sql_db._query_one("SELECT count(*) AS c FROM t_question_kp")["c"]
        sql_db.init_tables()                      # 再跑一次，验证迁移幂等
        after = sql_db._query_one("SELECT count(*) AS c FROM t_question_kp")["c"]
        check("重复 init_tables() 不改变关联行数（幂等）", before == after, f"{before} → {after}")

        # ---------- 3. DAO set_question_kps ----------
        print("=" * 70)
        print("3. DAO set_question_kps：单值 / 多值 / 去重 / 主 kp 兜底")
        print("=" * 70)
        q1 = _make_question(course_id, doc_id, teacher, stem="DAO-基础")
        n = sql_db.set_question_kps(q1, [KP_A, KP_B, KP_C], primary=KP_B)
        check("写入 3 个知识点 → 返回 3", n == 3, f"return={n}")
        check("关联行 = 3", len(_kp_ids_of(q1)) == 3, str(_kp_ids_of(q1)))
        check("主知识点 = 指定的 KP_B", _primary_of(q1) == KP_B, str(_primary_of(q1)))
        check("投影：t_question.kp_id = KP_B",
              sql_db.get_question(q1)["kp_id"] == KP_B, str(sql_db.get_question(q1)["kp_id"]))
        check("get_question_kps 主知识点排首位",
              sql_db.get_question_kps(q1)[0]["kp_id"] == KP_B, str(sql_db.get_question_kps(q1)))

        sql_db.set_question_kps(q1, [KP_A, KP_A, KP_B, "", None], primary=KP_A)
        check("重复/空串/None → 去重后 2 行", len(_kp_ids_of(q1)) == 2, str(_kp_ids_of(q1)))

        sql_db.set_question_kps(q1, [KP_A, KP_B], primary="kp_not_in_list")
        check("primary 不在列表 → 兜底取首项（KP_A）", _primary_of(q1) == KP_A, str(_primary_of(q1)))
        sql_db.set_question_kps(q1, [KP_A, KP_B], primary=None)
        check("primary=None → 兜底取首项（KP_A）", _primary_of(q1) == KP_A, str(_primary_of(q1)))

        # ---------- 4. DB 层约束 ----------
        print("=" * 70)
        print("4. DB 层约束：uq_qkp_primary")
        print("=" * 70)
        try:
            sql_db._execute(
                "INSERT INTO t_question_kp (question_id, kp_id, is_primary) VALUES (?, ?, 1)",
                (q1, "kp_constraint_probe"))
            check("插入第二个 is_primary=1 → 应被拒绝", False, "竟然插入成功")
            sql_db._execute("DELETE FROM t_question_kp WHERE kp_id = ?", ("kp_constraint_probe",))
        except Exception as e:
            check("插入第二个 is_primary=1 → 被 uq_qkp_primary 拒绝", True, type(e).__name__)

        # ---------- 5. 只改其他字段不塌缩多 KP（关键回归） ----------
        print("=" * 70)
        print("5. 只改其他字段不塌缩多 KP（本次改造的关键回归）")
        print("=" * 70)
        q2 = _make_question(course_id, doc_id, teacher, stem="DAO-不塌缩")
        sql_db.set_question_kps(q2, [KP_A, KP_B, KP_C], primary=KP_A)
        sql_db.update_question(q2, difficulty=5)              # 不传任何 kp 字段
        check("update(difficulty=5) 后关联仍为 3 个（未被塌缩成 1 个）",
              len(_kp_ids_of(q2)) == 3, str(_kp_ids_of(q2)))
        check("update(difficulty=5) 后主知识点不变", _primary_of(q2) == KP_A, str(_primary_of(q2)))
        check("update(difficulty=5) 确实写入了难度", sql_db.get_question(q2)["difficulty"] == 5)
        sql_db.update_question(q2, kp_ids=[KP_B], kp_primary=KP_B)
        check("显式传 kp_ids 才做全量替换（3 → 1）", _kp_ids_of(q2) == [KP_B], str(_kp_ids_of(q2)))

        # ---------- 6. 清空语义 ----------
        print("=" * 70)
        print("6. 清空语义：kp_ids=[] 与 kp_id=（空串）")
        print("=" * 70)
        q3 = _make_question(course_id, doc_id, teacher, stem="DAO-清空")
        sql_db.set_question_kps(q3, [KP_A, KP_B], primary=KP_A)
        sql_db.update_question(q3, kp_ids=[])
        check("kp_ids=[] → 关联清空", _kp_ids_of(q3) == [], str(_kp_ids_of(q3)))
        check("kp_ids=[] → 投影落 NULL", sql_db.get_question(q3)["kp_id"] is None,
              repr(sql_db.get_question(q3)["kp_id"]))
        sql_db.update_question(q3, kp_id=KP_C)
        check("kp_id 单值语义写入 1 行", _kp_ids_of(q3) == [KP_C], str(_kp_ids_of(q3)))
        sql_db.update_question(q3, kp_id="")
        check("kp_id=（空串）→ 关联清空", _kp_ids_of(q3) == [], str(_kp_ids_of(q3)))
        check("kp_id=（空串）→ 投影落 NULL", sql_db.get_question(q3)["kp_id"] is None,
              repr(sql_db.get_question(q3)["kp_id"]))
        sql_db.update_question(q3, kp_id=None)
        check("kp_id=None → 视为不修改（仍为空）", _kp_ids_of(q3) == [], str(_kp_ids_of(q3)))

        # ---------- 7. 按知识点过滤（含次要知识点） ----------
        print("=" * 70)
        print("7. 按知识点过滤：主知识点 / 次要知识点 / 多值 / 不过滤")
        print("=" * 70)
        q4 = _make_question(course_id, doc_id, teacher, stem="DAO-过滤")
        sql_db.set_question_kps(q4, [KP_A, KP_B], primary=KP_A)     # KP_B 为次要
        _, rows_main = sql_db.list_questions(course_id, kp_id=KP_A, page_size=500)
        check("按主知识点过滤命中", q4 in [r["question_id"] for r in rows_main])
        _, rows_second = sql_db.list_questions(course_id, kp_id=KP_B, page_size=500)
        check("按**次要**知识点过滤同样命中（旧口径会漏掉）",
              q4 in [r["question_id"] for r in rows_second])
        _, rows_multi = sql_db.list_questions(course_id, kp_ids=[KP_C], page_size=500)
        check("kp_ids=[KP_C] 多值过滤（本题不挂 KP_C → 不命中）",
              q4 not in [r["question_id"] for r in rows_multi])
        _, rows_none = sql_db.list_questions(course_id, page_size=500)
        check("不传 kp 过滤 → 不作限制", q4 in [r["question_id"] for r in rows_none])
        prac = sql_db.list_practice_questions(course_id, kp_id=KP_B, limit=500)
        check("出题池按次要知识点过滤也命中",
              q4 in [r["question_id"] for r in prac])

        # ---------- 8. 覆盖率统计口径（决策 2a） ----------
        print("=" * 70)
        print("8. 覆盖率统计口径：一题多挂 → 每个 kp 各计 1 题")
        print("=" * 70)
        grouped, unlinked = sql_db.count_questions_grouped_by_kp(course_id)
        check("KP_A 计数 ≥ 1", grouped.get(KP_A, 0) >= 1, str(grouped.get(KP_A)))
        check("KP_B 计入（次要知识点也被统计）", grouped.get(KP_B, 0) >= 1, str(grouped.get(KP_B)))
        check("同一题在 KP_A、KP_B 上各计 1 次（决策 2a）",
              grouped.get(KP_A, 0) >= 1 and grouped.get(KP_B, 0) >= 1)
        expect_unlinked = sql_db._query_one(
            "SELECT count(*) AS c FROM t_question q WHERE q.course_id = ? AND NOT EXISTS "
            "(SELECT 1 FROM t_question_kp k WHERE k.question_id = q.question_id)",
            (course_id,))["c"]
        check("unlinked = 关联表中无任何行的题数（新口径）",
              unlinked == expect_unlinked, f"unlinked={unlinked} expect={expect_unlinked}")

        # ---------- 9. 级联清理 ----------
        print("=" * 70)
        print("9. 级联清理（不留孤儿关联行）")
        print("=" * 70)
        q_del = _make_question(course_id, doc_id, teacher, stem="DAO-删题")
        sql_db.set_question_kps(q_del, [KP_A, KP_B], primary=KP_A)
        sql_db.delete_question(q_del)
        check("删题后关联行清零（FK 顺序正确、未报错）", len(_kp_ids_of(q_del)) == 0,
              str(_kp_ids_of(q_del)))
        check("删题本身成功", sql_db.get_question(q_del) is None)

        doc2 = sql_db.create_document(course_id, teacher, "kp测试文档2.txt", "TXT", 10)
        q_doc = _make_question(course_id, doc2, teacher, stem="DAO-按文档删题")
        sql_db.set_question_kps(q_doc, [KP_C], primary=KP_C)
        sql_db.delete_questions_by_document(course_id, doc2)
        check("按文档删题后关联行清零", len(_kp_ids_of(q_doc)) == 0, str(_kp_ids_of(q_doc)))

        # ---------- 10. 服务层：多知识点保存 ----------
        # 用「注入式图谱」而不是真实 Neo4j：测试课程没有图谱节点，而本脚本遵守
        # 「只读图谱、不写图谱」的纪律（见 test_question_coverage.py 的同一约定）。
        print("=" * 70)
        print("10. 服务层：多知识点保存 / 校验 / 不塌缩")
        print("=" * 70)
        import app.services.question_service as qs

        class _FakeGraph:
            """模拟图谱可用：知识点查询返回注入的 kp 集合"""

            def __init__(self, kp_ids):
                self.kp_ids = set(kp_ids)

            def query(self, cypher, params=None):
                params = params or {}
                if "kp_id IN" in cypher:
                    want = set(params.get("ids") or [])
                    return [{"kp_id": k} for k in self.kp_ids if k in want]
                if "kp_id: $kp_id" in cypher:
                    want = params.get("kp_id")
                    return [{"kp_id": want}] if want in self.kp_ids else []
                return []

        class _BrokenGraph:
            """模拟图谱不可用：任何查询都抛异常"""

            @staticmethod
            def query(*args, **kwargs):
                raise RuntimeError("模拟 Neo4j 不可用")

        original_db = qs.db
        try:
            qs.db = _FakeGraph([KP_A, KP_B, KP_C])
            q5 = QuestionService.create_question(
                teacher, course_id,
                _payload_multi([KP_A, KP_B, KP_C], kp_primary=KP_B, stem="服务层-多知识点"))
            check("服务层：一次保存 3 个知识点 → 成功", q5["ok"], str(q5.get("message")))
            check("服务层：kp_checked=True（确实校验过图谱）",
                  q5["ok"] and q5["data"].get("kp_checked") is True,
                  str(q5.get("data", {}).get("kp_checked")))
            sid = q5["data"]["question_id"] if q5["ok"] else None
            if sid:
                check("服务层：关联行 = 3", len(_kp_ids_of(sid)) == 3, str(_kp_ids_of(sid)))
                check("服务层：kp_primary 生效（主知识点 = KP_B）", _primary_of(sid) == KP_B,
                      str(_primary_of(sid)))
                check("服务层：投影 t_question.kp_id = KP_B",
                      sql_db.get_question(sid)["kp_id"] == KP_B,
                      str(sql_db.get_question(sid)["kp_id"]))

                r_upd = QuestionService.update_question(teacher, sid, {"difficulty": 4})
                check("服务层：只改难度 → 成功", r_upd["ok"], str(r_upd.get("message")))
                check("**服务层：只改难度后多 KP 不被塌缩（3 个仍在）**",
                      len(_kp_ids_of(sid)) == 3, str(_kp_ids_of(sid)))

                r_repl = QuestionService.update_question(teacher, sid, {"kp_ids": [KP_A]})
                check("服务层：显式传 kp_ids → 全量替换（3 → 1）",
                      r_repl["ok"] and _kp_ids_of(sid) == [KP_A], str(_kp_ids_of(sid)))
                r_clr = QuestionService.update_question(teacher, sid, {"kp_ids": []})
                check("服务层：kp_ids=[] → 清空关联且投影落 NULL",
                      r_clr["ok"] and _kp_ids_of(sid) == []
                      and sql_db.get_question(sid)["kp_id"] is None,
                      f"{_kp_ids_of(sid)} / {repr(sql_db.get_question(sid)['kp_id'])}")

            r_dangle = QuestionService.create_question(
                teacher, course_id,
                _payload_multi([KP_A, "kp_not_exist_probe"], stem="服务层-悬空kp"))
            check("服务层：多知识点中含悬空 kp → 拒绝（4002）",
                  (not r_dangle["ok"]) and r_dangle["code"] == 4002,
                  f"code={r_dangle.get('code')} msg={r_dangle.get('message')}")

            # 降级路径：图谱不可用时放行并标记 kp_checked=False
            qs.db = _BrokenGraph()
            q6 = QuestionService.create_question(
                teacher, course_id,
                _payload_multi([KP_A, KP_B], kp_primary=KP_A, stem="服务层-降级"))
            check("服务层：图谱不可用 → 放行多知识点保存", q6["ok"], str(q6.get("message")))
            check("服务层：图谱不可用 → kp_checked=False（降级事实已透出）",
                  q6["ok"] and q6["data"].get("kp_checked") is False,
                  str(q6.get("data", {}).get("kp_checked")))
        finally:
            qs.db = original_db

        # ---------- 11. 上限校验 ----------
        print("=" * 70)
        print("11. 上限：单题知识点数量")
        print("=" * 70)
        qs.db = _BrokenGraph()               # 上限校验发生在图谱校验之前，不受图谱影响
        try:
            r_over = QuestionService.create_question(
                teacher, course_id,
                _payload_multi([f"kp_over_{i}" for i in range(MAX_KP_PER_QUESTION + 1)],
                               stem="服务层-超限"))
            check(f"超过 {MAX_KP_PER_QUESTION} 个知识点 → 拒绝（1001）",
                  (not r_over["ok"]) and r_over["code"] == 1001,
                  f"code={r_over.get('code')} msg={r_over.get('message')}")
            r_exact = QuestionService.create_question(
                teacher, course_id,
                _payload_multi([f"kp_ok_{i}" for i in range(MAX_KP_PER_QUESTION)],
                               stem="服务层-恰好上限"))
            check(f"恰好 {MAX_KP_PER_QUESTION} 个 → 允许", r_exact["ok"], str(r_exact.get("message")))
        finally:
            qs.db = original_db

        # ---------- 12. 回归：seed_practice_data 的清理路径 ----------
        # 该脚本的 clean_previous_seed() 用**原生 SQL** 直接 DELETE t_question（不走 delete_question）。
        # 新表加了指向 t_question 的物理外键、且连接默认 foreign_keys=ON，而 create_question
        # 现在总会写关联行 —— 若不先删关联行，这个清理函数会 100% 抛 FK 错误。
        print("=" * 70)
        print("12. 回归：seed_practice_data.clean_previous_seed() 的外键顺序")
        print("=" * 70)
        import seed_practice_data as seed

        q_seed = sql_db.create_question(
            course_id=course_id, document_id=doc_id, kp_id=KP_A, q_type="SINGLE",
            stem="seed-清理回归", options=[{"key": "A", "text": "甲"}, {"key": "B", "text": "乙"}],
            answer="A", created_by=teacher, source="IMPORT",
            kp_ids=[KP_A, KP_B], kp_primary=KP_A,
        )
        check("seed 场景：IMPORT 题带 2 行关联", len(_kp_ids_of(q_seed)) == 2, str(_kp_ids_of(q_seed)))
        try:
            removed = seed.clean_previous_seed(course_id)
            check("seed 清理未抛外键错误（修复前必然抛 FOREIGN KEY constraint failed）", True,
                  str(removed))
            check("seed 清理后该题关联行清零（先删子表）", len(_kp_ids_of(q_seed)) == 0,
                  str(_kp_ids_of(q_seed)))
            check("seed 清理确实删掉了该 IMPORT 题", sql_db.get_question(q_seed) is None)
        except Exception as e:
            check("seed 清理未抛外键错误（修复前必然抛 FOREIGN KEY constraint failed）", False,
                  f"{type(e).__name__}: {e}")

    finally:
        # 清理：整课删除（顺带验证删课程不留孤儿关联行）
        if created_course is not None:
            sql_db.delete_course(created_course)
            leftover = sql_db._query_one(
                "SELECT count(*) AS c FROM t_question_kp k JOIN t_question q "
                "ON q.question_id = k.question_id WHERE q.course_id = ?", (created_course,))["c"]
            check("删课程后该课题目无残留关联行", leftover == 0, f"残留 {leftover} 行")

    # ---------- 汇总 ----------
    print("\n" + "=" * 70)
    print("校验结果")
    print("=" * 70)
    failed = 0
    for name, ok, detail in checks:
        mark = "✓" if ok else "✗"
        if not ok:
            failed += 1
        line = f"{mark} {name}"
        if detail and not ok:
            line += f"    → {detail}"
        print(line)

    total = len(checks)
    print("-" * 70)
    print(f"通过 {total - failed}/{total}" + (f"，失败 {failed}" if failed else "，全部通过"))
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)



