"""
图谱完整性测试：验证 get_graph_v1 硬不变量 + 全量 Course × Document 无悬挂边 / 跨文档 / 跨课程异常。

验证内容（对应 Phase 10 规格「十三、必须增加测试」）：
  1. 每条 edge.source 都存在 node
  2. 每条 edge.target 都存在 node
  3. node.document_id = edge 端点 document_id（同一文档）
  4. node.course_id   = edge 端点 course_id（同一课程）
  5. 无跨文档 edge
  6. 无跨课程 edge
  7. get_graph_v1 返回数据满足 edge.source ∈ node_ids 且 edge.target ∈ node_ids

扫描全部现有 Course × Document，不只测单个文档。

运行方式（backend 目录下，需 Neo4j 已启动）：
    python test_graph_integrity.py
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.database import db
from app.core.sql_database import sql_db
from app.services.kg_manager import KnowledgeGraphManager


def main():
    checks = []

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    # ---------- 全局数据一致性（Neo4j 原始层） ----------

    # 1. 节点 kp_id 全部非空
    null_kp = db.query("MATCH (n:KnowledgePoint) WHERE n.kp_id IS NULL RETURN count(n) AS c")[0]["c"]
    check("所有 KnowledgePoint 均有非空 kp_id", null_kp == 0, f"NULL={null_kp}")

    # 2. 无重复节点（同 course_id + document_id + name）
    dup = db.query(
        "MATCH (n:KnowledgePoint) WITH n.course_id AS cid, n.document_id AS did, n.name AS name, count(n) AS c "
        "WHERE c > 1 RETURN count(*) AS cnt"
    )[0]["cnt"]
    check("无重复节点（同 course_id+document_id+name）", dup == 0, f"重复组={dup}")

    # 3. 无跨课程关系（两端节点 course_id 不一致）
    cross_course = db.query(
        "MATCH (a:KnowledgePoint)-[r]->(b:KnowledgePoint) "
        "WHERE a.course_id <> b.course_id RETURN count(r) AS c"
    )[0]["c"]
    check("无跨课程 edge", cross_course == 0, f"跨课程={cross_course}")

    # 4. 无跨文档关系（两端节点 document_id 不一致）
    cross_doc = db.query(
        "MATCH (a:KnowledgePoint)-[r]->(b:KnowledgePoint) "
        "WHERE a.document_id <> b.document_id RETURN count(r) AS c"
    )[0]["c"]
    check("无跨文档 edge", cross_doc == 0, f"跨文档={cross_doc}")

    # 5. 关系端点 kp_id 全部非空
    null_endpoint = db.query(
        "MATCH (a:KnowledgePoint)-[r]->(b:KnowledgePoint) "
        "WHERE a.kp_id IS NULL OR b.kp_id IS NULL RETURN count(r) AS c"
    )[0]["c"]
    check("所有关系两端节点均有非空 kp_id", null_endpoint == 0, f"NULL端点={null_endpoint}")

    # 6. 关系 document_id / course_id 与端点节点一致（非空且相等）
    rel_mismatch = db.query(
        "MATCH (a:KnowledgePoint)-[r]->(b:KnowledgePoint) "
        "WHERE r.document_id IS NULL OR r.course_id IS NULL "
        "   OR r.document_id <> a.document_id OR r.document_id <> b.document_id "
        "   OR r.course_id <> a.course_id OR r.course_id <> b.course_id "
        "RETURN count(r) AS c"
    )[0]["c"]
    check("关系 document_id/course_id 与端点节点一致", rel_mismatch == 0, f"不一致={rel_mismatch}")

    # ---------- get_graph_v1 硬不变量（API 返回层，逐文档） ----------
    total_docs = 0
    dangling_edges = 0
    for c in sql_db.list_courses():
        cid = c["course_id"]
        for d in sql_db.list_documents_by_course(cid):
            did = d["doc_id"]
            total_docs += 1
            g = KnowledgeGraphManager.get_graph_v1(cid, did, limit=2000)
            node_ids = {n["id"] for n in g["nodes"]}
            for e in g["edges"]:
                if e["source"] not in node_ids or e["target"] not in node_ids:
                    dangling_edges += 1
                    check(f"get_graph_v1 悬挂边 course={cid} doc={did}",
                          False, f"{e['source']} -> {e['target']} ({e['type']})")

    check("get_graph_v1 全部文档无悬挂边", dangling_edges == 0,
          f"扫描 {total_docs} 文档，悬挂边={dangling_edges}")

    db.close()

    print("=" * 64)
    passed = 0
    for name, ok, detail in checks:
        print(f"  {'✓' if ok else '✗'} {name}" + (f"  ({detail})" if detail else ""))
        passed += ok
    print(f"\n通过 {passed}/{len(checks)}")
    print("=" * 64)
    return passed == len(checks)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
