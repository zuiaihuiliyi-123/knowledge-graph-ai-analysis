"""
题目推荐打分器（P2）

把「随机抽题」升级为「按学生学情选卷」：对候选题池逐题打分 → 分桶 → 配额裁剪 → 题型均衡，
并给每题生成人类可读的推荐理由（前端可直接展示）。

八个信号（权重集中在下方常量，便于调参与实验）：

| 信号 | 含义 | 数据来源 |
|------|------|----------|
| ① 薄弱度 need | 该知识点掌握度越低越该练 | 掌握度（本模块 `kp_mastery()`） |
| ② 遗忘到期 due | 距上次作答越久越该复习 | `t_answer_record.answered_at` |
| ③ 难度适配 diff_fit | 目标难度 = 1 + 4·掌握度/100（最近发展区） | `t_question.difficulty` |
| ④ 新颖度 novelty | 没做过 > 做错过 > 做过且最近答对 | `t_answer_record` |
| ⑤ 错题优先 wrong_flag | 最近一次答错的题优先 | `t_answer_record` |
| ⑥ 知识点重要性 | 图谱中度数越高的知识点越优先 | Neo4j 度数（`(n)--(m)`） |
| ⑦ 题目区分度 quality | 全班正确率过高/过低（≥3 次作答后）降权 | 全班作答统计 |
| ⑧ 多样性配额 | 同知识点题数上限 + 题型均衡 | 组卷阶段 |

分桶（`mode`）：
- `weak`     薄弱强化（掌握度 ≤ 60 的知识点）
- `review`   复习巩固（做过且到期，或最近答错）
- `new`      路径新知识（`PathRecommender` 推荐的下一步知识点）
- `advanced` 进阶提升（掌握度 ≥ 70 的巩固题）
- `mixed`    分层组卷（默认，四桶按比例拼卷，桶内不足自动回填）
- `random`   随机基线（用于与现状对照 / A-B 实验）

掌握度说明：这里是**推荐用的轻量掌握度**（练习表现 + 学生自评，读时计算、不落库）；
对外公开的「掌握度指标」与前端展示属后续工作，本模块先把内部实现沉淀成可复用函数。
"""
import math
import os
import random
from datetime import datetime

from ..core.database import db
from ..core.sql_database import _blob_to_vec, sql_db
from .vector_index import vector_index

# ---------- 权重与阈值（调参集中在此） ----------

W_NEED = 0.30          # 薄弱度（掌握度越低越优先）
W_DUE = 0.20           # 遗忘到期（越久没练越优先）
W_DIFF = 0.15          # 难度适配（最近发展区）
W_NOVELTY = 0.10       # 新颖度（没做过优先，避免反复刷同一题）
W_WRONG = 0.10         # 最近答错优先
W_IMPORTANCE = 0.05    # 知识点图谱重要性（度数）
# ⑦ 语义相关度（L2 阶段 F 新增）：候选题目向量 与「学生最近错题向量」的最大余弦。
#   存在的唯一理由是修**缺口 7**——其余 6 个信号在同一知识点内完全相同（need/due/
#   importance/wrong 都按 kp 取值），导致「同 kp 内的题」无法区分；语义信号是唯一
#   能区分它们的机制。权重来源：need 0.35→0.30、importance 0.10→0.05（合计让出 0.10），
#   其余权重不变，以保证「一次只改一处、可归因」。
W_SEMANTIC = 0.10
# 语义信号标准化后的「分布宽度」：均值→0.5，±SEM_Z_SPREAD×σ→1/0。
# 0.25 表示 ±2σ 覆盖满量程（正态下约 95% 的候选落在 0~1 内，尾部截断）。
SEM_Z_SPREAD = 0.25

HALF_LIFE_DAYS = 14.0  # 练习表现的时间衰减半衰期（天）
REVIEW_DUE_DAYS = 7.0  # 超过该天数未练视为"该复习"
WEAK_THRESHOLD = 60.0  # 掌握度 ≤ 60 记入「薄弱」
ADVANCED_THRESHOLD = 70.0   # 掌握度 ≥ 70 记入「进阶」
MASTERY_DEFAULT = 50.0      # 无任何证据时的中性掌握度
DIFF_SLOPE = 4.0            # 目标难度 = 1 + DIFF_SLOPE·掌握度/100（难度取 1-5）
QUALITY_MIN_ATTEMPTS = 3    # 全班作答达到该次数后才评估区分度
QUALITY_LOW, QUALITY_HIGH = 0.2, 0.95   # 正确率低于/高于该值视为区分度差
QUALITY_PENALTY = 0.3                   # 区分度差时的乘性惩罚
KP_QUOTA_DIVISOR = 3        # 同一知识点的题数上限 = ceil(count / 该值)

