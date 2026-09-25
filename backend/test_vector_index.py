"""
向量检索层（L2 阶段 E）——端到端验证

覆盖范围：
  1. 迁移与存储：`embedding_blob` 列已建、真实向量已回填、BLOB 体积显著小于 JSON
  2. **数值一致性**：numpy 路径与纯 Python 路径的 top-k 顺序与余弦完全一致（diff < 1e-6）
  3. 自检索：用向量自身作查询 → 首位是自己且余弦 ≈ 1.0
  4. 边界：top_k 截断 / 空查询 / top_k 超量 / 不存在的课程 / 维度为 0
  5. 缓存：命中计数、`VECTOR_CACHE_TTL=0` 时每次重载、`invalidate()` 生效
  6. 回落 JSON：把某行 blob 置空后仍能检索到该 kp（**回填进度不影响正确性**）
  7. 体积与性能：BLOB vs JSON 的存储比、numpy vs 纯 Python 的耗时对比（信息性）

依赖：**不依赖 Neo4j、不依赖 EMBEDDING_API_KEY**——直接用库里已有的真实向量（course 65 的 83 条）。
运行方式（backend 目录下）：
    python test_vector_index.py
"""
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import app.services.vector_index as vi_mod
from app.core.sql_database import _blob_to_vec, _vec_to_blob, sql_db
from app.services.vector_index import VectorIndex

COURSE_ID = 65


def _pick_course() -> int:
    """挑一个真正有知识点向量的课程（默认 65，不存在则取向量最多的课程）"""
    row = sql_db._query_one(
        "SELECT course_id, count(*) AS c FROM t_kp_embedding GROUP BY course_id "
        "ORDER BY c DESC LIMIT 1")
    if not row:
        return COURSE_ID
    return row["course_id"]


