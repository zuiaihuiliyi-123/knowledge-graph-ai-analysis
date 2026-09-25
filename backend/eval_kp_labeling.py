"""
知识点自动标注离线评估（P3）

两种模式，二者都只读、不改库：

  A) 留一评估（真实数据，需 Neo4j 已启动）
     python eval_kp_labeling.py --course-id 65 --document-id 101
     对「已挂 kp_id」的题做弱标签：临时隐藏该题的 kp_id，用标注器预测 top-k，
     统计 hit@1 / hit@3 / hit@5、MRR、耗时与各层命中占比。
     —— 衡量真实题库上的标注准确率，但标签来自人工/历史挂载，属**弱标签**。

  B) 自洽评估（离线，无需 Neo4j、无需 EMBEDDING_API_KEY）
     python eval_kp_labeling.py --snapshot backup/neo4j_20260905_145113.json --course-id 5 --synthetic
     把备份里每个知识点的 description 当作查询文本，评估「知识点描述 → 该知识点」的
     字面匹配 + 图谱扩展效果（向量路因无 key 自动关闭）。
     —— 衡量描述级语义可恢复性，是**自洽指标**，不等于真实题目上的表现。

输出：控制台表格 + eval_data/eval_report_kp_labeling.json（含全部阈值/权重，便于复现）。
"""
import argparse
import json
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.sql_database import sql_db
from app.services import kp_labeler
from app.services.embedding import EmbeddingClient
from app.services.kp_labeler import (
    GRAPH_MIN_SCORE, MIN_SCORE, TOP_K, VEC_FLOOR, W_GRAPH, W_LEX, W_VEC,
    KpCatalog, label_question,
)


def _hit_metrics(rows: list, top_k: int) -> dict:
    """rows: [{"rank": int|None}]（rank = 真值在候选中的位置，1 起；未命中为 None）"""
    total = len(rows)
    if not total:
        return {"total": 0, "hit@1": 0.0, "hit@3": 0.0, "hit@5": 0.0, "mrr": 0.0}

    def _hit(n):
        return round(sum(1 for r in rows if r["rank"] and r["rank"] <= n) / total, 4)

    mrr = round(sum((1.0 / r["rank"]) for r in rows if r["rank"]) / total, 4)
    return {"total": total, "hit@1": _hit(1), "hit@3": _hit(3),
            "hit@5": _hit(min(5, max(top_k, 1))), "mrr": mrr}


