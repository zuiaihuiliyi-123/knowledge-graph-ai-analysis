"""
题目推荐打分器（P2）端到端验证

覆盖范围：
  1. 基本出题：数量正确、只取本课程启用中的题、**返回体不含 answer/analysis**（防泄题硬断言）
  2. 元信息：reason/bucket/bucket_label/score/mastery + meta（weights / kp_quota / candidates）
  3. 配额与题型均衡：同知识点题数 ≤ kp_quota；题型尽量不单一
  4. 模式语义：weak/review/new/advanced/mixed/random；`new` 的题必须来自路径推荐；
     单桶无题时 meta.bucket_empty=true（已用其它桶回填）
  5. 可复现：同 seed 两次结果完全一致
  6. 冷启动：从无作答的学生也能出题，不报错
  7. 参数与降级：非法 mode → 4001；不存在课程 → 2001；推荐内部异常 → 降级随机出题（degraded=true）
  8. 图库不可用：仍能出题（graph_available=false），不抛异常

运行方式（backend 目录下，需 Neo4j 已启动以获得真实图谱信号）：
    python test_question_recommend.py            # 默认课程 65 / 学生 110
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.database import db
from app.core.sql_database import sql_db
from app.services import question_recommender as qr
from app.services.question_service import PracticeService

COURSE_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 65
STUDENT = int(sys.argv[2]) if len(sys.argv) > 2 else 110      # demo_student：有大量作答
COLD_STUDENT = 1                                              # 从无作答（冷启动对照）


class _BrokenGraph:
    """模拟图库不可用"""

    @staticmethod
    def query(*args, **kwargs):
        raise RuntimeError("模拟 Neo4j 不可用")


def main():
    checks = []

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    course = sql_db.get_course(COURSE_ID)
    if course is None:
        print(f"✗ 课程不存在：course_id={COURSE_ID}")
        return False
    docs = sql_db.list_documents_by_course(COURSE_ID)
    doc_id = docs[0]["doc_id"] if docs else None
    course_qids = {r["question_id"] for r in
                   sql_db.list_questions(COURSE_ID, page=1, page_size=100000)[1]}

    # ---------- 1. 基本出题 + 防泄题 ----------
    res = PracticeService.recommend_questions(STUDENT, COURSE_ID, document_id=doc_id,
                                              count=10, mode="mixed", seed=42)
    check("mixed 模式出题成功", res["ok"], str(res.get("message")))
    data = res["data"]
    items, meta = data["items"], data["meta"]
    check("返回题数 = 请求数（题库足够时）", len(items) == 10, str(len(items)))
    check("题目全部来自本课程", all(it["question_id"] in course_qids for it in items))
    leaked = [k for it in items for k in ("answer", "analysis") if k in it]
    check("**防泄题**：返回体不含 answer/analysis", not leaked, str(leaked))
    check("每题都带推荐理由与分桶",
          all(it.get("reason") and it.get("bucket_label") for it in items))
    check("meta 完整（weights/kp_quota/candidates/graph_available）",
          all(k in meta for k in ("weights", "kp_quota", "candidates", "graph_available")))
    check("meta.weights 与打分器常量逐个一致（含第 7 信号 semantic）",
          meta["weights"] == {"need": qr.W_NEED, "due": qr.W_DUE, "diff_fit": qr.W_DIFF,
                              "novelty": qr.W_NOVELTY, "wrong": qr.W_WRONG,
                              "importance": qr.W_IMPORTANCE, "semantic": qr.W_SEMANTIC}
          and abs(sum(meta["weights"].values()) - 1.0) < 1e-9,
          str(meta["weights"]))
    check("题目按推荐分数降序",
          all(items[i]["score"] >= items[i + 1]["score"] for i in range(len(items) - 1)))
    check("带出知识点名称（图谱可用时）",
          (not meta["graph_available"]) or any(it.get("kp_name") for it in items),
          f"graph={meta['graph_available']}")

    # ---------- 2. 配额与题型均衡 ----------
    kp_counts = {}
    for it in items:
        if it.get("kp_id"):
            kp_counts[it["kp_id"]] = kp_counts.get(it["kp_id"], 0) + 1
    relaxed = meta["buckets"].get("quota_relaxed", 0)
    check("同知识点题数不超过配额（或已标记放宽）",
          relaxed > 0 or all(v <= meta["kp_quota"] for v in kp_counts.values()),
          f"max={max(kp_counts.values()) if kp_counts else 0} quota={meta['kp_quota']} "
          f"relaxed={relaxed}")
    check("题型不单一（题库含多题型时至少 2 种）",
          len({it["q_type"] for it in items}) >= 2,
          str(sorted({it["q_type"] for it in items})))

    # ---------- 3. 模式语义 ----------
    for mode in ("weak", "review", "new", "advanced"):
        r = PracticeService.recommend_questions(STUDENT, COURSE_ID, document_id=doc_id,
                                                count=6, mode=mode, seed=7)
        m = r["data"]["meta"]
        got = m.get("requested_bucket_count", -1)
        if mode == "new":
            ids = set(m.get("next_kp_ids") or [])
            ok = all(it.get("kp_id") in ids for it in r["data"]["items"] if it.get("kp_id"))
            check("new 模式：题目来自路径推荐的知识点", ok, f"next_kp_ids={len(ids)}")
        check(f"{mode} 模式：桶语义与回填标记一致",
              (got > 0 and not m.get("bucket_empty")) or (got == 0 and bool(m.get("bucket_empty"))),
              f"bucket_count={got} bucket_empty={m.get('bucket_empty')}")

    # ---------- 4. 可复现 ----------
    a = PracticeService.recommend_questions(STUDENT, COURSE_ID, document_id=doc_id,
                                            count=8, mode="random", seed=123)["data"]
    b = PracticeService.recommend_questions(STUDENT, COURSE_ID, document_id=doc_id,
                                            count=8, mode="random", seed=123)["data"]
    check("random 模式同 seed 结果可复现",
          [i["question_id"] for i in a["items"]] == [i["question_id"] for i in b["items"]])
    c = PracticeService.recommend_questions(STUDENT, COURSE_ID, document_id=doc_id,
                                            count=8, mode="random", seed=999)["data"]
    check("random 模式不同 seed 结果不同",
          [i["question_id"] for i in a["items"]] != [i["question_id"] for i in c["items"]])

    # 冷启动：无作答/无自评的学生
    cold = PracticeService.recommend_questions(COLD_STUDENT, COURSE_ID, document_id=doc_id,
                                               count=5, mode="mixed", seed=3)
    check("冷启动学生能正常出题", cold["ok"] and cold["data"]["count"] > 0,
          str(cold.get("message")))
    check("冷启动时掌握度标记「证据不足」（mastery_available=False）",
          cold["data"]["meta"]["mastery_available"] is False,
          str(cold["data"]["meta"].get("mastery_available")))

    # ---------- 5. 参数与降级 ----------
    check("非法 mode → 4001",
          PracticeService.recommend_questions(STUDENT, COURSE_ID, mode="xxx")["code"] == 4001)
    check("不存在课程 → 2001",
          PracticeService.recommend_questions(STUDENT, 999999)["code"] == 2001)
    check("count 上限截断到 50",
          PracticeService.recommend_questions(STUDENT, COURSE_ID, document_id=doc_id,
                                              count=999)["data"]["count"] <= 50)

    import app.services.question_recommender as qr_module
    original_db = qr_module.db
    qr_module.db = _BrokenGraph()          # 图库不可用：仍应能出题
    try:
        down = PracticeService.recommend_questions(STUDENT, COURSE_ID, document_id=doc_id,
                                                   count=5, mode="mixed", seed=5)
        check("图库不可用：仍能出题且 graph_available=False",
              down["ok"] and down["data"]["meta"]["graph_available"] is False,
              f"ok={down.get('ok')} graph={down['data']['meta'].get('graph_available')}")
    finally:
        qr_module.db = original_db

    original_recommend = qr_module.QuestionRecommender.recommend
    qr_module.QuestionRecommender.recommend = staticmethod(
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("模拟推荐内部异常")))
    try:
        deg = PracticeService.recommend_questions(STUDENT, COURSE_ID, document_id=doc_id, count=5)
        check("推荐异常 → 降级随机出题（degraded=True 且有题）",
              deg["ok"] and deg["data"]["meta"]["degraded"] is True and deg["data"]["count"] > 0,
              f"degraded={deg['data']['meta'].get('degraded')} count={deg['data']['count']}")
    finally:
        qr_module.QuestionRecommender.recommend = original_recommend

    db.close()
    return checks


if __name__ == "__main__":
    result = main()
    if result is False:
        sys.exit(1)
    print("=" * 68)
    passed = 0
    for name, ok, detail in result:
        print(f"  {'✓' if ok else '✗'} {name}" + (f"  ({detail})" if detail else ""))
        passed += ok
    print(f"\n通过 {passed}/{len(result)}")
    print("=" * 68)
    sys.exit(0 if passed == len(result) else 1)
