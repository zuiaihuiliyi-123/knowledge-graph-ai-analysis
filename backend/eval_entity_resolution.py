"""文档级实体消歧与融合的**独立评测**（不与 V1.1 抽取准确率评测混在一起）。

运行方式（在 backend 目录下执行）：
    python eval_entity_resolution.py

只读：读真实知识图谱与真实文档正文，**不写任何库**，结果落
`eval_data/eval_report_entity_resolution.json`。

## 评测口径（必须先读，否则数字会被误读）

模块有两道闸门，本脚本**分别**报告：

  A. 候选闸门（`RECALL_THRESHOLD`）：一对实体是否被"捞出来给人看"。
     这是本模块的主要价值所在 —— 召回 + 人工/LLM 复核。
  B. 自动融合闸门（`AUTO_SAME_THRESHOLD`）：是否**不经过人**直接融合。
     这是风险所在：错一次就永久污染图谱（Neo4j 只读、只能靠撤销映射回退）。

对课程知识图谱而言 B 的错误代价远高于 A 的漏报，所以两个口径都要看，
**不能只报一个综合 F1**。

## 样本与来源

`eval_data/entity_resolution_gold.json`：93 对（SAME 20 / DIFFERENT 73），
来自 14 个真实文档；正样本由真实正文里的显式等价式收割后**逐条人工复核**。
正样本规模小是方法层面的上限（抽取阶段已按名合并），**不得据此宣称系统整体准确率**。
"""
import asyncio
import io
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.sql_database import sql_db
from app.core.storage import resolve_document_path
from app.services.document_parser import DocumentParser
from app.services.fusion import entity_candidates as EC
from app.services.fusion import entity_context as ECTX
from app.services.fusion import fusion_config as CFG
from app.services.fusion import fusion_pipeline as FP
from app.services.fusion.entity_resolver import band_of
from app.services.kg_manager import KnowledgeGraphManager

GOLD = os.path.join("eval_data", "entity_resolution_gold.json")
OUT = os.path.join("eval_data", "eval_report_entity_resolution.json")


def _alias_profile_node(name: str) -> dict:
    """给「只以别名形态出现、并未成为图节点」的一侧造一个 profile 输入。

    这是**真实会发生的情形**：模型如果把 `先来先服务` 也抽成了节点，
    它就是这么一条记录。但没有真实抽取产物可依，所以
    类别与定义一律留空 —— 宁可让模块靠"名称 + 重建上下文"判断，
    也不替模型编一份定义来喂给自己。
    """
    return {"id": f"alias::{name}", "label": name, "type": "concept", "description": "",
            "properties": {"category": "", "confidence": None, "is_manual": False}}


