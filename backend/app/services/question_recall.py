"""
题目召回层（L2 阶段 D）

把「候选池 = 全量题库」换成「多通道召回出的候选子集」，再交给 question_recommender 精排。
设计依据：docs/题库与推荐系统设计.md §四。

**为什么是「先召回知识点、再召回题目」**
    本项目核心信号（掌握度 / 遗忘 / 错题 / 路径）**全部在知识点维度**——学生的「薄弱点」
    是一个 kp 集合，而不是题目集合。所以 `kp → 题目` 才是自然的召回层次。

**通道（都是 kp 维度，零新依赖）**

| 通道 | 依据 | 依赖 |
|------|------|------|
| R1 outer | 路径推荐的「下一步」知识点（前置已满足） | Neo4j 只读；不可用则静默失效 |
| R2 due   | 到期复习：距上次作答 > REVIEW_DUE_DAYS | 无 |
| R3 wrong | 错题所在知识点 + 图谱一跳邻居（PRECEDES/CONTAINS 无向） | Neo4j 仅用于邻居 |
| R4 weak  | 未掌握 / 证据不足（mastery < WEAK_THRESHOLD 或无证据） | 无 |
| R5 semantic | 用最近错题的向量检索**知识点向量**（语义近邻，与 R3 的结构邻域互补） | 题目向量 + 知识点向量；缺任一则静默失效 |

> **与文档的偏差（有意）**：文档 §4.1 的 R6「兜底：高区分度题」在本实现中由**全空回落**
> 承担（所有通道皆空 → 返回空 kp 集 → 调用方交回全量候选池），而不做独立通道——
> 同样的安全目标，更少的机制与更小的误召回面。

**纪律（对应文档 §4.2）**
1. 每通道独立降级：单通道异常只记 `meta.by_channel`，不影响其他通道；
2. kp 预算：`kp_budget = max(KP_BUDGET_MIN, want // 4)`，按 `CHANNEL_QUOTA` 分给各通道；
3. `want` 上限：`want = max(RECALL_MIN, RECALL_MULTIPLIER × count)`，候选集不随题库无限膨胀；
4. 全空回落：`kp_ids == []` 时由调用方回落全量候选池（`fallback_used=True`）；
5. 通道配额：未用满的名额按优先级顺延补给其他通道；
6. **作用方式**：默认 `RECALL_MODE="bias"` —— 召回结果交给打分器**降权**（未命中的 kp ×`RECALL_PENALTY`），
   候选池仍是全量、**不丢题**；`"filter"` 才按召回 kp 硬过滤（缩小候选集但必然损失覆盖，需评估）。
"""
from ..core.database import db
from ..core.sql_database import _blob_to_vec, sql_db
from .question_recommender import REVIEW_DUE_DAYS, WEAK_THRESHOLD
from .vector_index import vector_index
import os

