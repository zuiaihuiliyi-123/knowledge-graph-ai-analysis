"""
端到端测试：文本 → DeepSeek 提取 → 写入 Neo4j → 查回验证

运行方式（在 backend 目录下执行）：
    python test_pipeline.py
"""
import asyncio
import sys

# Windows 控制台默认 GBK，避免中文/特殊字符输出报错
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.services.knowledge_extractor import KnowledgeExtractor
from app.services.kg_manager import KnowledgeGraphManager
from app.core.database import db

COURSE_ID = "course_test_001"
DOCUMENT_ID = "doc_test_001"

TEST_TEXT = """
线性表是数据结构中最基本、最常见的一种结构，它由 n 个数据元素组成的有限序列。
线性表有两种存储结构：顺序存储结构（顺序表）和链式存储结构（链表）。
顺序表用一组地址连续的存储单元依次存储数据元素，支持随机访问。
链表通过指针将各个结点链接起来，插入和删除操作不需要移动元素。
栈是一种操作受限的线性表，只能在一端（栈顶）进行插入和删除，遵循后进先出（LIFO）原则。
队列也是一种操作受限的线性表，只允许在一端插入、另一端删除，遵循先进先出（FIFO）原则。
"""


async def main():
    # Step 1: LLM 提取
    print("=" * 60)
    print("Step 1: 调用 DeepSeek 提取知识")
    print("=" * 60)
    extractor = KnowledgeExtractor()
    result = await extractor.extract(TEST_TEXT)
    if result.get("error"):
        print(f"  [错误] {result['error']}")
        return

    entities = result["entities"]
    relations = result["relations"]
    print(f"  提取到 {len(entities)} 个实体、{len(relations)} 条关系")

    # Step 2: 写入 Neo4j
    print("\n" + "=" * 60)
    print("Step 2: 写入本地 Neo4j")
    print("=" * 60)

    # 初始化索引约束 + 清理本课程旧数据，保证可重复运行
    db.init_schema()
    db.query(
        "MATCH (n:KnowledgePoint {course_id: $cid}) DETACH DELETE n",
        {"cid": COURSE_ID},
    )

    stats = KnowledgeGraphManager.build_graph(COURSE_ID, DOCUMENT_ID, entities, relations)
    print(f"  写入 {stats['node_count']} 个节点、{stats['relation_count']} 条关系")

    # Step 3: 从库中查回验证
    print("\n" + "=" * 60)
    print("Step 3: 从 Neo4j 查回验证")
    print("=" * 60)

    node_cnt = db.query(
        "MATCH (n:KnowledgePoint {course_id: $cid}) RETURN count(n) AS cnt",
        {"cid": COURSE_ID},
    )[0]["cnt"]
    rel_cnt = db.query(
        "MATCH (n:KnowledgePoint {course_id: $cid})-[r]->(m:KnowledgePoint {course_id: $cid}) "
        "RETURN count(r) AS cnt",
        {"cid": COURSE_ID},
    )[0]["cnt"]
    print(f"  库中实际节点数: {node_cnt}")
    print(f"  库中实际关系数: {rel_cnt}")

    sample = db.query(
        "MATCH (a:KnowledgePoint {course_id: $cid})-[r]->(b:KnowledgePoint {course_id: $cid}) "
        "RETURN a.name AS source, type(r) AS rel, r.relation_type AS label, b.name AS target "
        "LIMIT 5",
        {"cid": COURSE_ID},
    )
    print("\n  [示例三元组] (主体) -[英文类型/中文标签]-> (客体)")
    for row in sample:
        print(f"    - ({row['source']}) -[{row['rel']}/{row['label']}]-> ({row['target']})")

    # 一致性校验
    print()
    if node_cnt == len(entities) and rel_cnt == len(relations):
        print("✓ 端到端流程跑通：文本 → LLM 提取 → Neo4j 存储 → 查回验证，数量完全一致")
    else:
        print(f"⚠ 数量不一致：提取 {len(entities)}实体/{len(relations)}关系，"
              f"库中 {node_cnt}节点/{rel_cnt}关系（可能存在同名去重）")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        db.close()
