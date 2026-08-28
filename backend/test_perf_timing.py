"""
性能测试：文档解析 -> 分块 -> 并发抽取 的端到端耗时

验证并发抽取（asyncio.gather + asyncio.to_thread）将长文档抽取压到目标时间（≤60s）。
注意：本脚本只计时，不写图，不修改 course 5 的 Neo4j 数据。

运行方式（backend 目录下）：python test_perf_timing.py
"""
import asyncio
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.sql_database import sql_db
from app.services.document_parser import DocumentParser
from app.services.knowledge_extractor import KnowledgeExtractor
from app.utils.text_processor import chunk_text_for_llm

COURSE_ID = 5


async def main():
    docs = sql_db.list_documents_by_course(COURSE_ID)
    if not docs:
        print(f"✗ course_id={COURSE_ID} 下没有文档记录")
        return
    path = docs[0].get("file_path")
    print(f"[1] 文档: {path}")

    # 1. 解析
    t0 = time.time()
    text = await DocumentParser.parse(path)
    t_parse = time.time() - t0
    print(f"[2] 解析: {len(text)} 字符，耗时 {t_parse:.2f}s")

    # 2. 分块
    chunks = chunk_text_for_llm(text, max_tokens=3000)
    print(f"[3] 分块: {len(chunks)} 块")

    # 3. 并发抽取（计时）
    extractor = KnowledgeExtractor()
    t1 = time.time()
    result = await extractor.extract(text)
    t_extract = time.time() - t1
    print(f"[4] 并发抽取: 耗时 {t_extract:.2f}s，实体 {len(result.get('entities', []))}，"
          f"关系 {len(result.get('relations', []))}")
    if result.get("error"):
        print(f"    ✗ {result['error']}")

    total = t_parse + t_extract
    verdict = "✓ 达标（≤60s）" if total <= 60 else "✗ 未达标（>60s）"
    print(f"\n总耗时（解析+抽取）: {total:.2f}s  {verdict}")


if __name__ == "__main__":
    asyncio.run(main())