# 候选集目标规模：max(200, 10 × count)
RECALL_MIN = 200
RECALL_MULTIPLIER = 10
# kp 预算 = max(20, want // 4)：把「知识点个数」也限住，避免弱 kp 极多时退化成全量
KP_BUDGET_MIN = 20
KP_BUDGET_DIVISOR = 4
# 各通道的 kp 名额占比（总和可 < 1，余量按优先级顺延；见 _allocate）
# 注：曾按「每桶保底」的思路加过一条 `advanced`（高掌握度）通道，**实测证伪并撤销**——
#     组卷的 advanced 桶本来就不缺料（`filled=0`），而加该通道后 Recall@K 均值 0.9667→0.9534、
#     最小值 0.9000→0.8667、选择性 10.3%→3.5%，**两个指标都变差**。详见 §13.8。
CHANNEL_QUOTA = (
    ("outer", 0.35),
    ("due", 0.25),
    ("weak", 0.20),
    ("wrong", 0.10),
    ("semantic", 0.10),
)
# 错题邻域只取最近 N 道错题，避免历史错题把召回面拉得过大
WRONG_RECENT_LIMIT = 20
# 召回层的作用方式（L2 阶段 D 修订 —— **实测驱动**）：
#   "bias"   —— **默认**。召回只给命中的知识点**降权/提权**（见 RECALL_PENALTY），
#               候选池仍是**全量**。→ 完全不丢题，Recall@K 天然 = 1.0，收益体现在排序与分桶。
#   "filter" —— 按召回 kp **硬过滤**候选池。能显著缩小候选集（实测 3121 题时降 14.3%），
#               但**必然损失覆盖**：被排除的 kp 的题整条消失，实测 Recall@K 均值 0.8267、
#               **最低 0.2000**（30 题只剩 6 题）→ **不满足 ≥0.95 安全阀**。
# 结论：只有题库大到「全量打分不可接受」时才用 filter，并接受该覆盖损失。
RECALL_MODE = os.getenv("RECALL_MODE", "bias")
# bias 模式下，**未命中召回**的知识点其题目得分乘以该系数（<1 = 降权）。
# 只降不升：保持 score ∈ [0,1] 的既有语义（score 会展示给前端）。
RECALL_PENALTY = float(os.getenv("RECALL_PENALTY", "0.7"))
# filter 模式的规模门控：题库小于该规模时不值得付出覆盖损失（bias 模式无此问题）。
# 小规模时全量扫描本就很快（121 题 O(N) 打分 < 5ms），硬过滤是**净负收益**。
MIN_QUESTIONS_FOR_FILTER = int(os.getenv("MIN_QUESTIONS_FOR_FILTER", "2000"))
# R5 语义近邻：取最近 N 道错题的向量各查 top-K 个知识点，取并集
SEMANTIC_QUERY_LIMIT = 3
SEMANTIC_TOP_K = 8
# 召回层总开关（环境变量）：RECALL_ENABLED=0 → 直接返回空 kp 集，调用方回落全量候选池。
# 用于 A/B 对照与 Recall@K 评测（见 eval_question_recall.py）。
RECALL_ENABLED = os.getenv("RECALL_ENABLED", "1") != "0"


def _allocate(total: int, avail: dict) -> dict:
    """按 CHANNEL_QUOTA 把 total 个 kp 名额分给各通道。

    - 只给「有候选」的通道分名额；
    - 取整后的剩余名额按配额从大到小循环补给仍有候选的通道；
    - 名额不会超过该通道实际候选数（多余的自然顺延给下一个通道）。
    """
    keys = [k for k, _ in CHANNEL_QUOTA if avail.get(k)]
    share = {k: 0 for k, _ in CHANNEL_QUOTA}
    if not keys or total <= 0:
        return share
    ratio = dict(CHANNEL_QUOTA)
    assigned = 0
    for k in keys:
        n = min(avail[k], int(total * ratio[k]))
        share[k] = n
        assigned += n
    guard = 0
    while assigned < total and guard < total * 4:
        guard += 1
        progressed = False
        for k in keys:
            if share[k] < avail[k]:
                share[k] += 1
                assigned += 1
                progressed = True
                if assigned >= total:
                    break
        if not progressed:
            break
    return share


