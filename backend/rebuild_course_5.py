"""
重建 course_id=5 的图谱数据（用新提示词重新抽取，验证 PRECEDES）

流程：定位文档 -> 解析 PDF -> 重新抽取 -> 删除旧图 -> 写入新图 -> 验证 PRECEDES
运行方式（backend 目录下）：python rebuild_course_5.py

说明：只重建 Neo4j 图谱，不动 SQLite 的课程/文档记录（文档文件仍在、记录仍有效）。
"""
import sys
import os
import asyncio

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.database import db
from app.core.sql_database import sql_db
from app.services.document_parser import DocumentParser
from app.services.knowledge_extractor import KnowledgeExtractor
from app.services.kg_manager import KnowledgeGraphManager

COURSE_ID = 5


async def rebuild():
    # 1. 从 SQLite 定位文档路径（避免硬编码）
    docs = sql_db.list_documents_by_course(COURSE_ID)
    if not docs:
        print(f"✗ course_id={COURSE_ID} 下没有文档记录")
        return
    doc_path = docs[0].get("file_path")
    if not os.path.exists(doc_path):
        doc_path = str(doc_path).replace("\\", "/")
        if not os.path.exists(doc_path):
            print(f"✗ 文档文件不存在: {doc_path}")
            return
    print(f"[1] 文档: {doc_path}")

    # 2. 解析 PDF -> 文本
    print("[2] 解析文档…")
    text = await DocumentParser.parse(doc_path)
    print(f"    文本 {len(text)} 字符")

    # 3. 用新提示词重新抽取
    print("[3] 重新抽取（逐块调用 LLM，请稍候…）")
    extractor = KnowledgeExtractor()
    result = await extractor.extract(text)
    entities = result.get("entities", [])
    relations = result.get("relations", [])
    print(f"    实体 {len(entities)}，关系 {len(relations)}")
    if result.get("error"):
        print(f"    ✗ 提取失败: {result['error']}")
        return

    # 4. 删除旧图
    print("[4] 删除旧图…")
    nodes_deleted, edges_deleted = db.delete_course_graph(COURSE_ID)
    print(f"    删除 {nodes_deleted} 节点、{edges_deleted} 边")

    # 5. 写入新图（复用 kg_manager.build_graph，走统一的节点/关系 DAO）
    print("[5] 写入新图…")
    stats = KnowledgeGraphManager.build_graph(COURSE_ID, entities, relations)
    print(f"    入图 {stats['node_count']} 节点、{stats['relation_count']} 边")

    # 6. 验证关系类型分布
    print("[6] 验证关系类型分布…")
    dist = db.query(
        "MATCH (:KnowledgePoint {course_id: $cid})-[r]->(:KnowledgePoint {course_id: $cid}) "
        "RETURN type(r) AS t, count(r) AS cnt ORDER BY cnt DESC",
        {"cid": COURSE_ID},
    )
    for r in dist:
        print(f"    {r['t']}: {r['cnt']}")

    precedes_cnt = next((r["cnt"] for r in dist if r["t"] == "PRECEDES"), 0)
    if precedes_cnt > 0:
        print(f"\n✓ PRECEDES = {precedes_cnt}，路径推荐已可用")
        examples = db.query(
            "MATCH (a:KnowledgePoint {course_id: $cid})-[r:PRECEDES]->(b:KnowledgePoint {course_id: $cid}) "
            "RETURN a.name AS source, b.name AS target LIMIT 10",
            {"cid": COURSE_ID},
        )
        print("  示例前置关系:")
        for e in examples:
            print(f"    {e['source']} -> {e['target']}")
    else:
        print("\n✗ PRECEDES 仍为 0，需继续调整提示词")

    db.close()
    print("\n重建完成。")


if __name__ == "__main__":
    asyncio.run(rebuild())