# ---------- L2（一题多知识点）口径开关（决策 1a / 4a） ----------
# 支持环境变量覆盖，便于 A/B 对照（不必改代码）：
#   KP_ATTRIBUTION_MODE=primary KP_QUOTA_MODE=primary_only python xxx.py   → 复现 L2 之前的单标签口径
# 掌握度归因（决策 1a）：一题挂 N 个知识点时，该题作答结果计入**全部 N 个**。
#   "all"     与论文口径一致；副作用是多标签密度高时掌握度会系统性偏低（weak 桶膨胀）；
#   "primary" 只计入主知识点（= L2 之前的单标签行为），作为 A/B 回退开关。
KP_ATTRIBUTION_MODE = os.getenv("KP_ATTRIBUTION_MODE", "all")
# 配额口径（决策 4a）：一题挂的**每个**知识点各占用一个配额槽（任一命中即占用），
#   且**任一**知识点饱和即拒收该题。副作用是约束更紧（上限更早触顶）→ 需结合
#   `meta.fill_rate` 与 `meta.quota_relaxed_count` 观察；若回填率明显上升再调 KP_QUOTA_DIVISOR。
#   "primary_only" = L2 之前的行为（只看主知识点）。
KP_QUOTA_MODE = os.getenv("KP_QUOTA_MODE", "any_hit")

# mixed 模式的四桶配比（按 count 分配，余数依次补给靠前的桶）
MIXED_RATIO = (("weak", 0.40), ("review", 0.25), ("new", 0.20), ("advanced", 0.15))

# 第 7 信号「语义相关度」的查询向量上限：取学生**最近**多少道错题参与相似度计算
SEMANTIC_SIGNAL_QUERY_LIMIT = 20
# 第 7 信号开关（A/B 用）：SEMANTIC_SIGNAL=0 → 不计算语义相关度（sem_map 恒 None，
# 该信号对所有候选取 0）。用于量化「第 7 信号到底改变了什么」。
SEMANTIC_SIGNAL_ENABLED = os.getenv("SEMANTIC_SIGNAL", "1") != "0"

VALID_MODES = ("weak", "review", "new", "advanced", "mixed", "random")

BUCKET_LABELS = {
    "weak": "薄弱强化", "review": "复习巩固", "new": "路径新知识",
    "advanced": "进阶提升", "random": "随机练习",
}


