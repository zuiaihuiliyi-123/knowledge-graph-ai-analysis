"""召回层评测（L2 阶段 D）：**Recall@K** —— 候选集是否包含「全量打分的前 K 名」

安全阀依据 `docs/题库与推荐系统设计.md` §9.1：**Recall@K ≥ 0.95**。
该指标过低说明召回把「本该推荐给学生的题」挡在了候选集外，即推荐质量下降。

方法（同一学生、同一模式、同一 seed，**只切换召回开关**）：
- **OFF（基线）**：`RECALL_ENABLED=False` → 候选池 = 全量；取打分最高的前 K 题作为真值；
- **ON**：`RECALL_ENABLED=True` → 候选池 = 召回子集；`debug_top` 设大以取到**全部**候选；
- `Recall@K = |OFF_topK ∩ ON_all| / K`。

用法（backend 目录下）：
    python eval_question_recall.py --course-id 65 --k 30
产出：`eval_data/eval_report_question_recall.json` + 控制台摘要。
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

import app.services.question_recall as recall_mod
from app.core.sql_database import sql_db
from app.services.question_recommender import VALID_MODES, QuestionRecommender

# 取「全部候选」用的超大 debug_top（候选集不可能超过它）
BIG = 100000
TARGET = 0.95


def students(course_id: int) -> list:
    """有作答记录的学生（没有作答记录则各通道都空，只会走回落路径，测不出选择性）"""
    return [r["uid"] for r in sql_db._query(
        "SELECT DISTINCT user_id AS uid FROM t_answer_record WHERE course_id = ? "
        "ORDER BY user_id", (course_id,))]


def run(user_id, course_id, mode, seed, debug_top, enabled: bool) -> dict:
    """跑一次推荐；`enabled` 决定召回层开/关（唯一变量）。"""
    recall_mod.RECALL_ENABLED = enabled
    res = QuestionRecommender.recommend(user_id, course_id, count=10, mode=mode,
                                        seed=seed, debug_top=debug_top)
    meta = res["meta"]
    return {
        "picked": len(res["items"]),
        "candidates": meta.get("candidates"),
        "tops": [c["question_id"] for c in (meta.get("top_candidates") or [])],
        "recall": meta.get("recall") or {},
    }


def main():
    ap = argparse.ArgumentParser(description="召回层 Recall@K 评测（只读）")
    ap.add_argument("--course-id", type=int, default=65)
    ap.add_argument("--k", type=int, default=30, help="真值取打分前 K 名（建议 count×3）")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    users = students(args.course_id)
    if not users:
        print(f"课程 {args.course_id} 没有作答记录，无法评测（各通道都会是空的）")
        return 1
    print(f"课程 {args.course_id}：学生 {len(users)} 人 × 模式 {len(VALID_MODES)} 个，"
          f"K={args.k}，seed={args.seed}\n")
    print(f"{'stu':>5s} {'mode':9s} {'cand_off':>9s} {'cand_on':>8s} {'drop%':>7s} "
          f"{'K':>4s} {'hit':>4s} {'Recall@K':>9s} {'fallback':>9s} {'kp_recall':>10s}")
    print("-" * 92)

    detail, recalls = [], []
    try:
        for uid in users:
            for mode in VALID_MODES:
                off = run(uid, args.course_id, mode, args.seed, args.k, False)
                on = run(uid, args.course_id, mode, args.seed, BIG, True)
                truth, cand = set(off["tops"]), set(on["tops"])
                k_eff = len(truth)
                hit = len(truth & cand)
                r = round(hit / k_eff, 4) if k_eff else None
                c_off, c_on = off["candidates"] or 0, on["candidates"] or 0
                drop = round((c_off - c_on) / c_off * 100, 2) if c_off else 0.0
                if r is not None:
                    recalls.append(r)
                detail.append({"user_id": uid, "mode": mode, "k_eff": k_eff, "hit": hit,
                               "recall_at_k": r, "cand_off": c_off, "cand_on": c_on,
                               "drop_pct": drop,
                               "fallback_used": (on["recall"] or {}).get("fallback_used"),
                               "kp_recalled": (on["recall"] or {}).get("kp_ids"),
                               "by_channel": (on["recall"] or {}).get("by_channel")})
                print(f"{uid:>5d} {mode:9s} {c_off:>9d} {c_on:>8d} {drop:>7.2f} "
                      f"{k_eff:>4d} {hit:>4d} {(f'{r:.4f}' if r is not None else 'n/a'):>9s} "
                      f"{str(detail[-1]['fallback_used']):>9s} "
                      f"{str(detail[-1]['kp_recalled']):>10s}")
    finally:
        recall_mod.RECALL_ENABLED = True

    lows = [d for d in detail if d["recall_at_k"] is not None and d["recall_at_k"] < TARGET]
    print("-" * 92)
    print(f"Recall@K 均值 = {mean(recalls):.4f}｜最小值 = {min(recalls):.4f}｜"
          f"低于 {TARGET} 的条目 = {len(lows)}/{len(detail)}")
    print(f"候选集均值：{mean(d['cand_off'] for d in detail):.1f} → "
          f"{mean(d['cand_on'] for d in detail):.1f}"
          f"（降幅 {mean(d['drop_pct'] for d in detail):.1f}%）")
    print(f"回落全量的条目 = {sum(1 for d in detail if d['fallback_used'])}/{len(detail)}")
    print("结论：" + ("PASS（>= 0.95 安全阀）" if not lows else f"FAIL：{len(lows)} 条低于 0.95"))

    out = args.out or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "eval_data", "eval_report_question_recall.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"course_id": args.course_id, "k": args.k, "seed": args.seed,
                   "target": TARGET, "recall_at_k_mean": round(mean(recalls), 4),
                   "recall_at_k_min": min(recalls), "below_target": len(lows),
                   "detail": detail}, f, ensure_ascii=False, indent=2)
    print(f"明细已写入 {os.path.relpath(out)}")
    return 0 if not lows else 2


if __name__ == "__main__":
    sys.exit(main())
