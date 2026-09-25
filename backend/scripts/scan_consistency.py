"""
Phase 9 迁移后全库一致性扫描（只读，不修改任何数据）。

按规格「十九、迁移后最终一致性扫描」检查：
1. 孤立 document_id（SQL 行指向不存在的文档）
2. 不存在的 course_id（SQL 行指向不存在的课程）
3. 不存在的 kp_id（SQL 行指向不存在的 Neo4j 节点）
4. Neo4j KP 的 document_id 与 t_document 不匹配
5. Relation 两端 document_id 不一致
6. LearningRecord document_id 与 kp 所属文档不一致
7. Favorite document_id 与 kp 所属文档不一致
8. Embedding document_id 与 kp 所属文档不一致

发现异常只输出清单，不修数据。

**降级约定**：Neo4j 不可用时**不中断**——跳过第 3/4/5/6/7/8 项（均需图库节点判定），
报告里打印 `[skip]` 行并以 `CONSISTENCY_SCAN_PARTIAL_CLEAN` 收尾。这样 L2 的
「孤儿」与「主知识点投影漂移」两项**不变量检查（不依赖图库）在任何环境下都能跑**。

用法（backend/ 目录下）：
    python scripts/scan_consistency.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.sql_database import sql_db
from app.core.database import db


def main():
    issues = []
    skipped = []

    # 现有合法 (course_id, document_id) 集合
    docs = sql_db._query("SELECT course_id, doc_id FROM t_document")
    valid_course_doc = {(d["course_id"], d["doc_id"]) for d in docs}
    valid_courses = {c["course_id"] for c in sql_db.list_courses()}

    # Neo4j 节点：kp_id -> (course_id, document_id)
    # ⚠️ 图库不可用时**不中断**：本脚本最有价值的检查（L2 孤儿、主知识点投影漂移）
    #    完全不依赖图库，若因图库宕机直接抛 ServiceUnavailable 退出，这些检查就白跑了。
    #    降级策略：跳过所有依赖图库的项，并在报告里显式标注（而非静默给出错误结论）。
    kp_rows, kp_map, rel_rows, graph_note = [], {}, [], None
    try:
        kp_rows = db.query("MATCH (n:KnowledgePoint) RETURN n.kp_id AS kp_id, "
                           "n.course_id AS course_id, n.document_id AS document_id")
        for r in kp_rows:
            kp_map[r["kp_id"]] = (r["course_id"], r["document_id"])
        # 5: Relation 两端 document_id 一致性（同一连接，失败则一并跳过）
        rel_rows = db.query(
            "MATCH (a:KnowledgePoint)-[r]->(b:KnowledgePoint) "
            "RETURN a.document_id AS a_doc, b.document_id AS b_doc, "
            "r.document_id AS r_doc, a.course_id AS a_cid, b.course_id AS b_cid")
    except Exception as e:
        graph_note = f"{type(e).__name__}: {e}"
        kp_rows, kp_map, rel_rows = [], {}, []
    graph_ok = graph_note is None
    if not graph_ok:
        skipped.append(f"Neo4j 不可用（{graph_note}）→ 跳过第 3/4/5/6/7/8 项"
                       f"（均需图库节点才能判定，避免把全部 kp 误判为「不存在」）")

    # 1/2/3: SQL 侧孤立检查
    sql_tables = ("t_learning_record", "t_student_favorite", "t_kp_embedding")
    for t in sql_tables:
        rows = sql_db._query(
            f"SELECT course_id, document_id, kp_id FROM {t}")
        for r in rows:
            cid, did, kp = r["course_id"], r["document_id"], r["kp_id"]
            if cid not in valid_courses:
                issues.append(f"{t}: 不存在的 course_id={cid} (kp_id={kp})")
            if did is not None and (cid, did) not in valid_course_doc:
                issues.append(f"{t}: 孤立 document_id={did} for course_id={cid} (kp_id={kp})")
            if not graph_ok:
                continue                    # 图库不可用：kp 存在性/归属无从判断
            if kp and kp not in kp_map:
                issues.append(f"{t}: 不存在的 kp_id={kp} (course_id={cid})")
            # 6/7/8: document_id 与 kp 所属文档不一致
            if kp in kp_map:
                kp_cid, kp_did = kp_map[kp]
                if kp_cid != cid:
                    issues.append(f"{t}: kp_id={kp} 课程不一致 (SQL={cid}, Neo4j={kp_cid})")
                if did is not None and kp_did is not None and did != kp_did:
                    issues.append(f"{t}: kp_id={kp} 文档不一致 (SQL={did}, Neo4j={kp_did})")

    # L2（一题多知识点）：t_question_kp 是题目-知识点关联的**权威来源**。
    # 它没有 course_id / document_id 列，故经 t_question 取作用域，再走同一套 kp 校验。
    qkp_rows = sql_db._query(
        "SELECT q.course_id AS course_id, qk.kp_id AS kp_id "
        "FROM t_question_kp qk JOIN t_question q ON q.question_id = qk.question_id")
    for r in qkp_rows:
        cid, kp = r["course_id"], r["kp_id"]
        if cid not in valid_courses:
            issues.append(f"t_question_kp: 不存在的 course_id={cid} (kp_id={kp})")
        if not graph_ok:
            continue                        # 图库不可用：kp 存在性/归属无从判断
        if kp not in kp_map:
            issues.append(f"t_question_kp: 不存在的 kp_id={kp} (course_id={cid})")
        elif kp_map[kp][0] != cid:
            issues.append(f"t_question_kp: kp_id={kp} 课程不一致 "
                          f"(SQL={cid}, Neo4j={kp_map[kp][0]})")

    # L2（阶段 E）：t_question_embedding **无外键** → 删题不会级联，必须由 DAO 显式清理。
    # 历史上有 3 个删除点漏了这一步（delete_question / delete_questions_by_course /
    # delete_questions_by_document），产生「题目已不存在但向量还在」的**孤儿行**：
    # 不报错，但会污染向量索引（检索出已删除的题号）。此检查用于防止同类回归。
    for r in sql_db._query(
        "SELECT e.question_id AS question_id, e.course_id AS e_course, "
        "  e.document_id AS e_doc, q.course_id AS q_course, q.document_id AS q_doc "
        "FROM t_question_embedding e "
        "LEFT JOIN t_question q ON q.question_id = e.question_id"
    ):
        if r["q_course"] is None:
            issues.append(f"t_question_embedding: 孤儿向量 question_id={r['question_id']} "
                          f"(course_id={r['e_course']}，题目已不存在)")
            continue
        if r["q_course"] != r["e_course"]:
            issues.append(f"t_question_embedding: 课程不一致 question_id={r['question_id']} "
                          f"(向量={r['e_course']}, 题目={r['q_course']})")
        if (r["e_doc"] or None) != (r["q_doc"] or None):
            issues.append(f"t_question_embedding: document_id 漂移 question_id={r['question_id']} "
                          f"(向量={r['e_doc']}, 题目={r['q_doc']})")

    # L2 专用不变量：t_question.kp_id 是 t_question_kp 中 is_primary=1 那一行的**投影**，
    # 两者必须一致（由 set_question_kps() 同事务维护）。出现漂移说明有人绕过了唯一写入口。
    for r in sql_db._query(
        "SELECT q.question_id AS question_id, q.kp_id AS q_kp, "
        "  (SELECT k.kp_id FROM t_question_kp k "
        "   WHERE k.question_id = q.question_id AND k.is_primary = 1) AS p_kp "
        "FROM t_question q"
    ):
        if (r["q_kp"] or "") != (r["p_kp"] or ""):
            issues.append(
                f"t_question: 主知识点投影漂移 question_id={r['question_id']} "
                f"(t_question.kp_id={r['q_kp']} / is_primary={r['p_kp']})")

    # 4: Neo4j KP document_id 与 t_document 不匹配
    if graph_ok:
        for r in kp_rows:
            cid, did = r["course_id"], r["document_id"]
            if did is None:
                issues.append(f"Neo4j KP: document_id 为空 (kp_id={r['kp_id']}, course_id={cid})")
            elif (cid, did) not in valid_course_doc:
                issues.append(f"Neo4j KP: document_id={did} 无对应 t_document "
                              f"(kp_id={r['kp_id']}, course_id={cid})")

    # 5: Relation 两端 document_id 不一致
    for r in rel_rows:
        if r["a_doc"] != r["b_doc"]:
            issues.append(f"Relation: 两端 document_id 不一致 "
                          f"({r['a_doc']} vs {r['b_doc']}, course {r['a_cid']}/{r['b_cid']})")
        if r["r_doc"] is not None and r["r_doc"] != r["a_doc"]:
            issues.append(f"Relation: 关系 document_id={r['r_doc']} 与端节点 {r['a_doc']} 不一致")

    # 汇总
    print("=" * 60)
    print(f"一致性扫描结果：发现 {len(issues)} 条异常"
          f"（图库可用={graph_ok}，Neo4j 节点={len(kp_rows)}）")
    print("=" * 60)
    for s in skipped:
        print("  [skip] " + s)
    for i in issues[:200]:
        print("  [!] " + i)
    if not issues:
        print("  CONSISTENCY_SCAN_CLEAN" if graph_ok else "  CONSISTENCY_SCAN_PARTIAL_CLEAN")
    else:
        print(f"  CONSISTENCY_SCAN_FOUND_{len(issues)}_ISSUES")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