def load_snapshot(path: str, course_id) -> tuple:
    """候选源加载（两种格式自动识别），返回 (items, relations)

    1) Neo4j JSON 备份：节点 labels 含 KnowledgePoint，props 里有 kp_id/name/description
    2) Gold 标注文件（eval_data/gold_*.json）：entities[{name,category,description}] +
       relations[{source,target,type}]，此时 kp_id 就用 name（跨文件对得上）
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # ---- Gold 标注格式 ----
    if isinstance(data.get("entities"), list):
        items = [
            {"kp_id": e.get("name"), "name": e.get("name") or "",
             "category": e.get("category") or "", "description": e.get("description") or "",
             "document_id": None}
            for e in data["entities"] if e.get("name")
        ]
        relations = {}
        for rel in data.get("relations", []):
            if rel.get("type") not in ("PRECEDES", "CONTAINS"):
                continue
            if rel.get("source") and rel.get("target"):
                relations.setdefault(rel["source"], []).append((rel["type"], rel["target"]))
        return items, relations

    # ---- Neo4j 备份格式 ----
    node_course, items = {}, []
    for node in data.get("nodes", []):
        if "KnowledgePoint" not in (node.get("labels") or []):
            continue
        props = node.get("props") or {}
        if course_id is not None and props.get("course_id") != course_id:
            continue
        kp_id = props.get("kp_id")
        if not kp_id:
            continue
        node_course[node.get("id")] = kp_id
        items.append({
            "kp_id": kp_id,
            "name": props.get("name") or kp_id,
            "category": props.get("category") or "",
            "description": props.get("description") or "",
            "document_id": props.get("document_id"),
        })
    relations = {}
    for rel in data.get("relationships", []):
        if rel.get("type") not in ("PRECEDES", "CONTAINS"):
            continue
        a, b = node_course.get(rel.get("start")), node_course.get(rel.get("end"))
        if a and b:
            relations.setdefault(a, []).append((rel.get("type"), b))
    return items, relations


def eval_synthetic(items: list, relations: dict, top_k: int = TOP_K,
                   min_desc: int = 8) -> dict:
    """模式 B：把知识点 description 当查询，评估能否标注回该知识点"""
    catalog = KpCatalog(0, items=items, relations=relations).load()
    rows, skipped, graph_only_hits = [], 0, 0
    layer_hits = {"lexical": 0, "vector": 0, "graph": 0}
    t0 = time.time()
    for it in items:
        text = (it.get("description") or "").strip()
        if len(text) < min_desc:
            skipped += 1
            continue
        res = label_question({"stem": text, "options": []}, catalog, top_k=top_k)
        rank = None
        for i, c in enumerate(res["candidates"], start=1):
            if c["kp_id"] == it["kp_id"]:
                rank = i
                if c.get("graph_only"):
                    graph_only_hits += 1
                for layer, value in (c.get("sources") or {}).items():
                    if value:
                        layer_hits[layer] = layer_hits.get(layer, 0) + 1
                break
        rows.append({"kp_id": it["kp_id"], "name": it["name"], "rank": rank,
                     "top": (res["candidates"][0]["kp_id"] if res["candidates"] else None)})
    metrics = _hit_metrics(rows, top_k)
    metrics.update({
        "mode": "synthetic(description->kp)",
        "catalog_size": len(items),
        "skipped_no_description": skipped,
        "graph_only_hits": graph_only_hits,
        "layer_hits": layer_hits,
        "elapsed_sec": round(time.time() - t0, 3),
    })
    return {"metrics": metrics, "rows": rows}


def eval_course(course_id: int, document_id, top_k: int = TOP_K,
                use_vector: bool = False) -> dict:
    """模式 A：真实题库留一评估（隐藏题目已挂 kp_id，用标注器预测）

    `use_vector=True` 时启用**向量路**（需 `EMBEDDING_API_KEY` + 两张向量表有数据）。
    题目向量与知识点向量都从库里**批量读回**（BLOB 快读）→ **评测本身不产生 embedding API 调用**。
    该参数是 L2 阶段 E 新增：此前本函数不传 embedder/kp_vectors，向量路恒为关闭，
    所以历史报告（hit@1 0.102）只反映「字面 + 图谱」两层。
    """
    catalog = KpCatalog(course_id, document_id).load()
    embedder = EmbeddingClient() if use_vector else None
    if embedder is not None and not embedder.available:
        embedder = None                                   # 无 key → 自动回落，不报错
    kp_vectors = kp_labeler._load_kp_vectors(course_id, document_id) if embedder else {}
    _, questions = sql_db.list_questions(course_id, document_id=document_id,
                                          page=1, page_size=100000)
    labeled = [q for q in questions if (q.get("kp_id") or "").strip()]
    q_vectors = (kp_labeler._load_question_vectors(labeled, course_id)
                 if kp_vectors else {})
    rows, no_cand, layer = [], 0, {"lexical": 0, "vector": 0, "graph": 0}
    t0 = time.time()
    for q in labeled:
        res = label_question(q, catalog, embedder=embedder, kp_vectors=kp_vectors,
                             top_k=top_k, q_vec=q_vectors.get(q["question_id"]))
        rank = None
        for i, c in enumerate(res["candidates"], start=1):
            if c["kp_id"] == q["kp_id"]:
                rank = i
                # 命中项带哪些证据层（用于判断「向量路贡献了多少命中」）
                for name, val in (c.get("sources") or {}).items():
                    if val:
                        layer[name] = layer.get(name, 0) + 1
                break
        if not res["candidates"]:
            no_cand += 1
        rows.append({"question_id": q["question_id"], "kp_id": q["kp_id"],
                     "stem": (q.get("stem") or "")[:60], "rank": rank,
                     "top": (res["candidates"][0]["kp_id"] if res["candidates"] else None)})
    metrics = _hit_metrics(rows, top_k)
    metrics.update({
        "mode": "leave-one-out(real questions)",
        "course_id": course_id,
        "document_id": document_id,
        "catalog_size": len(catalog.items),
        "catalog_source": catalog.source,
        "graph_available": bool(catalog.graph_available),
        "vector_enabled": bool(kp_vectors),
        "kp_vector_count": len(kp_vectors),
        "question_vector_count": len(q_vectors),
        "hit_layer_breakdown": layer,
        "questions_without_candidates": no_cand,
        "elapsed_sec": round(time.time() - t0, 3),
        "reason": catalog.reason,
    })
    return {"metrics": metrics, "rows": rows}


def _params() -> dict:
    """冻结本次评估的算法参数（与项目「实验参数冻结」纪律一致）"""
    return {
        "top_k": TOP_K,
        "weights": {"lexical": W_LEX, "vector": W_VEC, "graph": W_GRAPH},
        "vec_floor": VEC_FLOOR,
        "min_score": MIN_SCORE,
        "graph_min_score": GRAPH_MIN_SCORE,
        "lex_base": kp_labeler.LEX_BASE,
        "lex_min_name_len": kp_labeler.LEX_MIN_NAME_LEN,
        "option_weight": kp_labeler.OPTION_WEIGHT,
        "graph_decay": kp_labeler.GRAPH_DECAY,
        "apply_min_score": kp_labeler.APPLY_MIN_SCORE,
    }


def main():
    ap = argparse.ArgumentParser(description="知识点自动标注离线评估")
    ap.add_argument("--course-id", type=int, default=None)
    ap.add_argument("--document-id", type=int, default=None)
    ap.add_argument("--snapshot", default=None, help="Neo4j JSON 备份路径（离线模式 B）")
    ap.add_argument("--synthetic", action="store_true",
                    help="与 --snapshot 搭配：用知识点描述做自洽评估")
    ap.add_argument("--top-k", type=int, default=TOP_K)
    ap.add_argument("--no-vector", action="store_true",
                    help="关闭向量路（默认开启；无 EMBEDDING_API_KEY 时自动回落）")
    ap.add_argument("--out", default=os.path.join("eval_data", "eval_report_kp_labeling.json"))
    args = ap.parse_args()

    if args.snapshot and args.synthetic:
        items, relations = load_snapshot(args.snapshot, args.course_id)
        if not items:
            print(f"✗ 备份 {args.snapshot} 中没有 course_id={args.course_id} 的知识点")
            return False
        print(f"离线自洽评估：课程 {args.course_id}，知识点 {len(items)} 个，"
              f"PRECEDES/CONTAINS 关系 {sum(len(v) for v in relations.values())} 条")
        result = eval_synthetic(items, relations, top_k=args.top_k)
    else:
        if args.course_id is None:
            print("✗ 请给出 --course-id（真实留一评估）或 --snapshot + --synthetic（离线自洽评估）")
            return False
        sql_db.init_tables()
        result = eval_course(args.course_id, args.document_id, top_k=args.top_k,
                             use_vector=not args.no_vector)
        # A/B：有向量数据时同时跑一次「关向量」作对照，量化向量路到底带来多少增益
        if not args.no_vector and result["metrics"].get("vector_enabled"):
            bm = eval_course(args.course_id, args.document_id, top_k=args.top_k,
                             use_vector=False)["metrics"]
            cur = result["metrics"]
            result["metrics"]["ab_without_vector"] = {
                k: bm[k] for k in ("hit@1", "hit@3", "hit@5", "mrr",
                                   "questions_without_candidates")}
            print("\n向量路 A/B（同一题库、同一参数、同一评分口径）：")
            print(f"  {'指标':<26}{'关向量':>10s}{'开向量':>10s}{'增益':>12s}")
            print("  " + "-" * 58)
            for k in ("hit@1", "hit@3", "hit@5", "mrr"):
                print(f"  {k:<26}{bm[k]:>10.4f}{cur[k]:>10.4f}{cur[k] - bm[k]:>+12.4f}")
            print(f"  {'无候选的题数':<24}{bm['questions_without_candidates']:>10d}"
                  f"{cur['questions_without_candidates']:>10d}"
                  f"{cur['questions_without_candidates'] - bm['questions_without_candidates']:>+12d}")

    m = result["metrics"]
    print("\n指标：")
    for key in ("mode", "total", "hit@1", "hit@3", "hit@5", "mrr", "catalog_size",
                "catalog_source", "graph_available", "vector_enabled",
                "kp_vector_count", "question_vector_count", "hit_layer_breakdown",
                "graph_only_hits", "layer_hits",
                "skipped_no_description", "questions_without_candidates",
                "elapsed_sec", "reason"):
        if key in m:
            print(f"  {key:<30}{m[key]}")

    payload = {
        "params": _params(),
        "metrics": m,
        "samples": result["rows"][:50],
        "command": " ".join(sys.argv),
    }
    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n报告已写入：{args.out}")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)

