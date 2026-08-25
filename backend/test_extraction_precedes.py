"""
验证新提示词能否产出 PRECEDES 关系（对 course_id=5 的面向对象文档做 A/B 重抽）

旧提示词抽取结果：PRECEDES = 0（只有 RELATED_TO 33 + CONTAINS 21）
本脚本用新提示词重新抽取同一文档，检查 PRECEDES 是否被产出。

运行方式（backend 目录下）：python test_extraction_precedes.py
"""
import sys
import asyncio
from collections import Counter

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.services.document_parser import DocumentParser
from app.services.knowledge_extractor import KnowledgeExtractor

DOC = "./data/uploads/5/4_第6章 面向对象程序设计.pdf"
# 截取上限：过长文档只取前 MAX_CHARS 字做部分验证，避免过多 LLM 调用
MAX_CHARS = 30000


async def main():
    print("[1] 解析文档…")
    text = await DocumentParser.parse(DOC)
    total = len(text)
    print(f"    文档全文 {total} 字符")
    if total > MAX_CHARS:
        text = text[:MAX_CHARS]
        print(f"    超过 {MAX_CHARS}，截取前 {MAX_CHARS} 字做部分验证")

    print("[2] 用新提示词提取（逐块调用 LLM，请稍候…）")
    extractor = KnowledgeExtractor()
    result = await extractor.extract(text)

    entities = result.get("entities", [])
    relations = result.get("relations", [])
    print(f"    实体数: {len(entities)}")
    print(f"    关系数: {len(relations)}")

    if result.get("error"):
        print(f"    [!] 提取错误: {result['error']}")

    dist = Counter((r.get("type") or "未知") for r in relations)
    print("[3] 关系类型分布:")
    for t, c in dist.most_common():
        print(f"    {t}: {c}")

    precedes = [r for r in relations if r.get("type") == "PRECEDES"]
    print(f"[4] PRECEDES 数量: {len(precedes)}")
    for r in precedes[:20]:
        print(f"    {r.get('source')}  ->  {r.get('target')}")

    print("[5] 结论:")
    if precedes:
        print(f"    ✓ 新提示词产出了 {len(precedes)} 条 PRECEDES（旧提示词为 0）")
    else:
        print("    ✗ 仍未产出 PRECEDES，需继续调整提示词")


if __name__ == "__main__":
    asyncio.run(main())
