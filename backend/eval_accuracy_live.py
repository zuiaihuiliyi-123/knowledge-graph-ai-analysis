"""
知识抽取准确率评测：以《Hello 算法》第7章「树」为基准，对比 LLM 抽取结果与人工金标准

评测对象（eval_data/ 目录）：
- 第7章_树.txt         测试文本（15330 字符，约 1.2 万 tokens，分块抽取）
- gold_第7章_树.json   人工金标准（49 实体 / 33 关系，含 description/evidence/confidence）
- 第7章_树_标注说明.md  标注规则与归一化约定

评估方法（对齐赛题「知识抽取准确率测试」交付物）：
- 实体匹配：名称精确匹配（先做同义归一化：满二叉树→完美二叉树、BFS/DFS→广度/深度优先遍历等）
- 关系匹配：严格匹配（source/type/target 全等）+ 宽松匹配（source-target 对，忽略类型与方向）
- 指标：Precision / Recall / F1，多轮运行取平均并统计跨轮稳定性
- 按关系类型细分统计（CONTAINS / APPLIES_TO / RELATED_TO / PRECEDES）
  注意：本文本 gold 中 PRECEDES 为 0 条，模型输出任何 PRECEDES 均为误报，可量化此类系统性错误

运行方式（backend 目录下）：python eval_accuracy_live.py
输出：控制台摘要 + eval_data/eval_report_第7章_树.json（完整结果）
"""
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.services.knowledge_extractor import KnowledgeExtractor

BACKEND_DIR = Path(__file__).resolve().parent
EVAL_DIR = BACKEND_DIR / "eval_data"
TEXT_FILE = EVAL_DIR / "第7章_树.txt"
GOLD_FILE = EVAL_DIR / "gold_第7章_树.json"
REPORT_FILE = EVAL_DIR / "eval_report_第7章_树.json"

# 同义归一化（gold 已用规范名；模型输出下列名称时映射到规范名再匹配）
# 规则来源：第7章_树_标注说明.md 第二节；其余映射来自首轮评测差异分析（模型 3 轮稳定出现的写法变体）
ENTITY_ALIASES = {
    "满二叉树": "完美二叉树",
    "广度优先搜索": "广度优先遍历",
    "深度优先搜索": "深度优先遍历",
    "BFS": "广度优先遍历",
    "DFS": "深度优先遍历",
    "节点的高度": "高度",
    "节点高度": "高度",
    "二叉树的高度": "高度",
    "节点的深度": "深度",
    "AVL树": "AVL 树",
    "旋转操作": "旋转",
    "先右旋再左旋": "先右旋后左旋",
    "先左旋再右旋": "先左旋后右旋",
    "插入操作": "插入节点",
    "删除操作": "删除节点",
    "查找操作": "查找节点",
    "索引映射公式": "映射公式",
    "数组表示法": "数组表示",
}

RUNS = 3  # 多轮运行以评估稳定性
RELATION_TYPES = ("PRECEDES", "CONTAINS", "RELATED_TO", "APPLIES_TO")


def normalize(name: str) -> str:
    name = (name or "").strip()
    return ENTITY_ALIASES.get(name, name)


def prf(pred: set, gold: set) -> dict:
    """精确率 / 召回率 / F1"""
    tp = len(pred & gold)
    precision = tp / len(pred) if pred else 0.0
    recall = tp / len(gold) if gold else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "tp": tp, "fp": len(pred) - tp, "fn": len(gold) - tp,
        "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
    }


def evaluate(gold: dict, entities: list, relations: list) -> dict:
    """对单次抽取结果计算指标（实体 + 关系严格/宽松 + 按关系类型细分）"""
    gold_entities = {normalize(e["name"]) for e in gold["entities"]}
    gold_rel_strict = {
        (normalize(r["source"]), r["type"], normalize(r["target"]))
        for r in gold["relations"]
    }
    gold_rel_lenient = {
        frozenset({normalize(r["source"]), normalize(r["target"])})
        for r in gold["relations"]
    }

    pred_entities = {normalize(e.get("name")) for e in entities}
    pred_entities.discard("")

    pred_rel_strict = set()
    pred_rel_lenient = set()
    for r in relations:
        s = normalize(r.get("source"))
        t = normalize(r.get("target"))
        typ = (r.get("type") or "").strip().upper()
        if not s or not t or typ not in RELATION_TYPES:
            continue
        pred_rel_strict.add((s, typ, t))
        pred_rel_lenient.add(frozenset({s, t}))

    # 按关系类型细分（模型在 PRECEDES 上的误报是重点观察项）
    per_type = {}
    for typ in RELATION_TYPES:
        gold_t = {r for r in gold_rel_strict if r[1] == typ}
        pred_t = {r for r in pred_rel_strict if r[1] == typ}
        per_type[typ] = {
            "gold": len(gold_t), "pred": len(pred_t),
            "tp": len(pred_t & gold_t),
            "fp": len(pred_t - gold_t),
            "fn": len(gold_t - pred_t),
            "strict_f1": prf(pred_t, gold_t)["f1"],
        }

    return {
        "entities": prf(pred_entities, gold_entities),
        "relations_strict": prf(pred_rel_strict, gold_rel_strict),
        "relations_lenient": prf(pred_rel_lenient, gold_rel_lenient),
        "relations_by_type": per_type,
        "pred_entities": sorted(pred_entities),
        "pred_relations": sorted(f"{s} -[{t}]-> {o}" for s, t, o in pred_rel_strict),
        "false_positive_relations": sorted(
            f"{s} -[{t}]-> {o}" for s, t, o in (pred_rel_strict - gold_rel_strict)
        ),
        "false_negative_relations": sorted(
            f"{s} -[{t}]-> {o}" for s, t, o in (gold_rel_strict - pred_rel_strict)
        ),
    }


