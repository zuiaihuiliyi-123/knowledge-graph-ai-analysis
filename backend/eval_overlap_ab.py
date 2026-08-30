"""
overlap A/B 对照评测：同一文档分别用 overlap=0 与 overlap=400 抽取，
对比分块数、实体/关系数量、各关系类型分布、重复实体数、被过滤关系数、耗时，
并列出两组结果之间新增/缺失的关系（含 evidence，便于人工核对 PRECEDES 是否有证据）。

用法（在 backend/ 目录下）：
    python eval_overlap_ab.py [文档路径]   # 默认第6章 pdf
"""
import asyncio
import time

from app.services.knowledge_extractor import KnowledgeExtractor, _CHUNK_MAX_TOKENS
from app.services.document_parser import DocumentParser
from app.utils.text_processor import chunk_text_for_llm, estimate_tokens
from eval_config import make_config, save_config

_MAX_CONCURRENCY = 4
_REL_TYPES = ("PRECEDES", "CONTAINS", "RELATED_TO", "APPLIES_TO")


async def _run_once(extractor: KnowledgeExtractor, text: str, overlap: int) -> dict:
    """对给定 overlap 跑一次抽取，返回含 raw/merged 统计的结果"""
    chunks = chunk_text_for_llm(text, max_tokens=_CHUNK_MAX_TOKENS, overlap_tokens=overlap)

    sem = asyncio.Semaphore(_MAX_CONCURRENCY)

    async def _run(c: str) -> dict:
        async with sem:
            return await asyncio.to_thread(extractor._extract_single, c)

    t0 = time.time()
    raw_results = await asyncio.gather(*[_run(c) for c in chunks])
    # 失败分块串行重试一次（与产品 extract 一致）
    for i, r in enumerate(raw_results):
        if r.get("error"):
            raw_results[i] = await asyncio.to_thread(extractor._extract_single, chunks[i])
    elapsed = time.time() - t0

    # raw（未合并/未过滤）统计
    raw_entities = sum(len(r.get("entities", [])) for r in raw_results)
    raw_relations = sum(len(r.get("relations", [])) for r in raw_results)

    merged = extractor._merge_results(raw_results)
    entities = merged.get("entities", [])
    relations = merged.get("relations", [])

    type_counts = {t: 0 for t in _REL_TYPES}
    for r in relations:
        if r.get("type") in type_counts:
            type_counts[r["type"]] += 1

    return {
        "overlap": overlap,
        "chunk_count": len(chunks),
        "chunk_tokens": [round(estimate_tokens(c)) for c in chunks],
        "raw_entities": raw_entities,
        "entities": len(entities),
        "dup_entities": raw_entities - len(entities),
        "raw_relations": raw_relations,
        "relations": len(relations),
        "filtered_relations": raw_relations - len(relations),
        "type_counts": type_counts,
        "elapsed": round(elapsed, 2),
        # (source, type, target) -> evidence，用于对比新增/缺失关系并核对证据
        "relation_map": {(r["source"], r["type"], r["target"]): r.get("evidence", "")
                         for r in relations},
    }


async def _load_text(path: str) -> str:
    if path.lower().endswith(".pdf"):
        return await DocumentParser.parse(path)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


async def main(path: str, temperature: float):
    text = await _load_text(path)
    print(f"文档: {path}")
    print(f"字符数: {len(text)} | 估算 tokens: {round(estimate_tokens(text))}\n")

    extractor = KnowledgeExtractor(temperature=temperature)
    r0 = await _run_once(extractor, text, 0)
    r400 = await _run_once(extractor, text, 400)

    rows = [
        ("Chunk 数", r0["chunk_count"], r400["chunk_count"]),
        ("实体总数(原始)", r0["raw_entities"], r400["raw_entities"]),
        ("去重后实体数", r0["entities"], r400["entities"]),
        ("重复/别名实体数", r0["dup_entities"], r400["dup_entities"]),
        ("关系总数", r0["relations"], r400["relations"]),
        ("被过滤关系数", r0["filtered_relations"], r400["filtered_relations"]),
        ("抽取耗时(秒)", r0["elapsed"], r400["elapsed"]),
    ]
    for t in _REL_TYPES:
        rows.append((f"{t} 数", r0["type_counts"][t], r400["type_counts"][t]))

    print("指标对比：")
    print(f"{'指标':<16}{'overlap=0':>12}{'overlap=400':>14}")
    for label, v0, v400 in rows:
        print(f"{label:<16}{str(v0):>12}{str(v400):>14}")

    added = {k: v for k, v in r400["relation_map"].items() if k not in r0["relation_map"]}
    lost = {k: v for k, v in r0["relation_map"].items() if k not in r400["relation_map"]}

    print(f"\noverlap=400 相比 overlap=0 新增关系 {len(added)} 条：")
    for (s, tp, t), ev in sorted(added.items()):
        print(f"  + {s} --[{tp}]--> {t}  证据: {ev[:40]}")

    print(f"\noverlap=400 相比 overlap=0 缺失关系 {len(lost)} 条：")
    for (s, tp, t), ev in sorted(lost.items()):
        print(f"  - {s} --[{tp}]--> {t}  证据: {ev[:40]}")

    # 冻结并记录本次实验参数（overlap 是 A/B 操纵变量，故额外列出两个档位）
    cfg = make_config(temperature=temperature)
    cfg["overlap_variants"] = [0, 400]
    save_config(cfg, "eval_data/experiment_config_overlap_ab.json")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default="data/uploads/5/4_第6章 面向对象程序设计.pdf")
    ap.add_argument("--temperature", type=float, default=0.0,
                    help="正式评测用 0 消除采样随机性；开发探索可用 0.15")
    a = ap.parse_args()
    asyncio.run(main(a.path, a.temperature))
