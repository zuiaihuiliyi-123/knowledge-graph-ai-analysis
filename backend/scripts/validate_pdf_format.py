"""
PDF 格式覆盖验证。

赛题要求「≥2 种文档格式」。六篇计分文档均为 TXT，本脚本用项目自己的
DocumentParser 解析一份真实 PDF，再走同一条 KnowledgeExtractor 抽取链路，
证明 PDF → 文本 → 实体/关系 的完整链路可用。

用法（backend 目录下）：
    EXTRACT_CACHE=0 python scripts/validate_pdf_format.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services.document_parser import DocumentParser  # noqa: E402
from app.services.knowledge_extractor import KnowledgeExtractor  # noqa: E402
PDF = BACKEND / "data/uploads/5/4_第6章 面向对象程序设计.pdf"
OUT = Path(
    os.environ.get("PDF_OUT_DIR", str(BACKEND / "eval_data/report6/pdf"))
)
if not OUT.is_absolute():
    OUT = BACKEND / OUT


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    text = await DocumentParser.parse(str(PDF))
    (OUT / "oop_ch6.txt").write_text(text, encoding="utf-8")
    print(f"PDF 解析：{len(text)} 字符 -> {OUT / 'oop_ch6.txt'}")

    extractor = KnowledgeExtractor(temperature=0.3)
    result = await extractor.extract(text, overlap_tokens=400)
    entities = result.get("entities", [])
    relations = result.get("relations", [])

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_pdf": str(PDF.relative_to(BACKEND)),
        "characters": len(text),
        "entities": len(entities),
        "relations": len(relations),
        "raw_relation_count": result.get("raw_relation_count", len(relations)),
        "dropped_counts": result.get("dropped_counts") or {},
        "error": result.get("error"),
        "sample_entities": entities[:10],
        "sample_relations": relations[:15],
        "all_entities": entities,
        "all_relations": relations,
    }
    (OUT / "pdf_extraction.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"抽取：实体 {len(entities)} 个，关系 {len(relations)} 条"
          f"（原始 {payload['raw_relation_count']} 条，丢弃 {sum(payload['dropped_counts'].values())} 条）")
    print("\n实体样例：")
    for e in entities[:8]:
        print(f"  - {e.get('name')} [{e.get('category')}]")
    print("\n关系样例：")
    for r in relations[:12]:
        print(f"  - {r.get('source')} -[{r.get('type')}]-> {r.get('target')}")
    print(f"\n结果：{OUT / 'pdf_extraction.json'}")


if __name__ == "__main__":
    asyncio.run(main())
