"""
试题知识点自动标注（Scope C / P3）

三层候选 + 融合打分，**每一层都可独立降级**，任何一路不可用都不抛异常：

| 层 | 证据 | 依赖 |
|----|------|------|
| ① 字面匹配 lexical | 题干/选项中命中知识点名称（越长越可信） | 需要「知识点名称」 |
| ② 向量召回 vector  | 题目 embedding 与知识点 embedding 的余弦相似度 | 需 EMBEDDING_API_KEY |
| ③ 图谱扩展 graph   | 首轮候选沿 PRECEDES / CONTAINS 一跳扩展（衰减） | 需 Neo4j（或外部快照/本地缓存） |

候选来源（KpCatalog）优先级：**Neo4j 图谱 → 本地缓存 t_kp_text → 外部快照（评测/离线演示）**。
图谱查询成功即写入 t_kp_text 缓存，使「字面匹配」在无图库时仍然可用。

设计取舍（与需求确认）：自动标注只产出**候选建议**，写库必须显式 `apply=True` 且分数达到
`APPLY_MIN_SCORE`，并且默认只补「尚未挂知识点」的题——宁可少标，不可错标。
"""

import hashlib
import json

from ..core.database import db
from ..core.sql_database import _blob_to_vec, sql_db
from .embedding import EmbeddingClient
from .vector_index import vector_index, cosine_pairs

# ---------- 调参与阈值（集中在此，便于实验与参数冻结） ----------

TOP_K = 5                 # 单题返回候选数
W_LEX = 0.45              # 字面匹配权重
W_VEC = 0.45              # 向量召回权重
W_GRAPH = 0.10            # 图谱扩展权重
VEC_FLOOR = 0.35          # 余弦下限：低于该值视为无证据（bge-m3 同域文本经验值）
LEX_BASE = 0.65           # 字面命中的基础分
LEX_MIN_NAME_LEN = 2      # 参与字面匹配的最短名称长度（单字知识点噪音太大）
OPTION_WEIGHT = 0.7       # 选项命中相对题干的权重
GRAPH_DECAY = 0.45        # 邻居继承分值比例
MIN_SCORE = 0.30          # 进入候选列表的最低分（有字面/向量直接证据）
GRAPH_MIN_SCORE = 0.01    # 仅由图谱扩展得到的候选：放行但标记 graph_only（低置信建议，禁止自动写库）
APPLY_MIN_SCORE = 0.60    # 批量自动写库的最低分（高于 MIN_SCORE，宁缺毋滥）
MAX_TEXT_LEN = 512        # 参与向量化的文本截断长度
_EMBED_BATCH = 32         # 单次 embedding 请求条数（与 embedding.py 保持一致）


# ---------- 候选知识点来源 ----------

