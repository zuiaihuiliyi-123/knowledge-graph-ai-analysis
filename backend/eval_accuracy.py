"""
知识抽取准确率评测：人工 Gold 标注 vs 系统预测，计算 Entity 与 Relation 的 P/R/F1。
Relation 分类别（PRECEDES/CONTAINS/RELATED_TO/APPLIES_TO）输出 TP/FP/FN/Precision/Recall/F1。

用法（在 backend/ 目录下）：
    python eval_accuracy.py --gold eval_data/gold.json --pred eval_data/pred.json

Gold / Pred 文件格式（均为 list，按 text_id 对齐）：
[
  {
    "text_id": "chapter_01_p03",
    "entities": [{"name": "函数调用", "category": "概念"}],
    "relations": [{"source": "函数调用", "target": "递归", "type": "PRECEDES"}]
  }
]

口径说明：Confidence 是模型自评置信度，不代表真实准确率；
真实准确率仅由本脚本按人工 Gold 标注计算 Precision / Recall / F1 得出。
"""
import argparse
import json

from app.services.knowledge_extractor import _normalize_entity_name

_REL_TYPES = ("PRECEDES", "CONTAINS", "RELATED_TO", "APPLIES_TO")


def _entity_set(items) -> set:
    s = set()
    for e in items:
        n = _normalize_entity_name(e.get("name", ""))
        if n:
            s.add(n)
    return s


def _relation_set(items) -> set:
    s = set()
    for r in items:
        src = _normalize_entity_name(r.get("source", ""))
        tgt = _normalize_entity_name(r.get("target", ""))
        typ = (r.get("type", "") or "").strip().upper()
        if src and tgt and typ in _REL_TYPES:
            s.add((src, tgt, typ))
    return s


def _prf(tp, fp, fn):
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main(gold_path, pred_path):
    gold = {d["text_id"]: d for d in load(gold_path)}
    pred = {d["text_id"]: d for d in load(pred_path)}

    ent_tp = ent_fp = ent_fn = 0
    rel_stat = {t: {"tp": 0, "fp": 0, "fn": 0} for t in _REL_TYPES}

    for tid, g in gold.items():
        p = pred.get(tid)
        g_ent = _entity_set(g.get("entities", []))
        g_rel = _relation_set(g.get("relations", []))

        if p is None:
            print(f"[警告] text_id={tid} 在 pred 中缺失，记为全部 FN")
            ent_fn += len(g_ent)
            for (_s, _t, typ) in g_rel:
                rel_stat[typ]["fn"] += 1
            continue

        p_ent = _entity_set(p.get("entities", []))
        ent_tp += len(g_ent & p_ent)
        ent_fp += len(p_ent - g_ent)
        ent_fn += len(g_ent - p_ent)

        p_rel = _relation_set(p.get("relations", []))
        for typ in _REL_TYPES:
            g_typ = {r for r in g_rel if r[2] == typ}
            p_typ = {r for r in p_rel if r[2] == typ}
            rel_stat[typ]["tp"] += len(g_typ & p_typ)
            rel_stat[typ]["fp"] += len(p_typ - g_typ)
            rel_stat[typ]["fn"] += len(g_typ - p_typ)

    ep, er, ef = _prf(ent_tp, ent_fp, ent_fn)
    print("=" * 60)
    print(f"Entity:  TP={ent_tp}  FP={ent_fp}  FN={ent_fn}")
    print(f"  Precision={ep:.3f}  Recall={er:.3f}  F1={ef:.3f}")

    print("=" * 60)
    print(f"{'关系类型':<12}{'TP':>6}{'FP':>6}{'FN':>6}{'Precision':>11}{'Recall':>9}{'F1':>9}")
    macro_f1 = 0.0
    n_types = 0
    for t in _REL_TYPES:
        s = rel_stat[t]
        p, r, f = _prf(s["tp"], s["fp"], s["fn"])
        print(f"{t:<12}{s['tp']:>6}{s['fp']:>6}{s['fn']:>6}{p:>11.3f}{r:>9.3f}{f:>9.3f}")
        if s["tp"] or s["fp"] or s["fn"]:
            macro_f1 += f
            n_types += 1
    macro_f1 = macro_f1 / n_types if n_types else 0.0
    print("-" * 60)
    print(f"Relation Macro-F1 = {macro_f1:.3f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("--pred", required=True)
    a = ap.parse_args()
    main(a.gold, a.pred)
