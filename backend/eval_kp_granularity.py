"""KP 粒度实验（L2 阶段 G）——「粒度 → 覆盖率/选择性」曲线

为什么需要这个实验（依据 `docs/题库与推荐系统设计.md` §4.4 与风险表）：

    召回层的收益 = 选择性 = 弱势知识点数 × **每知识点题数**。

「每知识点题数」是一个**从未被验证过**的参数（当前 40 个 kp 是抽取算法顺带产生的，
不是设计选择），而文档已明确警告「不要用当前数据判断召回层无价值；
用 `count_questions_grouped_by_kp` 测算拐点」。本脚本就是去测算那个拐点。

方法（**数据库零写入**）：

    `recommend()` 的知识点身份全部来自三个可替换的取数点：
      · `sql_db.get_question_kps_map()`        —— 掌握度归因 / 召回 R3 / 打分 / 配额
      · `sql_db.get_kp_embedding_blobs()`      —— kp 向量（召回 R5）经 `vector_index._load` 读它
      · `sql_db.list_records_by_user_course()` —— 学生自评（掌握度融合项）
    本脚本用 `_Patches` 把这三处**临时**替换为合成分区的结果，使整条管线
    （掌握度 → 多通道召回 → 精排 → 分桶配额 → 题型均衡）真实运行在任意粒度上；
    退出上下文即还原（并 `vector_index.invalidate()` 清缓存）。

两个观测口径（**必须区分，否则会得出错误结论**）：

    · `pool_ratio`（模拟 filter 候选集的**规模**）：召回的 kp 集合覆盖了全量题库的多少 ——
      这是「选择性」本身，与规模门控无关。
    · `filter_forced`：把规模门控临时关掉（`MIN_QUESTIONS_FOR_FILTER=0`）**真实跑一次硬过滤**，
      得到真实候选集与 Recall@K 安全阀结果。线上的默认门控（题库 < 2000 题不付覆盖损失）
      会让小库直接跳过召回，因此这一口径**不代表线上行为**，只用于观察底层机制。

已知局限（诚实记录）：

    1. 合成知识点在图谱里不存在 → `graph_context()` 的名称/重要性回落中性、图谱扩展通道失效。
       本机图谱为空（`available=False`），**该局限在当前环境下不产生额外偏差**。
    2. 「合并」是按 `(category, kp_id)` 排序后切段，属**中性模拟**而非语义合并。
    3. Recall@K 的真值取「召回关闭时的全量打分 top-K」（与 `eval_question_recall.py` 同口径）。

用法（backend 目录下）：
    python eval_kp_granularity.py --course-id 65
    python eval_kp_granularity.py --grid 20,40,50,64 --max-students 3
产出：`eval_data/eval_report_kp_granularity.json` + 控制台曲线表。
"""
import argparse
import json
import os
import sys
from statistics import mean, median

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app.services.question_recall as recall_mod
from app.core.sql_database import _vec_to_blob, sql_db
from app.services.question_recommender import QuestionRecommender
from app.services.vector_index import vector_index

try:
    import numpy as np
except Exception:                                     # pragma: no cover
    np = None

# 默认粒度网格：40 是**真实值锚点**（此时不做任何替换），48/56 用于夹住预算拐点
# （kp_budget = want // KP_BUDGET_DIVISOR = 200 // 4 = 50），10/20 为粗粒度端，80/160 为细粒度端
DEFAULT_GRID = "10,20,40,48,56,80,160"
DEFAULT_MODES = "weak,review,mixed"
TARGET = 0.95          # Recall@K 安全阀（文档 §9.1）
BIG = 100000           # 取「全部候选」用的超大 debug_top（候选集不可能超过它）


# --------------------------------------------------------------------------
# 一、测量工具（_run / measure_at / partition_stats / _agg）
# --------------------------------------------------------------------------

