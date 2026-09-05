"""
Phase 9 删除文档端到端清理验证（走 document_service.delete_document 完整链路）。

覆盖规格「十二、Delete Document 最终验证」：
    准备 D1、D2 两个文档，各含 图谱 / 向量 / 学习记录 / 收藏 / 本地文件；
    删除 D1 后验证 D1 全清空、D2 全部保持不变（含本地文件删除）。

运行方式（backend 目录下，需 Neo4j 已启动）：
    python test_delete_document.py
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.database import db
from app.core.sql_database import sql_db
from app.services.kg_manager import KnowledgeGraphManager
from app.services.document_service import DocumentService

TEST_COURSE_MARK = "__phase9_delete_doc_test__"

ENTITIES_1 = [{"name": "数组", "category": "概念"}, {"name": "指针", "category": "概念"}]
RELATIONS_1 = [{"source": "数组", "target": "指针", "type": "PRECEDES", "confidence": 0.9}]
ENTITIES_2 = [{"name": "链表", "category": "概念"}, {"name": "树", "category": "概念"}]
RELATIONS_2 = [{"source": "链表", "target": "树", "type": "PRECEDES", "confidence": 0.9}]


def _cleanup_existing():
    for c in sql_db.list_courses():
        if c["course_name"] == TEST_COURSE_MARK:
            db.delete_course_graph(c["course_id"])
            for d in sql_db.list_documents_by_course(c["course_id"]):
                sql_db.delete_learning_records_by_document(c["course_id"], d["doc_id"])
                sql_db.delete_favorites_by_document(c["course_id"], d["doc_id"])
                sql_db.delete_embeddings_by_document(c["course_id"], d["doc_id"])
                sql_db.delete_document(d["doc_id"])
            sql_db.delete_course(c["course_id"])


def main():
    checks = []

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    db.init_schema()
    _cleanup_existing()
    teacher = sql_db.ensure_default_teacher()
    cid = sql_db.create_course(TEST_COURSE_MARK, teacher)
    d1 = sql_db.create_document(cid, teacher, "删除测试A.txt", "TXT", 10)
    d2 = sql_db.create_document(cid, teacher, "删除测试B.txt", "TXT", 10)

    # 为两个文档各建：本地文件 + 图谱 + 向量 + 学习 + 收藏
    def seed(doc, entities, relations, kp_name):
        p = os.path.join("data", "uploads", str(cid), f"{doc}_del_test.txt")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("x")
        sql_db.update_document(doc, file_path=p)
        KnowledgeGraphManager.build_graph(cid, doc, entities, relations)
        g = KnowledgeGraphManager.get_graph_v1(cid, doc)
        kp_id = g["nodes"][0]["id"]
        sql_db.upsert_kp_embedding(cid, doc, kp_id, [0.1, 0.2])
        sql_db.upsert_learning_record(teacher, cid, doc, kp_id)
        sql_db.add_favorite(teacher, cid, doc, kp_id)
        return p, kp_id

    path1, kp1 = seed(d1, ENTITIES_1, RELATIONS_1, "数组")
    path2, kp2 = seed(d2, ENTITIES_2, RELATIONS_2, "链表")

    # 删除 D1
    result = DocumentService.delete_document(d1, teacher)
    check("delete_document 返回成功", result.get("ok") is True, str(result.get("message")))

    check("D1 图谱清空", len(KnowledgeGraphManager.get_graph_v1(cid, d1)["nodes"]) == 0)
    check("D1 向量清空", not sql_db.get_embeddings_by_document(cid, d1))
    check("D1 学习清空", not sql_db.list_records_by_user_course(teacher, cid, d1))
    check("D1 收藏清空", not sql_db.list_favorites_by_user_course(teacher, cid, d1))
    check("D1 本地文件删除", not os.path.exists(path1))
    check("D1 文档记录删除", sql_db.get_document(d1) is None)

    # D2 全部保持不变
    check("D2 图谱保留", {n["label"] for n in KnowledgeGraphManager.get_graph_v1(cid, d2)["nodes"]} == {"链表", "树"})
    check("D2 向量保留", len(sql_db.get_embeddings_by_document(cid, d2)) == 1)
    check("D2 学习保留", len(sql_db.list_records_by_user_course(teacher, cid, d2)) == 1)
    check("D2 收藏保留", len(sql_db.list_favorites_by_user_course(teacher, cid, d2)) == 1)
    check("D2 本地文件保留", os.path.exists(path2))
    check("D2 文档记录保留", sql_db.get_document(d2) is not None)

    # 清理
    db.delete_course_graph(cid)
    for d in (d2,):
        sql_db.delete_learning_records_by_document(cid, d)
        sql_db.delete_favorites_by_document(cid, d)
        sql_db.delete_embeddings_by_document(cid, d)
        sql_db.delete_document(d)
    sql_db.delete_course(cid)
    if os.path.exists(path2):
        os.remove(path2)
    db.close()

    print("=" * 60)
    passed = 0
    for name, ok, detail in checks:
        print(f"  {'✓' if ok else '✗'} {name}" + (f"  ({detail})" if detail else ""))
        passed += ok
    print(f"\n通过 {passed}/{len(checks)}")
    print("=" * 60)
    return passed == len(checks)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
