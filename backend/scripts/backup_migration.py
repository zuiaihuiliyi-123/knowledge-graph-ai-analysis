"""
Phase 9 迁移前备份脚本。

职责：
1. 创建 backend/backup/ 目录（若不存在）；
2. 备份 SQLite（data/app.db）为 backup/sqlite_<ts>.db；
3. 导出 Neo4j 全量数据为 backup/neo4j_<ts>.json（APOC 不可用时的 JSON 兜底导出）。

用法（backend/ 目录下）：
    python scripts/backup_migration.py
"""
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings, BASE_DIR
from app.core.database import db


def main():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = Path(BASE_DIR) / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)

    # 1) SQLite 文件备份
    sqlite_src = Path(settings.SQLITE_DB_PATH)
    sqlite_dst = backup_dir / f"sqlite_{ts}.db"
    if not sqlite_src.exists():
        print(f"[backup] SQLite 源不存在: {sqlite_src}")
        return 1
    shutil.copy2(sqlite_src, sqlite_dst)
    print(f"[backup] SQLite 已备份: {sqlite_dst} ({sqlite_src.stat().st_size} bytes)")

    # 2) Neo4j 全量导出（JSON 兜底，APOC 不可用）
    nodes = []
    rels = []
    for rec in db.query("MATCH (n) RETURN n"):
        n = rec["n"]
        nodes.append({"id": n.element_id, "labels": list(n.labels),
                      "props": dict(n)})
    for rec in db.query("MATCH ()-[r]->() RETURN r"):
        r = rec["r"]
        rels.append({"id": r.element_id, "type": r.type,
                     "start": r.start_node.element_id,
                     "end": r.end_node.element_id,
                     "props": dict(r)})

    neo4j_dst = backup_dir / f"neo4j_{ts}.json"
    payload = {
        "backup_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "node_count": len(nodes),
        "relationship_count": len(rels),
        "nodes": nodes,
        "relationships": rels,
    }
    neo4j_dst.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[backup] Neo4j 已导出: {neo4j_dst} (节点 {len(nodes)}，关系 {len(rels)})")

    print(f"[backup] 完成，备份目录: {backup_dir}")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