def _parse_ts(text):
    """解析 'YYYY-MM-DD HH:MM:SS'（t_answer_record.answered_at 的存储格式）"""
    if not text:
        return None
    try:
        return datetime.strptime(str(text)[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def kp_mastery(answer_records, manual_records, now=None, half_life_days: float = HALF_LIFE_DAYS) -> dict:
    """按知识点算「轻量掌握度」（0-100），返回 {kp_id: {...}}。

    练习信号：时间衰减加权的正确率
        acc = Σ w_i·correct_i / Σ w_i ,  w_i = 0.5 ^ (Δdays / half_life)
    自评信号：MASTERED=1.0 / LEARNING=0.5 / 其他=0
    融合：有练习数据 → 0.5·acc + 0.5·自评；无练习数据 → 自评（evidence=0，前端可标注"证据不足"）

    `answer_records` / `manual_records` 由调用方传入（同一学生同一课程），避免重复查库。
    """
    now = now or datetime.now()
    buckets = {}
    for r in answer_records:
        kp_id = r.get("kp_id") or r.get("question_kp_id")
        if not kp_id:
            continue
        answered_at = _parse_ts(r.get("answered_at"))
        delta_days = max(0.0, (now - answered_at).total_seconds() / 86400) if answered_at else 0.0
        weight = 0.5 ** (delta_days / half_life_days)
        b = buckets.setdefault(kp_id, {"w_sum": 0.0, "w_correct": 0.0, "attempts": 0,
                                       "last_at": None, "days_since": None})
        b["w_sum"] += weight
        # 掌握度得分（Scope B）：教师批改后的主观题可能得部分分（0~100），
        # 用 score/100 参与加权比布尔 is_correct 更贴近真实掌握程度；
        # 记录未携带 score 时退回 is_correct（与旧调用方/旧数据兼容）。
        score = r.get("score")
        if score is None:
            credit = 1.0 if r.get("is_correct") else 0.0
        else:
            try:
                credit = _clamp(float(score) / 100.0)
            except (TypeError, ValueError):
                credit = 1.0 if r.get("is_correct") else 0.0
        b["w_correct"] += weight * credit
        b["attempts"] += 1
        if answered_at and (b["last_at"] is None or answered_at > b["last_at"]):
            b["last_at"] = answered_at
    for kp_id, b in buckets.items():
        if b["last_at"]:
            b["days_since"] = round((now - b["last_at"]).total_seconds() / 86400, 2)

    manual = {}
    for r in manual_records:
        kp_id = r.get("kp_id")
        if not kp_id:
            continue
        status = r.get("status")
        score = 1.0 if status == "MASTERED" else (0.5 if status == "LEARNING" else 0.0)
        prev = manual.get(kp_id)
        if prev is None or score > prev:
            manual[kp_id] = score

    result = {}
    for kp_id in set(buckets) | set(manual):
        b = buckets.get(kp_id)
        acc = (b["w_correct"] / b["w_sum"]) if b and b["w_sum"] > 0 else None
        manual_score = manual.get(kp_id)
        if acc is None and manual_score is None:
            mastery = None
        elif acc is None:
            mastery = manual_score * 100
        elif manual_score is None:
            mastery = acc * 100
        else:
            mastery = (0.5 * acc + 0.5 * manual_score) * 100
        result[kp_id] = {
            "mastery": round(mastery, 1) if mastery is not None else None,
            "accuracy": round(acc * 100, 1) if acc is not None else None,
            "manual": manual_score,
            "attempts": b["attempts"] if b else 0,
            "last_at": b["last_at"].strftime("%Y-%m-%d %H:%M:%S") if b and b["last_at"] else None,
            "days_since": b["days_since"] if b else None,
        }
    return result


def _mastery_stats(mastery: dict) -> dict:
    """掌握度整体分布（全课程知识点，不含未观测点）。用于观测口径切换的副作用。"""
    vals = [v["mastery"] for v in (mastery or {}).values() if v.get("mastery") is not None]
    if not vals:
        return {"kps": len(mastery or {}), "with_evidence": 0, "avg": None,
                "min": None, "max": None, "below_weak": 0}
    return {
        "kps": len(mastery or {}),
        "with_evidence": len(vals),
        "avg": round(sum(vals) / len(vals), 2),
        "min": round(min(vals), 2),
        "max": round(max(vals), 2),
        # 低于 WEAK_THRESHOLD 的知识点数——决策 1a「掌握度偏低 → weak 桶膨胀」的直接观测量
        "below_weak": sum(1 for v in vals if v < WEAK_THRESHOLD),
    }


def graph_context(course_id: int, document_id=None) -> dict:
    """取图谱上下文：`kp_id → 名称`、`kp_id → 归一化度数（重要性）`、图谱是否可用。

    图谱不可用（未启动/查询异常）时全部降级：`available=False`，重要性取中性 0.5，
    名称回落到 `kp_id`，"路径新知识"桶自动失效——不抛异常、不阻塞出题。
    """
    ctx = {"available": False, "names": {}, "importance": {}}
    try:
        cypher = ("MATCH (n:KnowledgePoint {course_id: $cid"
                  + (", document_id: $did" if document_id else "") + "}) "
                  "OPTIONAL MATCH (n)--(m:KnowledgePoint) "
                  "RETURN n.kp_id AS kp_id, n.name AS name, count(m) AS degree")
        params = {"cid": course_id}
        if document_id:
            params["did"] = document_id
        rows = db.query(cypher, params)
    except Exception:
        return ctx
    if not rows:
        return ctx

    ctx["available"] = True
    max_degree = max((r.get("degree") or 0) for r in rows) or 1
    for r in rows:
        kp_id = r.get("kp_id")
        if not kp_id:
            continue
        ctx["names"][kp_id] = r.get("name") or kp_id
        ctx["importance"][kp_id] = _clamp((r.get("degree") or 0) / max_degree)
    return ctx


def next_knowledge_ids(course_id: int, document_id, mastered_names, ctx) -> set:
    """学习路径推荐的「下一步知识点」→ kp_id 集合（图谱不可用/无推荐时返回空集）。"""
    from .path_recommender import PathRecommender      # 延迟导入：避免模块级循环依赖
    try:
        recs = PathRecommender.recommend_next(mastered_names or [], course_id, document_id)
    except Exception:
        return set()
    name_to_kp = {name: kp_id for kp_id, name in (ctx.get("names") or {}).items()}
    return {name_to_kp[r.get("name")] for r in recs if r.get("name") in name_to_kp}


def _target_difficulty(mastery: float) -> float:
    """最近发展区：掌握度越高，越应该给难题（目标难度 1-5）"""
    return 1.0 + DIFF_SLOPE * (mastery / 100.0)


def _reason(bucket: str, kp_name: str, mastery, mine, days_since, quality_flag: bool) -> str:
    """生成人类可读的推荐理由（前端直接展示，也便于解释"为什么推这题"）"""
    name = kp_name or "该知识点"
    if bucket == "review":
        if days_since is not None:
            return f"「{name}」最近答错，已隔 {days_since:.0f} 天，建议复习巩固"
        return f"「{name}」最近答错，建议复习巩固"
    if bucket == "new":
        return f"「{name}」是学习路径推荐的下一步，可以开始练了"
    if bucket == "weak":
        if mastery is None:
            return f"「{name}」还没有作答记录，建议先摸底练几题"
        return f"「{name}」掌握度 {mastery:.0f}%，建议巩固薄弱点"
    return f"「{name}」掌握度 {mastery:.0f}%，来道进阶题保持手感"


def _normalize_semantic(sem_map: dict) -> dict:
    """把第 7 信号的原始余弦按**候选集内分布**标准化到 `[0, 1]`。

    为什么必须标准化：同域中文文本的 bge-m3 余弦普遍落在 0.6~0.96（course 65 实测
    中位 0.769、std 0.089），直接用原始余弦时，权重 0.10 实际只贡献约 0.10×0.09≈0.009
    的分数区分度——**名义权重与实际影响力差一个数量级**。映射规则：均值 → 0.5，
    ±2σ → 1 / 0，超出截断。

    代价（诚实记录）：信号值由此变成**相对值**，依赖候选池的分布——
    同一道题在不同候选池里得分可能不同。对精排（排序）而言这是可接受的，
    因为排序本身只关心候选集内部的相对顺序。

    `std≈0` 时返回全 0.5：此时所有候选语义相关度相同，**不该用噪声制造虚假差异**。
    """
    if not sem_map:
        return {}
    vals = list(sem_map.values())
    mu = sum(vals) / len(vals)
    sd = (sum((v - mu) ** 2 for v in vals) / len(vals)) ** 0.5
    if sd < 1e-6:
        return {qid: 0.5 for qid in sem_map}
    return {qid: _clamp(0.5 + SEM_Z_SPREAD * (v - mu) / sd) for qid, v in sem_map.items()}


def build_candidates(rows, answer_by_question: dict, mastery: dict, ctx: dict,
                     next_kp_ids: set, quality_stats: dict, now=None,
                     q_kp_map: dict = None, recall_kp_ids: set = None,
                     recall_penalty: float = 1.0, sem_map: dict = None) -> list:
    """把题目行 + 各信号组装成「带分数 / 桶 / 理由」的候选列表（按分数降序）。

    L2：每个候选附加 `kp_ids`（该题挂的**全部**知识点），供组卷的「任一命中即占用配额槽」
    （决策 4a）使用。**打分与分桶仍按主知识点**——`KP_ATTRIBUTION_MODE` 只影响掌握度归因；
    「按最弱知识点打分」是另一个待评估口径，不在本次决策范围内（避免一次改太多无法归因）。

    `recall_kp_ids`：召回层命中的知识点集合（bias 模式）；**未命中者**的题目得分 ×`recall_penalty`。
    用降权而非过滤，是为了**不丢题**（Recall@K 安全阀天然满足），只改变排序。
    """
    now = now or datetime.now()
    candidates = []
    names = ctx.get("names") or {}
    q_kp_map = q_kp_map or {}
    # 第 7 信号的标准化只做一次（按整个候选集的余弦分布），不在循环里重复计算
    sem_norm = _normalize_semantic(sem_map)
    for row in rows:
        qid = row["question_id"]
        # 主知识点：关联表按 is_primary DESC 排序，首项即主知识点（与 t_question.kp_id 投影一致）
        kp_ids = q_kp_map.get(qid) or ([row.get("kp_id")] if row.get("kp_id") else [])
        kp_id = kp_ids[0] if kp_ids else None
        kp_info = mastery.get(kp_id) or {}
        m = kp_info.get("mastery")
        m_eff = m if m is not None else MASTERY_DEFAULT

        need = _clamp((100.0 - m_eff) / 100.0)
        mine = answer_by_question.get(qid)
        days_since = None
        if mine and mine.get("last_at"):
            days_since = max(0.0, (now - mine["last_at"]).total_seconds() / 86400)
        due = _clamp(days_since / REVIEW_DUE_DAYS) if days_since is not None else 0.0

        difficulty = row.get("difficulty") or 3
        diff_fit = _clamp(1.0 - abs(difficulty - _target_difficulty(m_eff)) / DIFF_SLOPE)
        # 对错语义（Scope B）：last_is_correct 可能是 None（待批改）——
        # 只有明确的 False 才算"最近答错"，明确的 True 才算"掌握得不错"。
        novelty = 1.0 if not mine else (0.3 if mine.get("last_is_correct") is True else 0.5)
        wrong_flag = 1.0 if (mine and mine.get("last_is_correct") is False) else 0.0
        importance = ctx["importance"].get(kp_id, 0.5) if ctx.get("available") else 0.5
        # ⑦ 语义相关度：与「学生最近错题」的最大余弦，**已按候选集分布标准化到 [0,1]**
        # （直接用原始余弦时权重 0.10 实际只发挥约 1/10 的区分度，见 _normalize_semantic）
        # 这是唯一能区分**同一知识点下不同题目**的信号（其余 6 个在同 kp 内完全相同）。
        # 无向量证据的题取 0.5（中性）——与上面 importance 的未知回落口径一致，不因缺向量受罚
        sem_raw = (sem_map or {}).get(qid)
        semantic = sem_norm.get(qid, 0.5) if sem_norm else 0.0

        score = (W_NEED * need + W_DUE * due + W_DIFF * diff_fit
                 + W_NOVELTY * novelty + W_WRONG * wrong_flag
                 + W_IMPORTANCE * importance + W_SEMANTIC * semantic)

        # L2 阶段 D：召回偏置（bias 模式，默认）——未命中召回的知识点降权，命中者保持原分。
        # 用**降权**而非过滤：不丢题（Recall@K 安全阀天然满足），只改变排序。
        if recall_kp_ids is not None and kp_id not in recall_kp_ids:
            score *= recall_penalty

        # 区分度：全班正确率过高（太水）/过低（太偏）降权，需至少 QUALITY_MIN_ATTEMPTS 次作答
        st = quality_stats.get(qid) or {}
        q_attempts = st.get("attempts", 0)
        q_rate = (st.get("correct", 0) / q_attempts) if q_attempts else None
        quality_flag = bool(q_attempts >= QUALITY_MIN_ATTEMPTS and q_rate is not None
                            and (q_rate < QUALITY_LOW or q_rate > QUALITY_HIGH))
        if quality_flag:
            score *= (1.0 - QUALITY_PENALTY)

        if wrong_flag:
            bucket = "review"
        elif kp_id in next_kp_ids:
            bucket = "new"
        elif m is None or need >= (100.0 - WEAK_THRESHOLD) / 100.0:
            bucket = "weak"
        else:
            bucket = "advanced"

        candidates.append({
            "row": row,
            "question_id": qid,
            "q_type": row.get("q_type"),
            "difficulty": difficulty,
            "kp_id": kp_id,
            "kp_ids": kp_ids,          # L2：全部知识点（组卷配额用，决策 4a）
            "kp_name": names.get(kp_id) if kp_id else None,
            "bucket": bucket,
            "score": round(score, 4),
            "mastery": m,
            "attempts": (mine or {}).get("attempts", 0),
            "reason": _reason(bucket, names.get(kp_id), m, mine, days_since, quality_flag),
            "signals": {
                "need": round(need, 3), "due": round(due, 3), "diff_fit": round(diff_fit, 3),
                "novelty": novelty, "wrong": wrong_flag,
                "importance": round(importance, 3), "quality_flag": quality_flag,
                "semantic": round(semantic, 3),
                # 原始余弦（未标准化）：仅用于观测/评测，**不参与打分**
                # （signals 必须与 score 的加权和严格对应，故参与打分的只有 semantic）
                "semantic_raw": round(sem_raw, 4) if sem_raw is not None else None,
            },
        })
    candidates.sort(key=lambda c: (-c["score"], c["question_id"]))
    return candidates


def allocate_buckets(count: int, mode: str) -> dict:
    """按模式算各桶目标数量（mixed 按 MIXED_RATIO 分配，余数补给靠前的桶）"""
    if mode == "mixed":
        targets, assigned = {}, 0
        for bucket, ratio in MIXED_RATIO:
            targets[bucket] = int(count * ratio)
            assigned += targets[bucket]
        for bucket, _ in MIXED_RATIO:
            if assigned >= count:
                break
            targets[bucket] += 1
            assigned += 1
        return targets
    return {}                              # 单桶模式：由调用方按 mode 直接取该桶


def pick_with_quota(cands, want, kp_counter, kp_limit, used_types, used_qids) -> list:
    """从候选里挑 want 题：遵守「同知识点题数上限」，并优先补上尚未出现的题型。

    两轮挑选：第一轮只接受"题型还没出现过"的题（题型均衡），第二轮放开该限制补足数量。
    未挂知识点的题不占知识点配额，按题计数。

    L2（决策 4a，`KP_QUOTA_MODE="any_hit"`）：一题挂的**每个**知识点各占用一个配额槽，
    且**任一**知识点已饱和就拒收该题；`"primary_only"` 时退化为只看主知识点（L2 之前行为）。
    """
    picked = []
    for prefer_new_type in (True, False):
        for c in cands:
            if len(picked) >= want:
                return picked
            if c["question_id"] in used_qids:
                continue
            if KP_QUOTA_MODE == "primary_only":
                keys = [c["kp_id"] or f"__q{c['question_id']}"]
            else:
                keys = list(c.get("kp_ids") or [])
                if not keys:
                    keys = [f"__q{c['question_id']}"]   # 未挂知识点：按题计数，不占 kp 配额
            if any(kp_counter.get(k, 0) >= kp_limit for k in keys):
                continue
            if prefer_new_type and c["q_type"] in used_types:
                continue
            picked.append(c)
            used_qids.add(c["question_id"])
            for k in keys:
                kp_counter[k] = kp_counter.get(k, 0) + 1
            used_types.add(c["q_type"])
    return picked


class QuestionRecommender:
    """题目推荐器：打分 → 分桶 → 配额裁剪 → 题型均衡。

    `recommend()` 返回的是**数据库原始行**（含 answer/options），调用方必须走
    `PracticeService._public_view()` 投影后再下发给学生——防泄题纪律不能破。
    """

    @staticmethod
    def recommend(user_id: int, course_id: int, document_id=None, kp_id: str = None,
                  q_type: str = None, count: int = 10, mode: str = "mixed",
                  seed: int = None, now=None, debug_top: int = 0) -> dict:
        """返回 {"items": [{row, reason, bucket, kp_name, mastery, signals}], "meta": {...}}

        `debug_top > 0` 时额外在 meta 里返回 `top_candidates`（打分最高的前 N 题，
        仅 question_id + score，**不含答案**）。仅供评测/调试使用——`eval_question_recall.py`
        用它计算 Recall@K（召回候选集是否包含全量打分的前 K 名）。正常业务调用保持 0。
        """
        now = now or datetime.now()
        rng = random.Random(seed) if seed is not None else random.Random()
        mode = (mode or "mixed").lower()
        if mode not in VALID_MODES:
            mode = "mixed"

        # 1) 我的作答 → 逐题统计（最近一次/次数）+ 知识点维度明细
        #    口径：逐题统计包含「待批改」记录（学生确实做过，影响新颖度与遗忘到期），
        #    但对错只在已批改记录上判定；掌握度只喂已批改记录（未批改对错未知）。
        #    L2：知识点映射取自关联表（多值），供决策 1a 归因与召回 R3「错题→kp」使用。
        #    注意只对**已作答的题**取映射——阶段 D 顺带取消了「全课程扫一遍题目」的开销。
        records = sql_db.list_answer_records(user_id, course_id=course_id)
        q_kp_answered = sql_db.get_question_kps_map([r["question_id"] for r in records])
        answer_by_question, enriched = {}, []
        for r in records:
            qid = r["question_id"]
            graded = (r.get("grade_status") or "GRADED") == "GRADED"
            st = answer_by_question.setdefault(
                qid, {"attempts": 0, "correct": 0, "last_at": None, "last_is_correct": False})
            st["attempts"] += 1
            ts = _parse_ts(r["answered_at"])
            if graded:
                st["correct"] += 1 if r["is_correct"] else 0
                if ts and (st["last_at"] is None or ts > st["last_at"]):
                    st["last_at"], st["last_is_correct"] = ts, bool(r["is_correct"])
                # score 一并传入：教师批改后的主观题可能得部分分（见 kp_mastery）
                # L2 决策 1a：一题挂 N 个知识点 → 作答结果计入**全部 N 个**
                # （KP_ATTRIBUTION_MODE="primary" 时退化为只计主知识点，即 L2 之前的单标签行为）
                kps = q_kp_answered.get(qid) or []
                if KP_ATTRIBUTION_MODE == "primary":
                    kps = kps[:1]
                for kp in kps:
                    enriched.append({"kp_id": kp, "is_correct": r["is_correct"],
                                     "score": r["score"], "answered_at": r["answered_at"]})
            else:
                # 待批改：只更新"最近作答时间"，对错标记为 None（既不当作对，也不当作错）
                if ts and (st["last_at"] is None or ts > st["last_at"]):
                    st["last_at"], st["last_is_correct"] = ts, None

        # 4) 轻量掌握度（练习表现 + 学生自评；读时计算，不落库）
        manual = sql_db.list_records_by_user_course(user_id, course_id)
        mastery = kp_mastery(enriched, manual, now=now)

        # 5) 图谱上下文 + 学习路径的"下一步知识点"
        ctx = graph_context(course_id, document_id)
        mastered_names = [ctx["names"].get(k) for k, v in mastery.items()
                          if (v.get("mastery") or 0) >= 80 and ctx["names"].get(k)]
        next_ids = (next_knowledge_ids(course_id, document_id, mastered_names, ctx)
                    if ctx["available"] else set())

        # 6) 召回（L2 阶段 D）：把候选池从「全量题库」换成「多通道召回出的知识点子集」
        #    延迟导入：question_recall 需要本模块的阈值常量，模块级导入会成环
        from .question_recall import QuestionRecall, RECALL_PENALTY
        recall = QuestionRecall.recall(
            course_id, document_id=document_id, kp_id=kp_id, count=count,
            mastery=mastery, next_kp_ids=next_ids,
            answer_by_question=answer_by_question, q_kp_map=q_kp_answered,
        )
        recall_kps = recall["kp_ids"]
        use_filter = recall["meta"].get("mode") == "filter"
        fallback_used = bool(use_filter and not recall_kps)
        # bias 模式（默认）：把命中召回的 kp 交给打分器做**降权偏置**（不丢题）
        bias_kps = set(recall_kps) if (not use_filter and recall_kps) else None

        # 7) 候选池：`filter` 按召回 kp 硬过滤（缩小候选集，但会损失覆盖）；
        #    `bias` 取**全量**，由打分器对未命中的 kp 降权 → Recall@K 天然满足安全阀。
        #    Scope B：auto_grade_only=True —— 主观题（FILL/ESSAY）不进自动组卷候选池，
        #    实现「自动组卷不硬插入主观题」；学生显式指定 q_type=FILL/ESSAY 时才会取到。
        _, rows = sql_db.list_questions(
            course_id, document_id=document_id, kp_id=(kp_id or "").strip() or None,
            kp_ids=(recall_kps or None) if use_filter else None,
            q_type=(q_type or "").strip().upper() or None, is_active=True,
            page=1, page_size=100000, auto_grade_only=True,
        )
        # 候选池的知识点映射（多值，供决策 4a 的配额「任一命中」使用）
        q_kp = sql_db.get_question_kps_map([r["question_id"] for r in rows])
        # 全班区分度统计：内部已按 grade_status='GRADED' 过滤（未批改的主观题不参与）
        quality_stats = sql_db.question_answer_stats(course_id)

        # 7.5) 第 7 信号「语义相关度」的查询集：学生**最近的错题向量**
        #      取「最大余弦」而非均值——只要与任一道错题语义相近就值得练。
        #      任何一步失败都置 None（该信号按「无证据」处理，不影响其他 6 个信号）。
        sem_map = None
        if SEMANTIC_SIGNAL_ENABLED:                       # 关闭时该信号对所有候选取 0（A/B 用）
            try:
                wrong_recent = [(qid, st.get("last_at"))
                                for qid, st in answer_by_question.items()
                                if st.get("last_is_correct") is False]
                if wrong_recent:
                    wrong_recent.sort(key=lambda x: (x[1] is None, x[1]))
                    qids = [q for q, _ in wrong_recent[-SEMANTIC_SIGNAL_QUERY_LIMIT:]]
                    rows_v = sql_db.get_question_embedding_blobs(course_id, question_ids=qids)
                    # ⚠️ 必须用「行里实际的 question_id」与向量配对：部分错题可能没有向量，
                    #    若直接用 qids 与 vecs 按下标配对，query_ids 会与实际向量**错位**，
                    #    自匹配屏蔽就会打偏（曾因此让语义信号仍给错题自己打满分）。
                    pairs = [(r["question_id"], _blob_to_vec(r["blob"]))
                             for r in rows_v if r.get("blob")]
                    if pairs:
                        sem_map = vector_index.question_max_similarity(
                            course_id, document_id, [p[1] for p in pairs],
                            query_ids=[p[0] for p in pairs]) or None
            except Exception:
                sem_map = None

        # 8) 逐题打分
        candidates = build_candidates(rows, answer_by_question, mastery, ctx, next_ids,
                                      quality_stats, now=now, q_kp_map=q_kp,
                                      recall_kp_ids=bias_kps, recall_penalty=RECALL_PENALTY,
                                      sem_map=sem_map)
        # 调试/评测：曝光打分最高的前 N 题。必须在 random 洗牌**之前**取，保持「按分数」语义
        top_candidates = ([{"question_id": c["question_id"], "score": c["score"]}
                           for c in candidates[:debug_top]] if debug_top > 0 else None)
        if mode == "random":
            rng.shuffle(candidates)

        # 9) 组卷：桶配额 + 同知识点题数上限 + 题型均衡；不足时回填、再不足则放宽配额
        kp_limit = max(1, math.ceil(count / KP_QUOTA_DIVISOR))
        kp_counter, used_types, used_qids, picked = {}, set(), set(), []
        bucket_counts = {}
        if mode == "random":
            picked = pick_with_quota(candidates, count, kp_counter, kp_limit,
                                     used_types, used_qids)
            bucket_counts = {"random": len(picked)}
        else:
            for bucket, want in (allocate_buckets(count, mode) or {mode: count}).items():
                pool = [c for c in candidates if c["bucket"] == bucket]
                got = pick_with_quota(pool, want, kp_counter, kp_limit, used_types, used_qids)
                bucket_counts[bucket] = len(got)
                picked.extend(got)
            if len(picked) < count:                     # 桶内不足 → 其余桶按分数回填
                rest = [c for c in candidates if c["question_id"] not in used_qids]
                extra = pick_with_quota(rest, count - len(picked), kp_counter, kp_limit,
                                        used_types, used_qids)
                bucket_counts["filled"] = len(extra)
                picked.extend(extra)
            if len(picked) < count:                     # 仍不足（配额卡住）→ 放宽配额凑满
                rest = [c for c in candidates if c["question_id"] not in used_qids]
                extra = pick_with_quota(rest, count - len(picked), kp_counter, 10 ** 6,
                                        used_types, used_qids)
                bucket_counts["quota_relaxed"] = len(extra)
                picked.extend(extra)

        picked.sort(key=lambda c: (-c["score"], c["question_id"]))
        meta = {
            "mode": mode,
            "count": len(picked),
            "requested": count,
            "buckets": bucket_counts,
            "candidates": len(candidates),
            # Scope B：自动组卷默认排除主观题；显式指定 q_type=FILL/ESSAY 时才是 "requested"
            "subjectivity_policy": ("requested"
                                    if (q_type or "").strip().upper() in ("FILL", "ESSAY")
                                    else "excluded"),
            "kp_quota": kp_limit,
            # L2 监控（决策 4a 副作用观察口径）：回填率 = 靠「其余桶回填 + 放宽配额」凑来的
            # 题数 / 总题数。该值显著上升说明配额约束被多标签口径收得过紧，需调 KP_QUOTA_DIVISOR。
            "filled_count": bucket_counts.get("filled", 0),
            "quota_relaxed_count": bucket_counts.get("quota_relaxed", 0),
            "fill_rate": (round((bucket_counts.get("filled", 0)
                                 + bucket_counts.get("quota_relaxed", 0)) / len(picked), 3)
                          if picked else 0.0),
            # L2 口径快照：可回溯「这次结果是在哪个口径下产生的」
            "kp_attribution_mode": KP_ATTRIBUTION_MODE,
            "kp_quota_mode": KP_QUOTA_MODE,
            # L2 阶段 D：召回层事实（候选池为何是这么大、各通道贡献、是否回落全量）
            "recall": {
                "disabled": recall["meta"].get("disabled", False),
                "mode": recall["meta"].get("mode"),
                "penalty": recall["meta"].get("penalty"),
                "skipped_small_library": recall["meta"].get("skipped_small_library", False),
                "library_questions": recall["meta"].get("library_questions"),
                "kp_ids": len(recall_kps),
                # 仅 debug_top > 0（评测/调试）时下发**命中的 kp 明细**：
                # `kp_ids` 只是计数，而召回/粒度分析需要「命中了哪些 kp」才能推导候选池规模
                # （见 eval_kp_granularity.py）。正常业务调用保持 None，不增加响应体积。
                "kp_id_list": sorted(recall_kps) if debug_top > 0 else None,
                "by_channel": recall["by_channel"],
                "fallback_used": fallback_used,
                "kp_budget": recall["meta"]["kp_budget"],
                "want": recall["meta"]["want"],
                "channel_available": recall["meta"]["channel_available"],
                "errors": recall["meta"]["errors"],
                "scope_kp": recall["meta"]["scope_kp"],
            },
            # 全局掌握度统计（**全课程知识点**，不是被抽中题目的均值）：
            # 用于观测决策 1a 的副作用——多 kp 归因会把一条作答同时记到多个知识点上。
            "mastery_stats": _mastery_stats(mastery),
            "mastery_available": any(v.get("mastery") is not None for v in mastery.values()),
            "graph_available": ctx["available"],
            "next_kp_ids": sorted(next_ids),
            "weights": {"need": W_NEED, "due": W_DUE, "diff_fit": W_DIFF,
                        "novelty": W_NOVELTY, "wrong": W_WRONG,
                        "importance": W_IMPORTANCE, "semantic": W_SEMANTIC},
            # 第 7 信号是否生效（需题目向量层有数据 + 学生有错题记录）
            "semantic_available": bool(sem_map),
        }
        if top_candidates is not None:
            meta["top_candidates"] = top_candidates
        if mode not in ("mixed", "random"):
            # 单桶模式：桶内无题时会用其它桶回填，这里显式告知（前端可提示"该类型暂无题"）
            meta["requested_bucket"] = mode
            meta["requested_bucket_count"] = bucket_counts.get(mode, 0)
            meta["bucket_empty"] = bucket_counts.get(mode, 0) == 0
        return {
            "items": [{
                "row": c["row"], "reason": c["reason"], "bucket": c["bucket"],
                "bucket_label": BUCKET_LABELS.get(c["bucket"], c["bucket"]),
                "kp_id": c["kp_id"], "kp_name": c["kp_name"], "mastery": c["mastery"],
                "attempts": c["attempts"], "score": c["score"], "signals": c["signals"],
            } for c in picked],
            "meta": meta,
        }
