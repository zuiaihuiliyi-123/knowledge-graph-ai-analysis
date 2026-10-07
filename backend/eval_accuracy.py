"""
A10 知识抽取标准评测器 —— 全仓库唯一的准确率测量实现

================================================================================
【为什么只有一个文件】
================================================================================
本文件是"知识抽取准确率"的唯一事实来源（single source of truth）。

历史上仓库存在三套并行实现，别名表、名称规范化、gold 格式、指标公式各不相同：

    test_extraction_accuracy.py : 硬编码 gold + 硬编码 708 字节测试文本 → 产出 0.93
    eval_accuracy_live.py       : 18 条别名 + 硬编码第7章路径         → 产出售 0.164
    eval_accuracy.py            : 空别名表 + list-of-docs 格式        → 未产出已发布数字

三者的 0.93 与 0.164 互相不可比且都无法复现。本次收敛后：

    - 本文件 = 唯一的评分实现（归一化、匹配、指标、归因、报告）
    - 抽取驱动（调 LLM）也收进本文件 --live 模式，故全仓库只剩这一个 eval_* 文件

================================================================================
【测量口径】
================================================================================
实体主指标
    名称精确匹配，匹配前经过冻结的归一化（_normalize_entity_name + ENTITY_ALIASES）。

关系主指标 relation_primary（赛题申报口径）
    Precision = |Pred ∩ Full| / |Pred|
    Recall    = |Pred ∩ Core| / |Core|
    定义动机：金标准若采用"宁可漏标"的保守标注，而模型输出是"尽可能全"的，
    用选择性 gold 算完备性预测的 Precision 会系统性失真。故分两层：
        Core 集：文本明确、无争议、按决策树必然成立的关系（gold 中 core=true）
        Full 集：Core ∪ 合理但边界模糊的关系（gold 中全部关系）
    模型输出仍须落在 Full 集内才算对；方向错误、类型错误、真臆造照样全额扣分。
    注意：旧 gold 未标 core 字段时 Core == Full，行为与旧版严格匹配完全一致。

辅助指标（全部保留，作为诊断层）
    relation_core      保守下界（Pred 对 Core 的完整 P/R/F1）
    relation_full      Full 集上的完整 P/R/F1
    实体类别准确率、按关系类型 P/R/F1、Macro-F1、忽略方向的 Pair Match、方向错误率

置信度
    gold/pred 里的 confidence 是模型自评置信度，不代表真实准确率，
    不参与任何 Precision / Recall / F1 计算。

================================================================================
【Gold 格式】
================================================================================
[
  {
    "text_id": "数据结构_第7章_树",
    "source": "Hello算法_第7章_树",
    "entities": [
      {"name": "完美二叉树", "category": "概念", "description": "一句话描述"}
    ],
    "relations": [
      {"source": "二叉树", "target": "完美二叉树", "type": "CONTAINS",
       "evidence": "原文逐字短句", "confidence": 0.9, "core": true}
    ]
  }
]

【Pred 格式】（本文件 --live 模式写出，也可手工构造）
[
  {
    "text_id": "数据结构_第7章_树",
    "runs": [
      {"entities": [...], "relations": [...]},
      ...                                    # 多轮；单轮可省略 runs 层
    ]
  }
]

================================================================================
【用法】
================================================================================
    # ① 实时抽取并落盘预测（此步之后不再调用 LLM）
    python eval_accuracy.py --live \
        --gold eval_data/gold_第7章_树.json \
        --text eval_data/第7章_树.txt \
        --runs 3 --temperature 0 \
        --pred eval_data/pred_第7章_树.json

    # ② 离线评分（不调 LLM，可反复重算）
    python eval_accuracy.py \
        --gold eval_data/gold_第7章_树.json \
        --pred eval_data/pred_第7章_树.json \
        --output eval_data/eval_report_第7章_树.json \
        --markdown eval_data/评测报告_第7章_树.md

正式评测原则
    - Gold 必须在预测前冻结
    - 归一化规则必须在预测前冻结
    - Prediction 不得反向修改 Gold
    - 禁止预测后修改 gold / 别名表 / 匹配规则；如需修改，升级版本号并全量重算
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import statistics
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

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

# 归一化版本号。修改 ENTITY_ALIASES 或 _normalize_entity_name 后必须递增，
# 并全量重算所有历史数字——否则新老报告不可比。
NORMALIZATION_VERSION = "n1"


# ============================================================
# 丢弃原因代号
# ============================================================
#
# 与 knowledge_extractor._merge_results 的记账保持同一套代号，勿单方面改名。
#
# 这些原因**不是**同一件事，此前被混为一谈（全部计入 synonym_split）：
#   invalid_endpoint      端点为空            —— Prompt 输出结构缺陷
#   invalid_relation_type 关系类型非法        —— 违反四类白名单
#   synonym_split         归一化后自环        —— 同一知识点被拆成两个写法
#   dangling_endpoint     端点不在实体表中    —— 关系与实体列表不一致
#   duplicate             重复三元组          —— 多因分块重叠
#   low_confidence        低于置信度阈值      —— **抽到了但被本系统过滤掉**
#
# 最后一项最值得注意：这类关系如果本来是对的，会被 missed_core 记成
# "完全漏抽"，从而把"阈值设太严"误诊为"模型召回不足"。
DROP_REASON_LABELS = {
    "invalid_endpoint": "端点为空",
    "invalid_relation_type": "关系类型非法",
    "synonym_split": "归一化后构成自环（同义拆分）",
    "dangling_endpoint": "端点不在实体表中（悬空边）",
    "duplicate": "重复三元组",
    "low_confidence": "低于置信度阈值被过滤",
}


# ============================================================
# 归一化：全仓库唯一一份别名表（正式评测前必须冻结）
# ============================================================
#
# 合并来源：
#   1. eval_accuracy_live.py  的 18 条（第7章评测实际使用，含标注说明 §二 的规则）
#   2. test_extraction_accuracy.py 的 2 条（数据结构第一章评测使用）
#
# 冻结纪律：如需新增，递增 NORMALIZATION_VERSION 并全量重算。
# 严禁"看到预测结果后再补别名"——那是变相的追数据。

ENTITY_ALIASES = {
    # --- 来自标注规范（第7章_树_标注说明.md §二）---
    "满二叉树": "完美二叉树",
    "广度优先搜索": "广度优先遍历",
    "深度优先搜索": "深度优先遍历",
    "BFS": "广度优先遍历",
    "DFS": "深度优先遍历",
    "节点的高度": "高度",
    "节点高度": "高度",
    "二叉树的高度": "高度",
    "节点的深度": "深度",
    # --- 来自第7章首轮评测差异分析（模型 3 轮稳定出现的写法变体）---
    "AVL树": "AVL 树",
    "旋转操作": "旋转",
    "先右旋再左旋": "先右旋后左旋",
    "先左旋再右旋": "先左旋后右旋",
    "插入操作": "插入节点",
    "删除操作": "删除节点",
    "查找操作": "查找节点",
    "索引映射公式": "映射公式",
    "数组表示法": "数组表示",
    # --- 来自数据结构第一章评测 ---
    "顺序存储结构": "顺序表",
    "链式存储结构": "链表",
}


def aliases_sha256() -> str:
    """别名表指纹，写入报告头，保证"这份数字用的是哪张表"可追溯。"""
    payload = json.dumps(
        ENTITY_ALIASES, ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
class PrimaryPRF:
    """
    主指标：Precision 与 Recall 使用不同的分母集合，故 tp/fp/fn 无法用
    单一三元组表达，这里显式列出各计数，避免误读。
    """
    pred_total: int
    core_total: int
    full_total: int
    hit_core: int          # |Pred ∩ Core|，即真正"找到且必须找到"的
    hit_full: int          # |Pred ∩ Full|，即"输出且被金标准认可"的
    precision: float       # hit_full / pred_total
    recall: float          # hit_core / core_total
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
    """
    读 JSON，失败时给出可读原因而不是原始 JSONDecodeError。

    直接 json.load 遇到空文件只会说 "Expecting value: line 1 column 1"，
    看到这句话的人无从判断是自己格式写错了、还是文件根本没保存。
    实测最常见的就是编辑器里改了没保存、磁盘上仍是 0 字节。
    """
    if not path.is_file():
        raise ValueError(f"文件不存在：{path}")

    if path.stat().st_size == 0:
        raise ValueError(
            f"文件是空的（0 字节）：{path}\n"
            "多半是编辑器里改过但没保存。保存后重试。"
        )

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"不是合法 JSON：{path}\n"
            f"  位置：第 {error.lineno} 行第 {error.colno} 列\n"
            f"  原因：{error.msg}"
        ) from error


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_docs(path: Path, label: str) -> list[dict[str, Any]]:
    """
    读取 gold / pred 并统一成 list-of-docs。

    兼容「单章直接写成裸 dict」的写法，此时自动包成 [doc]，与
    scripts/validate_gold.py 的兼容口径一致。标注员手里的单章文件
    天然就是一个 dict，强制再手写一层 list 只会让人额外维护一份
    转换副本；而两份副本会漂移 —— 改了原件忘了重新转换，评测就会
    静默地用旧 gold 算分。

    这里只做结构校验，text_id 是否缺失留给 ensure_text_ids 判断：
    能不能省略取决于「另一边有几个文档」，只看单个文件是不够的。
    """
    data = load_json(path)

    if isinstance(data, dict):
        docs = [data]
        print(f"提示：{label} 是单个文档（裸 dict），已按 [doc] 处理。")
    elif isinstance(data, list):
        docs = data
    else:
        raise ValueError(
            f"{label} 顶层必须是 list（list-of-docs）或 dict（单个文档），"
            f"实际是 {type(data).__name__}：{path}"
        )

    if not docs:
        raise ValueError(f"{label} 是空列表，里面没有任何文档：{path}")

    bad = [index for index, doc in enumerate(docs) if not isinstance(doc, dict)]
    if bad:
        raise ValueError(
            f"{label} 的第 {bad} 个元素不是对象（dict），"
            f"实际是 {type(docs[bad[0]]).__name__}：{path}"
        )

    return docs


def ensure_text_ids(
    gold_docs: list[dict[str, Any]],
    pred_docs: list[dict[str, Any]] | None,
    gold_path: Path,
) -> None:
    """
    确保 gold 与 pred 都带 text_id，能唯一补出来时就地补上，补不出来才报错。

    text_id 是两边的配对键。只有在「两边各自只有一个文档」时才可以省：
    此时配对关系是唯一确定的，补出来的 id 不可能把谁跟谁配错。一旦任意
    一边有多个文档，缺 text_id 就无法判断哪份 gold 该配哪份 pred，这时
    必须报错让人自己写清楚 —— 放任它跑会静默地拿 A 章的 gold 去算 B 章
    的 pred，分数看着正常但毫无意义。

    pred_docs 传 None 表示只处理 gold 一侧（--live 模式在 pred 落盘之前
    就要用到 text_id，此时还没有 pred 文件可读）。

    补出来的 id 只存在于本次运行的内存里，不写回任何文件。
    """
    gold_missing = [i for i, doc in enumerate(gold_docs) if not doc.get("text_id")]
    pred_missing = (
        [i for i, doc in enumerate(pred_docs) if not doc.get("text_id")]
        if pred_docs is not None
        else []
    )

    if not gold_missing and not pred_missing:
        return

    unambiguous = len(gold_docs) == 1 and (
        pred_docs is None or len(pred_docs) == 1
    )

    if not unambiguous:
        side, missing = (
            ("Gold", gold_missing) if gold_missing else ("Pred", pred_missing)
        )
        raise ValueError(
            f"{side} 的第 {missing} 个文档缺少 text_id。\n"
            "text_id 是 gold 与 pred 的配对键，两边必须写成同一个值；\n"
            "只有在两边各自只有一个文档时才可以省略（此时配对唯一）。\n"
            "按下面这样给每个文档补上即可：\n"
            '  {"text_id": "Python编程_第2章_表达式", '
            '"entities": [...], "relations": [...]}'
        )

    # 两边各只有一个文档：用 gold 已有的 text_id，没有就取 gold 文件名。
    derived = gold_docs[0].get("text_id") or gold_path.stem
    gold_docs[0]["text_id"] = derived
    if pred_docs is not None:
        pred_docs[0]["text_id"] = derived

    print(
        f"提示：两边各只有一个文档，未写 text_id，已临时取 "
        f"{derived!r} 作为配对键（仅本次运行内存内生效，不写回文件）。"
    )


def round4(value: float) -> float:
    return round(float(value), 4)


def mean(values: list[float]) -> float:
    return round4(sum(values) / len(values)) if values else 0.0


def stdev(values: list[float]) -> float:
    """样本标准差（<2 个样本返回 0）。用于多轮运行的抖动度量。"""
    if len(values) < 2:
        return 0.0
    return round4(statistics.stdev(values))


def prf_from_sets(pred: set, gold: set) -> PRF:
    """基于集合的标准 P/R/F1。"""
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


def primary_prf(
    pred: set[tuple[str, str, str]],
    core: set[tuple[str, str, str]],
    full: set[tuple[str, str, str]],
) -> PrimaryPRF:
    """主指标：Precision 用 Full 集，Recall 用 Core 集。"""
    hit_core = len(pred & core)
    hit_full = len(pred & full)

    precision = hit_full / len(pred) if pred else 0.0
    recall = hit_core / len(core) if core else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall)
        else 0.0
    )

    return PrimaryPRF(
        pred_total=len(pred),
        core_total=len(core),
        full_total=len(full),
        hit_core=hit_core,
        hit_full=hit_full,
        precision=round4(precision),
        recall=round4(recall),
        f1=round4(f1),
    )


# ============================================================
# 名称归一化
# ============================================================

def normalize_name(name: Any) -> str:
    """
    统一走项目本身的基础规范化（全角转半角 / 折叠空白 / 纯英文大写），
    再应用冻结别名表。

    历史教训：eval_accuracy_live.py 与 test_extraction_accuracy.py 曾各自
    实现为裸 .strip()，导致同一实体在两份报告里是否匹配不一致。
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
    """
    result: dict[str, str] = {}

    for entity in entities:
        name = normalize_name(entity.get("name"))
        category = (entity.get("category") or "").strip()

        if not name:
            continue

        if name in result and result[name] != category:
            continue

        result[name] = category

    return result


def evaluate_entity_category(
    gold_entities: list[dict[str, Any]],
    pred_entities: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    只对实体名称匹配成功的实体评价 category。

    分母 evaluated = Gold / Pred 名称交集数量。
    该指标表示："已经识别出这个知识点以后，类别判断是否正确"。
    """
    gold_map = entity_category_map(gold_entities)
    pred_map = entity_category_map(pred_entities)

    common_names = set(gold_map) & set(pred_map)

    correct = sum(
        1 for name in common_names if gold_map[name] == pred_map[name]
    )

    evaluated = len(common_names)

    return {
        "gold_entity_count": len(gold_map),
        "pred_entity_count": len(pred_map),
        "evaluated_entity_count": evaluated,
        "correct": correct,
        "accuracy": round4(correct / evaluated if evaluated else 0.0),
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


def relation_set(
    relations: list[dict[str, Any]],
) -> set[tuple[str, str, str]]:
    result = set()

    for relation in relations:
        normalized = normalize_relation(relation)
        if normalized is not None:
            result.add(normalized)

    return result


def collect_dropped_relations(
    relations: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """
    收集被 normalize_relation / 去重 丢弃的关系及原因。

    原因代号（与 knowledge_extractor._merge_results 的记账对齐，勿单方面改名）：
        invalid_endpoint      端点为空
        invalid_relation_type 关系类型非法
        synonym_split         归一化后构成自环（同义拆分）
        duplicate             重复三元组

    历史教训：本函数曾把上述四种原因统统归入 synonym_split，等于把
    "端点为空"和"类型非法"也算成同义拆分。三者是完全不同的 Prompt 缺陷，
    混在一个桶里等于这个归因列不可用。

    返回的 reason_code 供程序消费，reason 供人阅读。
    """
    dropped: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for relation in relations:
        source = normalize_name(relation.get("source"))
        target = normalize_name(relation.get("target"))
        rel_type = (relation.get("type") or "").strip().upper()

        def _record(reason_code: str) -> None:
            dropped.append({
                "source": source or str(relation.get("source") or ""),
                "type": rel_type,
                "target": target or str(relation.get("target") or ""),
                "reason_code": reason_code,
                "reason": DROP_REASON_LABELS[reason_code],
            })

        if not source or not target:
            _record("invalid_endpoint")
        elif rel_type not in RELATION_TYPES:
            _record("invalid_relation_type")
        elif source == target:
            _record("synonym_split")
        elif (source, rel_type, target) in seen:
            _record("duplicate")
        else:
            seen.add((source, rel_type, target))

    return dropped


@dataclass
class DropRecord:
    """一次预测中"被丢弃、未进入评分"的关系汇总。"""
    total: int
    by_reason: dict[str, int]
    samples: list[dict[str, Any]]
    raw_total: int          # 模型实际输出的关系条数（过滤前）
    raw_source: str         # "extractor"（取自抽取器记账）| "derived"（由预测文件推导）
    balanced: bool          # raw == scored + total 是否成立


def collect_all_drops(
    pred_doc: dict[str, Any],
    scored_total: int,
) -> DropRecord:
    """
    汇总丢弃记录。两个来源互斥，不会重复计数：

      ① 抽取器侧：pred_doc["dropped_counts"] / ["dropped_relations"]。
         `_merge_results` 在关系写入预测文件之前就按白名单/自环/悬空/重复/
         置信度阈值过滤，这些关系**不在** pred_doc["relations"] 里。
      ② 评测器侧：对 pred_doc["relations"] 再跑一遍 collect_dropped_relations。
         仅对手工构造的预测文件有效；live 路径下这一侧恒为空。

    对账等式：raw_total == scored_total + total。
    不成立时置 balanced=False，由报告显式披露，而不是悄悄给出一个对不上的归因。
    """
    relations = pred_doc.get("relations") or []
    local = collect_dropped_relations(relations)

    upstream_counts = pred_doc.get("dropped_counts") or {}
    upstream_samples = pred_doc.get("dropped_relations") or []

    if upstream_counts or upstream_samples or pred_doc.get("raw_relation_count") is not None:
        # ① 抽取器已给出精确记账，以它为准
        by_reason = {k: int(v) for k, v in upstream_counts.items()}
        if not by_reason:  # 只有明细、无计数时按明细回推
            for item in upstream_samples:
                code = item.get("reason_code", "unknown")
                by_reason[code] = by_reason.get(code, 0) + 1
        raw_total = pred_doc.get("raw_relation_count")
        if raw_total is None:
            raw_total = len(relations) + sum(by_reason.values())
        raw_source = "extractor"
    else:
        # ② 手写预测：relations 就是原始输出，丢弃项是它的子集
        by_reason = {}
        for item in local:
            code = item["reason_code"]
            by_reason[code] = by_reason.get(code, 0) + 1
        raw_total = len(relations)
        raw_source = "derived"

    total = sum(by_reason.values())

    return DropRecord(
        total=total,
        by_reason=by_reason,
        samples=list(upstream_samples) + local,
        raw_total=raw_total,
        raw_source=raw_source,
        balanced=(raw_total == scored_total + total),
    )


def relation_pair_set(
    relations: list[dict[str, Any]],
) -> set[frozenset[str]]:
    """
    辅助诊断指标：忽略关系类型、忽略方向，只判断两个实体是否被建立过关系。

    注意：这个指标不能作为 A10 的主要准确率。
    """
    result = set()

    for relation in relations:
        normalized = normalize_relation(relation)
        if normalized is None:
            continue

        source, _, target = normalized
        result.add(frozenset((source, target)))

    return result


def gold_core_relations(
    gold_relations: list[dict[str, Any]],
) -> set[tuple[str, str, str]]:
    """
    Core 集：gold 中 core=true 的关系。
    未标 core 字段视为 true（向后兼容旧 gold，行为等同旧版严格匹配）。
    """
    result = set()

    for relation in gold_relations:
        if relation.get("core", True) is False:
            continue
        normalized = normalize_relation(relation)
        if normalized is not None:
            result.add(normalized)

    return result


def gold_full_relations(
    gold_relations: list[dict[str, Any]],
) -> set[tuple[str, str, str]]:
    """Full 集：gold 中全部关系（Core ∪ 边界模糊项）。"""
    return relation_set(gold_relations)


# ============================================================
# 错误归因（5 类）
# ============================================================

def build_error_taxonomy(
    gold_relations: list[dict[str, Any]],
    pred_relations: list[dict[str, Any]],
    drops: "DropRecord | None" = None,
) -> dict[str, Any]:
    """
    把 FP / FN 自动归入 5 类，供 Prompt 迭代定位。

    已进入评分的预测（与 gold 比对后分三类）：
      1. fabricated         臆造：节点对不在 Full 集
      2. direction_reversed 方向颠倒：节点对命中，但方向相反
      3. type_confused      类型混淆：节点对与方向都对，关系类型错
    未进入评分的预测（在评分之前就被丢弃，见 drops）：
      4. synonym_split      同义拆分：归一化后构成自环（仅此一种，不再混入其他）
    漏报：
      5. missed_core        完全漏抽：Core 中未被任何预测触及

    另有 dropped_* 一组计数披露全部丢弃原因。它们不进 5 类主体，因为
    "被本系统过滤掉"与"模型判断错误"是两回事，混在一起会误导 Prompt 调整方向。
    """
    core = gold_core_relations(gold_relations)
    full = gold_full_relations(gold_relations)

    full_pairs = {frozenset((s, t)) for s, _, t in full}
    full_ordered = {(s, t): typ for s, typ, t in full}

    pred = relation_set(pred_relations)

    fabricated: list[dict[str, str]] = []
    direction_reversed: list[dict[str, str]] = []
    type_confused: list[dict[str, str]] = []

    for source, rel_type, target in sorted(pred):
        pair = frozenset((source, target))

        if pair not in full_pairs:
            fabricated.append(
                {"source": source, "type": rel_type, "target": target}
            )
        elif (source, target) not in full_ordered:
            # 节点对在 Full 中，但方向相反
            direction_reversed.append({
                "source": source, "type": rel_type, "target": target,
                "gold_type": full_ordered.get((target, source), ""),
            })
        elif full_ordered[(source, target)] != rel_type:
            type_confused.append({
                "source": source, "type": rel_type, "target": target,
                "gold_type": full_ordered[(source, target)],
            })

    if drops is None:
        drops = collect_all_drops({"relations": pred_relations}, len(pred))

    # synonym_split 只取"归一化后自环"这一种，其余丢弃原因单独成列
    synonym_split = [
        d for d in drops.samples if d.get("reason_code") == "synonym_split"
    ]

    missed_core = [
        {"source": s, "type": t, "target": o}
        for s, t, o in sorted(core - pred)
    ]

    counts = {
        # ---- 已评分预测的三类 FP ----
        "fabricated": len(fabricated),
        "direction_reversed": len(direction_reversed),
        "type_confused": len(type_confused),
        # ---- 未评分（评分前被丢弃）----
        "synonym_split": drops.by_reason.get("synonym_split", 0),
        "invalid_endpoint": drops.by_reason.get("invalid_endpoint", 0),
        "invalid_relation_type": drops.by_reason.get("invalid_relation_type", 0),
        "dangling_endpoint": drops.by_reason.get("dangling_endpoint", 0),
        "duplicate": drops.by_reason.get("duplicate", 0),
        "low_confidence": drops.by_reason.get("low_confidence", 0),
        # ---- 漏报 ----
        "missed_core": len(missed_core),
        # ---- 对账（三个数必须满足 raw == scored + dropped）----
        "pred_raw_total": drops.raw_total,
        "pred_total": len(pred),
        "dropped_total": drops.total,
        "core_total": len(core),
        "full_total": len(full),
    }

    return {
        "counts": counts,
        # 非计数信息单独放，避免混进 counts 后被跨文档求和
        "accounting": {
            "identity": "pred_raw_total == pred_total + dropped_total",
            "raw_source": drops.raw_source,
            "balanced": drops.balanced,
        },
        "direction_reversed": direction_reversed,
        "type_confused": type_confused,
        "synonym_split": synonym_split,
        "dropped_relations": drops.samples,
        "missed_core": missed_core,
        "fabricated": fabricated,
    }


# ============================================================
# 单文档评测
# ============================================================

def evaluate_document(
    gold_doc: dict[str, Any],
    pred_doc: dict[str, Any] | None,
) -> dict[str, Any]:

    if pred_doc is None:
        pred_doc = {"entities": [], "relations": []}

    gold_entities = gold_doc.get("entities", [])
    pred_entities = pred_doc.get("entities", [])

    gold_relations = gold_doc.get("relations", [])
    pred_relations = pred_doc.get("relations", [])

    # ---- Entity ----
    gold_entity_set = entity_set(gold_entities)
    pred_entity_set = entity_set(pred_entities)

    entity_metrics = prf_from_sets(
        pred=pred_entity_set, gold=gold_entity_set
    )

    # ---- Entity Category ----
    category_metrics = evaluate_entity_category(
        gold_entities, pred_entities
    )

    # ---- Relation：三层口径 ----
    gold_core = gold_core_relations(gold_relations)
    gold_full = gold_full_relations(gold_relations)
    pred_relation_set = relation_set(pred_relations)

    relation_primary = primary_prf(
        pred=pred_relation_set, core=gold_core, full=gold_full
    )
    relation_core = prf_from_sets(pred=pred_relation_set, gold=gold_core)
    relation_full = prf_from_sets(pred=pred_relation_set, gold=gold_full)

    # ---- Relation Pair（辅助诊断）----
    gold_pair_set = relation_pair_set(gold_relations)
    pred_pair_set = relation_pair_set(pred_relations)

    relation_pair = prf_from_sets(
        pred=pred_pair_set, gold=gold_pair_set
    )

    # ---- 错误归因 ----
    # drops 需要 pred_doc 里的抽取器记账（dropped_counts / raw_relation_count），
    # 故整体传入而不是只传 relations
    taxonomy = build_error_taxonomy(
        gold_relations,
        pred_relations,
        drops=collect_all_drops(pred_doc, len(pred_relation_set)),
    )

    # ---- 方向错误率 ----
    direction_errors = taxonomy["counts"]["direction_reversed"]
    direction_error_rate = round4(
        direction_errors / len(pred_relation_set)
        if pred_relation_set
        else 0.0
    )

    # ---- Per Relation Type（统一按 Full 集口径，与旧报告可比）----
    relation_by_type: dict[str, dict[str, Any]] = {}

    for relation_type in RELATION_TYPES:
        gold_type = {r for r in gold_full if r[1] == relation_type}
        pred_type = {r for r in pred_relation_set if r[1] == relation_type}

        metrics = prf_from_sets(pred=pred_type, gold=gold_type)

        relation_by_type[relation_type] = {
            "gold": len(gold_type),
            "pred": len(pred_type),
            **asdict(metrics),
        }

    # ---- 逐条 FP / FN（Full / Core 口径）----
    false_positive_relations = sorted(pred_relation_set - gold_full)
    false_negative_relations = sorted(gold_core - pred_relation_set)

    gold_by_pair = {(s, t): typ for s, typ, t in gold_full}
    pred_by_pair = {(s, t): typ for s, typ, t in pred_relation_set}

    type_confusions = []

    for pair in sorted(set(pred_by_pair) & set(gold_by_pair)):
        if pred_by_pair[pair] != gold_by_pair[pair]:
            type_confusions.append({
                "source": pair[0],
                "target": pair[1],
                "gold_type": gold_by_pair[pair],
                "pred_type": pred_by_pair[pair],
            })

    return {
        "text_id": gold_doc["text_id"],
        "gold": {
            "entity_count": len(gold_entity_set),
            "relation_core_count": len(gold_core),
            "relation_full_count": len(gold_full),
        },
        "entity": asdict(entity_metrics),
        # evaluate_entity_category 返回的是 dict，不能再套 asdict
        # （原实现这里对 dict 调 asdict，导致本文件一运行就崩，
        #   这也是它历史上从未产出过任何已发布数字的直接原因）
        "entity_category": category_metrics,
        "relation_primary": asdict(relation_primary),
        "relation_core": asdict(relation_core),
        "relation_full": asdict(relation_full),
        "relation_pair_auxiliary": asdict(relation_pair),
        "relation_direction_error_rate": direction_error_rate,
        "relations_by_type": relation_by_type,
        "error_taxonomy": taxonomy,
        "false_positive_relations": [
            {"source": s, "type": t, "target": o}
            for s, t, o in false_positive_relations
        ],
        "false_negative_relations": [
            {"source": s, "type": t, "target": o}
            for s, t, o in false_negative_relations
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
            f"Pred 缺失 {len(missing_pred)} 个 text_id：{missing_pred}"
        )

    if extra_pred:
        warnings.append(
            f"Pred 存在 Gold 未定义的额外 text_id：{extra_pred}"
        )

    return warnings


# ============================================================
# 总体汇总
# ============================================================

def _deep_get(data: dict[str, Any], path: list[str]) -> dict[str, Any]:
    current: Any = data

    for key in path:
        current = current[key]

    return current


def _aggregate_category_accuracy(
    document_results: list[dict[str, Any]],
) -> float:
    """以所有文档中"名称匹配成功的实体"作为 category accuracy 分母。"""
    correct = 0
    evaluated = 0

    for result in document_results:
        category = result["entity_category"]
        correct += category["correct"]
        evaluated += category.get("evaluated_entity_count", 0)

    if evaluated == 0:
        return 0.0

    return round4(correct / evaluated)


def aggregate_document_metrics(
    document_results: list[dict[str, Any]],
) -> dict[str, Any]:

    def aggregate_prf(path: list[str]) -> dict[str, Any]:
        tp = sum(_deep_get(r, path)["tp"] for r in document_results)
        fp = sum(_deep_get(r, path)["fp"] for r in document_results)
        fn = sum(_deep_get(r, path)["fn"] for r in document_results)

        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall)
            else 0.0
        )

        return {
            "tp": tp, "fp": fp, "fn": fn,
            "precision": round4(precision),
            "recall": round4(recall),
            "f1": round4(f1),
        }

    def macro_average(path: list[str]) -> dict[str, Any]:
        values = [_deep_get(r, path) for r in document_results]

        return {
            "precision": mean([v["precision"] for v in values]),
            "recall": mean([v["recall"] for v in values]),
            "f1": mean([v["f1"] for v in values]),
        }

    # 主指标：跨文档累加后重算（Micro），因为 P 与 R 的分母集合不同，
    # 不能对逐文档的 P/R 直接平均。
    pred_total = sum(
        r["relation_primary"]["pred_total"] for r in document_results
    )
    core_total = sum(
        r["relation_primary"]["core_total"] for r in document_results
    )
    full_total = sum(
        r["relation_primary"]["full_total"] for r in document_results
    )
    hit_core = sum(
        r["relation_primary"]["hit_core"] for r in document_results
    )
    hit_full = sum(
        r["relation_primary"]["hit_full"] for r in document_results
    )

    p = hit_full / pred_total if pred_total else 0.0
    rc = hit_core / core_total if core_total else 0.0
    f1 = 2 * p * rc / (p + rc) if (p + rc) else 0.0

    relation_primary_micro = {
        "pred_total": pred_total,
        "core_total": core_total,
        "full_total": full_total,
        "hit_core": hit_core,
        "hit_full": hit_full,
        "precision": round4(p),
        "recall": round4(rc),
        "f1": round4(f1),
    }

    summary: dict[str, Any] = {
        "entity_micro": aggregate_prf(["entity"]),
        "entity_macro": macro_average(["entity"]),
        "entity_category_accuracy": _aggregate_category_accuracy(
            document_results
        ),
        "relation_primary_micro": relation_primary_micro,
        "relation_core_micro": aggregate_prf(["relation_core"]),
        "relation_full_micro": aggregate_prf(["relation_full"]),
        "relation_strict_micro": aggregate_prf(["relation_full"]),
        "relation_strict_macro_by_document": macro_average(
            ["relation_full"]
        ),
        "relation_pair_auxiliary_micro": aggregate_prf(
            ["relation_pair_auxiliary"]
        ),
    }

    # ---- Relation Type ----
    relation_type_summary: dict[str, Any] = {}

    for relation_type in RELATION_TYPES:
        type_results = [
            r["relations_by_type"][relation_type]
            for r in document_results
        ]

        tp = sum(x["tp"] for x in type_results)
        fp = sum(x["fp"] for x in type_results)
        fn = sum(x["fn"] for x in type_results)

        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        type_f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall)
            else 0.0
        )

        micro = {
            "tp": tp, "fp": fp, "fn": fn,
            "precision": round4(precision),
            "recall": round4(recall),
            "f1": round4(type_f1),
        }

        active_f1_values = [
            x["f1"] for x in type_results if x["gold"] > 0 or x["pred"] > 0
        ]

        relation_type_summary[relation_type] = {
            "gold_total": sum(x["gold"] for x in type_results),
            "pred_total": sum(x["pred"] for x in type_results),
            "micro": micro,
            "mean_document_f1": mean([x["f1"] for x in type_results]),
            "active_document_f1": mean(active_f1_values),
        }

    # ---- Macro-F1 ----
    all_type_f1 = [
        relation_type_summary[t]["micro"]["f1"] for t in RELATION_TYPES
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
        relation_type_summary[t]["micro"]["f1"] for t in active_types
    ]

    summary["relation_by_type"] = relation_type_summary
    summary["relation_macro_f1"] = {
        "all_types": mean(all_type_f1),
        "active_types": mean(active_type_f1),
        "active_type_names": active_types,
    }

    # ---- 方向错误率（跨文档汇总）----
    total_pred = relation_primary_micro["pred_total"]
    total_direction_errors = sum(
        r["error_taxonomy"]["counts"]["direction_reversed"]
        for r in document_results
    )

    summary["direction_error_rate"] = round4(
        total_direction_errors / total_pred if total_pred else 0.0
    )

    # ---- 错误归因汇总 ----
    # counts 只含数值（非计数信息在 error_taxonomy["accounting"] 里），可安全求和
    taxonomy_total: dict[str, int] = {}

    for r in document_results:
        for key, value in r["error_taxonomy"]["counts"].items():
            taxonomy_total[key] = taxonomy_total.get(key, 0) + value

    summary["error_taxonomy_totals"] = taxonomy_total

    # ---- 对账：跨文档求和后判定 raw == scored + dropped ----
    summary["accounting"] = {
        "raw_source": (
            "extractor"
            if all(
                r["error_taxonomy"]["accounting"]["raw_source"] == "extractor"
                for r in document_results
            )
            else "derived"
        ),
        "raw": taxonomy_total.get("pred_raw_total", 0),
        "scored": taxonomy_total.get("pred_total", 0),
        "dropped": taxonomy_total.get("dropped_total", 0),
        "balanced": all(
            r["error_taxonomy"]["accounting"]["balanced"]
            for r in document_results
        ),
    }

    return summary