class KpCatalog:
    """知识点候选来源：图谱（成功即写缓存）→ 本地缓存 → 外部注入（评测/测试）"""

    def __init__(self, course_id: int, document_id=None, items=None, relations=None,
                 use_graph: bool = True):
        self.course_id = course_id
        self.document_id = document_id
        self.items = list(items or [])
        self.relations = dict(relations or {})      # kp_id -> [(rel_type, other_kp_id)]
        self.graph_available = False
        self.source = "none"
        self.reason = None
        self._use_graph = use_graph and not items   # 外部注入了候选就不必访问图谱

    def load(self):
        """按优先级装载候选；图谱失败静默降级到缓存"""
        if self.items:
            self.source = "injected"
            self.graph_available = bool(self.relations)
            return self
        if self._use_graph and self._load_from_graph():
            return self
        self._load_from_cache()
        return self

    def _load_from_graph(self) -> bool:
        doc_filter = ", document_id: $did" if self.document_id is not None else ""
        params = {"cid": self.course_id}
        if self.document_id is not None:
            params["did"] = self.document_id
        try:
            nodes = db.query(
                "MATCH (n:KnowledgePoint {course_id: $cid" + doc_filter + "}) "
                "RETURN n.kp_id AS kp_id, n.name AS name, n.category AS category, "
                "       n.description AS description, n.document_id AS document_id",
                params,
            )
            rels = db.query(
                "MATCH (a:KnowledgePoint {course_id: $cid" + doc_filter + "})"
                "-[r:PRECEDES|CONTAINS]->"
                "(b:KnowledgePoint {course_id: $cid" + doc_filter + "}) "
                "RETURN a.kp_id AS a, type(r) AS t, b.kp_id AS b",
                params,
            )
        except Exception as e:                       # 图库不可用：降级，不抛
            self.reason = f"图库不可用：{type(e).__name__}"
            return False

        self.items = [
            {"kp_id": r.get("kp_id"), "name": r.get("name") or r.get("kp_id"),
             "category": r.get("category") or "", "description": r.get("description") or "",
             "document_id": r.get("document_id")}
            for r in nodes if r.get("kp_id")
        ]
        rel_map = {}
        for r in rels:
            if r.get("a") and r.get("b"):
                rel_map.setdefault(r["a"], []).append((r.get("t"), r["b"]))
        self.relations = rel_map
        self.graph_available = True
        self.source = "graph"
        if self.items:
            # 写本地缓存：让「字面匹配」在下次无图库时仍可用（覆盖式更新）
            try:
                sql_db.upsert_kp_text_batch(self.course_id, self.items)
            except Exception:
                pass
        return True

    def _load_from_cache(self) -> bool:
        try:
            rows = sql_db.list_kp_text(self.course_id, document_id=self.document_id)
        except Exception:
            rows = []
        self.items = [
            {"kp_id": r["kp_id"], "name": r.get("name") or r["kp_id"],
             "category": r.get("category") or "", "description": r.get("description") or "",
             "document_id": r.get("document_id")}
            for r in rows
        ]
        self.graph_available = False
        self.source = "cache" if self.items else "none"
        if not self.items and self.reason is None:
            self.reason = "图谱与本地缓存都没有知识点（先建图谱或跑一次覆盖率刷新缓存）"
        return bool(self.items)

    def ids(self) -> set:
        return {it["kp_id"] for it in self.items}

    def by_id(self) -> dict:
        return {it["kp_id"]: it for it in self.items}


# ---------- 三层证据 ----------

def _lexical_scores(question: dict, items: list) -> dict:
    """① 字面匹配：知识点名称出现在题干（权重 1.0）或选项/提示（0.7）中。

    名称越长越可信（两字名易误命中），命中次数多再小幅加成。
    """
    stem = question.get("stem") or ""
    full = question_text(question)
    extra = full.replace(stem, "", 1) if stem and stem in full else ""
    scores = {}
    for it in items:
        name = (it.get("name") or "").strip()
        if len(name) < LEX_MIN_NAME_LEN:
            continue
        hit_stem = stem.count(name)
        hit_extra = extra.count(name) if extra else 0
        if not hit_stem and not hit_extra:
            continue
        length_bonus = min(0.25, 0.05 * (len(name) - LEX_MIN_NAME_LEN))
        weight = 1.0 if hit_stem else OPTION_WEIGHT
        score = (LEX_BASE + length_bonus) * weight
        score += min(0.15, 0.05 * (hit_stem + hit_extra - 1))
        scores[it["kp_id"]] = round(min(1.0, score), 4)
    return scores


def _vector_scores(q_vec, kp_vectors: dict) -> dict:
    """② 向量召回：题目向量与知识点向量的余弦相似度，线性拉伸到 [0,1]（低于 VEC_FLOOR 记 0）

    L2 阶段 E：余弦计算改走 `vector_index.cosine_pairs`——numpy 可用时向量化
    （实测 N=5,000 由 259.8ms 降到 0.42ms），numpy 不可用或维度异常时自动回落纯 Python。
    """
    scores = {}
    if not q_vec or not kp_vectors:
        return scores
    ids = list(kp_vectors.keys())
    vecs = [kp_vectors[k] for k in ids]
    for kp_id, cos in cosine_pairs(q_vec, ids, vecs):
        if cos <= VEC_FLOOR:
            continue
        scores[kp_id] = round(min(1.0, (cos - VEC_FLOOR) / (1.0 - VEC_FLOOR)), 4)
    return scores