class QuestionRecall:
    """多通道召回：输出一组「该学生现在最该练的知识点」，由调用方据此取题。"""

    # ---------- 各通道：返回**已排序**的 kp 列表（越靠前越该练） ----------

    @staticmethod
    def _outer(next_kp_ids) -> list:
        """R1：路径推荐的下一步（前置已满足）。顺序沿用 PathRecommender 的优先级。"""
        return [k for k in (next_kp_ids or []) if k]

    @staticmethod
    def _due(mastery: dict) -> list:
        """R2：到期复习。按「距上次作答天数」降序（越久没练越靠前）。"""
        rows = [(kp, v.get("days_since")) for kp, v in (mastery or {}).items()
                if v.get("days_since") is not None
                and v["days_since"] > REVIEW_DUE_DAYS]
        rows.sort(key=lambda x: -x[1])
        return [kp for kp, _ in rows]

    @staticmethod
    def _weak(mastery: dict) -> list:
        """R4：未掌握 / 证据不足。按掌握度升序（最弱最靠前，未观测的排在已知弱之后）。"""
        rows = [(kp, v.get("mastery")) for kp, v in (mastery or {}).items()]
        rows = [r for r in rows if r[1] is None or r[1] < WEAK_THRESHOLD]
        rows.sort(key=lambda x: (x[1] is None, x[1] if x[1] is not None else 0.0))
        return [kp for kp, _ in rows]

    @staticmethod
    def _wrong(answer_by_question: dict, q_kp_map: dict, course_id, document_id=None) -> list:
        """R3：错题所在知识点（+ 图谱一跳邻居）。取最近 WRONG_RECENT_LIMIT 道错题。"""
        wrong = [(qid, st.get("last_at")) for qid, st in (answer_by_question or {}).items()
                 if st.get("last_is_correct") is False]
        if not wrong:
            return []
        # 最近作答的错题排后面（切片取尾部）→ 先按时间升序，尾部即最近
        wrong.sort(key=lambda x: (x[1] is None, x[1]))
        base, seen = [], set()
        for qid, _ in wrong[-WRONG_RECENT_LIMIT:]:
            for kp in (q_kp_map.get(qid) or []):
                if kp and kp not in seen:
                    seen.add(kp)
                    base.append(kp)
        if not base:
            return []
        for kp in QuestionRecall._graph_neighbors(course_id, document_id, base):
            if kp not in seen:
                seen.add(kp)
                base.append(kp)
        return base

    @staticmethod
    def _graph_neighbors(course_id, document_id, kp_ids) -> list:
        """沿 PRECEDES / CONTAINS **无向**一跳取邻居；图库不可用或参数为空则返回 []。"""
        ids = [k for k in (kp_ids or []) if k]
        if not ids:
            return []
        doc_filter = ", document_id: $did" if document_id is not None else ""
        params = {"cid": course_id, "ids": ids}
        if document_id is not None:
            params["did"] = document_id
        try:
            recs = db.query(
                "MATCH (a:KnowledgePoint {course_id: $cid" + doc_filter + "})-"
                "[r:PRECEDES|CONTAINS]-"
                "(b:KnowledgePoint {course_id: $cid" + doc_filter + "}) "
                "WHERE a.kp_id IN $ids RETURN DISTINCT b.kp_id AS kp_id",
                params,
            )
        except Exception:
            return []
        return [r.get("kp_id") for r in recs if r.get("kp_id")]

    @staticmethod
    def _semantic(course_id, document_id, answer_by_question: dict) -> list:
        """R5：语义近邻（补充通道）。

        用学生**最近答错的题**的向量作查询，检索最相近的**知识点向量**，取并集。

        与 R3 互补：R3 走**结构**（错题所在 kp + 图谱前置关系），R5 走**语义**
        （题目文本与知识点描述的相似度）——能捞到图谱里没有直接路径、但语义上高度
        相关的知识点。这也是论文「题目向量 → 知识点」那一路的等价实现。

        依赖 `t_question_embedding` 与 `t_kp_embedding` 同时有数据。任一为空 /
        未配置 `EMBEDDING_API_KEY` / numpy 不可用 / 检索异常 → **返回空集**（静默降级）。
        """
        wrong = [(qid, st.get("last_at")) for qid, st in (answer_by_question or {}).items()
                 if st.get("last_is_correct") is False]
        if not wrong:
            return []
        wrong.sort(key=lambda x: (x[1] is None, x[1]))
        recent = [qid for qid, _ in wrong[-SEMANTIC_QUERY_LIMIT:]]
        try:
            rows = sql_db.get_question_embedding_blobs(
                course_id, document_id, question_ids=recent)
        except Exception:
            return []
        if not rows:
            return []
        out, seen = [], set()
        for r in rows[:SEMANTIC_QUERY_LIMIT]:
            try:
                q_vec = _blob_to_vec(r["blob"])
                hits = vector_index.kp_search(course_id, document_id, q_vec, SEMANTIC_TOP_K)
            except Exception:
                continue
            for kp_id, _cos in hits:
                if kp_id and kp_id not in seen:
                    seen.add(kp_id)
                    out.append(kp_id)
        return out

    # ---------- 编排 ----------

    @staticmethod
    def recall(course_id: int, document_id=None, kp_id: str = None,
               count: int = 10, mastery: dict = None, next_kp_ids=None,
               answer_by_question: dict = None, q_kp_map: dict = None) -> dict:
        """多通道召回，返回 `{kp_ids, by_channel, meta}`。

        入参都由调用方（`QuestionRecommender.recommend`）在既有流程里算好，本层不重复查库：
        - `mastery`：`kp_mastery()` 的产物（含 mastery / days_since）
        - `next_kp_ids`：`next_knowledge_ids()` 的产物（图谱不可用时为空集 → R1 自然失效）
        - `answer_by_question`：逐题作答统计（含 last_is_correct / last_at）
        - `q_kp_map`：`{question_id: [kp_id, ...]}`，用于把错题映射到知识点

        `kp_ids == []` 表示**无从召回**，调用方应回落全量候选池（纪律 4）。
        """
        if not RECALL_ENABLED:
            # A/B 开关（RECALL_ENABLED=0）：返回空 kp 集 → 调用方回落全量候选池，
            # 即完全复现阶段 D 之前的行为，供 Recall@K 评测作基线。
            return {"kp_ids": [], "by_channel": {}, "meta": {
                "disabled": True, "want": 0, "kp_budget": 0, "picked_kps": 0,
                "channel_quota": dict(CHANNEL_QUOTA), "channel_available": {},
                "errors": {}, "scope_kp": (kp_id or "").strip() or None,
            }}

        # "filter" 模式的规模门控：题库不够大时不值得付出覆盖损失（bias 模式无此问题）
        total_q = None
        if RECALL_MODE == "filter":
            try:
                total_q = sql_db.count_questions_by_course(course_id)
            except Exception:
                total_q = None
            if total_q is not None and total_q < MIN_QUESTIONS_FOR_FILTER:
                return {"kp_ids": [], "by_channel": {}, "meta": {
                    "disabled": False, "mode": RECALL_MODE,
                    "skipped_small_library": True, "library_questions": total_q,
                    "threshold": MIN_QUESTIONS_FOR_FILTER,
                    "want": 0, "kp_budget": 0, "picked_kps": 0,
                    "channel_quota": dict(CHANNEL_QUOTA), "channel_available": {},
                    "errors": {}, "scope_kp": (kp_id or "").strip() or None,
                }}

        want = max(RECALL_MIN, RECALL_MULTIPLIER * max(1, count))
        kp_budget = max(KP_BUDGET_MIN, want // KP_BUDGET_DIVISOR)
        only = (kp_id or "").strip()

        candidates, by_channel, errors = {}, {}, {}
        channels = (
            ("outer", lambda: QuestionRecall._outer(next_kp_ids)),
            ("due", lambda: QuestionRecall._due(mastery)),
            ("weak", lambda: QuestionRecall._weak(mastery)),
            ("wrong", lambda: QuestionRecall._wrong(answer_by_question, q_kp_map,
                                                    course_id, document_id)),
            # R5：语义近邻（需题目向量 + 知识点向量；缺任一则自动返回空集）
            ("semantic", lambda: QuestionRecall._semantic(
                course_id, document_id, answer_by_question)),
        )
        for name, fn in channels:
            try:
                kps = fn() or []
            except Exception as e:              # 纪律 1：单通道失败不影响其他通道
                kps, errors[name] = [], f"{type(e).__name__}"
            if only:
                kps = [k for k in kps if k == only]
            seen, uniq = set(), []
            for k in kps:
                if k not in seen:
                    seen.add(k)
                    uniq.append(k)
            candidates[name] = uniq
            by_channel[name] = {"kps": len(uniq)}

        avail = {k: len(v) for k, v in candidates.items()}
        share = _allocate(kp_budget, avail)

        picked, picked_set = [], set()
        for name, _ in CHANNEL_QUOTA:
            limit, add = share.get(name, 0), 0
            for k in candidates.get(name, []):
                if add >= limit:
                    break
                if k in picked_set:             # 已被更高优先级通道取走
                    continue
                picked_set.add(k)
                picked.append(k)
                add += 1
            by_channel[name]["picked_kps"] = add

        return {
            "kp_ids": picked,
            "by_channel": by_channel,
            "meta": {
                "mode": RECALL_MODE,
                "penalty": RECALL_PENALTY,
                "library_questions": total_q,
                "want": want,
                "kp_budget": kp_budget,
                "picked_kps": len(picked),
                "channel_quota": dict(CHANNEL_QUOTA),
                "channel_available": avail,
                "errors": errors,
                "scope_kp": only or None,
            },
        }