# ============================================================
# 主评测（支持多轮）
# ============================================================

def _normalize_pred_docs(
    pred_docs: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """
    把 pred 统一成 {text_id: [run1, run2, ...]}。

    接受两种写法：
        {"text_id": X, "runs": [ {...}, {...} ]}   多轮
        {"text_id": X, "entities": [...], "relations": [...]}   单轮

    注意：单轮写法必须逐字段搬运，不能只取 entities/relations ——
    抽取器的丢弃记账（raw_relation_count / dropped_counts /
    dropped_relations）也要一起带过去，否则归因层看不到它们。
    """
    result: dict[str, list[dict[str, Any]]] = {}

    for doc in pred_docs:
        text_id = doc.get("text_id")

        if "runs" in doc and isinstance(doc["runs"], list):
            result[text_id] = list(doc["runs"])
        else:
            result[text_id] = [{
                "entities": doc.get("entities", []),
                "relations": doc.get("relations", []),
                "raw_relation_count": doc.get("raw_relation_count"),
                "dropped_counts": doc.get("dropped_counts") or {},
                "dropped_relations": doc.get("dropped_relations") or [],
            }]

    return result


# 多轮汇总时需要计算 mean/std 的指标路径
_SUMMARY_SCALAR_PATHS: list[tuple[str, list[str]]] = [
    ("entity_precision", ["entity_micro", "precision"]),
    ("entity_recall", ["entity_micro", "recall"]),
    ("entity_f1", ["entity_micro", "f1"]),
    ("entity_category_accuracy", ["entity_category_accuracy"]),
    ("relation_primary_precision", ["relation_primary_micro", "precision"]),
    ("relation_primary_recall", ["relation_primary_micro", "recall"]),
    ("relation_primary_f1", ["relation_primary_micro", "f1"]),
    ("relation_core_precision", ["relation_core_micro", "precision"]),
    ("relation_core_recall", ["relation_core_micro", "recall"]),
    ("relation_core_f1", ["relation_core_micro", "f1"]),
    ("relation_full_precision", ["relation_full_micro", "precision"]),
    ("relation_full_recall", ["relation_full_micro", "recall"]),
    ("relation_full_f1", ["relation_full_micro", "f1"]),
    ("relation_pair_precision", ["relation_pair_auxiliary_micro", "precision"]),
    ("relation_pair_recall", ["relation_pair_auxiliary_micro", "recall"]),
    ("relation_pair_f1", ["relation_pair_auxiliary_micro", "f1"]),
    ("relation_macro_f1_active", ["relation_macro_f1", "active_types"]),
    ("direction_error_rate", ["direction_error_rate"]),
]


def evaluate_all(
    gold_docs: list[dict[str, Any]],
    pred_docs: list[dict[str, Any]],
) -> dict[str, Any]:

    warnings = validate_input(gold_docs, pred_docs)

    gold_map = {doc["text_id"]: doc for doc in gold_docs}
    pred_map = _normalize_pred_docs(pred_docs)

    run_count = max(
        (len(pred_map.get(tid, [{}])) for tid in gold_map),
        default=1,
    )

    # ---- 逐轮评测 ----
    per_run_summaries: list[dict[str, Any]] = []
    per_run_documents: list[list[dict[str, Any]]] = []

    for run_index in range(run_count):
        document_results = []

        for text_id in sorted(gold_map):
            runs = pred_map.get(text_id, [])
            pred_doc = runs[run_index] if run_index < len(runs) else None

            document_results.append(
                evaluate_document(
                    gold_doc=gold_map[text_id],
                    pred_doc=pred_doc,
                )
            )

        per_run_documents.append(document_results)
        per_run_summaries.append(
            aggregate_document_metrics(document_results)
        )

    # ---- 多轮聚合：mean / std ----
    stability: dict[str, dict[str, float]] = {}

    for label, path in _SUMMARY_SCALAR_PATHS:
        values = [_deep_get(s, path) for s in per_run_summaries]
        stability[label] = {
            "mean": mean(values),
            "std": stdev(values),
            "min": round4(min(values)) if values else 0.0,
            "max": round4(max(values)) if values else 0.0,
            "values": [round4(v) for v in values],
        }

    # ---- 跨轮稳定性：实体/关系出现频次 ----
    # 直接从原始 pred 重算，不走评测结果，避免污染主流程。
    entity_freq: dict[str, int] = {}
    relation_freq: dict[str, int] = {}

    for text_id, runs in pred_map.items():
        for pred_doc in runs:
            for entity in pred_doc.get("entities", []):
                name = normalize_name(entity.get("name"))
                if name:
                    entity_freq[name] = entity_freq.get(name, 0) + 1

            for relation in pred_doc.get("relations", []):
                normalized = normalize_relation(relation)
                if normalized is not None:
                    s, t, o = normalized
                    key = f"{s} -[{t}]-> {o}"
                    relation_freq[key] = relation_freq.get(key, 0) + 1

    # ---- 错误归因跨轮聚合 ----
    taxonomy_keys = [
        # 5 类主体
        "fabricated", "direction_reversed", "type_confused",
        "synonym_split", "missed_core",
        # 评分前被丢弃的明细（与上面互斥，不是同一批关系）
        "invalid_endpoint", "invalid_relation_type", "dangling_endpoint",
        "duplicate", "low_confidence", "dropped_total",
        # 对账
        "pred_raw_total", "pred_total", "core_total", "full_total",
    ]

    taxonomy_across_runs = {
        key: {
            "mean": mean([
                s["error_taxonomy_totals"].get(key, 0)
                for s in per_run_summaries
            ]),
            "values": [
                s["error_taxonomy_totals"].get(key, 0)
                for s in per_run_summaries
            ],
        }
        for key in taxonomy_keys
    }

    # ---- 对账：模型输出 == 计分 + 丢弃 ----
    reconciliation = {
        "identity": "pred_raw_total == pred_total + dropped_total",
        "raw_source": (
            per_run_summaries[0]["accounting"]["raw_source"]
            if per_run_summaries else "derived"
        ),
        "balanced_all_runs": all(
            s["accounting"]["balanced"] for s in per_run_summaries
        ),
        "per_run": [
            {
                "raw": s["error_taxonomy_totals"].get("pred_raw_total", 0),
                "scored": s["error_taxonomy_totals"].get("pred_total", 0),
                "dropped": s["error_taxonomy_totals"].get("dropped_total", 0),
                "reconciles": s["accounting"]["balanced"],
            }
            for s in per_run_summaries
        ],
    }

    return {
        "evaluation_version": "2.0",
        "relation_types": list(RELATION_TYPES),
        "entity_categories": list(ENTITY_CATEGORIES),

        "metric_policy": {
            "relation_primary": (
                "precision_on_Full_set__recall_on_Core_set"
            ),
            "relation_core": "conservative_lower_bound",
            "relation_full": "strict_match_against_full_gold",
            "entity_primary": "name_exact_after_frozen_normalization",
            "relation_pair": "auxiliary_only_ignore_type_and_direction",
            "confidence_is_not_accuracy": True,
            "accuracy_source": "manual_gold_annotation",
        },

        "normalization": {
            "version": NORMALIZATION_VERSION,
            "aliases": ENTITY_ALIASES,
            "aliases_sha256": aliases_sha256(),
        },

        "gold_sha256": {
            doc["text_id"]: hashlib.sha256(
                json.dumps(doc, ensure_ascii=False, sort_keys=True)
                .encode("utf-8")
            ).hexdigest()
            for doc in gold_docs
        },

        "runs": run_count,
        "warnings": warnings,
        "stability": stability,
        "entity_frequency": dict(
            sorted(entity_freq.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        "relation_frequency": dict(
            sorted(relation_freq.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        "error_taxonomy_across_runs": taxonomy_across_runs,
        "reconciliation": reconciliation,
        "summary": per_run_summaries[0] if per_run_summaries else {},
        "per_run_summaries": per_run_summaries,
        "documents": (
            per_run_documents[0] if per_run_documents else []
        ),
        "documents_per_run": per_run_documents,
    }


# ============================================================
# 实时抽取（吸收自原 eval_accuracy_live.py）
# ============================================================

async def run_live_extraction(
    text: str,
    runs: int = 3,
    temperature: float = 0.3,
    overlap_tokens: int | None = None,
) -> list[dict[str, Any]]:
    """
    调用 LLM 抽取 N 轮，返回逐轮原始结果。

    本函数是仓库里唯一调用 LLM 做抽取评测的入口；评分逻辑全在本文件其他部分。
    """
    from app.services.knowledge_extractor import (
        KnowledgeExtractor,
        _OVERLAP_TOKENS,
    )

    if overlap_tokens is None:
        overlap_tokens = _OVERLAP_TOKENS

    extractor = KnowledgeExtractor(temperature=temperature)

    results: list[dict[str, Any]] = []

    for index in range(runs):
        print(
            f"[{index + 1}/{runs}] 调用 LLM 抽取"
            f"（temperature={temperature}，自动分块 + 并发）…"
        )

        result = await extractor.extract(
            text, overlap_tokens=overlap_tokens
        )

        entities = result.get("entities", [])
        relations = result.get("relations", [])

        if result.get("error"):
            print(f"    ⚠ {result['error']}")

        raw = result.get("raw_relation_count", len(relations))
        dropped_counts = result.get("dropped_counts") or {}
        dropped_total = sum(dropped_counts.values())

        print(
            f"    实体 {len(entities)} 个，关系 {len(relations)} 条"
            + (f"（原始 {raw} 条，过滤掉 {dropped_total} 条）" if dropped_total else "")
        )
        if dropped_counts:
            detail = "、".join(
                f"{DROP_REASON_LABELS.get(k, k)} {v}"
                for k, v in sorted(dropped_counts.items(), key=lambda kv: -kv[1])
            )
            print(f"      丢弃明细：{detail}")

        results.append({
            "entities": entities,
            "relations": relations,
            "error": result.get("error"),
            # 抽取器侧的丢弃记账，随预测一起落盘——否则评分阶段无从知道
            # 这些关系存在过，low_confidence 会被误记成 missed_core
            "raw_relation_count": raw,
            "dropped_counts": dropped_counts,
            "dropped_relations": result.get("dropped_relations") or [],
        })

    return results


# ============================================================
# 控制台输出
# ============================================================

def print_report(report: dict[str, Any]) -> None:
    summary = report["summary"]
    stability = report["stability"]

    print("=" * 78)
    print("A10 知识抽取标准评测")
    print("=" * 78)
    print(
        f"轮次={report['runs']}  "
        f"归一化版本={report['normalization']['version']}  "
        f"别名表指纹={report['normalization']['aliases_sha256'][:12]}…"
    )

    def _line(label: str, prefix: str, note: str = "") -> None:
        """
        统一打印口径：标题行给多轮均值（与历史报告一致），
        下一行给第 1 轮的 pooled micro 作为对照。

        历史上两套实现一个报均值、一个报单轮，导致数字不可比；
        这里明确区分，不再混用。
        """
        p = stability[f"{prefix}_precision"]
        r = stability[f"{prefix}_recall"]
        f = stability[f"{prefix}_f1"]

        print(f"\n[{label}]")
        print(
            f"P={p['mean']:.4f} R={r['mean']:.4f} "
            f"F1={f['mean']:.4f} ± {f['std']:.4f}"
            f"   ← {report['runs']} 轮均值"
        )
        if f["values"]:
            print(f"  逐轮 F1: {f['values']}")
        if note:
            print(f"  {note}")

    _line("实体 entity", "entity")

    print("\n[实体类别]")
    print(
        f"Category Accuracy="
        f"{stability['entity_category_accuracy']['mean']:.4f}"
    )

    _line(
        "关系-主指标 relation_primary  ← 赛题申报口径",
        "relation_primary",
        note=(
            f"Pred={summary['relation_primary_micro']['pred_total']} "
            f"命中Full={summary['relation_primary_micro']['hit_full']} | "
            f"Core={summary['relation_primary_micro']['core_total']} "
            f"命中={summary['relation_primary_micro']['hit_core']} "
            f"（第1轮）"
        ),
    )

    _line("关系-保守下界 relation_core", "relation_core")
    _line("关系-Full 严格 relation_full", "relation_full")
    _line("关系-宽松辅助 pair_match", "relation_pair")

    print("注意：宽松辅助指标忽略关系类型和方向，不作为主准确率。")

    print(
        f"\n[方向错误率] "
        f"{stability['direction_error_rate']['mean']:.4f}"
        f" ± {stability['direction_error_rate']['std']:.4f}"
        "   （方向颠倒 / 全部输出关系，多轮均值）"
    )

    print("\n[各关系类型]")
    print(
        f"{'Type':<15}{'Gold':>8}{'Pred':>8}"
        f"{'P':>10}{'R':>10}{'F1':>10}"
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
    print(f"All 4 types   = {macro['all_types']:.4f}")
    print(
        f"Active types  = {macro['active_types']:.4f}"
        f"  ({', '.join(macro['active_type_names'])})"
    )

    print("\n[错误归因]（跨轮均值）")
    print("  注：④ 同义拆分在评分前就被丢弃，不在此列，见下方[丢弃明细]")
    labels = {
        "fabricated": "① 臆造",
        "direction_reversed": "② 方向颠倒",
        "type_confused": "③ 类型混淆",
        "synonym_split": "④ 同义拆分",
        "missed_core": "⑤ 完全漏抽(Core)",
    }

    for key, label in labels.items():
        item = report["error_taxonomy_across_runs"].get(key, {})
        print(
            f"  {label:<20} {item.get('mean', 0):>8.1f}"
            f"   {item.get('values', [])}"
        )

    # 评分前被丢弃的关系：与上面 5 类是互斥集合，不属于"模型判断错误"
    print("\n[丢弃明细]（评分前被过滤，跨轮均值；与上面的 5 类互斥）")
    drop_labels = {
        "invalid_endpoint": "端点为空",
        "invalid_relation_type": "关系类型非法",
        "synonym_split": "同义拆分（归一化后自环）",
        "dangling_endpoint": "端点不在实体表中",
        "duplicate": "重复三元组",
        "low_confidence": "低于置信度阈值被过滤",
    }

    for key, label in drop_labels.items():
        item = report["error_taxonomy_across_runs"].get(key, {})
        print(
            f"  {label:<26} {item.get('mean', 0):>8.1f}"
            f"   {item.get('values', [])}"
        )

    rec = report["reconciliation"]
    print("\n[对账]")
    print(f"  等式：{rec['identity']}")
    print(f"  原始计数来源：{rec['raw_source']}")
    for i, item in enumerate(rec["per_run"]):
        mark = "✓" if item["reconciles"] else "✗ 对不上"
        print(
            f"  第{i + 1}轮  原始 {item['raw']} = 计分 {item['scored']}"
            f" + 丢弃 {item['dropped']}  {mark}"
        )

    if report["warnings"]:
        print("\n[Warnings]")
        for warning in report["warnings"]:
            print(f"- {warning}")

    print("\n[评测口径]")
    print("Confidence 是模型自评置信度，不代表真实准确率。")
    print("真实准确率仅根据人工 Gold 标注计算 Precision / Recall / F1。")
    print(
        "关系主指标：Precision 用 Full 集、Recall 用 Core 集；"
        "报告须同时披露 relation_core 作为保守下界。"
    )


# ============================================================
# Markdown 渲染（禁止手抄数字）
# ============================================================

def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    stability = report["stability"]
    lines: list[str] = []

    lines.append("# 知识抽取准确率评测报告")
    lines.append("")
    lines.append(
        "> 本文件由 `backend/eval_accuracy.py --markdown` 从评测结果 JSON "
        "自动渲染，请勿手工修改数字。"
    )
    lines.append("")

    # ---- 口径 ----
    lines.append("## 一、评测口径")
    lines.append("")
    lines.append(
        "- **关系主指标 `relation_primary`**："
        "Precision 的分母集合为 Full 集，Recall 的分母集合为 Core 集。"
    )
    lines.append(
        "  - **Core 集**：文本明确、无争议、按决策树必然成立的关系"
        "（gold 中 `core=true`），用于计算 Recall。"
    )
    lines.append(
        "  - **Full 集**：Core ∪ 合理但边界模糊的关系（gold 中全部关系），"
        "用于计算 Precision。"
    )
    lines.append(
        "- **`relation_core`**：Pred 对 Core 集的完整 P/R/F1，"
        "保守下界，同时披露。"
    )
    lines.append(
        "- **`relation_full`**：Pred 对 Full 集的完整严格匹配 P/R/F1。"
    )
    lines.append(
        "- `confidence` 是模型自评置信度，**不代表真实准确率**，"
        "不参与任何 P/R/F1 计算。"
    )
    lines.append("")

    # ---- 配置快照 ----
    lines.append("## 二、可复现信息")
    lines.append("")
    lines.append(
        f"- 归一化版本：`{report['normalization']['version']}`"
    )
    lines.append(
        f"- 别名表指纹（SHA256）："
        f"`{report['normalization']['aliases_sha256']}`"
    )
    lines.append(f"- 轮次：{report['runs']}")
    lines.append("")
    lines.append("| text_id | Gold SHA256 |")
    lines.append("|---|---|")
    for text_id, digest in report["gold_sha256"].items():
        lines.append(f"| {text_id} | `{digest[:16]}…` |")
    lines.append("")

    # ---- 主结果 ----
    lines.append("## 三、主结果")
    lines.append("")
    lines.append(
        f"> 表中 P / R / F1 均为 **{report['runs']} 轮独立运行的均值**"
        "（与历史报告口径一致）；F1 列同时给出标准差。"
    )
    lines.append("")
    lines.append("| 指标 | Precision | Recall | F1 | F1 std |")
    lines.append("|---|---:|---:|---:|---:|")

    rows = [
        ("实体", "entity"),
        ("**关系（主指标，申报口径）**", "relation_primary"),
        ("关系（保守下界）", "relation_core"),
        ("关系（Full 严格）", "relation_full"),
        ("关系（宽松辅助，忽略类型与方向）", "relation_pair"),
    ]

    for label, prefix in rows:
        p = stability[f"{prefix}_precision"]
        r = stability[f"{prefix}_recall"]
        f = stability[f"{prefix}_f1"]
        lines.append(
            f"| {label} | {p['mean']:.4f} | {r['mean']:.4f} "
            f"| **{f['mean']:.4f}** | ±{f['std']:.4f} |"
        )

    lines.append("")
    lines.append(
        f"- 实体类别准确率："
        f"**{stability['entity_category_accuracy']['mean']:.4f}**"
    )
    lines.append(
        f"- 方向错误率：**{stability['direction_error_rate']['mean']:.4f}**"
    )
    lines.append("")

    # ---- 逐轮 ----
    lines.append("### 3.1 逐轮明细")
    lines.append("")
    header = "| 轮次 | " + " | ".join(
        lbl for lbl, _ in [
            ("实体 F1", None), ("关系主指标 F1", None),
            ("关系保守下界 F1", None), ("方向错误率", None),
        ]
    ) + " |"
    lines.append(header)
    lines.append("|---:|---:|---:|---:|---:|")

    for index in range(report["runs"]):
        lines.append(
            f"| {index + 1} "
            f"| {stability['entity_f1']['values'][index]:.4f} "
            f"| {stability['relation_primary_f1']['values'][index]:.4f} "
            f"| {stability['relation_core_f1']['values'][index]:.4f} "
            f"| {stability['direction_error_rate']['values'][index]:.4f} |"
        )

    lines.append("")

    # ---- 按关系类型 ----
    lines.append("## 四、按关系类型")
    lines.append("")
    lines.append("| 类型 | Gold | Pred | P | R | F1 |")
    lines.append("|---|---:|---:|---:|---:|---:|")

    for relation_type in RELATION_TYPES:
        d = summary["relation_by_type"][relation_type]
        m = d["micro"]
        lines.append(
            f"| {relation_type} | {d['gold_total']} | {d['pred_total']} "
            f"| {m['precision']:.4f} | {m['recall']:.4f} | {m['f1']:.4f} |"
        )

    macro = summary["relation_macro_f1"]
    lines.append("")
    lines.append(
        f"- Macro-F1（四类全参与）：{macro['all_types']:.4f}"
    )
    lines.append(
        f"- Macro-F1（活跃类）：{macro['active_types']:.4f} "
        f"（{', '.join(macro['active_type_names'])}）"
    )
    lines.append("")

    # ---- 错误归因 ----
    lines.append("## 五、错误归因（跨轮均值）")
    lines.append("")
    lines.append("### 5.1 已进入评分的预测")
    lines.append("")
    lines.append(
        "> 编号 ④（同义拆分）**不在此表**——那类关系在评分前就被丢弃，"
        "从未与 gold 比对，故列在 5.2。此处由 ③ 直接跳到 ⑤，不是遗漏。"
    )
    lines.append("")
    lines.append("| 类别 | 数量 | 逐轮 |")
    lines.append("|---|---:|---|")

    for key, label in [
        ("fabricated", "① 臆造（节点对不在 Full）"),
        ("direction_reversed", "② 方向颠倒"),
        ("type_confused", "③ 类型混淆"),
        ("missed_core", "⑤ 完全漏抽（Core）"),
    ]:
        item = report["error_taxonomy_across_runs"].get(key, {})
        lines.append(
            f"| {label} | {item.get('mean', 0):.1f} "
            f"| {item.get('values', [])} |"
        )

    lines.append("")
    lines.append("### 5.2 评分前被丢弃的预测")
    lines.append("")
    lines.append(
        "> 下列关系**从未进入 P/R/F1 计算**——它们在评分之前就被过滤掉了。"
        "因此不计入 5.1 的五类 FP，也不构成模型的判断错误。"
    )
    lines.append("")
    lines.append(
        "> **其中「低于置信度阈值被过滤」最需要留意**：这类关系模型其实抽到了，"
        "是本系统的阈值把它砍掉的。若它本来是对的，就会被 5.1 的"
        "「⑤ 完全漏抽」记成召回不足，即把「阈值设太严」误诊为「模型能力不够」。"
    )
    lines.append("")
    lines.append("| 丢弃原因 | 数量 | 逐轮 |")
    lines.append("|---|---:|---|")

    for key, label in [
        ("synonym_split", "④ 同义拆分（归一化后自环）"),
        ("invalid_endpoint", "端点为空"),
        ("invalid_relation_type", "关系类型非法"),
        ("dangling_endpoint", "端点不在实体表中（悬空边）"),
        ("duplicate", "重复三元组"),
        ("low_confidence", "低于置信度阈值被过滤"),
    ]:
        item = report["error_taxonomy_across_runs"].get(key, {})
        lines.append(
            f"| {label} | {item.get('mean', 0):.1f} "
            f"| {item.get('values', [])} |"
        )

    lines.append("")

    # ---- 对账 ----
    rec = report["reconciliation"]
    lines.append("### 5.3 对账")
    lines.append("")
    lines.append(f"等式：`{rec['identity']}`（原始计数来源：{rec['raw_source']}）")
    lines.append("")
    lines.append("| 轮次 | 原始输出 | 进入评分 | 丢弃 | 对上账 |")
    lines.append("|---:|---:|---:|---:|:---:|")

    for index, item in enumerate(rec["per_run"]):
        lines.append(
            f"| {index + 1} | {item['raw']} | {item['scored']} "
            f"| {item['dropped']} | {'✓' if item['reconciles'] else '✗'} |"
        )

    if not rec["balanced_all_runs"]:
        lines.append("")
        lines.append(
            "> ⚠ **对账未通过**：有轮次的「原始输出 ≠ 计分 + 丢弃」，"
            "说明存在未被记账的丢弃路径，第五节的分项数字不完整。"
        )

    lines.append("")

    # ---- 逐文档 ----
    lines.append("## 六、逐文档明细（第 1 轮）")
    lines.append("")
    lines.append(
        "> 逐文档数字取自第 1 轮，便于定位是哪个文档拖低了整体；"
        "跨轮汇总见第三节。"
    )
    lines.append("")
    lines.append(
        "| text_id | 实体 F1 | 关系主指标 F1 | 关系保守下界 F1 | 方向错误率 |"
    )
    lines.append("|---|---:|---:|---:|---:|")

    for doc in report["documents"]:
        lines.append(
            f"| {doc['text_id']} "
            f"| {doc['entity']['f1']:.4f} "
            f"| {doc['relation_primary']['f1']:.4f} "
            f"| {doc['relation_core']['f1']:.4f} "
            f"| {doc['relation_direction_error_rate']:.4f} |"
        )

    lines.append("")

    if report["warnings"]:
        lines.append("## 七、数据告警")
        lines.append("")
        for warning in report["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    return "\n".join(lines)


# ============================================================
# CLI
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="A10 知识抽取标准评测器（全仓库唯一实现）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  抽取并落盘：\n"
            "    python eval_accuracy.py --live --gold g.json --text t.txt \\\n"
            "        --runs 3 --temperature 0 --pred pred.json\n"
            "  离线评分：\n"
            "    python eval_accuracy.py --gold g.json --pred pred.json \\\n"
            "        --output report.json --markdown report.md\n"
        ),
    )

    parser.add_argument("--gold", required=True, help="Gold JSON 文件")

    parser.add_argument(
        "--pred", required=True,
        help="Prediction JSON 文件（--live 时作为输出路径）",
    )

    parser.add_argument(
        "--output", default=None,
        help="评测结果 JSON 输出路径（默认由 --pred 推导）",
    )

    parser.add_argument(
        "--markdown", default=None,
        help="Markdown 报告输出路径（可选）",
    )

    parser.add_argument(
        "--live", action="store_true",
        help="先调用 LLM 抽取（需配合 --text），结果落盘到 --pred",
    )

    parser.add_argument("--text", default=None, help="--live 模式的源文本文件")

    parser.add_argument(
        "--runs", type=int, default=3, help="--live 模式的抽取轮数（默认 3）",
    )

    parser.add_argument(
        "--temperature", type=float, default=0.3,
        help="--live 模式的温度（正式评测用 0）",
    )

    parser.add_argument(
        "--overlap-tokens", type=int, default=None,
        help="--live 模式的分块重叠 token 数（默认取抽取器常量）",
    )

    args = parser.parse_args()

    gold_path = Path(args.gold)
    pred_path = Path(args.pred)

    gold_docs = load_docs(gold_path, "Gold")

    # ---- ① 实时抽取 ----
    if args.live:
        if not args.text:
            parser.error("--live 模式必须提供 --text")

        text = Path(args.text).read_text(encoding="utf-8")

        print(
            f"源文本：{args.text}"
            f"（{len(text)} 字符 / {gold_path.name}）"
        )

        runs = asyncio.run(
            run_live_extraction(
                text=text,
                runs=args.runs,
                temperature=args.temperature,
                overlap_tokens=args.overlap_tokens,
            )
        )

        # pred 此刻还没落盘，只能先按 gold 一侧定下配对键
        ensure_text_ids(gold_docs, None, gold_path)
        text_id = gold_docs[0]["text_id"]

        save_json(
            pred_path,
            [{"text_id": text_id, "runs": runs}],
        )
        print(f"\n预测已落盘：{pred_path}")
        print(
            "提示：此后不再调用 LLM。评分请去掉 --live 重跑本脚本。"
        )

    # ---- ② 离线评分 ----
    pred_docs = load_docs(pred_path, "Pred")
    ensure_text_ids(gold_docs, pred_docs, gold_path)

    report = evaluate_all(gold_docs=gold_docs, pred_docs=pred_docs)

    output_path = Path(
        args.output
        or pred_path.with_name(f"eval_report_{pred_path.stem}.json")
    )

    save_json(output_path, report)
    print_report(report)

    if args.markdown:
        markdown_path = Path(args.markdown)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(
            render_markdown(report), encoding="utf-8"
        )
        print(f"Markdown 报告：{markdown_path}")

    print("\n" + "=" * 78)
    print(f"完整评测报告：{output_path}")
    print("=" * 78)


if __name__ == "__main__":
    main()