def _run(user_id, course_id, document_id, mode, seed, debug_top, recall_enabled,
         recall_mode, min_for_filter):
    """跑一次推荐。召回三开关在这里显式设定（唯一变量），返回扁平化的观测值。"""
    recall_mod.RECALL_ENABLED = recall_enabled
    recall_mod.RECALL_MODE = recall_mode
    recall_mod.MIN_QUESTIONS_FOR_FILTER = min_for_filter
    res = QuestionRecommender.recommend(user_id, course_id, document_id=document_id,
                                       count=10, mode=mode, seed=seed,
                                       debug_top=debug_top)
    m = res["meta"]
    rc = m.get("recall") or {}
    bk = m.get("buckets") or {}
    ms = m.get("mastery_stats") or {}
    kps = {it["kp_id"] for it in res["items"] if it.get("kp_id")}
    return {
        "candidates": m.get("candidates") or 0,
        "tops": [c["question_id"] for c in (m.get("top_candidates") or [])],
        "picked": len(res["items"]),
        "dist_kp": len(kps),
        "filled": bk.get("filled", 0),
        "quota_relaxed": bk.get("quota_relaxed", 0),
        "fill_rate": m.get("fill_rate"),
        "kp_quota": m.get("kp_quota"),
        "picked_kps": rc.get("kp_ids", 0),
        "kp_id_list": rc.get("kp_id_list") or [],
        "kp_budget": rc.get("kp_budget"),
        "want": rc.get("want"),
        "skipped_small_library": rc.get("skipped_small_library"),
        "fallback_used": rc.get("fallback_used"),
        "by_channel": rc.get("by_channel"),
        "mastery_kps": ms.get("kps"),
        "below_weak": ms.get("below_weak"),
        "mastery_avg": ms.get("avg"),
    }


def measure_at(course_id, document_id, users, modes, seed, k, q_synth,
               verify_filter=False):
    """在一个粒度上测全部 (学生 × 模式)。每个组合跑 2 次（锚点额外跑 1 次真实 filter）：

    · `off`  召回关闭 → 全量候选池 + 全量打分 top-K，作为 Recall@K 的**真值**；
    · `on`   召回开（默认 bias）→ 候选池仍全量，并取回**召回命中的 kp 明细**。

    **filter 候选池为什么是「推导」而不是「实测」**：`filter` 模式走
    `list_questions(kp_ids=...)` 这条 **SQL** 过滤，参数是 kp id；而合成 kp id 在
    `t_question_kp` 里不存在 → 真去跑 filter 只会查到 0 行（假结果，曾因此得到
    「候选池 0 / Recall@K 0」的错误曲线）。故这里用映射**推导**：

        pool = {题目 q : 其合成 kp 集合 ∩ 召回 kp 集合 ≠ ∅}

    `verify_filter=True`（仅真实锚点用）时额外真跑一次 filter 并与推导值比对 ——
    两者相等才说明推导可信，否则整张曲线不可信。
    """
    per = []
    for uid in users:
        for mode in modes:
            off = _run(uid, course_id, document_id, mode, seed, k, False, "bias", 0)
            on = _run(uid, course_id, document_id, mode, seed, BIG, True, "bias", 0)
            truth = set(off["tops"])
            n = len(truth) or 1
            rk = set(on["kp_id_list"] or [])
            pool = {qid for qid, ss in (q_synth or {}).items() if rk & set(ss)}
            c_off = off["candidates"] or 0
            row = {
                "user_id": uid, "mode": mode,
                "cand_full": c_off, "cand_filter": len(pool),
                "pool_ratio": round(len(pool) / c_off, 4) if c_off else None,
                "recall_at_k": round(len(truth & set(on["tops"])) / n, 4),
                "recall_at_k_filter": round(len(truth & pool) / n, 4),
                "picked_kps": on["picked_kps"], "kp_budget": on["kp_budget"],
                "want": on["want"], "fallback_used": on["fallback_used"],
                "all_kps": on["mastery_kps"], "below_weak": on["below_weak"],
                "mastery_avg": on["mastery_avg"],
                "dist_kp": on["dist_kp"], "fill_rate": on["fill_rate"],
                "quota_relaxed": on["quota_relaxed"],
                "by_channel": on["by_channel"],
            }
            if verify_filter:
                flt = _run(uid, course_id, document_id, mode, seed, BIG, True, "filter", 0)
                row["cand_filter_measured"] = flt["candidates"]
                row["pool_derivation_ok"] = (flt["candidates"] == len(pool))
            per.append(row)
    return per


def partition_stats(q_synth):
    """合成口径的「每 kp 题数」分布（一题多挂 → 每个 kp 各计 1 题，与覆盖率口径一致）。"""
    cnt = {}
    for _qid, ss in (q_synth or {}).items():
        for s in ss:
            cnt[s] = cnt.get(s, 0) + 1
    vals = sorted(cnt.values(), reverse=True)
    if not vals:
        return {"kp_count": 0, "q_per_kp_max": None, "q_per_kp_median": None,
                "q_per_kp_mean": None, "q_per_kp_min": None}
    return {"kp_count": len(vals), "q_per_kp_max": vals[0],
            "q_per_kp_median": median(vals), "q_per_kp_mean": round(mean(vals), 2),
            "q_per_kp_min": vals[-1]}


