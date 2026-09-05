"""
文档作用域迁移脚本（配合「课程-文档-知识图谱模型重构」Phase 4/9）。

职责：
1. 触发 SQLite 幂等迁移（补 document_id 列 + 回填）—— 已内置于 init_tables()；
2. 应用 Neo4j 文档作用域索引（init_schema）；
3. 按「一课程 == 一文档」映射，为旧 Neo4j 节点/关系回填 document_id。

用法（在 backend/ 目录下，先确认 Neo4j 已启动）:
    python scripts/migrate_document_scope.py

执行前建议先备份 SQLite 文件与 Neo4j（apoc.export.cypher.all 导出）。
"""
import sys
from pathlib import Path

# 让脚本可从 backend/ 目录外运行：把 backend/ 加入 sys.path，从而能 import app.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.sql_database import sql_db
from app.core.database import db


def main():
    # 1) SQLite：幂等加列 + 回填（已内置于 init_tables）
    sql_db.init_tables()

    # 2) Neo4j：应用索引（含 document_id 复合索引）
    db.init_schema()

    # 3) Neo4j：回填 document_id（旧数据一课程一文档）
    rows = sql_db._query(
        "SELECT course_id, doc_id FROM t_document ORDER BY course_id, doc_id"
    )
    mapping = {}
    for r in rows:
        cid = r["course_id"]
        if cid in mapping:
            print(f"[warn] 课程 {cid} 存在多个文档，仅回填 doc_id={mapping[cid]}")
            continue
        mapping[cid] = r["doc_id"]

    for course_id, doc_id in mapping.items():
        db.backfill_document_id(course_id, doc_id)

    print(f"[migrate] 完成：已为 {len(mapping)} 门课程回填 Neo4j document_id")


if __name__ == "__main__":
    main()