async def main():
    if not os.path.exists(GOLD):
        print(f"缺少 {GOLD}，请先运行 python build_entity_resolution_gold.py")
        return 1
    gold = json.load(io.open(GOLD, encoding="utf-8"))
    pairs = gold["pairs"]

    docs = {}
    for p in pairs:
        docs.setdefault(p["document_id"], p["course_id"])

    cache = {}
    for doc_id, course_id in docs.items():
        row = sql_db.get_document(doc_id)
        if not row:
            print(f"  跳过文档 {doc_id}（记录不存在）")
            continue
        try:
            graph = KnowledgeGraphManager.get_raw_graph_v1(course_id, doc_id, limit=2000)
        except Exception as e:  # noqa: BLE001
            print(f"  跳过文档 {doc_id}（读图失败：{e}）")
            continue
        chunks, ctx_err = await ECTX.load_chunks(course_id, doc_id)
        cache[doc_id] = {"course_id": course_id, "graph": graph, "chunks": chunks,
                         "ctx_err": ctx_err}

    # ---------------- 逐对打分 ----------------
    rows = []
    for p in pairs:
        c = cache.get(p["document_id"])
        if not c:
            continue
        nodes = c["graph"]["nodes"]
        edges = c["graph"]["edges"]
        by_label = {}
        for n in nodes:
            by_label.setdefault(n["label"], n)

        a_node = by_label.get(p["entity_a"])
        b_node = by_label.get(p["entity_b"])
        a_is_node, b_is_node = a_node is not None, b_node is not None
        if a_node is None:
            a_node = _alias_profile_node(p["entity_a"])
        if b_node is None:
            b_node = _alias_profile_node(p["entity_b"])

        # 上下文：只为这一对重建（不重建全文档，省掉无谓开销）
        ctxs = {}
        if c["chunks"]:
            for n in (a_node, b_node):
                ctxs[n["id"]] = ECTX.build_for(
                    n["id"], n["label"], n.get("description") or "", [], c["chunks"])

        profs = EC.build_profiles([a_node, b_node], edges, ctxs)
        if len(profs) < 2:
            continue
        _, pair_types = EC.build_edge_index(edges)
        detail = EC.score_pair(profs[0], profs[1], pair_types)
        decision, band = FP.entity_resolver.decide_by_rules(detail["score"], allow_review=False)

        rows.append({
            "document_id": p["document_id"], "kind": p["kind"], "label": p["label"],
            "entity_a": p["entity_a"], "entity_b": p["entity_b"],
            "score": round(detail["score"], 4), "band": band, "decision": decision,
            "blocked": detail.get("blocked"),
            "is_candidate": (not detail.get("blocked")) and detail["score"] >= CFG.RECALL_THRESHOLD,
            "auto_same": decision == "SAME",
            "both_nodes": a_is_node and b_is_node,
        })

    def prf(pred_fn, positive="SAME"):
        tp = fp = fn = tn = 0
        for r in rows:
            pred, truth = pred_fn(r), r["label"] == positive
            if pred and truth:
                tp += 1
            elif pred and not truth:
                fp += 1
            elif not pred and truth:
                fn += 1
            else:
                tn += 1
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
                "precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4)}

    cand_m = prf(lambda r: r["is_candidate"])
    auto_m = prf(lambda r: r["auto_same"])

    n_same = sum(1 for r in rows if r["label"] == "SAME")
    n_diff = len(rows) - n_same
    uncertain = sum(1 for r in rows if r["band"] == "UNCERTAIN")
    false_merge = [r for r in rows if r["auto_same"] and r["label"] == "DIFFERENT"]
    missed_same = [r for r in rows if r["label"] == "SAME" and not r["is_candidate"]]

    # ---------------- 隔离性：候选不得跨文档 ----------------
    isolation = {"checked_docs": 0, "cross_doc_candidates": 0, "details": []}
    for doc_id, c in cache.items():
        own = {n["id"] for n in c["graph"]["nodes"]}
        try:
            res = await FP.scan(c["course_id"], doc_id, persist=False)
        except Exception as e:  # noqa: BLE001
            isolation["details"].append({"document_id": doc_id, "error": str(e)[:80]})
            continue
        if not res["ok"]:
            isolation["details"].append({"document_id": doc_id, "error": res["message"][:80]})
            continue
        isolation["checked_docs"] += 1
        for cand in res["data"]["candidates"]:
            for side in ("source_kp_id", "target_kp_id"):
                if cand[side] not in own:
                    isolation["cross_doc_candidates"] += 1
                    isolation["details"].append(
                        {"document_id": doc_id, "kp_id": cand[side], "side": side})

    report = {
        "meta": {
            "gold": GOLD,
            "sample": {"total": len(rows), "same": n_same, "different": n_diff,
                       "documents": len(cache)},
            "thresholds": {"recall": CFG.RECALL_THRESHOLD,
                           "review": CFG.REVIEW_THRESHOLD,
                           "auto_same": CFG.AUTO_SAME_THRESHOLD},
            "caveats": [
                "样本量小（SAME 20 条），**不得**据此宣称系统整体准确率。",
                "⚠️ **本批数字偏乐观**：模块的「正文等价式」这一支（gloss_partners）"
                "正是因为在本样本上跑到 Recall 0.05 才补上的。也就是说，"
                "指标是在同一批样本上被改进过的 —— 换一批文档不可能有这么高。",
                "正样本的「另一端」多数并非图节点，是按真实正文里写出的别名重构的轮廓"
                "（类别与定义留空）——这低估了模块在有完整定义时的表现。",
                "所有指标都只覆盖这批文档，不能外推到未评测的文档。",
                "confidence / 得分是模型与规则的内部度量，**不等于准确率**。",
            ],
        },
        "candidate_gate": cand_m,
        "auto_merge_gate": auto_m,
        "false_merge_count": len(false_merge),
        "false_merges": [{"document_id": r["document_id"], "a": r["entity_a"],
                          "b": r["entity_b"], "score": r["score"], "kind": r["kind"]}
                         for r in false_merge],
        "uncertain_count": uncertain,
        "uncertain_rate": round(uncertain / len(rows), 4) if rows else 0.0,
        "missed_same": [{"document_id": r["document_id"], "a": r["entity_a"],
                         "b": r["entity_b"], "score": r["score"],
                         "blocked": r["blocked"]} for r in missed_same],
        "by_kind": {},
        "isolation": isolation,
        "rows": rows,
    }

    for kind in sorted({r["kind"] for r in rows}):
        sub = [r for r in rows if r["kind"] == kind]
        report["by_kind"][kind] = {
            "n": len(sub),
            "candidate_rate": round(sum(1 for r in sub if r["is_candidate"]) / len(sub), 4),
            "auto_same_rate": round(sum(1 for r in sub if r["auto_same"]) / len(sub), 4),
        }

    io.open(OUT, "w", encoding="utf-8").write(
        json.dumps(report, ensure_ascii=False, indent=2))

    # ---------------- 输出 ----------------
    print("=" * 74)
    print("文档级实体消歧与融合 · 评测报告")
    print("=" * 74)
    print(f"样本：{len(rows)} 对（SAME {n_same} / DIFFERENT {n_diff}），"
          f"覆盖 {len(cache)} 个真实文档\n")
    print("【候选闸门】一对实体是否被捞出给人看")
    print(f"  Precision {cand_m['precision']:.3f}  Recall {cand_m['recall']:.3f}  "
          f"F1 {cand_m['f1']:.3f}   (TP {cand_m['tp']} / FP {cand_m['fp']} / FN {cand_m['fn']})")
    print("\n【自动融合闸门】不经人直接融合（风险所在）")
    print(f"  Precision {auto_m['precision']:.3f}  Recall {auto_m['recall']:.3f}  "
          f"F1 {auto_m['f1']:.3f}   (TP {auto_m['tp']} / FP {auto_m['fp']})")
    print(f"  **False Merge（把不同实体自动融合）: {len(false_merge)} 次**")
    print(f"\n落在 UNCERTAIN 档（进人工复核池）: {uncertain}/{len(rows)} = "
          f"{report['uncertain_rate']:.1%}")
    print(f"漏掉的真同义对: {len(missed_same)}")
    for r in missed_same:
        why = f"被硬负门拦下（{r['blocked']}）" if r["blocked"] else f"得分 {r['score']} 低于召回线"
        print(f"    - [{r['document_id']}] {r['entity_a']} / {r['entity_b']}：{why}")
    print("\n按样本类型：")
    for k, v in report["by_kind"].items():
        print(f"  {k:<16} n={v['n']:<3} 进候选 {v['candidate_rate']:.1%}  "
              f"自动融合 {v['auto_same_rate']:.1%}")
    print(f"\n文档隔离：检查 {isolation['checked_docs']} 个文档的候选端点，"
          f"跨文档候选 {isolation['cross_doc_candidates']} 个")
    print(f"\n报告已写入 {OUT}")

    ok = (len(false_merge) == 0 and isolation["cross_doc_candidates"] == 0)
    print("\n" + ("关键安全指标通过：无错误自动融合、无跨文档候选"
                  if ok else "!! 关键安全指标未通过，详见报告"))
    print("=" * 74)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
