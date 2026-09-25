"""
向量检索层（L2 阶段 E）

把「纯 Python 逐条余弦」换成「**numpy 矩阵 + 进程内缓存**」，numpy 不可用时回落纯 Python。

**为什么这一步比"上 HNSW"更值**（实测，见 docs/题库与推荐系统设计.md §13.7）：

| 实现 | N=1,000 | N=5,000 | N=20,000 |
|------|--------:|--------:|---------:|
| 纯 Python 逐条余弦 | 51.4 ms | 259.8 ms | — |
| **numpy 矩阵乘法** | 0.161 ms | **0.42 ms** | 2.13 ms |
| （对照）HNSW | 0.174 ms | 0.34 ms | 0.63 ms |

→ N ≤ 5,000 时 numpy 与 HNSW 同级，却**零新依赖**；HNSW 留到 N 过万再谈（§11 明确不做）。

**缓存纪律**
- 键 = `(kind, course_id, document_id)`，进程内缓存，避免每次检索重读库、重建矩阵；
- **TTL 兜底**（`VECTOR_CACHE_TTL`，默认 60s）：多进程部署下写入方的 `invalidate()` 只作用于
  本进程，其他 worker 靠 TTL 收敛——这是**有意取舍**，不为小规模向量引入版本表；
- 写侧（`embedding.build_index` / `kp_labeler.ensure_question_vectors`）写完后调 `invalidate()`，
  同进程立即生效。

**回落与开关**
- `numpy` 不可用 → 纯 Python 余弦（复用 `qa_service._cosine`，延迟导入避免成环）；
- `VECTOR_FORCE_BRUTE=1` → 强制纯 Python 路径（A/B 对照用）。
"""
import os
import time

from ..core.sql_database import _blob_to_vec, sql_db

try:
    import numpy as _np
    _HAS_NUMPY = True
except ImportError:                                   # pragma: no cover - 环境相关
    _np = None
    _HAS_NUMPY = False

CACHE_TTL = float(os.getenv("VECTOR_CACHE_TTL", "60"))   # 0 = 不缓存（调试/对照）
FORCE_BRUTE = os.getenv("VECTOR_FORCE_BRUTE", "0") == "1"
# `question_max_similarity` 的矩阵规模上限：超过则跳过（矩阵内存 ≈ N×d×4 字节；
# 5 万 × 1024 维 ≈ 200MB）。该上限只影响「精排第 7 信号」，不影响向量检索本身。
MAX_SIM_VECTORS = int(os.getenv("VECTOR_MAX_SIM_VECTORS", "50000"))

_KP = "kp"
_QUESTION = "question"


def cosine_pairs(q_vec, ids, vecs) -> list:
    """批量余弦相似度 → `[(id, cosine)]`，**与入参顺序一致**。

    numpy 可用时向量化（N=5,000 时比逐条纯 Python 快 100× 以上），否则回落纯 Python。
    专供「调用方已经自己拿到向量」的场景（如 `kp_labeler` 的注入式向量路：向量由调用方
    提供或一次性从库里读好），与 `VectorIndex` 的区别是**不读库、不缓存、不做 top-k**。
    """
    ids = list(ids)
    if not q_vec or not ids:
        return []
    if _HAS_NUMPY and not FORCE_BRUTE:
        mat = _np.asarray(vecs, dtype=_np.float32)
        q = _np.asarray(q_vec, dtype=_np.float32)
        # 维度不一致 / 非二维输入（历史脏数据、注入的假向量）→ 回到纯 Python，保证不抛异常
        if mat.ndim == 2 and mat.shape[0] == len(ids) and mat.shape[1] == q.shape[0]:
            nq = float(_np.linalg.norm(q))
            if nq == 0.0:
                return []
            norms = _np.linalg.norm(mat, axis=1)
            norms[norms == 0] = 1.0
            sims = (mat @ q) / (norms * nq)
            return [(ids[i], round(float(sims[i]), 6)) for i in range(len(ids))]
    return _cosine_pairs_py(q_vec, ids, vecs)


def _cosine_pairs_py(q_vec, ids, vecs) -> list:
    from .qa_service import _cosine                  # 延迟导入：避免与 qa_service 成环
    return [(ids[i], _cosine(q_vec, v)) for i, v in enumerate(vecs)]


