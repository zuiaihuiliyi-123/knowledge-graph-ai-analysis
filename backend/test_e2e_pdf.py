"""
端到端测试（真实 PDF）：PDF 解析 → DeepSeek 提取 → Neo4j 存储 → 查回验证

运行方式（在 backend 目录下执行）：
    python test_e2e_pdf.py
"""
import asyncio
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.services.document_parser import DocumentParser
from app.services.knowledge_extractor import KnowledgeExtractor
from app.services.kg_manager import KnowledgeGraphManager
from app.core.database import db

PDF_PATH = os.path.join("data", "sample_docs", "数据结构第一章.pdf")
COURSE_ID = "course_e2e_001"
DOCUMENT_ID = "doc_e2e_001"


async def main():
    # Step 1: PDF 解析
    print("=" * 60)
    print("Step 1: PDF 解析（pdfplumber）")
    print("=" * 60)
    if not os.path.exists(PDF_PATH):
        print(f"  [错误] PDF 不存在: {PDF_PATH}")
        return
    text = await DocumentParser.parse(PDF_PATH)
    print(f"  提取 {len(text)} 字符")
    print(f"  预览: {text[:120].replace(chr(10), ' / ')}...")

    # Step 2: LLM 提取
    print("\n" + "=" * 60)
    print("Step 2: DeepSeek 知识提取")
    print("=" * 60)
    result = await KnowledgeExtractor().extract(text)
    if result.get("error"):
        print(f"  [错误] {result['error']}")
        return
    entities = result["entities"]
    relations = result["relations"]
    print(f"  提取 {len(entities)} 实体 / {len(relations)} 关系")

    # Step 3: 写入 Neo4j
    print("\n" + "=" * 60)
    print("Step 3: 写入 Neo4j")
    print("=" * 60)
    db.init_schema()
    db.query("MATCH (n:KnowledgePoint {course_id: $cid}) DETACH DELETE n", {"cid": COURSE_ID})
    stats = KnowledgeGraphManager.build_graph(COURSE_ID, DOCUMENT_ID, entities, relations)
    print(f"  写入 {stats['node_count']} 节点 / {stats['relation_count']} 关系")

    # Step 4: 查回验证
    print("\n" + "=" * 60)
    print("Step 4: 从 Neo4j 查回验证")
    print("=" * 60)
    node_cnt = db.query(
        "MATCH (n:KnowledgePoint {course_id: $cid}) RETURN count(n) AS cnt",
        {"cid": COURSE_ID},
    )[0]["cnt"]
    rel_cnt = db.query(
        "MATCH (:KnowledgePoint {course_id: $cid})-[r]->(:KnowledgePoint {course_id: $cid}) "
        "RETURN count(r) AS cnt",
        {"cid": COURSE_ID},
    )[0]["cnt"]
    print(f"  库中节点: {node_cnt} / 关系: {rel_cnt}")

    sample = db.query(
        "MATCH (a:KnowledgePoint {course_id: $cid})-[r]->(b:KnowledgePoint {course_id: $cid}) "
        "RETURN a.name AS source, type(r) AS rel, r.relation_type AS label, b.name AS target LIMIT 8",
        {"cid": COURSE_ID},
    )
    print("\n  [示例三元组] (主体) -[英文/中文]-> (客体)")
    for row in sample:
        print(f"    ({row['source']}) -[{row['rel']}/{row['label']}]-> ({row['target']})")

    print()
    if node_cnt == len(entities) and rel_cnt == len(relations):
        print("✓ 端到端跑通：真实 PDF → 解析 → 抽取 → Neo4j → 查回，数量一致")
    else:
        print(f"⚠ 数量不一致：提取 {len(entities)}/{len(relations)}，库中 {node_cnt}/{rel_cnt}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        db.close()