def _resolve_paths():
    """支持 --gold <文件> 指定金标准；报告文件名由 gold 文件名推导"""
    if "--gold" in sys.argv:
        gold_path = Path(sys.argv[sys.argv.index("--gold") + 1])
        if not gold_path.is_absolute():
            gold_path = BACKEND_DIR / gold_path
    else:
        gold_path = GOLD_FILE
    report_name = "eval_report_" + gold_path.stem.replace("gold_", "", 1) + ".json"
    return gold_path, EVAL_DIR / report_name


async def main():
    gold_path, report_path = _resolve_paths()
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    text = TEXT_FILE.read_text(encoding="utf-8")

    print(f"金标准: {gold_path.name}（{len(gold['entities'])} 实体 / {len(gold['relations'])} 关系）")
    print(f"测试文本: {len(text)} 字符 / {TEXT_FILE.name}\n")

    extractor = KnowledgeExtractor()
    all_results = []
    for i in range(RUNS):
        print(f"[{i + 1}/{RUNS}] 调用 LLM 抽取（自动分块 + 并发）…")
        result = await extractor.extract(text)
        entities = result.get("entities", [])
        relations = result.get("relations", [])
        if result.get("error"):
            print(f"    ⚠ {result['error']}")
        metrics = evaluate(gold, entities, relations)
        all_results.append({"entities": entities, "relations": relations, "metrics": metrics})
        print(f"    实体 {len(entities)} 个，关系 {len(relations)} 条"
              f" | 实体F1={metrics['entities']['f1']}"
              f" | 关系严格F1={metrics['relations_strict']['f1']}"
              f" | 关系宽松F1={metrics['relations_lenient']['f1']}")

    # ---- 汇总：平均指标 ----
    def avg(path):
        vals = []
        for r in all_results:
            cur = r["metrics"]
            for k in path:
                cur = cur[k]
            vals.append(cur)
        return round(sum(vals) / len(vals), 4)

    summary = {
        "runs": RUNS,
        "entity": {
            "precision": avg(["entities", "precision"]),
            "recall": avg(["entities", "recall"]),
            "f1": avg(["entities", "f1"]),
        },
        "relation_strict": {
            "precision": avg(["relations_strict", "precision"]),
            "recall": avg(["relations_strict", "recall"]),
            "f1": avg(["relations_strict", "f1"]),
        },
        "relation_lenient": {
            "precision": avg(["relations_lenient", "precision"]),
            "recall": avg(["relations_lenient", "recall"]),
            "f1": avg(["relations_lenient", "f1"]),
        },
    }

    # 按类型：多轮平均（每轮各类型已算好 f1）
    per_type_avg = {}
    for typ in RELATION_TYPES:
        per_type_avg[typ] = {
            "gold": sum(r["metrics"]["relations_by_type"][typ]["gold"] for r in all_results) // RUNS,
            "avg_pred": round(sum(r["metrics"]["relations_by_type"][typ]["pred"] for r in all_results) / RUNS, 2),
            "avg_tp": round(sum(r["metrics"]["relations_by_type"][typ]["tp"] for r in all_results) / RUNS, 2),
            "avg_fp": round(sum(r["metrics"]["relations_by_type"][typ]["fp"] for r in all_results) / RUNS, 2),
            "avg_fn": round(sum(r["metrics"]["relations_by_type"][typ]["fn"] for r in all_results) / RUNS, 2),
            "strict_f1": round(sum(r["metrics"]["relations_by_type"][typ]["strict_f1"] for r in all_results) / RUNS, 4),
        }

    # 跨轮稳定性：某个实体/关系在 RUNS 轮中出现的次数
    ent_freq = Counter()
    rel_freq = Counter()
    for r in all_results:
        ent_freq.update({normalize(e.get("name")) for e in r["entities"]})
        for rel in r["relations"]:
            s = normalize(rel.get("source")); t = normalize(rel.get("target"))
            typ = (rel.get("type") or "").strip().upper()
            if s and t and typ in RELATION_TYPES:
                rel_freq.update([(s, typ, t)])

    report = {
        "text_id": gold["text_id"],
        "gold": {
            "entity_count": len(gold["entities"]),
            "relation_count": len(gold["relations"]),
            "entities": [e["name"] for e in gold["entities"]],
            "relations": [f"{r['source']} -[{r['type']}]-> {r['target']}" for r in gold["relations"]],
        },
        "summary": summary,
        "relations_by_type": per_type_avg,
        "entity_stability": dict(ent_freq.most_common()),
        "relation_stability": {f"{s} -[{t}]-> {o}": c for (s, t, o), c in rel_freq.most_common()},
        "runs": all_results,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 控制台报告 ----
    print("\n" + "=" * 62)
    print(f"评测报告（{RUNS} 轮平均）  gold: {len(gold['entities'])}实体/{len(gold['relations'])}关系")
    print("=" * 62)
    print(f"实体       Precision={summary['entity']['precision']:<7}"
          f"Recall={summary['entity']['recall']:<7}"
          f"F1={summary['entity']['f1']}")
    print(f"关系(严格) Precision={summary['relation_strict']['precision']:<7}"
          f"Recall={summary['relation_strict']['recall']:<7}"
          f"F1={summary['relation_strict']['f1']}")
    print(f"关系(宽松) Precision={summary['relation_lenient']['precision']:<7}"
          f"Recall={summary['relation_lenient']['recall']:<7}"
          f"F1={summary['relation_lenient']['f1']}")
    print("\n按关系类型（多轮平均，TP=命中 / FP=误报 / FN=漏报）：")
    print(f"{'类型':<14}{'gold':>5}{'pred':>6}{'TP':>6}{'FP':>6}{'FN':>6}{'严格F1':>9}")
    for typ in RELATION_TYPES:
        d = per_type_avg[typ]
        print(f"{typ:<14}{d['gold']:>5}{d['avg_pred']:>6}{d['avg_tp']:>6}"
              f"{d['avg_fp']:>6}{d['avg_fn']:>6}{d['strict_f1']:>9}")
    if per_type_avg["PRECEDES"]["avg_fp"] > 0:
        print(f"\n⚠ 模型输出了 {per_type_avg['PRECEDES']['avg_fp']} 条 PRECEDES 误报"
              f"（gold 中 PRECEDES 应为 0，见标注说明的边界判定）")
    print(f"\n完整结果已写入 {report_path}")


def rescore_from_report():
    """不重复调用 LLM：用已保存的评测报告中的预测结果，按当前归一化规则重新计分。

    用途：迭代归一化映射表时快速对比（python eval_accuracy_live.py --from-report）
    输出：eval_data/eval_report_第7章_树_normalized.json + 控制台摘要
    """
    gold = json.loads(GOLD_FILE.read_text(encoding="utf-8"))
    rep = json.loads((EVAL_DIR / "eval_report_第7章_树.json").read_text(encoding="utf-8"))

    rescored = []
    for i, run in enumerate(rep["runs"]):
        metrics = evaluate(gold, run["entities"], run["relations"])
        rescored.append({**run, "metrics": metrics})
        print(f"[轮{i + 1}] 实体F1={metrics['entities']['f1']} "
              f"关系严格F1={metrics['relations_strict']['f1']} "
              f"关系宽松F1={metrics['relations_lenient']['f1']}")

    def _get(m, path):
        for k in path:
            m = m[k]
        return m

    def avg(path):
        return round(sum(_get(r["metrics"], path) for r in rescored) / len(rescored), 4)

    summary = {
        "entity": {k: avg(["entities", k]) for k in ("precision", "recall", "f1")},
        "relation_strict": {k: avg(["relations_strict", k]) for k in ("precision", "recall", "f1")},
        "relation_lenient": {k: avg(["relations_lenient", k]) for k in ("precision", "recall", "f1")},
    }
    out = {**rep, "normalized": True, "summary": summary, "runs": rescored}
    out_path = EVAL_DIR / "eval_report_第7章_树_normalized.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 62)
    print("归一化后重算结果（3 轮平均）")
    print("=" * 62)
    print(f"实体       Precision={summary['entity']['precision']:<7}"
          f"Recall={summary['entity']['recall']:<7}"
          f"F1={summary['entity']['f1']}")
    print(f"关系(严格) Precision={summary['relation_strict']['precision']:<7}"
          f"Recall={summary['relation_strict']['recall']:<7}"
          f"F1={summary['relation_strict']['f1']}")
    print(f"关系(宽松) Precision={summary['relation_lenient']['precision']:<7}"
          f"Recall={summary['relation_lenient']['recall']:<7}"
          f"F1={summary['relation_lenient']['f1']}")
    print(f"\n完整结果已写入 {out_path}")


if __name__ == "__main__":
    if "--from-report" in sys.argv:
        rescore_from_report()
    else:
        asyncio.run(main())