class VectorIndex:
    """向量检索统一入口（全局单例见文件末尾 `vector_index`）。"""

    def __init__(self):
        self._cache = {}          # (kind, course_id, document_id) -> {ids, mat|vecs, loaded_at}
        self._hits = 0
        self._misses = 0

    @property
    def backend(self) -> str:
        """当前实际后端：`numpy` / `brute_py`"""
        return "brute_py" if (FORCE_BRUTE or not _HAS_NUMPY) else "numpy"

    def stats(self) -> dict:
        return {"backend": self.backend, "cached": len(self._cache),
                "hits": self._hits, "misses": self._misses, "ttl": CACHE_TTL}

    def invalidate(self, kind=None, course_id=None) -> int:
        """失效缓存（写侧在数据变更后调用）。可按 kind / course_id 收窄，返回清掉的条数。"""
        keys = [k for k in self._cache
                if (kind is None or k[0] == kind) and (course_id is None or k[1] == course_id)]
        for k in keys:
            self._cache.pop(k, None)
        return len(keys)

    def kp_search(self, course_id, document_id, q_vec, top_k: int = 10) -> list:
        """知识点向量检索，返回 `[(kp_id, cosine)]` 按余弦降序。`document_id=None` 为课程级。"""
        return self._search(_KP, course_id, document_id, q_vec, top_k)

    def question_search(self, course_id, document_id, q_vec, top_k: int = 10) -> list:
        """题目向量检索，返回 `[(question_id, cosine)]` 按余弦降序。"""
        return self._search(_QUESTION, course_id, document_id, q_vec, top_k)

    def question_max_similarity(self, course_id, document_id, query_vecs,
                                query_ids=None, max_vectors: int = MAX_SIM_VECTORS) -> dict:
        """`{question_id: 与任一查询向量的最大余弦}`（查询向量已 L2 归一化 → 内积即余弦）。

        用途：精排**第 7 信号「语义相关度」**——查询向量取「学生最近的错题向量」，
        则与错题语义相近的题得分更高。这条信号能区分**同一知识点下的不同题目**
        （其余 6 个信号在同 kp 内完全相同，见文档 §1.2 缺口 7）。

        `query_ids` 与 `query_vecs` 一一对应；给了就**排除自匹配**——候选若就是某道错题本身，
        其余弦必然是 1.0，会拿满分。但"这道错题本身"已由 `wrong_flag` 信号与 `review` 桶
        表达，语义信号再计一次等于**重复计分**；排除后 ⑦ 只度量「与**其他**错题的相似度」，
        才是真正的新信息。

        numpy 可用时一次矩阵乘算完；反之回落逐条余弦。
        库内无题目向量 / 题目矩阵超过 `max_vectors` 时返回 `{}`（调用方按「无证据」处理）。
        """
        if not query_vecs:
            return {}
        ids, mat = self._load(_QUESTION, course_id, document_id)
        if not ids:
            return {}
        if len(ids) > max_vectors:
            return {}
        qid_list = list(query_ids) if query_ids else None
        if self.backend == "brute_py":
            from .qa_service import _cosine
            out = {}
            for j, qv in enumerate(query_vecs):
                for i, v in enumerate(mat):
                    if qid_list and ids[i] == qid_list[j]:
                        continue                          # 自匹配：跳过
                    c = _cosine(qv, v)
                    if c > out.get(ids[i], -2.0):
                        out[ids[i]] = c
            return out
        q = _np.asarray(query_vecs, dtype=_np.float32)           # (n, d)
        norms = _np.linalg.norm(q, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        sims = mat @ (q / norms).T                              # (m, n)
        if qid_list:
            pos = {str(qid): j for j, qid in enumerate(qid_list)}
            for i, qid in enumerate(ids):
                j = pos.get(str(qid))
                if j is not None:
                    sims[i, j] = -1.0                           # 屏蔽「自己与自己」
        best = sims.max(axis=1)
        # 只有单个查询且命中自匹配时，该行可能整行为 -1 → 视为无证据
        return {ids[i]: round(float(best[i]), 6) for i in range(len(ids)) if best[i] > -0.5}

    # ---------- 缓存加载 ----------

    def _load(self, kind: str, course_id, document_id):
        """返回 `(ids, container)`；container = numpy 矩阵（已 L2 归一化）或 list[list[float]]。"""
        key = (kind, course_id, document_id)
        now = time.monotonic()
        ent = self._cache.get(key)
        # CACHE_TTL > 0 才用缓存；TTL=0 表示「不缓存」（每次重载，便于对照与调试）
        if ent is not None and CACHE_TTL > 0 and now - ent["loaded_at"] < CACHE_TTL:
            self._hits += 1
            return ent["ids"], (ent["mat"] if "mat" in ent else ent["vecs"])
        self._misses += 1

        if kind == _KP:
            rows = sql_db.get_kp_embedding_blobs(course_id, document_id)
            key_field = "kp_id"
        else:
            rows = sql_db.get_question_embedding_blobs(course_id, document_id)
            key_field = "question_id"

        ids, blobs, width = [], [], None
        for r in rows:
            blob = r["blob"]
            if width is None:
                width = len(blob)
            if len(blob) != width:          # 维度不一致的脏行跳过，避免整批检索直接崩
                continue
            ids.append(r[key_field])
            blobs.append(blob)

        if self.backend == "brute_py":
            container = [_blob_to_vec(b) for b in blobs]
            ent = {"ids": ids, "vecs": container, "loaded_at": now}
        else:
            container = None
            if blobs:
                mat = _np.vstack([_np.frombuffer(b, dtype="<f4") for b in blobs]).astype(_np.float32)
                # 载入时一次性 L2 归一化：之后「点积 == 余弦」，检索路径只剩一次矩阵乘法
                norms = _np.linalg.norm(mat, axis=1, keepdims=True)
                norms[norms == 0] = 1.0
                container = mat / norms
            ent = {"ids": ids, "mat": container, "loaded_at": now}
        self._cache[key] = ent
        return ids, container

    # ---------- 实际检索 ----------

    def _search(self, kind: str, course_id, document_id, q_vec, top_k: int) -> list:
        if not q_vec or top_k <= 0:
            return []
        ids, container = self._load(kind, course_id, document_id)
        n = len(ids)
        if n == 0:
            return []
        k = min(top_k, n)

        if self.backend == "brute_py":
            from .qa_service import _cosine       # 延迟导入：避免与 qa_service 成环
            scored = [(_cosine(q_vec, v), i) for i, v in enumerate(container)]
            scored.sort(key=lambda x: (-x[0], str(ids[x[1]])))
            return [(ids[i], round(s, 6)) for s, i in scored[:k]]

        q = _np.asarray(q_vec, dtype=_np.float32)
        norm = float(_np.linalg.norm(q))
        if norm == 0.0:
            return []
        sims = container @ (q / norm)
        if k >= n:
            order = _np.argsort(-sims)
        else:
            part = _np.argpartition(-sims, k - 1)[:k]
            order = part[_np.argsort(-sims[part])]
        return [(ids[int(i)], round(float(sims[int(i)]), 6)) for i in order]


# 全局单例（与 core/database.py 的 db、core/sql_database.py 的 sql_db 同一约定）
vector_index = VectorIndex()