def _graph_expand(base_scores: dict, relations: dict, catalog_ids: set) -> dict:
    """③ 图谱扩展：首轮候选沿 PRECEDES / CONTAINS 一跳扩展到邻居（衰减继承分值）。

    只做「扩展」不做「新增」：邻居必须已有字面/向量证据才被加权，
    否则纯靠关系推出来的知识点会大量误标（宁缺毋滥）。
    """
    out = {}
    if not relations:
        return out
    for kp_id, base in base_scores.items():
        for _rel_type, other in relations.get(kp_id, []):
            if not other or other in base_scores:
                continue
            if catalog_ids and other not in catalog_ids:
                continue
            value = round(GRAPH_DECAY * base, 4)
            if value > out.get(other, 0.0):
                out[other] = value
    return out


def _confidence(score: float) -> str:
    """把融合分映射为可读置信度（前端展示与「是否建议采纳」用）"""
    if score >= 0.75:
        return "high"
    if score >= 0.50:
        return "medium"
    return "low"


def _fuse(lex: dict, vec: dict, graph: dict, top_k: int, min_score: float) -> list:
    """融合三层证据 → 排序裁剪。

    公式（在**可用证据层上做加权平均**，缺失的层不参与分母）：
        score = Σ(wᵢ·sᵢ) / Σ(wᵢ)   for sᵢ > 0
    这样「只有字面命中但命中很强（0.70）」也能达到写库阈值 0.60，
    而不会被"没有向量证据"无谓拉低（早期版本用固定权重求和，导致纯字面命中永远到不了 0.60）。

    阈值分两档：
    - 有字面/向量**直接证据**的候选：门槛 min_score（默认 0.30）；
    - 仅由图谱扩展得到的候选：门槛 GRAPH_MIN_SCORE，且标注 `graph_only=True`，
      只作为低置信"建议"呈现给教师，**不允许自动写库**（宁缺毋滥）；
      若调用方把 min_score 抬到默认值之上（收紧口径），这类建议一并过滤。
    """
    cand = []
    for kp_id in set(lex) | set(vec) | set(graph):
        l, v, g = lex.get(kp_id, 0.0), vec.get(kp_id, 0.0), graph.get(kp_id, 0.0)
        layers = [(W_LEX, l), (W_VEC, v), (W_GRAPH, g)]
        available = [(w, s) for w, s in layers if s > 0]
        if not available:
            continue
        score = round(sum(w * s for w, s in available) / sum(w for w, _ in available), 4)
        has_direct = (l > 0 or v > 0)
        threshold = min_score if has_direct else (
            GRAPH_MIN_SCORE if min_score <= MIN_SCORE else min_score
        )
        if score < threshold:
            continue
        cand.append({
            "kp_id": kp_id,
            "score": score,
            "confidence": _confidence(score),
            "graph_only": not has_direct,
            "sources": {"lexical": l, "vector": v, "graph": g},
        })
    cand.sort(key=lambda c: (-c["score"], c["kp_id"]))
    return cand[:top_k]


def ensure_question_vectors(questions: list, course_id: int, embedder) -> int:
    """为题目补齐/刷新向量（仅当题面指纹变化时重算），返回写入条数。

    embedding 未配置时返回 0（调用方据此把向量路标为不可用）。
    """
    if embedder is None or not getattr(embedder, "available", False):
        return 0
    pending = []
    for q in questions:
        text = question_text(q)
        hashed = text_hash(text)
        cached = sql_db.get_question_embedding(q["question_id"])
        if cached and cached.get("text_hash") == hashed and cached.get("embedding"):
            continue
        pending.append((q, text, hashed))
    written = 0
    for i in range(0, len(pending), _EMBED_BATCH):
        chunk = pending[i:i + _EMBED_BATCH]
        try:
            vectors = embedder.embed([c[1] for c in chunk])
        except Exception:
            break                          # embedding 服务异常：静默降级（向量路不参与）
        for (q, _text, hashed), vec in zip(chunk, vectors):
            try:
                sql_db.upsert_question_embedding(
                    q["question_id"], course_id, q.get("document_id"), hashed, vec,
                )
                written += 1
            except Exception:
                pass
    if written:
        # L2 阶段 E：写完失效检索层缓存（同进程立即生效；跨进程由 VECTOR_CACHE_TTL 收敛）
        vector_index.invalidate(kind="question", course_id=course_id)
    return written


