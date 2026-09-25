"""口径切换（L2 决策 1a 掌握度归因 / 4a 配额「任一命中」）前后对比

用途：切换前跑一次 `--tag before`，改完代码再跑一次 `--tag after`，两份 JSON 直接对比即可
回答「多标签口径的引入到底改变了什么」。**只读**：不写库、不改任何业务数据。

观测项（每个「学生 × 模式」一条）：
- picked：最终组卷题数；candidates：召回候选数
- buckets：各桶实际命中数（含 filled / quota_relaxed 两个应急计数）
- distinct_kp：组卷覆盖的**不同知识点数**（多标签口径下应上升或持平）
- mastery_avg：该学生掌握度均值（决策 1a 会系统性拉低它——这是预期副作用）

用法（backend 目录下）：
    python eval_recommend_shift.py --course-id 65 --tag before
    python eval_recommend_shift.py --course-id 65 --tag after
产出：eval_data/eval_report_recommend_shift_<tag>.json
"""
import argparse
import json
import os
import sys
from statistics import mean

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.sql_database import sql_db
from app.services.question_recommender import VALID_MODES, QuestionRecommender


def student_ids(course_id: int) -> list:
    """该课程有作答记录的学生（没有作答记录就反映不出掌握度差异）"""
    rows = sql_db._query(
        "SELECT DISTINCT user_id AS uid FROM t_answer_record WHERE course_id = ? "
        "ORDER BY user_id", (course_id,))
    return [r["uid"] for r in rows]


def one(user_id: int, course_id: int, mode: str, seed: int) -> dict:
    res = QuestionRecommender.recommend(
        user_id, course_id, count=10, mode=mode, seed=seed)
    items, meta = res["items"], res["meta"]
    kps = {it["kp_id"] for it in items if it.get("kp_id")}
    masteries = [it["mastery"] for it in items if it.get("mastery") is not None]
    sems = [(it.get("signals") or {}).get("semantic") for it in items]
    sems = [s for s in sems if s is not None]
    sem_raws = [(it.get("signals") or {}).get("semantic_raw") for it in items]
    sem_raws = [s for s in sem_raws if s is not None]
    buckets = dict(meta.get("buckets") or {})
    return {
        "user_id": user_id, "mode": mode,
        "picked": len(items), "candidates": meta.get("candidates"),
        "buckets": buckets,
        "filled": buckets.get("filled", 0),
        "quota_relaxed": buckets.get("quota_relaxed", 0),
        "fill_rate": meta.get("fill_rate"),
        "kp_attr_mode": meta.get("kp_attribution_mode"),
        "kp_quota_mode": meta.get("kp_quota_mode"),
        "mastery_stats": meta.get("mastery_stats"),
        # 第 7 信号（语义相关度）：是否生效 + 被抽中题目的语义相关度均值
        #   sem_avg     = 标准化后（参与打分的那个值）
        #   sem_raw_avg = 原始余弦（可与全体候选中位数对比，判断是否真的择优）
        "semantic_available": meta.get("semantic_available"),
        "semantic_avg": round(mean(sems), 4) if sems else None,
        "sem_raw_avg": round(mean(sem_raws), 4) if sem_raws else None,
        "distinct_kp": len(kps),
        "mastery_avg": round(mean(masteries), 2) if masteries else None,
        "kp_quota": meta.get("kp_quota"),
        "graph_available": meta.get("graph_available"),
    }


def main():
    ap = argparse.ArgumentParser(description="口径切换前后对比（只读）")
    ap.add_argument("--course-id", type=int, default=65)
    ap.add_argument("--tag", default="before", help="before / after（仅用于文件名与打印）")
    ap.add_argument("--seed", type=int, default=2026, help="固定随机种子（保证可复现）")
    ap.add_argument("--count", type=int, default=10)
    args = ap.parse_args()

    users = student_ids(args.course_id)
    print(f"课程 {args.course_id}：有作答记录的学生 {len(users)} 人，模式 {list(VALID_MODES)}")
    print(f"seed={args.seed}（固定，保证 before/after 可对比）\n")

    rows = [one(u, args.course_id, m, args.seed) for u in users for m in VALID_MODES]

    # 聚合：按模式看均值
    print(f"{'mode':10s} {'picked':>7s} {'cand':>7s} {'dist_kp':>8s} "
          f"{'filled':>7s} {'relaxed':>8s} {'mastery':>8s} "
          f"{'G_mastery':>10s} {'below_weak':>11s} {'sem_avg':>8s} {'sem_raw':>9s}")
    print("-" * 108)
    agg = {}
    for m in VALID_MODES:
        sub = [r for r in rows if r["mode"] == m]
        if not sub:
            continue
        ms = [r["mastery_avg"] for r in sub if r["mastery_avg"] is not None]
        gm = [r["mastery_stats"]["avg"] for r in sub
              if (r.get("mastery_stats") or {}).get("avg") is not None]
        bw = [r["mastery_stats"]["below_weak"] for r in sub
              if (r.get("mastery_stats") or {}).get("below_weak") is not None]
        sem = [r["semantic_avg"] for r in sub if r["semantic_avg"] is not None]
        semr = [r["sem_raw_avg"] for r in sub if r["sem_raw_avg"] is not None]
        rec = {
            "picked": round(mean(r["picked"] for r in sub), 2),
            "candidates": round(mean(r["candidates"] or 0 for r in sub), 2),
            "distinct_kp": round(mean(r["distinct_kp"] for r in sub), 2),
            "filled": round(mean(r["filled"] for r in sub), 2),
            "quota_relaxed": round(mean(r["quota_relaxed"] for r in sub), 2),
            "mastery_avg": round(mean(ms), 2) if ms else None,
            # 全局掌握度（全课程知识点）——观决策 1a 副作用的真指标
            "global_mastery_avg": round(mean(gm), 2) if gm else None,
            "below_weak_avg": round(mean(bw), 2) if bw else None,
            "semantic_avg": round(mean(sem), 4) if sem else None,
            "sem_raw_avg": round(mean(semr), 4) if semr else None,
        }
        agg[m] = rec
        print(f"{m:10s} {rec['picked']:7.2f} {rec['candidates']:7.2f} "
              f"{rec['distinct_kp']:8.2f} {rec['filled']:7.2f} "
              f"{rec['quota_relaxed']:8.2f} "
              f"{(rec['mastery_avg'] if rec['mastery_avg'] is not None else 0):8.2f} "
              f"{(rec['global_mastery_avg'] if rec['global_mastery_avg'] is not None else 0):10.2f} "
              f"{(rec['below_weak_avg'] if rec['below_weak_avg'] is not None else 0):11.2f} "
              f"{(rec['semantic_avg'] if rec['semantic_avg'] is not None else 0):8.3f} "
              f"{(rec['sem_raw_avg'] if rec['sem_raw_avg'] is not None else 0):10.4f}")

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval_data")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"eval_report_recommend_shift_{args.tag}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "tag": args.tag, "course_id": args.course_id, "seed": args.seed,
            "count": args.count, "students": len(users),
            "aggregate_by_mode": agg, "detail": rows,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n明细已写入 {os.path.relpath(path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