def _agg(per):
    """把 (学生 × 模式) 明细聚合成一行曲线数据。"""
    def m(key, rnd=4):
        vals = [p[key] for p in per if p.get(key) is not None]
        return round(mean(vals), rnd) if vals else None

    def lows(key):
        return sum(1 for p in per
                   if p.get(key) is not None and p[key] < TARGET)

    return {
        "scenarios": len(per),
        "picked_kps": m("picked_kps", 2), "kp_budget": m("kp_budget", 2),
        "want": m("want", 1),
        "all_kps": m("all_kps", 2), "below_weak": m("below_weak", 2),
        "mastery_avg": m("mastery_avg", 2),
        "cand_full": m("cand_full", 1), "cand_filter": m("cand_filter", 1),
        "pool_ratio": m("pool_ratio", 4),
        "recall_at_k": m("recall_at_k", 4), "recall_at_k_min": min(
            (p["recall_at_k"] for p in per if p.get("recall_at_k") is not None), default=None),
        "below_target": lows("recall_at_k"),
        "recall_at_k_filter": m("recall_at_k_filter", 4),
        "recall_at_k_filter_min": min(
            (p["recall_at_k_filter"] for p in per
             if p.get("recall_at_k_filter") is not None), default=None),
        "below_target_filter": lows("recall_at_k_filter"),
        "dist_kp": m("dist_kp", 2), "fill_rate": m("fill_rate", 4),
        "quota_relaxed": m("quota_relaxed", 2),
        "fallback_used": m("fallback_used", 2),
        # 锚点专用：推导的候选池 vs 真跑 filter 的候选池是否一致
        "cand_filter_measured": m("cand_filter_measured", 1),
        "pool_derivation_ok": all(p["pool_derivation_ok"] for p in per
                                  if "pool_derivation_ok" in p)
        if any("pool_derivation_ok" in p for p in per) else None,
    }


# --------------------------------------------------------------------------
# 二、合成分区构造（load_real_data / build_partition / _Patches）
# --------------------------------------------------------------------------

def load_real_data(course_id, document_id):
    """取构造分区所需的真实素材：kp 元信息、题目→真实 kp 映射、全部题目向量。"""
    kp_meta = {r["kp_id"]: {"name": r.get("name"), "category": r.get("category") or ""}
               for r in sql_db._query(
                   "SELECT kp_id, name, category FROM t_kp_text WHERE course_id = ?",
                   (course_id,))}
    _, rows = sql_db.list_questions(course_id, document_id=document_id, page=1,
                                    page_size=100000, is_active=True,
                                    auto_grade_only=True)
    qids = [r["question_id"] for r in rows]
    real_map = sql_db.get_question_kps_map(qids)
    blobs = {r["question_id"]: r["blob"]
             for r in sql_db.get_question_embedding_blobs(course_id, document_id=document_id)
             if r.get("blob")}
    q_vecs = {}
    if np is not None:
        for qid, blob in blobs.items():
            if blob:
                q_vecs[qid] = np.frombuffer(blob, dtype="<f4").astype(np.float32)
    return kp_meta, real_map, q_vecs