def _load_question_vectors(questions: list, course_id: int) -> dict:
    """从库里读回题目向量（BLOB 快读）→ `{question_id: vec}`。

    用途：批量标注 / 离线评测**复用已入库的向量**，避免 `label_question` 逐题重算——
    L2 阶段 E 修复：此前批量标注在 `ensure_question_vectors` 之后仍会对每题再调一次
    embedding API（121 题 = 121 次多余调用），实测标注耗时 22.5s 中大部分由此产生。
    """
    ids = [q.get("question_id") for q in (questions or []) if q.get("question_id") is not None]
    if not ids:
        return {}
    try:
        rows = sql_db.get_question_embedding_blobs(course_id, question_ids=ids)
    except Exception:
        return {}
    out = {}
    for r in rows:
        if r.get("blob"):
            out[r["question_id"]] = _blob_to_vec(r["blob"])
    return out


def label_question(question: dict, catalog, embedder=None, kp_vectors=None,
                   top_k: int = TOP_K, min_score: float = MIN_SCORE,
                   q_vec=None) -> dict:
    """对单题产出候选知识点（三层证据 + 融合），返回 {candidates, meta}

    `q_vec`：调用方**预给的题目向量**（可省一次 embedding API 调用）。批量路径与离线评测
    都应走它——向量已由 `ensure_question_vectors` 批量算好并入库，逐题重算是纯浪费。
    """
    items = catalog.items or []
    names = catalog.by_id()

    lexical = _lexical_scores(question, items)

    vector, vector_available = {}, False
    if kp_vectors and (q_vec is not None or (embedder is not None
                                             and getattr(embedder, "available", False))):
        try:
            # `q_vec` 由调用方预给（批量标注/评测已批量算好并入库）→ 避免逐题重复调用 embedding API
            if q_vec is None:
                q_vec = embedder.embed([question_text(question)])[0]
            vector = _vector_scores(q_vec, kp_vectors)
            vector_available = True
        except Exception:
            vector_available = False

    base = {k: max(lexical.get(k, 0.0), vector.get(k, 0.0))
            for k in set(lexical) | set(vector)}
    graph = _graph_expand(base, catalog.relations, catalog.ids())

    candidates = _fuse(lexical, vector, graph, top_k, min_score)
    for c in candidates:
        item = names.get(c["kp_id"])
        c["name"] = (item or {}).get("name") or c["kp_id"]
        c["category"] = (item or {}).get("category") or ""
        c["in_catalog"] = item is not None      # 不在候选清单内 → 不允许自动写库
    return {
        "candidates": candidates,
        "meta": {
            "catalog_size": len(items),
            "catalog_source": catalog.source,
            "graph_available": bool(catalog.graph_available and catalog.relations),
            "vector_available": vector_available,
            "lexical_hits": len(lexical),
            "vector_hits": len(vector),
            "graph_hits": len(graph),
            "reason": catalog.reason,
        },
    }


def _load_kp_vectors(course_id: int, document_id) -> dict:
    """知识点向量：优先文档级，其次课程内全部文档（kp_id → 向量）

    L2 阶段 E：改走 **BLOB 快读通道**（`_blob_to_vec` 免 JSON 解析）——
    实测 5,000 条 JSON 解析需 1,707ms，BLOB 为 0.02ms 量级；N=50,000 时差距达 18 秒。
    """
    try:
        rows = sql_db.get_kp_embedding_blobs(course_id, document_id)
    except Exception:
        rows = []
    vectors = {}
    for r in rows:
        if r.get("kp_id") and r.get("blob"):
            vectors[r["kp_id"]] = _blob_to_vec(r["blob"])
    return vectors


def label_one(question: dict, top_k: int = TOP_K, embedder=None, catalog=None) -> dict:
    """单题标注（供教师端「自动标注」按钮）：返回 {candidates, meta}"""
    course_id = question["course_id"]
    document_id = question.get("document_id")
    if embedder is None:
        embedder = EmbeddingClient()
    ensure_question_vectors([question], course_id, embedder)
    kp_vectors = _load_kp_vectors(course_id, document_id) \
        if getattr(embedder, "available", False) else {}
    # 同样复用刚入库的向量（单题路径也省一次 API 调用）
    q_vectors = _load_question_vectors([question], course_id) if kp_vectors else {}
    if catalog is None:
        catalog = KpCatalog(course_id, document_id).load()
    res = label_question(question, catalog, embedder=embedder, kp_vectors=kp_vectors,
                         top_k=top_k, q_vec=q_vectors.get(question.get("question_id")))
    res["meta"]["embedding_configured"] = bool(getattr(embedder, "available", False))
    res["meta"]["kp_vector_count"] = len(kp_vectors)
    return res


