"""
Phase 8/9 文档作用域隔离测试（Neo4j 图谱 + SQLite 学习/收藏/向量 + 推荐 + 删除）。

验证核心不变量：同一课程不同文档的知识图谱/学习状态/收藏/推荐互相独立，
删除某文档只清理该文档资源，不影响同课程其他文档。

Phase 9：修复为创建真实课程/文档记录（满足 t_course / t_document 外键），
全程使用真实 course_id / document_id，测试结束完整清理（含课程与文档记录）。

运行方式（backend 目录下，需 Neo4j 已启动）：
    python test_document_scope.py
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
from app.services.path_recommender import PathRecommender

# 隔离测试命名空间标记（用于唯一标识测试课程，避免与真实业务课程冲突）
TEST_COURSE_MARK = "__phase9_scope_test__"

# 文档 A：数组 -> 指针（PRECEDES）
ENTITIES_A = [
    {"name": "数组", "category": "概念", "description": "文档A的数组"},
    {"name": "指针", "category": "概念", "description": "文档A的指针"},
]
RELATIONS_A = [{"source": "数组", "target": "指针", "type": "PRECEDES", "confidence": 0.9}]

# 文档 B：数组 <-> 链表（RELATED_TO）
ENTITIES_B = [
    {"name": "数组", "category": "概念", "description": "文档B的数组"},
    {"name": "链表", "category": "概念", "description": "文档B的链表"},
]
RELATIONS_B = [{"source": "数组", "target": "链表", "type": "RELATED_TO", "confidence": 0.9}]


def _cleanup_existing_test_course():
    """清理可能残留的测试课程（幂等：先删图/记录/文档/课程）。"""
    for c in sql_db.list_courses():
        if c["course_name"] == TEST_COURSE_MARK:
            db.delete_course_graph(c["course_id"])
            for d in sql_db.list_documents_by_course(c["course_id"]):
                sql_db.delete_learning_records_by_document(c["course_id"], d["doc_id"])
                sql_db.delete_favorites_by_document(c["course_id"], d["doc_id"])
                sql_db.delete_embeddings_by_document(c["course_id"], d["doc_id"])
                sql_db.delete_document(d["doc_id"])
            sql_db.delete_course(c["course_id"])


def _setup():
    """创建真实测试课程 + 两个文档，返回 (course_id, doc_a, doc_b, teacher_id)。"""
    _cleanup_existing_test_course()
    teacher_id = sql_db.ensure_default_teacher()
    course_id = sql_db.create_course(TEST_COURSE_MARK, teacher_id)
    doc_a = sql_db.create_document(course_id, teacher_id, "测试文档A.txt", "TXT", 10)
    doc_b = sql_db.create_document(course_id, teacher_id, "测试文档B.txt", "TXT", 10)
    return course_id, doc_a, doc_b, teacher_id


def _teardown(course_id, doc_a, doc_b):
    """完整清理测试课程的全部资源（图/学习/收藏/向量/文档/课程）。"""
    db.delete_course_graph(course_id)
    for doc in (doc_a, doc_b):
        sql_db.delete_learning_records_by_document(course_id, doc)
        sql_db.delete_favorites_by_document(course_id, doc)
        sql_db.delete_embeddings_by_document(course_id, doc)
        sql_db.delete_document(doc)
    sql_db.delete_course(course_id)


def main():
    checks = []

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    db.init_schema()
    course_id, doc_a, doc_b, user_id = _setup()
    COURSE_ID, DOC_A, DOC_B = course_id, doc_a, doc_b

    # ---------- 8A：图谱隔离 ----------
    KnowledgeGraphManager.build_graph(COURSE_ID, DOC_A, ENTITIES_A, RELATIONS_A)
    KnowledgeGraphManager.build_graph(COURSE_ID, DOC_B, ENTITIES_B, RELATIONS_B)

    ga = KnowledgeGraphManager.get_graph_v1(COURSE_ID, DOC_A)
    gb = KnowledgeGraphManager.get_graph_v1(COURSE_ID, DOC_B)
    id_a = {n["label"]: n["id"] for n in ga["nodes"]}
    id_b = {n["label"]: n["id"] for n in gb["nodes"]}

    check("A 的「数组」与 B 的「数组」不是同一节点", id_a["数组"] != id_b["数组"])
    check("两者 kp_id 不同", id_a["数组"] != id_b["数组"])
    check("get_graph(doc=A) 只返回 A", {n["label"] for n in ga["nodes"]} == {"数组", "指针"},
          str({n['label'] for n in ga['nodes']}))
    check("get_graph(doc=B) 只返回 B", {n["label"] for n in gb["nodes"]} == {"数组", "链表"},
          str({n['label'] for n in gb['nodes']}))
    check("A 含 PRECEDES", "PRECEDES" in {e["type"] for e in ga["edges"]})
    check("B 含 RELATED_TO 且无 PRECEDES",
          {e["type"] for e in gb["edges"]} == {"RELATED_TO"})

    # 同一文档内同名节点去重：重复 build 不产生重复节点
    KnowledgeGraphManager.build_graph(COURSE_ID, DOC_A, ENTITIES_A, RELATIONS_A)
    ga2 = KnowledgeGraphManager.get_graph_v1(COURSE_ID, DOC_A)
    check("同一文档内同名节点保持去重", len(ga2["nodes"]) == 2, f"{len(ga2['nodes'])}")

    # ---------- 8B：学习记录 / 收藏 / 推荐 隔离 ----------

    # 学习记录：A 掌握「数组」，B 未掌握
    sql_db.upsert_learning_record(user_id, COURSE_ID, DOC_A, id_a["数组"], status="MASTERED")
    check("A 的学习记录只出现在 A",
          any(r["kp_id"] == id_a["数组"] for r in sql_db.list_records_by_user_course(user_id, COURSE_ID, DOC_A)))
    check("A 的学习记录不出现在 B",
          not any(r["kp_id"] == id_a["数组"] for r in sql_db.list_records_by_user_course(user_id, COURSE_ID, DOC_B)))

    # 收藏：A 收藏「数组」，B 无
    sql_db.add_favorite(user_id, COURSE_ID, DOC_A, id_a["数组"])
    fav_a = sql_db.list_favorites_by_user_course(user_id, COURSE_ID, DOC_A)
    fav_b = sql_db.list_favorites_by_user_course(user_id, COURSE_ID, DOC_B)
    check("A 的收藏只出现在 A", any(f["kp_id"] == id_a["数组"] for f in fav_a))
    check("A 的收藏不出现在 B", not any(f["kp_id"] == id_a["数组"] for f in fav_b))

    # 推荐：A 推荐「指针」，B 推荐「链表」（不跨文档）
    rec_a = {r["name"] for r in PathRecommender.recommend_next(["数组"], COURSE_ID, DOC_A)}
    rec_b = {r["name"] for r in PathRecommender.recommend_next(["数组"], COURSE_ID, DOC_B)}
    check("A 的推荐不跳到 B（A 推荐指针）", rec_a == {"指针"}, str(rec_a))
    check("B 的推荐不跳到 A（B 推荐链表）", rec_b == {"链表"}, str(rec_b))

    # 删除 A 的学习记录不影响 B
    sql_db.delete_learning_record(user_id, COURSE_ID, DOC_A, id_a["数组"])
    check("删除 A 的学习记录后 A 为空",
          not sql_db.list_records_by_user_course(user_id, COURSE_ID, DOC_A))

    # 删除 A 的「数组」节点不影响 B
    KnowledgeGraphManager.delete_node(COURSE_ID, DOC_A, id_a["数组"])
    gb3 = KnowledgeGraphManager.get_graph_v1(COURSE_ID, DOC_B)
    check("删除 A 的「数组」不影响 B 的「数组」",
          "数组" in {n["label"] for n in gb3["nodes"]})

    # ---------- 8C：文档级清理（SQL 侧 + 图谱侧） ----------
    # 给 B 建学习/收藏/向量，再按文档清理，验证 A 不受影响（A 已无「数组」节点）
    sql_db.upsert_learning_record(user_id, COURSE_ID, DOC_B, id_b["链表"], status="MASTERED")
    sql_db.add_favorite(user_id, COURSE_ID, DOC_B, id_b["链表"])
    sql_db.upsert_kp_embedding(COURSE_ID, DOC_B, id_b["链表"], [0.1, 0.2, 0.3])

    sql_db.delete_learning_records_by_document(COURSE_ID, DOC_B)
    sql_db.delete_favorites_by_document(COURSE_ID, DOC_B)
    sql_db.delete_embeddings_by_document(COURSE_ID, DOC_B)
    check("8C 删除 B 的学习记录", not sql_db.list_records_by_user_course(user_id, COURSE_ID, DOC_B))
    check("8C 删除 B 的收藏", not sql_db.list_favorites_by_user_course(user_id, COURSE_ID, DOC_B))
    check("8C 删除 B 的向量", not sql_db.get_embeddings_by_document(COURSE_ID, DOC_B))

    # 删除 B 的图谱，A 的剩余节点「指针」不受影响
    db.delete_document_graph(COURSE_ID, DOC_B)
    gb4 = KnowledgeGraphManager.get_graph_v1(COURSE_ID, DOC_B)
    ga4 = KnowledgeGraphManager.get_graph_v1(COURSE_ID, DOC_A)
    check("8C delete_document_graph(B) 清空 B", len(gb4["nodes"]) == 0)
    check("8C delete_document_graph(B) 不影响 A", {n["label"] for n in ga4["nodes"]} == {"指针"})

    _teardown(COURSE_ID, DOC_A, DOC_B)
    db.close()

    # ---------- 打印结果 ----------
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
