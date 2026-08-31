"""
A10 知识抽取标准评测器

功能：
1. Entity 名称 Precision / Recall / F1
2. Entity Category Accuracy
3. Relation 严格匹配 Precision / Recall / F1
4. Relation 按类型统计：
   - PRECEDES
   - CONTAINS
   - RELATED_TO
   - APPLIES_TO
5. Relation Macro-F1
   - all_types：固定四类全部参与
   - active_types：Gold 或 Pred 中实际出现的类型参与
6. Relation Pair Match（辅助诊断指标）
   - 忽略关系类型
   - 忽略方向
   - 不作为 A10 主准确率指标
7. 输出 False Positive / False Negative
8. 支持多 text_id
9. 支持固定的实体名称归一化
10. 支持从已有 prediction.json 直接重算，不调用 LLM

正式评测原则：
- Gold 必须在预测前冻结
- 归一化规则必须在预测前冻结
- Prediction 不得反向修改 Gold
- confidence 仅为模型自评，不代表准确率
- 真实准确率只由 Gold 对比得到的 P/R/F1 表示

运行：
    python eval_accuracy.py \
        --gold eval_data/gold.json \
        --pred eval_data/pred.json \
        --output eval_data/eval_report.json

建议 Gold / Pred 格式：

[
  {
    "text_id": "chapter_01",
    "entities": [
      {
        "name": "函数调用",
        "category": "概念",
        "description": "..."
      }
    ],
    "relations": [
      {
        "source": "函数调用",
        "target": "递归",
        "type": "PRECEDES",
        "evidence": "...",
        "confidence": 0.96
      }
    ]
  }
]
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from app.services.knowledge_extractor import _normalize_entity_name


RELATION_TYPES = (
    "PRECEDES",
    "CONTAINS",
    "RELATED_TO",
    "APPLIES_TO",
)

ENTITY_CATEGORIES = (
    "概念",
    "定理",
    "公式",
    "方法",
)


# ============================================================
# 数据结构
# ============================================================

@dataclass
class PRF:
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float


@dataclass
class CategoryMetrics:
    total_gold: int
    total_pred: int
    correct: int
    accuracy: float


# ============================================================
# 基础工具
# ============================================================

def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def round4(value: float) -> float:
    return round(float(value), 4)


def prf_from_sets(pred: set, gold: set) -> PRF:
    """
    基于集合进行标准 P/R/F1 计算。
    """
    tp = len(pred & gold)
    fp = len(pred - gold)
    fn = len(gold - pred)

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall)
        else 0.0
    )

    return PRF(
        tp=tp,
        fp=fp,
        fn=fn,
        precision=round4(precision),
        recall=round4(recall),
        f1=round4(f1),
    )


def mean(values: list[float]) -> float:
    return round4(sum(values) / len(values)) if values else 0.0


# ============================================================
# 名称归一化
# ============================================================

# 正式评测前必须冻结。
#
# 不建议这里动态根据预测结果临时增加 Alias。
# 如果要修改，应该升级 normalization version，
# 然后重新进行完整评测。
ENTITY_ALIASES = {
    # 示例：
    # "CNN": "卷积神经网络",
    # "BFS": "广度优先遍历",
    # "DFS": "深度优先遍历",
}


def normalize_name(name: Any) -> str:
    """
    统一走项目本身的基础规范化，再应用固定 Alias。

    注意：
    Alias 必须在 Gold 标注冻结之前确定。
    """
    name = _normalize_entity_name(name or "")
    if not name:
        return ""

    return ENTITY_ALIASES.get(name, name)


# ============================================================
# Entity 提取
# ============================================================

def entity_set(entities: list[dict[str, Any]]) -> set[str]:
    result = set()

    for entity in entities:
        name = normalize_name(entity.get("name"))
        if name:
            result.add(name)

    return result


def entity_category_map(
    entities: list[dict[str, Any]],
) -> dict[str, str]:
    """
    name -> category

    如果同一实体重复出现多个 category：
    - 只保留第一次出现的值
    - 同时产生 warning
    """
    result: dict[str, str] = {}

    for entity in entities:
        name = normalize_name(entity.get("name"))
        category = (entity.get("category") or "").strip()

        if not name:
            continue

        if name in result and result[name] != category:
            # 这里不抛异常，因为评测应该尽量继续执行。
            # 外层会收集 warning。
            continue

        result[name] = category

    return result


def evaluate_entity_category(
    gold_entities: list[dict[str, Any]],
    pred_entities: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    只对实体名称匹配成功的实体评价 category。

    分母：
        evaluated = Gold / Pred 名称交集数量

    因此该指标表示：
        “已经识别出这个知识点以后，类别判断是否正确”
    """
    gold_map = entity_category_map(gold_entities)
    pred_map = entity_category_map(pred_entities)

    common_names = set(gold_map) & set(pred_map)

    correct = sum(
        1
        for name in common_names
        if gold_map[name] == pred_map[name]
    )

    evaluated = len(common_names)

    return {
        "gold_entity_count": len(gold_map),
        "pred_entity_count": len(pred_map),
        "evaluated_entity_count": evaluated,
        "correct": correct,
        "accuracy": round4(
            correct / evaluated
            if evaluated
            else 0.0
        ),
    }

