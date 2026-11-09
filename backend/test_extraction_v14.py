"""v1.4 诊断字段关键回归测试（假客户端，不访问网络）。

重点验证「诊断只观察、不筛选」：paraphrase/missing 证据不删关系、
unmatched_mentions 不补实体、direction_basis 不改变方向、名称不被扩写、
置信度阈值与三元组去重保持原口径。

运行：cd backend && python test_extraction_v14.py
"""

import asyncio
import json
import os
import sys
from types import SimpleNamespace

# 必须在导入 app.* 之前设置：config 在模块导入时读取环境变量
os.environ["EXTRACT_CACHE"] = "0"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.config import settings
from app.services.knowledge_extractor import KnowledgeExtractor

failures = []


def check(label, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


SOURCE = (
    "二叉树是一种树结构。完全二叉树是二叉树的一种。"
    "要遍历二叉树，必须先掌握递归。二叉树支持插入节点。"
)

PAYLOAD = {
    "entities": [
        {"name": "二叉树", "category": "概念", "description": "一种树结构",
         "evidence": "二叉树是一种树结构"},
        {"name": "完全二叉树", "category": "概念", "description": "特殊的二叉树",
         "evidence": "完全二叉树包括二叉树"},  # paraphrase，不得因此删除
        {"name": "递归", "category": "方法", "description": "一种方法",
         "evidence": "要遍历二叉树，必须先掌握递归"},
        {"name": "遍历", "category": "方法", "description": "访问节点"},
        {"name": "节点", "category": "概念", "description": "树的组成单元"},
    ],
    "relations": [
        {"source": "二叉树", "target": "完全二叉树", "type": "CONTAINS",
         "evidence": "完全二叉树是二叉树的一种", "confidence": 0.9,
         "direction_basis": "二叉树是上位概念，完全二叉树是下位概念"},
        {"source": "递归", "target": "遍历", "type": "PRECEDES",
         "evidence": "要遍历二叉树，必须先掌握递归", "confidence": 0.9},
        # 同一三元组重复：应只保留一条，但重复统计要记到 2 次
        {"source": "二叉树", "target": "完全二叉树", "type": "CONTAINS",
         "evidence": "二叉树包括完全二叉树", "confidence": 0.9,
         "direction_basis": "重复条目"},
        # 证据缺失：不得因此删除
        {"source": "二叉树", "target": "遍历", "type": "RELATED_TO",
         "evidence": "", "confidence": 0.9},
        # 低于 APPLIES_TO 阈值 0.5：必须仍然被过滤
        {"source": "二叉树", "target": "节点", "type": "APPLIES_TO",
         "evidence": "二叉树支持插入节点", "confidence": 0.3},
    ],
    "unmatched_mentions": [
        {"name": "红黑树", "evidence": "红黑树是一种二叉搜索树", "reason": "未纳入实体"},
        {"name": "红黑树", "evidence": "红黑树是一种二叉搜索树", "reason": "重复条目"},
    ],
}


class FakeClient:
    def __init__(self, script):
        self.records = []
        self.script = list(script)
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.records.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        content, finish_reason = item
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=content),
            finish_reason=finish_reason,
        )])


def run_extract(script):
    old_mode = settings.LLM_JSON_MODE
    settings.LLM_JSON_MODE = True
    try:
        extractor = KnowledgeExtractor.__new__(KnowledgeExtractor)
        extractor.client = FakeClient(script)
        extractor.temperature = 0
        return asyncio.run(extractor.extract(SOURCE)), extractor.client
    finally:
        settings.LLM_JSON_MODE = old_mode


