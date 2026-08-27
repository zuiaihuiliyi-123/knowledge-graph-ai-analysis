"""
知识抽取准确率测试：以《数据结构第一章》样本为基准，对比 LLM 抽取结果与专家金标准

评估方法（对齐赛题「知识抽取准确率测试」交付物）：
- 金标准：由人工根据源文本标注的实体集 + 关系三元组集
- 实体匹配：名称精确匹配（顺序存储结构→顺序表、链式存储结构→链表 归一化）
- 关系匹配：严格匹配（source/type/target 全等）+ 宽松匹配（source-target 对，忽略类型与方向）
- 指标：Precision / Recall / F1（多轮运行取平均，并统计跨轮稳定性）

运行方式（backend 目录下）：python test_extraction_accuracy.py
输出：控制台摘要 + test_extraction_accuracy.json（完整结果）
"""
import asyncio
import json
import sys
from collections import Counter

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.services.knowledge_extractor import KnowledgeExtractor

# 源文本：data/sample_docs/数据结构第一章.txt 全文
SOURCE_TEXT = """第一章 线性表

线性表是数据结构中最基本、最常见的一种结构，它由 n 个数据元素组成的有限序列。
线性表有两种存储结构：顺序存储结构（顺序表）和链式存储结构（链表）。
顺序表用一组地址连续的存储单元依次存储数据元素，支持随机访问。
链表通过指针将各个结点链接起来，插入和删除操作不需要移动元素。

第二章 栈和队列

栈是一种操作受限的线性表，只能在一端（栈顶）进行插入和删除，遵循后进先出（LIFO）原则。
队列也是一种操作受限的线性表，只允许在一端插入、另一端删除，遵循先进先出（FIFO）原则。"""

# 专家金标准（人工标注）
GOLD_ENTITIES = {"线性表", "顺序表", "链表", "栈", "队列"}
GOLD_RELATIONS = {
    ("线性表", "CONTAINS", "顺序表"),
    ("线性表", "CONTAINS", "链表"),
    ("线性表", "CONTAINS", "栈"),
    ("线性表", "CONTAINS", "队列"),
}

# 别名归一化（同义表达统一为规范名）
ENTITY_ALIASES = {
    "顺序存储结构": "顺序表",
    "链式存储结构": "链表",
}

RUNS = 3  # 多轮运行以评估稳定性


def normalize(name: str) -> str:
    name = (name or "").strip()
    return ENTITY_ALIASES.get(name, name)


def evaluate(entities: list, relations: list) -> dict:
    """对单次抽取结果计算精确率/召回率/F1"""
    pred_entities = {normalize(e.get("name")) for e in entities}
    pred_entities.discard("")

    # 关系：三元组 -> (source, type, target)；另存 source-target 对用于宽松匹配
    pred_rel_strict = set()
    pred_rel_lenient = set()
    for r in relations:
        s = normalize(r.get("source"))
        t = normalize(r.get("target"))
        typ = (r.get("type") or "").strip()
        if not s or not t or not typ:
            continue
        pred_rel_strict.add((s, typ, t))
        pred_rel_lenient.add(frozenset({s, t}))

    gold_rel_lenient = {frozenset({s, t}) for s, _, t in GOLD_RELATIONS}

    def prf(pred, gold):
        tp = len(pred & gold)
        precision = tp / len(pred) if pred else 0.0
        recall = tp / len(gold) if gold else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        return {"tp": tp, "fp": len(pred) - tp, "fn": len(gold) - tp,
                "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}

    return {
        "entities": prf(pred_entities, GOLD_ENTITIES),
        "relations_strict": prf(pred_rel_strict, GOLD_RELATIONS),
        "relations_lenient": prf(pred_rel_lenient, gold_rel_lenient),
        "pred_entities": sorted(pred_entities),
        "pred_relations": sorted(f"{s} -[{t}]-> {o}" for s, t, o in pred_rel_strict),
    }


async def main():
    extractor = KnowledgeExtractor()
    all_results = []
    for i in range(RUNS):
        print(f"[{i + 1}/{RUNS}] 调用 LLM 抽取…")
        result = await extractor.extract(SOURCE_TEXT)
        entities = result.get("entities", [])
        relations = result.get("relations", [])
        if result.get("error"):
            print(f"    ✗ {result['error']}")
        metrics = evaluate(entities, relations)
        all_results.append({"entities": entities, "relations": relations, "metrics": metrics})
        print(f"    实体 {len(entities)} 个，关系 {len(relations)} 条"
              f" | 实体F1={metrics['entities']['f1']}"
              f" | 关系严格F1={metrics['relations_strict']['f1']}")

    # 汇总：平均指标 + 跨轮稳定性
    def avg(key_path):
        vals = []
        for r in all_results:
            m = r["metrics"]
            cur = m
            for k in key_path:
                cur = cur[k]
            vals.append(cur)
        return round(sum(vals) / len(vals), 4)

    # 跨轮稳定性：某个实体/关系在 3 轮中出现的次数
    ent_freq = Counter()
    rel_freq = Counter()
    for r in all_results:
        ent_freq.update({normalize(e.get("name")) for e in r["entities"]})
        for rel in r["relations"]:
            s = normalize(rel.get("source")); t = normalize(rel.get("target"))
            typ = (rel.get("type") or "").strip()
            if s and t and typ:
                rel_freq.update([(s, typ, t)])

    summary = {
        "runs": RUNS,
        "avg_entity_precision": avg(["entities", "precision"]),
        "avg_entity_recall": avg(["entities", "recall"]),
        "avg_entity_f1": avg(["entities", "f1"]),
        "avg_relation_strict_precision": avg(["relations_strict", "precision"]),
        "avg_relation_strict_recall": avg(["relations_strict", "recall"]),
        "avg_relation_strict_f1": avg(["relations_strict", "f1"]),
        "avg_relation_lenient_precision": avg(["relations_lenient", "precision"]),
        "avg_relation_lenient_recall": avg(["relations_lenient", "recall"]),
        "avg_relation_lenient_f1": avg(["relations_lenient", "f1"]),
        "entity_stability": {k: v for k, v in ent_freq.items()},
        "relation_stability": {f"{s} -[{t}]-> {o}": c for (s, t, o), c in rel_freq.items()},
    }

    out = {"gold_entities": sorted(GOLD_ENTITIES),
           "gold_relations": [f"{s} -[{t}]-> {o}" for s, t, o in sorted(GOLD_RELATIONS)],
           "summary": summary,
           "runs": all_results}

    with open("test_extraction_accuracy.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print("平均指标（", RUNS, "轮）")
    print("=" * 60)
    print(f"实体  Precision={summary['avg_entity_precision']}  "
          f"Recall={summary['avg_entity_recall']}  F1={summary['avg_entity_f1']}")
    print(f"关系(严格) Precision={summary['avg_relation_strict_precision']}  "
          f"Recall={summary['avg_relation_strict_recall']}  F1={summary['avg_relation_strict_f1']}")
    print(f"关系(宽松) Precision={summary['avg_relation_lenient_precision']}  "
          f"Recall={summary['avg_relation_lenient_recall']}  F1={summary['avg_relation_lenient_f1']}")
    print("\n完整结果已写入 test_extraction_accuracy.json")


if __name__ == "__main__":
    asyncio.run(main())