# ============================================================
# Relation 提取
# ============================================================

def normalize_relation(
    relation: dict[str, Any],
) -> tuple[str, str, str] | None:
    source = normalize_name(relation.get("source"))
    target = normalize_name(relation.get("target"))
    rel_type = (relation.get("type") or "").strip().upper()

    if not source or not target:
        return None

    if rel_type not in RELATION_TYPES:
        return None

    if source == target:
        return None

    return source, rel_type, target


def relation_set(relations: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    result = set()

    for relation in relations:
        normalized = normalize_relation(relation)
        if normalized is not None:
            result.add(normalized)

    return result


def relation_pair_set(
    relations: list[dict[str, Any]],
) -> set[frozenset[str]]:
    """
    辅助诊断指标：
    忽略关系类型、忽略方向，只判断两个实体是否被建立过关系。

    注意：
    这个指标不能作为 A10 的主要准确率。
    """
    result = set()

    for relation in relations:
        normalized = normalize_relation(relation)
        if normalized is None:
            continue

        source, _, target = normalized
        if source == target:
            continue

        result.add(frozenset((source, target)))

    return result


# ============================================================
# 单文档评测
# ============================================================

def evaluate_document(
    gold_doc: dict[str, Any],
    pred_doc: dict[str, Any] | None,
) -> dict[str, Any]:

    if pred_doc is None:
        pred_doc = {
            "entities": [],
            "relations": [],
        }

    gold_entities = gold_doc.get("entities", [])
    pred_entities = pred_doc.get("entities", [])

    gold_relations = gold_doc.get("relations", [])
    pred_relations = pred_doc.get("relations", [])

    # -------------------------
    # Entity
    # -------------------------

    gold_entity_set = entity_set(gold_entities)
    pred_entity_set = entity_set(pred_entities)

    entity_metrics = prf_from_sets(
        pred=pred_entity_set,
        gold=gold_entity_set,
    )

    # -------------------------
    # Entity Category
    # -------------------------

    category_metrics = evaluate_entity_category(
        gold_entities,
        pred_entities,
    )

    # -------------------------
    # Relation Strict
    # -------------------------

    gold_relation_set = relation_set(gold_relations)
    pred_relation_set = relation_set(pred_relations)

    relation_strict = prf_from_sets(
        pred=pred_relation_set,
        gold=gold_relation_set,
    )

    # -------------------------
    # Relation Pair Match
    # -------------------------

    gold_pair_set = relation_pair_set(gold_relations)
    pred_pair_set = relation_pair_set(pred_relations)

    relation_pair = prf_from_sets(
        pred=pred_pair_set,
        gold=gold_pair_set,
    )

    # -------------------------
    # Per Relation Type
    # -------------------------

    relation_by_type: dict[str, dict[str, Any]] = {}

    for relation_type in RELATION_TYPES:
        gold_type = {
            relation
            for relation in gold_relation_set
            if relation[1] == relation_type
        }

        pred_type = {
            relation
            for relation in pred_relation_set
            if relation[1] == relation_type
        }

        metrics = prf_from_sets(
            pred=pred_type,
            gold=gold_type,
        )

        relation_by_type[relation_type] = {
            "gold": len(gold_type),
            "pred": len(pred_type),
            **asdict(metrics),
        }

    # -------------------------
    # Error Analysis
    # -------------------------

    false_positive_relations = sorted(
        pred_relation_set - gold_relation_set
    )

    false_negative_relations = sorted(
        gold_relation_set - pred_relation_set
    )

    # 只比较实体对相同、但关系类型不同的情况。
    pred_by_pair = {
        (source, target): relation_type
        for source, relation_type, target in pred_relation_set
    }

    gold_by_pair = {
        (source, target): relation_type
        for source, relation_type, target in gold_relation_set
    }

    type_confusions = []

    common_pairs = set(pred_by_pair) & set(gold_by_pair)

    for pair in sorted(common_pairs):
        pred_type = pred_by_pair[pair]
        gold_type = gold_by_pair[pair]

        if pred_type != gold_type:
            type_confusions.append(
                {
                    "source": pair[0],
                    "target": pair[1],
                    "gold_type": gold_type,
                    "pred_type": pred_type,
                }
            )

    return {
        "text_id": gold_doc["text_id"],
        "entity": asdict(entity_metrics),
        "entity_category": asdict(category_metrics),
        "relation_strict": asdict(relation_strict),
        "relation_pair_auxiliary": asdict(relation_pair),
        "relations_by_type": relation_by_type,
        "false_positive_relations": [
            {
                "source": source,
                "type": rel_type,
                "target": target,
            }
            for source, rel_type, target in false_positive_relations
        ],
        "false_negative_relations": [
            {
                "source": source,
                "type": rel_type,
                "target": target,
            }
            for source, rel_type, target in false_negative_relations
        ],
        "type_confusions": type_confusions,
    }


# ============================================================
# 数据完整性检查
# ============================================================

def validate_input(
    gold_docs: list[dict[str, Any]],
    pred_docs: list[dict[str, Any]],
) -> list[str]:

    warnings: list[str] = []

    gold_ids = [d.get("text_id") for d in gold_docs]
    pred_ids = [d.get("text_id") for d in pred_docs]

    if len(gold_ids) != len(set(gold_ids)):
        warnings.append("Gold 中存在重复 text_id。")

    if len(pred_ids) != len(set(pred_ids)):
        warnings.append("Pred 中存在重复 text_id。")

    gold_set = set(gold_ids)
    pred_set = set(pred_ids)

    missing_pred = sorted(gold_set - pred_set)
    extra_pred = sorted(pred_set - gold_set)

    if missing_pred:
        warnings.append(
            f"Pred 缺失 {len(missing_pred)} 个 text_id："
            f"{missing_pred}"
        )

    if extra_pred:
        warnings.append(
            f"Pred 存在 Gold 未定义的额外 text_id："
            f"{extra_pred}"
        )

    return warnings


# ============================================================
# 总体汇总
# ============================================================

def aggregate_document_metrics(
    document_results: list[dict[str, Any]],
) -> dict[str, Any]:

    # -------------------------
    # Micro：累加 TP/FP/FN
    # -------------------------

    def aggregate_prf(path: list[str]) -> dict[str, Any]:
        tp = sum(
            _deep_get(result, path)["tp"]
            for result in document_results
        )
        fp = sum(
            _deep_get(result, path)["fp"]
            for result in document_results
        )
        fn = sum(
            _deep_get(result, path)["fn"]
            for result in document_results
        )

        metrics = _prf_from_counts(tp, fp, fn)
        return asdict(metrics)

    # -------------------------
    # Macro：以 document 为单位平均
    # -------------------------

    def macro_average(path: list[str]) -> dict[str, Any]:
        values = [
            _deep_get(result, path)
            for result in document_results
        ]

        return {
            "precision": mean([v["precision"] for v in values]),
            "recall": mean([v["recall"] for v in values]),
            "f1": mean([v["f1"] for v in values]),
        }

    summary = {
        "entity_micro": aggregate_prf(["entity"]),
        "entity_macro": macro_average(["entity"]),

        "relation_strict_micro": aggregate_prf(["relation_strict"]),
        "relation_strict_macro_by_document": macro_average(
            ["relation_strict"]
        ),

        "relation_pair_auxiliary_micro": aggregate_prf(
            ["relation_pair_auxiliary"]
        ),

        "entity_category_accuracy": round4(
            (
                sum(
                    r["entity_category"]["correct"]
                    for r in document_results
                )
                /
                sum(
                    len(
                        set(
                            # 这里最终值由单文档 evaluate 给出；
                            # denominator 使用 pred/gold common entity
                            # 不再重复读取原始文件。
                            []
                        )
                    )
                )
            )
            if False
            else _aggregate_category_accuracy(document_results)
        ),
    }

    # -------------------------
    # Relation Type
    # -------------------------

    relation_type_summary: dict[str, Any] = {}

    for relation_type in RELATION_TYPES:
        type_results = [
            result["relations_by_type"][relation_type]
            for result in document_results
        ]

        tp = sum(x["tp"] for x in type_results)
        fp = sum(x["fp"] for x in type_results)
        fn = sum(x["fn"] for x in type_results)

        micro = _prf_from_counts(tp, fp, fn)

        active_f1_values = [
            x["f1"]
            for x in type_results
            if x["gold"] > 0 or x["pred"] > 0
        ]

        relation_type_summary[relation_type] = {
            "gold_total": sum(x["gold"] for x in type_results),
            "pred_total": sum(x["pred"] for x in type_results),
            "micro": asdict(micro),
            "mean_document_f1": mean(
                [x["f1"] for x in type_results]
            ),
            "active_document_f1": mean(active_f1_values),
        }

    # -------------------------
    # Macro-F1
    # -------------------------

    all_type_f1 = [
        relation_type_summary[t]["micro"]["f1"]
        for t in RELATION_TYPES
    ]

    active_types = [
        t
        for t in RELATION_TYPES
        if (
            relation_type_summary[t]["gold_total"] > 0
            or relation_type_summary[t]["pred_total"] > 0
        )
    ]

    active_type_f1 = [
        relation_type_summary[t]["micro"]["f1"]
        for t in active_types
    ]

    relation_macro_f1 = {
        "all_types": mean(all_type_f1),
        "active_types": mean(active_type_f1),
        "active_type_names": active_types,
    }

    summary["relation_by_type"] = relation_type_summary
    summary["relation_macro_f1"] = relation_macro_f1

    return summary


def _aggregate_category_accuracy(
    document_results: list[dict[str, Any]],
) -> float:
    """
    以所有文档中“名称匹配成功的实体”作为 category accuracy 分母。
    """
    correct = 0
    evaluated = 0

    for result in document_results:
        # 当前 evaluate_document 没直接保存 common_entity_count，
        # 因此为了避免重复设计，这里暂时从 category_metrics 的：
        # total_gold/total_pred 无法准确恢复 common。
        #
        # 后续正式版建议在 evaluate_document 中加入：
        # evaluated_entity_count
        #
        # 这里保留为兼容实现：
        category = result["entity_category"]
        correct += category["correct"]

        # 暂用 max(total_gold, total_pred) 不是最佳定义。
        # 为避免产生误导，这里返回 0，直到加入 explicit denominator。
        # 正式代码应直接使用 evaluated_entity_count。
        evaluated += category.get("evaluated_entity_count", 0)

    if evaluated == 0:
        return 0.0

    return correct / evaluated


def _prf_from_counts(
    tp: int,
    fp: int,
    fn: int,
) -> PRF:

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )

    return PRF(
        tp=tp,
        fp=fp,
        fn=fn,
        precision=round4(precision),
        recall=round4(recall),
        f1=round4(f1),
    )