def label_questions(course_id: int, document_id=None, question_ids=None, only_missing: bool = True,
                    apply: bool = False, top_k: int = 3,
                    apply_threshold: float = APPLY_MIN_SCORE, min_score: float = MIN_SCORE,
                    catalog=None, embedder=None) -> dict:
    """批量标注：默认**只产出建议**（apply=False）；apply=True 时把达阈值候选**并入**该题的知识点。

    安全策略（与需求一致：宁可少标，不可错标）：
    1. 默认只处理「尚未挂知识点」的题（only_missing=True）；
    2. 写库阈值 APPLY_MIN_SCORE=0.60 高于入候选阈值 MIN_SCORE=0.30；
    3. 候选必须存在于知识点清单（in_catalog）才允许写库，避免写出悬空 kp_id（4002 同口径）；
    4. 候选不得是纯图谱推断（graph_only=False）——低置信建议一律不落库；
    5. 单题最多并入 `top_k` 个候选，且合并后总数不超过 MAX_KP_PER_QUESTION。

    **L2 变更（2026-09-22）**：写库由「只写最高分的 1 个 kp」改为「并入 top_k 个达阈值候选」
    （落 `t_question_kp` 关联表，即论文 2.4.3 的一题多知识点口径）。
    语义是**合并（merge）而非覆盖**：该题已有的知识点与其主知识点**一律保留**，只新增
    尚不存在的候选——避免自动标注毁掉教师手工维护的多知识点集合，也避免夺走主知识点。
    在默认 `only_missing=True` 路径下题目本就没有知识点，合并与覆盖**等价，行为不变**。
    """
    _, rows = sql_db.list_questions(course_id, document_id=document_id,
                                    page=1, page_size=100000)
    wanted = set()
    for x in (question_ids or []):
        try:
            wanted.add(int(x))
        except (TypeError, ValueError):
            continue
    questions = [r for r in rows if (not wanted or r["question_id"] in wanted)]
    if only_missing:
        questions = [r for r in questions if not (r.get("kp_id") or "").strip()]

    if embedder is None:
        embedder = EmbeddingClient()
    vectors_written = ensure_question_vectors(questions, course_id, embedder)
    kp_vectors = _load_kp_vectors(course_id, document_id) \
        if getattr(embedder, "available", False) else {}
    # 复用刚入库的题目向量：避免 label_question 里逐题重算（每题一次 embedding API）
    q_vectors = _load_question_vectors(questions, course_id) if kp_vectors else {}
    if catalog is None:
        catalog = KpCatalog(course_id, document_id).load()
    names = catalog.by_id()

    # 延迟导入：question_service 反向依赖本模块，模块级导入会成环（同 path_recommender 的既有做法）
    from .question_service import MAX_KP_PER_QUESTION

    items, with_candidates, applied_count, applied_kp_count = [], 0, 0, 0
    for q in questions:
        res = label_question(q, catalog, embedder=embedder, kp_vectors=kp_vectors,
                             top_k=top_k, min_score=min_score,
                             q_vec=q_vectors.get(q["question_id"]))
        candidates = res["candidates"]
        if candidates:
            with_candidates += 1
        entry = {
            "question_id": q["question_id"],
            "q_type": q["q_type"],
            "stem": (q.get("stem") or "")[:80],
            "current_kp_id": q.get("kp_id") or "",
            "current_kp_name": (names.get(q.get("kp_id")) or {}).get("name") or "",
            "candidates": candidates,
            "applied": False,
            "applied_kp_id": None,      # 兼容字段：本次新增的第一个知识点
            "applied_kp_ids": [],       # L2：本次实际新增的全部知识点
        }
        # 达标候选：必须在知识点清单内 + 非纯图谱推断 + 分数达写库阈值；最多 top_k 个
        accepted = [c for c in candidates
                    if c.get("in_catalog") and not c.get("graph_only")
                    and c["score"] >= apply_threshold][:top_k]
        if apply and accepted:
            qid = q["question_id"]
            try:
                existing = sql_db.get_question_kps(qid)
                existing_ids = [r["kp_id"] for r in existing]
                # 主知识点：已有则沿用（不夺权），否则取分数最高的新候选
                existing_primary = next((r["kp_id"] for r in existing if r["is_primary"]), None)
                scores = {r["kp_id"]: r["score"] for r in existing
                          if r["score"] is not None}
                # 来源保真：既有行沿用其原 source（教师手工挂的仍是 MANUAL），只给新增行打 AI
                sources = {r["kp_id"]: r["source"] for r in existing}
                new_ids = [c["kp_id"] for c in accepted if c["kp_id"] not in existing_ids]
                # 合并后再按上限截断（existing 在前，保证教师既有标注优先保留）
                merged = existing_ids + new_ids
                if len(merged) > MAX_KP_PER_QUESTION:
                    merged = merged[:MAX_KP_PER_QUESTION]
                    new_ids = [k for k in new_ids if k in merged]
                if new_ids:
                    for c in accepted:
                        if c["kp_id"] in new_ids:
                            scores[c["kp_id"]] = c["score"]
                            sources[c["kp_id"]] = "AI"
                    sql_db.set_question_kps(
                        qid, merged, primary=existing_primary or new_ids[0],
                        source="AI", scores=scores, sources=sources,
                    )
                    entry["applied"] = True
                    entry["applied_kp_ids"] = new_ids
                    entry["applied_kp_id"] = new_ids[0]
                    applied_count += 1
                    applied_kp_count += len(new_ids)
            except Exception as e:
                entry["apply_error"] = str(e)
        items.append(entry)

    return {"ok": True, "code": 0, "message": "success", "data": {
        "course_id": course_id,
        "document_id": document_id,
        "scanned": len(questions),
        "with_candidates": with_candidates,
        "applied": applied_count,                  # 实际有新增关联的**题数**
        "applied_kp_count": applied_kp_count,      # 实际新增的**关联行数**（一题可多挂）
        "apply": bool(apply),
        "apply_mode": "merge",                     # 合并语义：不覆盖已有知识点，也不夺走主知识点
        "items": items,
        "meta": {
            "catalog_size": len(catalog.items),
            "catalog_source": catalog.source,
            "graph_available": bool(catalog.graph_available),
            "embedding_configured": bool(getattr(embedder, "available", False)),
            "vectors_written": vectors_written,
            "kp_vector_count": len(kp_vectors),
            "top_k": top_k,                        # 同时是写库时「单题最多并入知识点数」的上限
            "min_score": min_score,
            "apply_threshold": apply_threshold,
            "weights": {"lexical": W_LEX, "vector": W_VEC, "graph": W_GRAPH},
            "reason": catalog.reason,
        },
    }}