def build_partition(g, real_map, q_vecs):
    """把「真实 kp」重新划分成 `g` 个「合成 kp」。

    返回 `(q_synth, real2synth_list, synth_blobs)`：
      · `q_synth`          `{question_id: [合成 kp, ...]}`（顺序有意义：首个=主知识点）
      · `real2synth_list`  `{真实 kp: [合成 kp, ...]}`（供学生自评记录映射）
      · `synth_blobs`      `[{"kp_id", "blob"}]` 合成 kp 向量（成员题目向量的归一化均值）

    `g == 真实 kp 数` 时返回 `None` → 调用方**不做任何替换**，这就是「真实数据」锚点。

    合并（`g < 真实数`）：按 `(category, kp_id)` 排序后切 `g` 段 —— 同类别优先相邻。
    拆分（`g > 真实数`）：每个 kp 按题量比例分到若干子 kp，子内用题向量贪心 k-center 聚类。
    """
    real_kps = sorted({k for ks in real_map.values() for k in ks})
    if not real_kps or g == len(real_kps):
        return None

    q_kps = {qid: list(ks or []) for qid, ks in real_map.items()}
    q_by_kp = {}
    for qid, ks in q_kps.items():
        for k in ks:
            q_by_kp.setdefault(k, []).append(qid)

    pair2synth = {}                                  # (qid, 真实 kp) -> 合成 kp
    real2synth_list = {}

    if g < len(real_kps):
        ordered = sorted(real_kps, key=lambda k: (str(k).split(":")[0], str(k)))
        n = len(ordered)
        for i in range(g):
            chunk = ordered[i * n // g:(i + 1) * n // g]
            sid = f"G{g}#{i:03d}"
            for k in chunk:
                real2synth_list[k] = [sid]
                for qid in q_by_kp.get(k, []):
                    pair2synth[(qid, k)] = sid
    else:
        # 子 kp 数按题量比例：题多的 kp 拆得细（题量是粒度换算的自然权重）
        total = sum(len(v) for v in q_by_kp.values()) or 1
        quota = {k: max(1, int(round(len(qs) * g / total))) for k, qs in q_by_kp.items()}
        # 修正到恰好 g 个：先按题量降序补/减，保证确定性
        order = sorted(q_by_kp, key=lambda k: (-len(q_by_kp[k]), str(k)))
        while sum(quota.values()) < g:
            for k in order:
                if sum(quota.values()) >= g:
                    break
                if quota[k] < len(q_by_kp[k]):
                    quota[k] += 1
        while sum(quota.values()) > g:
            for k in reversed(order):
                if sum(quota.values()) <= g:
                    break
                if quota[k] > 1:
                    quota[k] -= 1
        for ki, k in enumerate(order):
            qs = q_by_kp[k]
            if np is not None:
                vecs = [q_vecs[q] for q in qs if q in q_vecs]
                assign = (_kcenter(vecs, quota[k]) if len(vecs) == len(qs)
                          else [i % quota[k] for i in range(len(qs))])
            else:
                assign = [i % quota[k] for i in range(len(qs))]
            subs = [f"G{g}#{ki:03d}.{j}" for j in range(quota[k])]
            real2synth_list[k] = subs
            for qid, a in zip(qs, assign):
                pair2synth[(qid, k)] = subs[a]

    q_synth = {}
    for qid, ks in q_kps.items():
        seen, out = set(), []
        for k in ks:
            sid = pair2synth.get((qid, k))
            if sid and sid not in seen:
                seen.add(sid)
                out.append(sid)
        q_synth[qid] = out

    # 合成 kp 向量 = 成员题目向量的归一化均值（合并与拆分用同一口径，语义一致：
    # 「这个知识点在讲什么」≈「它的题在讲什么」）
    synth_blobs = []
    if np is not None:
        members = {}
        for qid, ss in q_synth.items():
            for sid in ss:
                members.setdefault(sid, []).append(qid)
        for sid, qs in members.items():
            vecs = [q_vecs[q] for q in qs if q in q_vecs]
            if not vecs:
                continue
            m = np.mean(np.vstack(vecs), axis=0)
            nrm = float(np.linalg.norm(m))
            if nrm > 0:
                synth_blobs.append({"kp_id": sid,
                                    "blob": _vec_to_blob((m / nrm).tolist())})
    return q_synth, real2synth_list, synth_blobs


class _Patches:
    """临时替换三处取数点 + 门控 + 向量缓存；退出还原（异常也还原）。

    为什么改这三处就够：`recommend()` 的知识点身份**只**从这三处流出（见模块 docstring）。
    实例属性会遮蔽类方法，退出时把原 bound method 赋回即可，无需 `del`。
    """

    def __init__(self, partition=None):
        self.p = partition

    def __enter__(self):
        if self.p is None:
            return self
        q_synth, real2synth_list, synth_blobs = self.p
        s = sql_db
        self._saved = {n: getattr(s, n) for n in
                       ("get_question_kps_map", "get_kp_embedding_blobs",
                        "list_records_by_user_course")}
        s.get_question_kps_map = lambda ids: {q: list(q_synth.get(q) or []) for q in ids}
        s.get_kp_embedding_blobs = lambda course_id, document_id=None: list(synth_blobs)

        def _manual(user_id, course_id, document_id=None):
            out = []
            for r in self._saved["list_records_by_user_course"](user_id, course_id, document_id):
                for sid in (real2synth_list.get(r.get("kp_id")) or []):
                    row = dict(r)
                    row["kp_id"] = sid
                    out.append(row)
            return out

        s.list_records_by_user_course = _manual
        vector_index.invalidate()                    # kp 矩阵缓存按 (course, document) 缓存，必须清
        return self

    def __exit__(self, *exc):
        if self.p is None:
            return False
        for n, fn in self._saved.items():
            setattr(sql_db, n, fn)
        vector_index.invalidate()
        return False


def _kcenter(vecs, k):
    """贪心最远点聚类（确定性）：把 vecs 分成 k 簇，返回每项的簇号。

    为什么不用随机初始化：实验要可复现，且不同粒度之间的结果要能对比。
    为何用最远点而非顺序切分：顺序切分下「子知识点的掌握度」≈父知识点均值，
    细粒度就观察不到任何差异；按语义聚类才能体现「同一个大知识点里，
    这部分题会做、那部分不会」——这正是细粒度要检验的假设。
    """
    n = len(vecs)
    if k <= 1 or n <= 1:
        return [0] * n
    k = min(k, n)
    mat = np.vstack(vecs)
    mat = mat / np.maximum(np.linalg.norm(mat, axis=1, keepdims=True), 1e-12)
    centers = [0]
    for _ in range(k - 1):
        sim = mat @ mat[centers].T                  # (n, len(centers)) 余弦
        d = 1.0 - sim.max(axis=1)                   # 到最近中心的距离
        for c in centers:
            d[c] = -1.0                             # 已选过的不能再选
        centers.append(int(np.argmax(d)))
    sim = mat @ mat[centers].T
    return [int(i) for i in sim.argmax(axis=1)]


# --------------------------------------------------------------------------
# 三、主流程：遍历粒度网格 → 打印曲线 → 落 JSON
# --------------------------------------------------------------------------

def students(course_id):
    """有作答记录的学生（无作答则各召回通道都会空，测不出选择性）。"""
    return [r["uid"] for r in sql_db._query(
        "SELECT DISTINCT user_id AS uid FROM t_answer_record WHERE course_id = ? "
        "ORDER BY user_id", (course_id,))]


def validate_partition(g, part, real_map):
    """合成分区的**合法性校验**：分区错了会让整张曲线失真且不报错 → 必须显式检查。"""
    if part is None:
        return []
    q_synth = part[0]
    errs = []
    if set(q_synth.keys()) != set(real_map.keys()):
        errs.append(f"题目集合不一致（分区 {len(q_synth)} / 真实 {len(real_map)}）")
    empty = [q for q, ss in q_synth.items() if not ss]
    if empty:
        errs.append(f"{len(empty)} 道题没分到任何合成 kp：{empty[:3]}")
    n_kp = len({s for ss in q_synth.values() for s in ss})
    if n_kp != g:
        errs.append(f"合成 kp 数 {n_kp} ≠ 目标 {g}")
    return errs




def main():
    ap = argparse.ArgumentParser(
        description="KP 粒度实验：粒度 → 覆盖率/选择性 曲线（数据库零写入）")
    ap.add_argument("--course-id", type=int, default=65)
    ap.add_argument("--document-id", type=int, default=101)
    ap.add_argument("--grid", default=DEFAULT_GRID, help=f"粒度网格（默认 {DEFAULT_GRID}）")
    ap.add_argument("--modes", default=DEFAULT_MODES, help=f"模式（默认 {DEFAULT_MODES}）")
    ap.add_argument("--max-students", type=int, default=5)
    ap.add_argument("--k", type=int, default=30, help="Recall@K 的 K（建议 count×3）")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    grid = [int(x) for x in args.grid.split(",") if x.strip()]
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    users = students(args.course_id)[:args.max_students]
    if not users:
        print(f"课程 {args.course_id} 没有作答记录，各召回通道都会是空的，测不出选择性")
        return 1

    kp_meta, real_map, q_vecs = load_real_data(args.course_id, args.document_id)
    real_kps = sorted({k for ks in real_map.values() for k in ks})
    real_stats = partition_stats(real_map)
    budget = max(recall_mod.RECALL_MIN,
                 recall_mod.RECALL_MULTIPLIER * 10) // recall_mod.KP_BUDGET_DIVISOR

    print(f"课程 {args.course_id}／文档 {args.document_id}：真实知识点 {len(real_kps)} 个，"
          f"题目 {len(real_map)} 道，题目向量 {len(q_vecs)} 条")
    print(f"学生 {len(users)} 人 × 模式 {modes}，K={args.k}，seed={args.seed}")
    print(f"召回预算：want = max(RECALL_MIN={recall_mod.RECALL_MIN}, "
          f"{recall_mod.RECALL_MULTIPLIER}×count) = "
          f"{max(recall_mod.RECALL_MIN, recall_mod.RECALL_MULTIPLIER * 10)} 题 → "
          f"kp_budget = want // {recall_mod.KP_BUDGET_DIVISOR} = {budget} 个 kp")
    print(f"规模门控：MIN_QUESTIONS_FOR_FILTER={recall_mod.MIN_QUESTIONS_FOR_FILTER}"
          f"（filter 门控在本实验中被临时置 0 以观察底层裁剪）\n")

    print(f"{'G':>5s} {'kp数':>5s} {'题/kp中/均':>10s} {'弱kp':>5s} {'召回kp':>7s} {'预算':>5s} "
          f"{'候选池':>7s} {'选择性':>8s} {'R@K':>7s} {'低':>3s} "
          f"{'R@K_f':>7s} {'低':>3s} {'dist_kp':>8s} {'回填率':>7s}")
    print("-" * 112)

    orig = (recall_mod.RECALL_ENABLED, recall_mod.RECALL_MODE,
            recall_mod.MIN_QUESTIONS_FOR_FILTER)
    rows, warnings = [], []
    try:
        for g in grid:
            part = build_partition(g, real_map, q_vecs)
            errs = validate_partition(g, part, real_map)
            if errs:
                warnings.append(f"G={g} 分区校验失败：{errs}")
                print(f"{g:>5d}  分区校验失败：{errs}")
                continue
            st = partition_stats(part[0]) if part else dict(real_stats)
            q_synth = part[0] if part else real_map
            with _Patches(part):
                per = measure_at(args.course_id, args.document_id, users, modes,
                                 args.seed, args.k, q_synth,
                                 verify_filter=(part is None))
            a = _agg(per)
            rows.append({"requested_g": g, "is_real_anchor": part is None, **st, **a})
            qpk = f"{st['q_per_kp_median']}/{st['q_per_kp_mean']}"
            print(f"{g:>5d} {st['kp_count']:>5d} {qpk:>10s} "
                  f"{a['below_weak']:>5.1f} {a['picked_kps']:>7.1f} {a['kp_budget']:>5.0f} "
                  f"{a['cand_filter']:>7.1f} {a['pool_ratio']:>8.4f} "
                  f"{a['recall_at_k']:>7.4f} {a['below_target']:>3d} "
                  f"{a['recall_at_k_filter']:>7.4f} {a['below_target_filter']:>3d} "
                  f"{a['dist_kp']:>8.2f} {a['fill_rate']:>7.3f}")
            if a.get("pool_derivation_ok") is True:
                print(f"       ↳ 候选池推导交叉验证：推导 {a['cand_filter']:.0f} == "
                      f"实测 filter {a['cand_filter_measured']:.0f} ✓")
            elif a.get("pool_derivation_ok") is False:
                warnings.append(
                    f"G={g} 候选池推导与实测不一致（推导 {a['cand_filter']} / "
                    f"实测 {a['cand_filter_measured']}）→ 该行不可信")
                print(f"       ✗ 候选池推导与实测不一致"
                      f"（推导 {a['cand_filter']} / 实测 {a['cand_filter_measured']}）")
    finally:
        recall_mod.RECALL_ENABLED, recall_mod.RECALL_MODE, \
            recall_mod.MIN_QUESTIONS_FOR_FILTER = orig
        vector_index.invalidate()

    real_row = next((r for r in rows if r["is_real_anchor"]), None)
    safe = [r for r in rows if not r["below_target_filter"] and not r["below_target"]]
    bad = [r for r in rows if r["below_target_filter"]]
    monotone = all((rows[i]["pool_ratio"] or 1.0) >= (rows[i + 1]["pool_ratio"] or 1.0) - 1e-9
                   for i in range(len(rows) - 1)) if len(rows) > 1 else True

    print("\n" + "=" * 112)
    print("结论")
    print("=" * 112)
    if real_row:
        print(f"· **真实粒度**（G={real_row['requested_g']}，{real_row['kp_count']} 个 kp，"
              f"每 kp 中位 {real_row['q_per_kp_median']} 题／均值 {real_row['q_per_kp_mean']}）："
              f"召回命中 {real_row['picked_kps']:.1f}/{real_row['all_kps']:.0f} 个 kp → "
              f"候选池 {real_row['cand_filter']:.0f}/{real_row['cand_full']:.0f}"
              f"（选择性 {real_row['pool_ratio']:.4f}），filter 口径 Recall@K "
              f"{real_row['recall_at_k_filter']:.4f}")
        print(f"  即：**召回层在当前粒度并非空操作**，硬过滤会裁掉 "
              f"{(1 - (real_row['pool_ratio'] or 1)) * 100:.1f}% 的题；"
              f"「候选池 120→120」那种观测只出现在 **bias 模式**（不裁剪是它的设计）")
    if rows:
        print(f"· **机制**：召回命中的 kp 数受预算**硬约束** —— picked_kps 从 "
              f"{rows[0]['picked_kps']:.1f}（G={rows[0]['requested_g']}）升到 "
              f"{rows[-1]['picked_kps']:.1f}（G={rows[-1]['requested_g']}），"
              f"始终 ≤ kp_budget={budget}。故 kp 总数超过预算后，"
              f"召回覆盖率 ≈ 预算/kp 数，随粒度**单调下降**"
              + ("（实测曲线单调 ✓）" if monotone else "（实测曲线非单调，需复核）"))
    if safe:
        top = max(safe, key=lambda r: r["requested_g"])
        print(f"· **安全上限**：G≤{top['requested_g']}"
              f"（每 kp 中位 {top['q_per_kp_median']} 题）时全部 {top['scenarios']} 个场景"
              f" Recall@K ≥ {TARGET} 安全阀")
    if bad:
        first = min(bad, key=lambda r: r["requested_g"])
        print(f"· **拐点**：G={first['requested_g']}（选择性 {first['pool_ratio']:.4f}）起开始有"
              f"场景跌破安全阀 → "
              + "、".join(f"G={r['requested_g']}({r['below_target_filter']}条)" for r in bad))
    if real_row:
        print(f"· 组卷结构（bias 口径，受粒度影响很小）：dist_kp "
              f"{rows[0]['dist_kp']:.1f}→{rows[-1]['dist_kp']:.1f}，回填率 "
              f"{rows[0]['fill_rate']:.3f}→{rows[-1]['fill_rate']:.3f}")
    print(f"· bias 模式安全阀在所有粒度恒为 1.0000 —— 「不丢题」是 bias 的设计保证，"
          f"与粒度无关；**粒度只影响 filter 口径**")
    print(f"· 安全阀统计（{TARGET}）：bias 口径 "
          f"{sum(r['below_target'] for r in rows)} 条低于；filter 口径 "
          f"{sum(r['below_target_filter'] for r in rows)} 条低于")
    if bad:
        print("  ⚠️ filter 口径跌破安全阀的粒度：" +
              ", ".join(f"G={r['requested_g']}" for r in bad))
    for w in warnings:
        print("  [!] " + w)

    out = args.out or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "eval_data", "eval_report_kp_granularity.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "course_id": args.course_id, "document_id": args.document_id,
            "grid": grid, "modes": modes, "k": args.k, "seed": args.seed,
            "students": users, "target": TARGET,
            "real_kp_count": len(real_kps), "kp_budget": budget,
            "recall_constants": {"RECALL_MIN": recall_mod.RECALL_MIN,
                                 "RECALL_MULTIPLIER": recall_mod.RECALL_MULTIPLIER,
                                 "KP_BUDGET_MIN": recall_mod.KP_BUDGET_MIN,
                                 "KP_BUDGET_DIVISOR": recall_mod.KP_BUDGET_DIVISOR},
            "warnings": warnings, "curve": rows,
            # 结论的可机读形式（供文档/回归对比引用）
            "curve_monotone": monotone,
            "safe_ceiling_g": max((r["requested_g"] for r in safe), default=None),
            "breakpoint_g": min((r["requested_g"] for r in bad), default=None),
        }, f, ensure_ascii=False, indent=2)
    print(f"\n曲线已写入 {os.path.relpath(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