def _deep_get(data: dict[str, Any], path: list[str]) -> dict[str, Any]:
    current: Any = data

    for key in path:
        current = current[key]

    return current


# ============================================================
# 主评测
# ============================================================

def evaluate_all(
    gold_docs: list[dict[str, Any]],
    pred_docs: list[dict[str, Any]],
) -> dict[str, Any]:

    warnings = validate_input(gold_docs, pred_docs)

    gold_map = {
        doc["text_id"]: doc
        for doc in gold_docs
    }

    pred_map = {
        doc["text_id"]: doc
        for doc in pred_docs
    }

    document_results = []

    for text_id in sorted(gold_map):
        result = evaluate_document(
            gold_doc=gold_map[text_id],
            pred_doc=pred_map.get(text_id),
        )
        document_results.append(result)

    summary = aggregate_document_metrics(
        document_results
    )

    return {
        "evaluation_version": "1.0",
        "relation_types": list(RELATION_TYPES),
        "entity_categories": list(ENTITY_CATEGORIES),

        "metric_policy": {
            "entity_primary": "name_exact_after_frozen_normalization",
            "relation_primary": "strict_source_type_target",
            "relation_pair": (
                "auxiliary_only_ignore_type_and_direction"
            ),
            "confidence_is_not_accuracy": True,
            "accuracy_source": "manual_gold_annotation",
        },

        "normalization": {
            "version": "v1.0",
            "aliases": ENTITY_ALIASES,
        },

        "warnings": warnings,

        "summary": summary,

        "documents": document_results,
    }


