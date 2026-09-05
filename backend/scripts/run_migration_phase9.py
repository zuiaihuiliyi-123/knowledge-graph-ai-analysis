"""
Phase 9 迁移执行脚本（SQLite + Neo4j document_id 回填 + 验证）。

流程（幂等，可重复执行）：
1. 记录迁移前 Neo4j kp_id 快照 -> backup/kp_ids_before.json（首次运行时生成）；
2. SQLite：init_tables() 加列 + 回填 document_id（仅填 NULL）；
3. Neo4j：init_schema() 建索引 + 按「一课程一文档」映射回填 document_id（仅填 NULL）；
4. 验证 SQLite / Neo4j 的 document_id IS NULL 全部为 0；
5. 记录迁移后 kp_id 快照 -> backup/kp_ids_after.json，对比迁移前是否被修改。

用法（backend/ 目录下）：
    python scripts/run_migration_phase9.py
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import BASE_DIR
from app.core.sql_database import sql_db
from app.core.database import db


def _kp_id_set():
    rows = db.query("MATCH (n:KnowledgePoint) RETURN n.kp_id AS kp_id")
    return sorted(r["kp_id"] for r in rows if r["kp_id"])


def _neo4j_doc_mapping():
    """按「一课程一文档」构建 course_id -> doc_id 映射；发现一课程多文档则报错停止。"""
    rows = sql_db._query("SELECT course_id, doc_id FROM t_document ORDER BY course_id, doc_id")
    mapping = {}
    conflicts = []
    for r in rows:
        cid = r["course_id"]
        if cid in mapping and mapping[cid] != r["doc_id"]:
            conflicts.append((cid, mapping[cid], r["doc_id"]))
        else:
            mapping[cid] = r["doc_id"]
    return mapping, conflicts


def main():
    backup_dir = Path(BASE_DIR) / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 1) 迁移前 kp_id 快照（仅在文件不存在时生成，保证幂等比较基准一致）
    before_file = backup_dir / "kp_ids_before.json"
    if not before_file.exists():
        before = _kp_id_set()
        before_file.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")
        print(f"[migrate] 迁移前 kp_id 快照已保存: {before_file} ({len(before)} 个)")
    else:
        before = json.loads(before_file.read_text(encoding="utf-8"))
        print(f"[migrate] 复用已有迁移前 kp_id 快照 ({len(before)} 个)")

    # 2) 校验「一课程一文档」前提
    mapping, conflicts = _neo4j_doc_mapping()
    if conflicts:
        print("[migrate][ABORT] 发现一课程多文档，停止迁移，请人工处理:")
        for cid, a, b in conflicts:
            print(f"   course_id={cid} -> doc_id {a} 与 {b}")
        return 1
    print(f"[migrate] 一课程一文档前提成立，共 {len(mapping)} 门课程待回填")

    # 3) SQLite：加列 + 回填（幂等）
    sql_db.init_tables()

    # 4) Neo4j：索引 + 回填（幂等，仅填 NULL）
    db.init_schema()
    for course_id, doc_id in mapping.items():
        db.backfill_document_id(course_id, doc_id)

    # 5) 验证 NULL 归零
    sql_null = {}
    for t in ("t_learning_record", "t_student_favorite", "t_kp_embedding"):
        sql_null[t] = sql_db._query_one(
            f"SELECT count(*) AS c FROM {t} WHERE document_id IS NULL")["c"]

    kp_null = db.query("MATCH (n:KnowledgePoint) WHERE n.document_id IS NULL "
                       "RETURN count(n) AS c")[0]["c"]
    rel_null = db.query("MATCH ()-[r]->() WHERE r.document_id IS NULL "
                        "RETURN count(r) AS c")[0]["c"]

    # 6) 迁移后 kp_id 快照
    after = _kp_id_set()
    after_file = backup_dir / f"kp_ids_after_{ts}.json"
    after_file.write_text(json.dumps(after, ensure_ascii=False), encoding="utf-8")

    kp_unchanged = (set(before) == set(after)) and (len(before) == len(after))

    print()
    print("=" * 60)
    print("迁移验证结果")
    print("=" * 60)
    print(f"SQLite document_id IS NULL: {sql_null}")
    print(f"Neo4j KnowledgePoint document_id IS NULL: {kp_null}")
    print(f"Neo4j Relation document_id IS NULL: {rel_null}")
    print(f"kp_id 数量：迁移前 {len(before)} -> 迁移后 {len(after)}")
    print(f"kp_id 是否未修改：{kp_unchanged}")

    ok = (all(v == 0 for v in sql_null.values())
          and kp_null == 0 and rel_null == 0 and kp_unchanged)
    print(f"\nMIGRATION_RESULT: {'PASS' if ok else 'FAIL'}")
    db.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
