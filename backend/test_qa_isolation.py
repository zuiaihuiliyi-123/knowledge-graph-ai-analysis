"""
Phase 10 QA/RAG 文档隔离真实 API 联调（规格 十三 / 十四）。

验证核心不变量：同一课程两个文档各含同名知识点「数组」但描述不同，
向量检索 / 关键词检索 / 来源卡片 / LLM 生成 四层都必须限定 course_id + document_id，
A1 的来源绝不出现 A2，反之亦然。

依赖真实外部 API（已确认可用）：
  - Embedding：SiliconFlow BAAI/bge-m3（EMBEDDING_API_KEY）
  - LLM：DeepSeek deepseek-chat（LLM_API_KEY）

运行方式（backend 目录下，需 Neo4j 已启动）：
    python test_qa_isolation.py
"""
import asyncio
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.database import db
from app.core.sql_database import sql_db
from app.services.kg_manager import KnowledgeGraphManager
from app.services.embedding import KnowledgeEmbedder
from app.services.qa_service import QAService

TEST_COURSE_MARK = "__phase10_qa_isolation_test__"

# 文档 A1：C 语言语境下的「数组」
ENTITIES_A1 = [
    {"name": "数组", "category": "概念",
     "description": "在 C 语言中，数组是一段连续内存中定长的同类型元素序列，下标从 0 开始。"},
    {"name": "指针", "category": "概念",
     "description": "指针是存储内存地址的变量，用于间接访问数据。"},
]
RELATIONS_A1 = [{"source": "数组", "target": "指针", "type": "PRECEDES", "confidence": 0.9}]

# 文档 A2：Python 语境下的「数组」
ENTITIES_A2 = [
    {"name": "数组", "category": "概念",
     "description": "在 Python 中，list 是动态可变的序列容器，可存储任意类型元素，支持 append 等操作。"},
    {"name": "链表", "category": "概念",
     "description": "链表是通过指针链接的节点序列，支持动态插入与删除。"},
]
RELATIONS_A2 = [{"source": "数组", "target": "链表", "type": "RELATED_TO", "confidence": 0.9}]

QUESTION = "数组是什么？它有什么特点？"


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


async def main():
    checks = []

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    db.init_schema()
    _cleanup_existing()
    teacher = sql_db.ensure_default_teacher()
    cid = sql_db.create_course(TEST_COURSE_MARK, teacher)
    d1 = sql_db.create_document(cid, teacher, "文档A1_C语言数组.txt", "TXT", 10)
    d2 = sql_db.create_document(cid, teacher, "文档A2_Python数组.txt", "TXT", 10)

    # 构建图谱 + 真实向量索引
    KnowledgeGraphManager.build_graph(cid, d1, ENTITIES_A1, RELATIONS_A1)
    KnowledgeGraphManager.build_graph(cid, d2, ENTITIES_A2, RELATIONS_A2)
    n1 = KnowledgeEmbedder().build_index(cid, d1)
    n2 = KnowledgeEmbedder().build_index(cid, d2)
    check("A1 向量索引已构建", n1 == 2, f"{n1}")
    check("A2 向量索引已构建", n2 == 2, f"{n2}")

    # 收集两个文档各自的 kp_id 集合，用于隔离断言
    kp_a1 = {n["id"] for n in KnowledgeGraphManager.get_graph_v1(cid, d1)["nodes"]}
    kp_a2 = {n["id"] for n in KnowledgeGraphManager.get_graph_v1(cid, d2)["nodes"]}
    check("A1/A2 kp_id 集合不相交", not (kp_a1 & kp_a2), f"{kp_a1} vs {kp_a2}")

    qa = QAService()

    # ---------- 第 1 层：向量检索 ----------
    vec_a1 = qa._vector_search(QUESTION, cid, d1, top_k=3)
    vec_a2 = qa._vector_search(QUESTION, cid, d2, top_k=3)
    check("向量检索 A1 非空", len(vec_a1) > 0, str([n['name'] for n in vec_a1]))
    check("向量检索 A2 非空", len(vec_a2) > 0, str([n['name'] for n in vec_a2]))
    check("向量检索 A1 全部限定 A1 文档",
          all(n["kp_id"] in kp_a1 for n in vec_a1), str([n['kp_id'] for n in vec_a1]))
    check("向量检索 A2 全部限定 A2 文档",
          all(n["kp_id"] in kp_a2 for n in vec_a2), str([n['kp_id'] for n in vec_a2]))
    check("向量检索 A1 不含 A2 的 kp_id", all(n["kp_id"] not in kp_a2 for n in vec_a1))
    check("向量检索 A2 不含 A1 的 kp_id", all(n["kp_id"] not in kp_a1 for n in vec_a2))

    # ---------- 第 2 层：关键词检索 ----------
    kw_a1 = qa._keyword_search(QUESTION, cid, d1, top_k=3)
    kw_a2 = qa._keyword_search(QUESTION, cid, d2, top_k=3)
    check("关键词检索 A1 全部限定 A1 文档",
          all(n["kp_id"] in kp_a1 for n in kw_a1), str([n['kp_id'] for n in kw_a1]))
    check("关键词检索 A2 全部限定 A2 文档",
          all(n["kp_id"] in kp_a2 for n in kw_a2), str([n['kp_id'] for n in kw_a2]))

    # ---------- 第 3 层：来源卡片（结构化节点） ----------
    cards_a1 = qa.search_related_nodes(QUESTION, cid, d1, top_k=3)
    cards_a2 = qa.search_related_nodes(QUESTION, cid, d2, top_k=3)
    check("来源卡片 A1 含 kp_id/name/description",
          all(all(k in n for k in ("kp_id", "name", "category", "description")) for n in cards_a1))
    check("来源卡片 A1 全部限定 A1 文档",
          all(n["kp_id"] in kp_a1 for n in cards_a1), str([n['name'] for n in cards_a1]))
    check("来源卡片 A2 全部限定 A2 文档",
          all(n["kp_id"] in kp_a2 for n in cards_a2), str([n['name'] for n in cards_a2]))

    # ---------- 第 4 层：LLM 生成（端到端） ----------
    ans_a1 = await qa.ask(QUESTION, cid, d1)
    ans_a2 = await qa.ask(QUESTION, cid, d2)
    check("QA(A1) 有回答", bool(ans_a1 and "数组" in ans_a1), ans_a1[:60])
    check("QA(A2) 有回答", bool(ans_a2 and "数组" in ans_a2), ans_a2[:60])
    check("QA(A1) 命中 C 语言语义", ("C" in ans_a1 or "内存" in ans_a1 or "定长" in ans_a1), ans_a1[:60])
    check("QA(A2) 命中 Python 语义", ("Python" in ans_a2 or "list" in ans_a2 or "动态" in ans_a2), ans_a2[:60])

    # 清理
    db.delete_course_graph(cid)
    for d in (d1, d2):
        sql_db.delete_learning_records_by_document(cid, d)
        sql_db.delete_favorites_by_document(cid, d)
        sql_db.delete_embeddings_by_document(cid, d)
        sql_db.delete_document(d)
    sql_db.delete_course(cid)
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
    sys.exit(0 if asyncio.run(main()) else 1)