# ============================================================
# 控制台输出
# ============================================================

def print_report(report: dict[str, Any]) -> None:
    summary = report["summary"]

    print("=" * 78)
    print("A10 知识抽取标准评测")
    print("=" * 78)

    print("\n[实体]")
    e = summary["entity_micro"]
    print(
        f"Micro P={e['precision']:.4f} "
        f"R={e['recall']:.4f} "
        f"F1={e['f1']:.4f}"
    )

    print("\n[实体类别]")
    print(
        "Category Accuracy="
        f"{summary['entity_category_accuracy']:.4f}"
    )

    print("\n[关系-严格匹配]")
    r = summary["relation_strict_micro"]
    print(
        f"Micro P={r['precision']:.4f} "
        f"R={r['recall']:.4f} "
        f"F1={r['f1']:.4f}"
    )

    print("\n[关系-宽松辅助指标]")
    aux = summary["relation_pair_auxiliary_micro"]
    print(
        f"Pair Match P={aux['precision']:.4f} "
        f"R={aux['recall']:.4f} "
        f"F1={aux['f1']:.4f}"
    )
    print("注意：该指标忽略关系类型和方向，不作为主准确率。")

    print("\n[各关系类型]")
    print(
        f"{'Type':<15}"
        f"{'Gold':>8}"
        f"{'Pred':>8}"
        f"{'P':>10}"
        f"{'R':>10}"
        f"{'F1':>10}"
    )

    for relation_type in RELATION_TYPES:
        d = summary["relation_by_type"][relation_type]
        m = d["micro"]

        print(
            f"{relation_type:<15}"
            f"{d['gold_total']:>8}"
            f"{d['pred_total']:>8}"
            f"{m['precision']:>10.4f}"
            f"{m['recall']:>10.4f}"
            f"{m['f1']:>10.4f}"
        )

    macro = summary["relation_macro_f1"]

    print("\n[Relation Macro-F1]")
    print(
        f"All 4 types   = {macro['all_types']:.4f}"
    )
    print(
        f"Active types  = {macro['active_types']:.4f}"
        f"  ({', '.join(macro['active_type_names'])})"
    )

    print("\n[错误分析摘要]")
    total_fp = 0
    total_fn = 0
    total_confusion = 0

    for doc in report["documents"]:
        total_fp += len(doc["false_positive_relations"])
        total_fn += len(doc["false_negative_relations"])
        total_confusion += len(doc["type_confusions"])

    print(f"Relation FP = {total_fp}")
    print(f"Relation FN = {total_fn}")
    print(f"Type Confusion = {total_confusion}")

    if report["warnings"]:
        print("\n[Warnings]")
        for warning in report["warnings"]:
            print(f"- {warning}")

    print("\n[评测口径]")
    print("Confidence 是模型自评置信度，不代表真实准确率。")
    print(
        "真实准确率仅根据人工 Gold 标注计算 "
        "Precision / Recall / F1。"
    )


# ============================================================
# CLI
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="A10 知识抽取标准评测器"
    )

    parser.add_argument(
        "--gold",
        required=True,
        help="Gold JSON 文件",
    )

    parser.add_argument(
        "--pred",
        required=True,
        help="Prediction JSON 文件",
    )

    parser.add_argument(
        "--output",
        default="eval_data/eval_report.json",
        help="输出报告路径",
    )

    args = parser.parse_args()

    gold_path = Path(args.gold)
    pred_path = Path(args.pred)
    output_path = Path(args.output)

    gold_docs = load_json(gold_path)
    pred_docs = load_json(pred_path)

    if not isinstance(gold_docs, list):
        raise ValueError("Gold 必须是 list。")

    if not isinstance(pred_docs, list):
        raise ValueError("Pred 必须是 list。")

    report = evaluate_all(
        gold_docs=gold_docs,
        pred_docs=pred_docs,
    )

    save_json(output_path, report)
    print_report(report)

    print("\n" + "=" * 78)
    print(f"完整评测报告：{output_path}")
    print("=" * 78)


if __name__ == "__main__":
    main()