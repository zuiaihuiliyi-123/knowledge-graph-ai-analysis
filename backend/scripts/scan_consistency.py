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

    # 现有合法 (course_id, document_id) 集合
    docs = sql_db._query("SELECT course_id, doc_id FROM t_document")
    valid_course_doc = {(d["course_id"], d["doc_id"]) for d in docs}
    valid_courses = {c["course_id"] for c in sql_db.list_courses()}

    # Neo4j 节点：kp_id -> (course_id, document_id)
    kp_rows = db.query("MATCH (n:KnowledgePoint) RETURN n.kp_id AS kp_id, "
                       "n.course_id AS course_id, n.document_id AS document_id")
    kp_map = {}
    for r in kp_rows:
        kp_map[r["kp_id"]] = (r["course_id"], r["document_id"])

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
            if kp and kp not in kp_map:
                issues.append(f"{t}: 不存在的 kp_id={kp} (course_id={cid})")
            # 6/7/8: document_id 与 kp 所属文档不一致
            if kp in kp_map:
                kp_cid, kp_did = kp_map[kp]
                if kp_cid != cid:
                    issues.append(f"{t}: kp_id={kp} 课程不一致 (SQL={cid}, Neo4j={kp_cid})")
                if did is not None and kp_did is not None and did != kp_did:
                    issues.append(f"{t}: kp_id={kp} 文档不一致 (SQL={did}, Neo4j={kp_did})")

    # 4: Neo4j KP document_id 与 t_document 不匹配
    for r in kp_rows:
        cid, did = r["course_id"], r["document_id"]
        if did is None:
            issues.append(f"Neo4j KP: document_id 为空 (kp_id={r['kp_id']}, course_id={cid})")
        elif (cid, did) not in valid_course_doc:
            issues.append(f"Neo4j KP: document_id={did} 无对应 t_document "
                          f"(kp_id={r['kp_id']}, course_id={cid})")

    # 5: Relation 两端 document_id 不一致
    rel_rows = db.query(
        "MATCH (a:KnowledgePoint)-[r]->(b:KnowledgePoint) "
        "RETURN a.document_id AS a_doc, b.document_id AS b_doc, "
        "r.document_id AS r_doc, a.course_id AS a_cid, b.course_id AS b_cid")
    for r in rel_rows:
        if r["a_doc"] != r["b_doc"]:
            issues.append(f"Relation: 两端 document_id 不一致 "
                          f"({r['a_doc']} vs {r['b_doc']}, course {r['a_cid']}/{r['b_cid']})")
        if r["r_doc"] is not None and r["r_doc"] != r["a_doc"]:
            issues.append(f"Relation: 关系 document_id={r['r_doc']} 与端节点 {r['a_doc']} 不一致")

    # 汇总
    print("=" * 60)
    print(f"一致性扫描结果：发现 {len(issues)} 条异常")
    print("=" * 60)
    for i in issues[:200]:
        print("  [!] " + i)
    if not issues:
        print("  CONSISTENCY_SCAN_CLEAN")
    else:
        print(f"  CONSISTENCY_SCAN_FOUND_{len(issues)}_ISSUES")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
