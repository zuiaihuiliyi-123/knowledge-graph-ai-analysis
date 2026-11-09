"""同一文本、同一温度下对比「现行抽取器」与「knowledge_extraction_v3」。

只负责调用与落盘；评分仍交给 backend/eval_accuracy.py（唯一评分实现）。
v3 原文件放在同目录的 untrusted_v3/ 下，仅把相对导入改成 app.* 绝对导入。

用法（在 backend/ 目录下）：
    python scripts/extract_compare/run_compare.py \
        --text eval_data/第7章_树.txt \
        --out-prefix scripts/extract_compare/out/tree_v3compare \
        --domain computer_science
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.services import knowledge_extractor as current_mod  # noqa: E402
from app.services.knowledge_extractor import KnowledgeExtractor as CurrentExtractor  # noqa: E402
from untrusted_v3 import extraction as v3mod  # noqa: E402
from untrusted_v3.extraction import KnowledgeExtractor as V3Extractor  # noqa: E402


def use_current_ontology_rules_for_v3() -> None:
    """把现行 Prompt 的领域规则迁进 v3 的两阶段 Prompt，只替换规则内容。

    目的：把「两阶段架构」与「领域知识」两个变量拆开。否则 v3 的低分无法
    区分是架构问题，还是它自带的领域规则包本就不完整。
    """
    prompt = current_mod.EXTRACTION_PROMPT
    entity_rules = prompt[prompt.index("## 一、实体要求"):prompt.index("## 二、关系类型")].strip()
    relation_rules = prompt[prompt.index("## 二、关系类型"):prompt.index("## 十、输出格式")].strip()

    v3mod._prompt_for_entity = (
        lambda domain: v3mod.BASE_ENTITY_PROMPT + "\n" + entity_rules
    )

    def relation_prompt(domain, include_few_shot: bool = True) -> str:
        text = v3mod.BASE_RELATION_PROMPT + "\n" + relation_rules
        if include_few_shot:
            text += "\n" + "\n".join(v3mod.FEW_SHOT.values())
        return text

    v3mod._prompt_for_relation = relation_prompt


def restore_invalid_evidence_relations(result: dict) -> dict:
    """消融：把被「evidence 必须逐字出现在原文」规则误杀的关系加回来。"""
    relations = list(result.get("relations", []))
    for item in result.get("dropped_relations", []) or []:
        if item.get("reason_code") != "invalid_evidence":
            continue
        relations.append({
            "source": item.get("source", ""),
            "target": item.get("target", ""),
            "type": item.get("type", ""),
            "evidence": "",
            "confidence": 0.8,
        })
    seen = set()
    kept = []
    for relation in relations:
        key = (relation["source"], relation["type"], relation["target"])
        if key in seen:
            continue
        seen.add(key)
        kept.append(relation)
    result = dict(result)
    result["relations"] = kept
    result["raw_relation_count"] = len(kept)
    result["dropped_counts"] = {}
    result["dropped_relations"] = []
    return result


class UsageRecorder:
    """记录一次抽取实际发出的请求数与 token 用量（含内部重试）。"""

    def __init__(self) -> None:
        self.calls = 0
        self.failed_calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.lock = threading.Lock()

    def attach(self, extractor) -> None:
        completions = extractor.client.chat.completions
        original = completions.create

        def create(*args, **kwargs):
            try:
                response = original(*args, **kwargs)
            except Exception:
                with self.lock:
                    self.calls += 1
                    self.failed_calls += 1
                raise
            usage = getattr(response, "usage", None)
            with self.lock:
                self.calls += 1
                if usage is not None:
                    self.prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
                    self.completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
                    self.total_tokens += int(getattr(usage, "total_tokens", 0) or 0)
            return response

        completions.create = create

    def as_dict(self) -> dict:
        return {
            "calls": self.calls,
            "failed_calls": self.failed_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


async def run_current(text: str, temperature: float) -> tuple[dict, dict]:
    extractor = CurrentExtractor(temperature=temperature)
    recorder = UsageRecorder()
    recorder.attach(extractor)
    started = time.perf_counter()
    result = await extractor.extract(text)
    elapsed = time.perf_counter() - started
    return result, {"wall_seconds": round(elapsed, 2), **recorder.as_dict()}


async def run_v3(
    text: str,
    temperature: float,
    domain: str,
    keep_invalid_evidence: bool = False,
) -> tuple[dict, dict]:
    extractor = V3Extractor(temperature=temperature, entity_temperature=temperature)
    recorder = UsageRecorder()
    recorder.attach(extractor)
    started = time.perf_counter()
    result = await extractor.extract(text, domain=domain)
    elapsed = time.perf_counter() - started
    if keep_invalid_evidence:
        result = restore_invalid_evidence_relations(result)
    return result, {"wall_seconds": round(elapsed, 2), **recorder.as_dict()}


def to_pred(text_id: str, result: dict) -> dict:
    return {
        "text_id": text_id,
        "runs": [{
            "entities": result.get("entities", []),
            "relations": result.get("relations", []),
            "error": result.get("error"),
            "raw_relation_count": result.get("raw_relation_count", len(result.get("relations", []))),
            "dropped_counts": result.get("dropped_counts") or {},
            "dropped_relations": result.get("dropped_relations") or [],
        }],
    }


def summarize(name: str, result: dict, stats: dict) -> dict:
    return {
        "name": name,
        "entities": len(result.get("entities", [])),
        "relations": len(result.get("relations", [])),
        "raw_relations": result.get("raw_relation_count"),
        "dropped_counts": result.get("dropped_counts") or {},
        "chunk_count": result.get("chunk_count"),
        "failed_chunk_count": result.get("failed_chunk_count"),
        "entity_duplicates": result.get("entity_duplicates"),
        "unmatched_mentions": len(result.get("unmatched_mentions") or []),
        # v1.4：诊断只记录、不参与评分；v3 结果没有该字段时为 None
        "diagnostics": (result.get("diagnostics") or {}).get("summary"),
        "relation_duplicates": len(result.get("relation_duplicates") or []),
        "error": result.get("error"),
        "error_types": result.get("error_types"),
        **stats,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", required=True)
    parser.add_argument("--text-id", default=None)
    parser.add_argument("--out-prefix", required=True)
    parser.add_argument("--domain", default="general")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--only", choices=["current", "v3", "both"], default="both")
    parser.add_argument("--v3-rules", choices=["pack", "current"], default="pack",
                        help="pack=使用 v3 自带领域规则包；current=改用现行 Prompt 的领域规则")
    parser.add_argument("--v3-keep-invalid-evidence", action="store_true",
                        help="消融：不做 evidence 逐字校验，保留被它丢弃的关系")
    args = parser.parse_args()

    text_path = Path(args.text)
    text = text_path.read_text(encoding="utf-8")
    text_id = args.text_id or text_path.stem
    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)

    print(f"文本：{text_path}（{len(text)} 字符），温度={args.temperature}，v3 domain={args.domain}")

    summary = {"text": str(text_path), "text_id": text_id,
               "chars": len(text), "temperature": args.temperature,
               "domain": args.domain, "runs": {}}

    if args.only in ("current", "both"):
        print("[current] 调用现行抽取器 …")
        result, stats = await run_current(text, args.temperature)
        summary["runs"]["current"] = summarize("current", result, stats)
        (out_prefix.parent / f"{out_prefix.name}_current_pred.json").write_text(
            json.dumps([to_pred(text_id, result)], ensure_ascii=False, indent=1),
            encoding="utf-8")
        (out_prefix.parent / f"{out_prefix.name}_current_raw.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print("  ", summary["runs"]["current"])

    if args.only in ("v3", "both"):
        if args.v3_rules == "current":
            use_current_ontology_rules_for_v3()
            print("[v3] 已切换到现行 Prompt 的领域规则")
        print("[v3] 调用两阶段抽取器 …")
        result, stats = await run_v3(
            text, args.temperature, args.domain,
            keep_invalid_evidence=args.v3_keep_invalid_evidence,
        )
        summary["runs"]["v3"] = summarize("v3", result, stats)
        (out_prefix.parent / f"{out_prefix.name}_v3_pred.json").write_text(
            json.dumps([to_pred(text_id, result)], ensure_ascii=False, indent=1),
            encoding="utf-8")
        (out_prefix.parent / f"{out_prefix.name}_v3_raw.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print("  ", summary["runs"]["v3"])

    (out_prefix.parent / f"{out_prefix.name}_stats.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"统计已写入 {out_prefix}_stats.json")


if __name__ == "__main__":
    asyncio.run(main())