def main():
    checks = []

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    course_id = _pick_course()
    rows = sql_db.get_kp_embedding_blobs(course_id, None)
    if not rows:
        print(f"✗ 课程 {course_id} 没有知识点向量，无法验证（请先跑一次图谱索引构建）")
        return False
    ids = [r["kp_id"] for r in rows]
    q_blob = rows[0]["blob"]
    q_vec = _blob_to_vec(q_blob)
    gold_id = ids[0]
    print(f"课程 {course_id}：知识点向量 {len(rows)} 条，维度 {len(q_vec)}，"
          f"取用 {gold_id}\n")

    # ---------- 1. 迁移与存储 ----------
    print("=" * 70)
    print("1. 迁移与存储（embedding_blob）")
    print("=" * 70)
    cols = {r["name"] for r in sql_db._query("PRAGMA table_info(t_kp_embedding)")}
    check("t_kp_embedding 已有 embedding_blob 列", "embedding_blob" in cols)
    cols_q = {r["name"] for r in sql_db._query("PRAGMA table_info(t_question_embedding)")}
    check("t_question_embedding 已有 embedding_blob 列", "embedding_blob" in cols_q)
    stat = sql_db._query_one(
        "SELECT count(*) AS n, sum(embedding_blob IS NULL) AS nulls, "
        "       sum(length(embedding_blob)) AS b, sum(length(embedding)) AS j "
        "FROM t_kp_embedding")
    check("全部向量已回填 BLOB（NULL 数为 0）", stat["nulls"] == 0,
          f"NULL={stat['nulls']}/{stat['n']}")
    ratio = (stat["j"] / stat["b"]) if stat["b"] else 0
    check("BLOB 体积显著小于 JSON（≥ 4×）", ratio >= 4.0,
          f"JSON {stat['j']} 字符 vs BLOB {stat['b']} 字节 → {ratio:.2f}×")
    check("BLOB 每条约 4×维度 字节（float32）", len(q_blob) == len(q_vec) * 4,
          f"len(blob)={len(q_blob)} len(vec)={len(q_vec)}")

    # ---------- 2. 数值一致性：numpy vs 纯 Python ----------
    print("=" * 70)
    print("2. 数值一致性：numpy 路径 vs 纯 Python 路径")
    print("=" * 70)
    vi_mod.FORCE_BRUTE = False
    vi_np = VectorIndex()
    res_np = vi_np.kp_search(course_id, None, q_vec, 10)
    check("numpy 后端", vi_np.backend == "numpy", vi_np.backend)

    t0 = time.perf_counter()
    vi_mod.FORCE_BRUTE = True
    vi_br = VectorIndex()
    res_br = vi_br.kp_search(course_id, None, q_vec, 10)
    t_brute = time.perf_counter() - t0
    check("纯 Python 回落后端", vi_br.backend == "brute_py", vi_br.backend)

    check("两条路径返回条数一致", len(res_np) == len(res_br), f"{len(res_np)} vs {len(res_br)}")
    same_order = [a[0] for a in res_np] == [b[0] for b in res_br]
    max_diff = max((abs(a[1] - b[1]) for a, b in zip(res_np, res_br)), default=0.0)
    check("top-k **顺序完全一致**", same_order, f"numpy={[a[0] for a in res_np]} brute={[b[0] for b in res_br]}")
    # 两条路径都把结果 round 到 6 位小数 → 差值上限为「一格舍入」(1e-6)；
    # 浮点表示会让它偶尔多出极小尾巴，故容差取 2 格。真正的等值性由「top-k 顺序完全一致」保证。
    check("余弦值差异 ≤ 2e-6（一格舍入 + 浮点尾巴）",
          max_diff <= 2e-6, f"max_diff={max_diff:.2e}")

    # 计时对比（用各自后端重跑若干次，取均值）
    vi_mod.FORCE_BRUTE = False
    vi = VectorIndex()
    vi.kp_search(course_id, None, q_vec, 10)          # 预热（载入矩阵）
    n_rep = 30
    t0 = time.perf_counter()
    for _ in range(n_rep):
        vi.kp_search(course_id, None, q_vec, 10)
    ms_np = (time.perf_counter() - t0) / n_rep * 1000

    vi_mod.FORCE_BRUTE = True
    vib = VectorIndex()
    vib.kp_search(course_id, None, q_vec, 10)         # 预热
    t0 = time.perf_counter()
    for _ in range(n_rep):
        vib.kp_search(course_id, None, q_vec, 10)
    ms_br = (time.perf_counter() - t0) / n_rep * 1000
    print(f"    检索耗时（N={len(rows)}，{n_rep} 次均值）：numpy {ms_np:.3f} ms / "
          f"纯 Python {ms_br:.3f} ms → **{ms_br / ms_np:.1f}× 加速**")
    vi_mod.FORCE_BRUTE = False

    # ---------- 3. 自检索（用向量自身作查询） ----------
    print("=" * 70)
    print("3. 自检索：用向量自身作查询")
    print("=" * 70)
    vi = VectorIndex()
    top1 = vi.kp_search(course_id, None, q_vec, 1)
    check("首位应为自己", bool(top1) and top1[0][0] == gold_id, str(top1))
    check("自身余弦 ≈ 1.0", bool(top1) and abs(top1[0][1] - 1.0) < 1e-4,
          str(top1[0][1] if top1 else None))

    # ---------- 4. 边界 ----------
    print("=" * 70)
    print("4. 边界：top_k 截断 / 空查询 / 超量 / 不存在的课程")
    print("=" * 70)
    check("top_k=3 生效", len(vi.kp_search(course_id, None, q_vec, 3)) == 3)
    check("top_k 超量 → 返回全部",
          len(vi.kp_search(course_id, None, q_vec, 10 ** 6)) == len(rows))
    check("空查询 → 空结果", vi.kp_search(course_id, None, [], 5) == [])
    check("全零查询 → 空结果", vi.kp_search(course_id, None, [0.0] * len(q_vec), 5) == [])
    check("不存在的课程 → 空结果", vi.kp_search(999999, None, q_vec, 5) == [])
    check("题目向量为空（本机 0 条）→ 空结果且不抛异常",
          vi.question_search(course_id, None, q_vec, 5) == [])

    # ---------- 5. 缓存 ----------
    print("=" * 70)
    print("5. 缓存：命中计数 / TTL / invalidate")
    print("=" * 70)
    v = VectorIndex()
    v.kp_search(course_id, None, q_vec, 5)
    s1 = v.stats()
    v.kp_search(course_id, None, q_vec, 5)
    s2 = v.stats()
    check("首次 miss、第二次 hit",
          s1["misses"] == 1 and s1["hits"] == 0 and s2["hits"] == 1 and s2["misses"] == 1,
          f"{s1} → {s2}")
    check("invalidate() 清掉缓存", v.invalidate() == 1 and v.stats()["cached"] == 0,
          str(v.stats()))
    v.kp_search(course_id, None, q_vec, 5)
    check("按 kind 收窄失效（question 无缓存 → 清 0 条）",
          v.invalidate(kind="question") == 0 and v.invalidate(kind="kp") == 1)

    old_ttl = vi_mod.CACHE_TTL
    try:
        vi_mod.CACHE_TTL = 0                    # 0 = 不缓存 → 每次 reload（miss 累加）
        v0 = VectorIndex()
        v0.kp_search(course_id, None, q_vec, 5)
        v0.kp_search(course_id, None, q_vec, 5)
        check("TTL=0 时每次重新载入（2 次均 miss）", v0.stats()["misses"] == 2, str(v0.stats()))
    finally:
        vi_mod.CACHE_TTL = old_ttl

    # ---------- 6. 回落 JSON：回填进度不影响正确性 ----------
    print("=" * 70)
    print("6. 回落 JSON：某行 blob 置空后仍可检索")
    print("=" * 70)
    target = ids[1] if len(ids) > 1 else ids[0]
    orig_blob = sql_db._query_one(
        "SELECT embedding_blob FROM t_kp_embedding WHERE course_id = ? AND kp_id = ?",
        (course_id, target))["embedding_blob"]
    sql_db._execute(
        "UPDATE t_kp_embedding SET embedding_blob = NULL WHERE course_id = ? AND kp_id = ?",
        (course_id, target))
    try:
        got = [k for k, _ in VectorIndex().kp_search(course_id, None, q_vec, len(rows))]
        check("blob 为空的行由 JSON 回落读出、仍出现在结果里", target in got,
              f"目标 {target} 未命中")
        check("回落行的条数不变（仍能读全）", len(got) == len(rows), f"{len(got)} vs {len(rows)}")
    finally:
        sql_db._execute(
            "UPDATE t_kp_embedding SET embedding_blob = ? WHERE course_id = ? AND kp_id = ?",
            (orig_blob, course_id, target))

    # ---------- 汇总 ----------
    print("\n" + "=" * 70)
    print("校验结果")
    print("=" * 70)
    failed = 0
    for name, ok, detail in checks:
        if not ok:
            failed += 1
        line = f"{'✓' if ok else '✗'} {name}"
        if detail and not ok:
            line += f"    → {detail}"
        print(line)
    total = len(checks)
    print("-" * 70)
    print(f"通过 {total - failed}/{total}" + (f"，失败 {failed}" if failed else "，全部通过"))
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
