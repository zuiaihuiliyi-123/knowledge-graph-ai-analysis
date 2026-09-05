"""
Phase 9 计数快照脚本：输出各业务表/图当前计数，用于迁移前后 / 幂等前后对比。

用法（backend/ 目录下）：
    python scripts/verify_counts.py
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.sql_database import sql_db
from app.core.database import db


def main():
    kp = db.query("MATCH (n:KnowledgePoint) RETURN count(n) AS c")[0]["c"]
    rel = db.query("MATCH ()-[r]->() RETURN count(r) AS c")[0]["c"]
    kp_null = db.query("MATCH (n:KnowledgePoint) WHERE n.document_id IS NULL "
                       "RETURN count(n) AS c")[0]["c"]
    rel_null = db.query("MATCH ()-[r]->() WHERE r.document_id IS NULL "
                        "RETURN count(r) AS c")[0]["c"]

    snapshot = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "course_count": sql_db._query_one("SELECT count(*) AS c FROM t_course")["c"],
        "document_count": sql_db._query_one("SELECT count(*) AS c FROM t_document")["c"],
        "knowledge_point_count": kp,
        "relation_count": rel,
        "kp_document_id_null": kp_null,
        "relation_document_id_null": rel_null,
        "learning_record_count": sql_db._query_one("SELECT count(*) AS c FROM t_learning_record")["c"],
        "favorite_count": sql_db._query_one("SELECT count(*) AS c FROM t_student_favorite")["c"],
        "embedding_count": sql_db._query_one("SELECT count(*) AS c FROM t_kp_embedding")["c"],
        "learning_null": sql_db._query_one(
            "SELECT count(*) AS c FROM t_learning_record WHERE document_id IS NULL")["c"],
        "favorite_null": sql_db._query_one(
            "SELECT count(*) AS c FROM t_student_favorite WHERE document_id IS NULL")["c"],
        "embedding_null": sql_db._query_one(
            "SELECT count(*) AS c FROM t_kp_embedding WHERE document_id IS NULL")["c"],
    }

    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