def question_text(question: dict) -> str:
    """题目文本 = 题干 + 选项/空位（用于向量化与字面匹配）。

    填空题把每空 label/hint 也算进去（提示词常含关键概念，如「单位 kg」）。

    ⚠️ `options` 必须兼容**两种形态**：解析好的 `list`（服务层/测试传入）与
    **DB 原始行的 JSON 字符串**（`sql_db.list_questions()` 直接返回的行）。
    历史实现只处理 `list`，于是批量标注路径下**选项被完全忽略**，造成三个后果：
    ① 字面层的「选项命中」从未生效（`OPTION_WEIGHT=0.7` 形同虚设）；
    ② 向量只嵌入题干 → **题干相同的题向量完全相同**（语义信号无法区分它们）；
    ③ `text_hash` 不含选项 → **改选项不触发重算向量**。
    已在函数入口统一解析，两种形态行为一致。
    """
    parts = [question.get("stem") or ""]
    raw = question.get("options")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = []
    options = raw if isinstance(raw, list) else []
    for item in options:
        if isinstance(item, dict):
            parts.append(str(item.get("text") or ""))
            parts.append(str(item.get("label") or ""))
            parts.append(str(item.get("hint") or ""))
        elif isinstance(item, str):
            parts.append(item)
    return " ".join(p for p in parts if p).strip()[:MAX_TEXT_LEN]


def text_hash(text: str) -> str:
    """题目文本指纹：题面/选项变更后才需要重算向量"""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]