def main() -> None:
    print("v1.4 抽取诊断关键回归测试")
    content = json.dumps(PAYLOAD, ensure_ascii=False)
    result, client = run_extract([(content, "stop")])

    relations = {
        (r["source"], r["type"], r["target"]): r
        for r in result.get("relations", [])
    }
    entities = {e["name"]: e for e in result.get("entities", [])}

    check("1. paraphrase 证据的关系保留",
          ("二叉树", "CONTAINS", "完全二叉树") in relations,
          str(sorted(relations)))

    check("2. 证据缺失（空串）的关系保留",
          ("二叉树", "RELATED_TO", "遍历") in relations,
          str(sorted(relations)))

    check("3. unmatched_mentions 不会自动补成实体",
          "红黑树" not in entities
          and len(result.get("unmatched_mentions", [])) == 1,
          f"entities={sorted(entities)} unmatched={result.get('unmatched_mentions')}")

    check("4. direction_basis 原样保留且不改变方向",
          relations[("二叉树", "CONTAINS", "完全二叉树")].get("direction_basis")
          == "二叉树是上位概念，完全二叉树是下位概念",
          str(relations[("二叉树", "CONTAINS", "完全二叉树")]))

    check("5. 中文实体名不被扩写/加前缀",
          all(name in SOURCE for name in entities)
          and "二叉树" in entities and "完全二叉树" in entities,
          str(sorted(entities)))

    check("6. confidence 阈值仍然生效（0.3 的 APPLIES_TO 被丢弃）",
          ("二叉树", "APPLIES_TO", "节点") not in relations
          and result["dropped_counts"].get("low_confidence") == 1,
          str(result["dropped_counts"]))

    duplicate = relations.get(("二叉树", "CONTAINS", "完全二叉树"))
    dup_stats = {(
        item["source"], item["type"], item["target"]): item
        for item in result.get("relation_duplicates", [])
    }
    check("7. 三元组去重不变，重复次数/不同证据被单独统计",
          duplicate is not None
          and len([r for r in result["relations"] if (
              r["source"], r["type"], r["target"]) == ("二叉树", "CONTAINS", "完全二叉树")]) == 1
          and dup_stats[("二叉树", "CONTAINS", "完全二叉树")]["occurrences"] == 2
          and dup_stats[("二叉树", "CONTAINS", "完全二叉树")]["distinct_evidences"] == 2,
          str(dup_stats))

    check("8. 实体 evidence 在合并后保留",
          entities["二叉树"].get("evidence") == "二叉树是一种树结构"
          and entities["完全二叉树"].get("evidence") == "完全二叉树包括二叉树",
          str({k: v.get("evidence") for k, v in entities.items()}))

    check("9. 最终诊断包含分块汇总、分块明细与四分类摘要",
          isinstance(result.get("diagnostics"), dict)
          and result["diagnostics"]["summary"]["entities"]["total"] == len(entities)
          and len(result["diagnostics"].get("chunks", [])) == result["chunk_count"]
          and len(result["diagnostics"].get("chunk_details", [])) == result["chunk_count"]
          and result["diagnostics"]["chunk_details"][0]["relations"],
          str(result.get("diagnostics", {}).get("summary")))

    # 10. 重试路径保持原行为，且重试成功的结果同样带诊断
    retry_result, retry_client = run_extract([("这不是 JSON", "stop"), (content, "stop")])
    check("10. 非法 JSON 仍重试一次，且诊断字段不丢",
          len(retry_client.records) == 2
          and not retry_result.get("error")
          and isinstance(retry_result.get("diagnostics"), dict),
          f"calls={len(retry_client.records)} error={retry_result.get('error')}")

    # 11. 旧调用方只传 results（无源码）时仍兼容
    legacy = KnowledgeExtractor._merge_results([{
        "entities": [{"name": "二叉树", "category": "概念", "description": "x"}],
        "relations": [],
    }])
    check("11. _merge_results 单参数旧调用兼容且诊断降级",
          legacy["diagnostics"]["summary"]["source_available"] is False
          and legacy["entities"][0]["name"] == "二叉树",
          str(legacy["diagnostics"]["summary"]))

    # 12. 旧缓存 payload 缺 diagnostics 时能现场补算，不抛异常
    annotated = KnowledgeExtractor._with_chunk_diagnostics(
        {"entities": [{"name": "二叉树", "evidence": "二叉树"}], "relations": []},
        SOURCE,
    )
    check("12. 旧缓存 payload 缺诊断时可补算",
          annotated["diagnostics"]["summary"]["entities"]["evidence"]["exact_match"] == 1,
          str(annotated["diagnostics"]["summary"]))

    total = 12
    print(f"\n通过 {total - len(failures)}/{total}")
    if failures:
        print("失败项：" + "；".join(failures))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
