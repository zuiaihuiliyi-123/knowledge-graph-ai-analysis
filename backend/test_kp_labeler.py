"""
试题知识点自动标注（Scope C）端到端验证

覆盖范围：
  1. 文本工具：题目文本拼接（含填空题提示）与指纹稳定性
  2. 字面匹配：题干命中权重高于选项、名称越长越可信、单字名称不参与
  3. 向量召回：假 embedding 注入命中；embedding 不可用/异常时静默降级
  4. 图谱扩展：邻居以衰减分值进入候选并标记 graph_only（禁止自动写库）
  5. 融合打分：阈值过滤、top_k 截断、排序与置信度分级
  6. 批量标注：默认只出建议不写库；apply 只写达阈值且非 graph_only 的候选
  7. 降级链：图谱不可用 → 走本地缓存（t_kp_text）；两者都没有 → 返回空并给出原因
  8. 权限与参数：越权 4003、文档不存在 2002；课程删除后新表无孤儿数据

运行方式（backend 目录下；**不依赖 Neo4j，也不依赖 EMBEDDING_API_KEY**）：
    python test_kp_labeler.py
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import app.services.kp_labeler as labeler
from app.core.config import settings
from app.core.security import hash_password
from app.core.sql_database import sql_db
from app.services.kp_labeler import (
    KpCatalog, ensure_question_vectors, label_question, label_questions,
    question_text, text_hash,
)
from app.services.question_service import QuestionService

TEST_COURSE_MARK = "__kp_labeler_test__"

KP_ITEMS = [
    {"kp_id": "kp_linear", "name": "线性表", "category": "概念",
     "description": "由 n 个数据元素组成的有限序列", "document_id": None},
    {"kp_id": "kp_stack", "name": "栈", "category": "概念",
     "description": "只允许在栈顶插入删除的线性表", "document_id": None},
    {"kp_id": "kp_queue", "name": "队列", "category": "概念",
     "description": "先进先出的线性表", "document_id": None},
]
# 线性表 CONTAINS 栈 / 队列（用于验证图谱扩展只做「扩展」不做「新增」）
KP_RELATIONS = {"kp_linear": [("CONTAINS", "kp_stack"), ("CONTAINS", "kp_queue")]}

QUESTION = {
    "question_id": 900001, "course_id": 999, "document_id": None, "q_type": "SINGLE",
    "stem": "栈是一种操作受限的线性表，这句话是否正确？",
    "options": [{"key": "A", "text": "正确"}, {"key": "B", "text": "错误"}],
}


class _FakeEmbedder:
    """可控 embedding：按文本映射到固定向量，便于断言向量路"""

    def __init__(self, mapping=None, available=True, fail=False):
        self.mapping = mapping or {}
        self.available = available
        self.fail = fail
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        if self.fail:
            raise RuntimeError("模拟 embedding 服务不可用")
        return [self.mapping.get(t, [0.0, 0.0]) for t in texts]


class _BrokenGraph:
    """模拟 Neo4j 不可用：任何查询都抛异常"""

    @staticmethod
    def query(*args, **kwargs):
        raise RuntimeError("模拟 Neo4j 不可用")


def _catalog():
    """注入式目录：不访问图谱（source=injected），带关系用于图谱扩展"""
    return KpCatalog(999, items=KP_ITEMS, relations=KP_RELATIONS).load()


def _ensure_user(username, role, name):
    existing = sql_db.get_user_by_username(username)
    if existing:
        return existing["user_id"]
    return sql_db.create_user(username, hash_password("kp-pass-123"), role=role, display_name=name)


def _cleanup_existing_test_course():
    for c in sql_db.list_courses():
        if c["course_name"] == TEST_COURSE_MARK:
            sql_db.delete_course(c["course_id"])


def main():
    checks = []

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    sql_db.init_tables()          # 确保 Scope C 的新表（t_kp_text / t_question_embedding）已创建

    # ---------- 1. 文本工具 ----------
    q_fill = {"stem": "水的密度是 ____", "options": [
        {"key": 1, "label": "第1空", "hint": "单位：kg/m³", "answer": "1000"}]}
    text = question_text(q_fill)
    check("题目文本包含题干与填空提示", "水的密度是" in text and "单位：kg/m³" in text, text)
    check("文本指纹稳定", text_hash(text) == text_hash(text))
    check("文本指纹随题干变化", text_hash(text) != text_hash(text + "！"))

    # ---------- 2~5. 三层证据与融合 ----------
    catalog = _catalog()
    check("注入式目录不访问图谱（source=injected）", catalog.source == "injected")

    res = label_question(QUESTION, catalog, top_k=5)
    cands = {c["kp_id"]: c for c in res["candidates"]}
    check("字面匹配命中题干中的知识点", "kp_linear" in cands, str(list(cands)))
    check("字面匹配：题干命中得分达到阈值",
          cands.get("kp_linear", {}).get("sources", {}).get("lexical", 0) > 0
          and cands["kp_linear"]["score"] >= labeler.MIN_SCORE,
          str(cands.get("kp_linear")))
    check("单字名称（栈）不参与字面匹配",
          cands.get("kp_stack", {}).get("sources", {}).get("lexical") == 0,
          str(cands.get("kp_stack")))
    check("图谱扩展加入 CONTAINS 邻居且标记 graph_only",
          cands.get("kp_stack", {}).get("graph_only") is True
          and cands.get("kp_queue", {}).get("graph_only") is True,
          str([(k, v.get("graph_only")) for k, v in cands.items()]))
    check("排序：有直接证据的候选排在 graph_only 之前",
          res["candidates"][0]["kp_id"] == "kp_linear",
          str([c["kp_id"] for c in res["candidates"]]))
    check("候选带 in_catalog 标记", all(c["in_catalog"] for c in res["candidates"]))
    check("meta 记录字面/向量/图谱命中数",
          res["meta"]["lexical_hits"] >= 1 and res["meta"]["vector_hits"] == 0
          and res["meta"]["graph_hits"] >= 1, str(res["meta"]))

    # 向量路：题目文本 → [1,0]，队列向量同向 → 余弦 1.0
    q_text = question_text(QUESTION)
    fake = _FakeEmbedder({q_text: [1.0, 0.0]})
    kp_vectors = {"kp_queue": [1.0, 0.0], "kp_stack": [0.0, 1.0]}
    res_v = label_question(QUESTION, catalog, embedder=fake, kp_vectors=kp_vectors, top_k=5)
    v_cands = {c["kp_id"]: c for c in res_v["candidates"]}
    check("向量召回：同向知识点得满分并成为首位",
          res_v["candidates"][0]["kp_id"] == "kp_queue"
          and v_cands["kp_queue"]["sources"]["vector"] == 1.0,
          str(res_v["candidates"]))
    check("向量召回：正交知识点不计入向量证据",
          v_cands.get("kp_stack", {}).get("sources", {}).get("vector") == 0.0,
          str(v_cands.get("kp_stack")))
    check("向量路可用时 meta.vector_available=True", res_v["meta"]["vector_available"] is True)

    res_off = label_question(QUESTION, catalog, embedder=_FakeEmbedder(available=False),
                             kp_vectors=kp_vectors, top_k=5)
    check("embedding 未配置时向量路不参与且不报错",
          res_off["meta"]["vector_available"] is False and bool(res_off["candidates"]),
          str(res_off["meta"]))
    res_err = label_question(QUESTION, catalog, embedder=_FakeEmbedder({}, fail=True),
                             kp_vectors=kp_vectors, top_k=5)
    check("embedding 抛异常时静默降级（仍返回字面候选）",
          res_err["meta"]["vector_available"] is False
          and res_err["candidates"][0]["kp_id"] == "kp_linear", str(res_err["meta"]))

    res_hi = label_question(QUESTION, catalog, top_k=5, min_score=0.5)
    check("收紧阈值后仅保留直接证据候选（graph_only 建议被过滤）",
          [c["kp_id"] for c in res_hi["candidates"]] == ["kp_linear"],
          str([(c["kp_id"], c["score"]) for c in res_hi["candidates"]]))
    check("top_k 截断生效", len(label_question(QUESTION, catalog, top_k=1)["candidates"]) == 1)

    catalog_small = KpCatalog(999, items=KP_ITEMS[:1], relations=KP_RELATIONS).load()
    res_small = label_question(QUESTION, catalog_small, top_k=5)
    check("图谱扩展只在本课程知识点清单内扩展",
          [c["kp_id"] for c in res_small["candidates"]] == ["kp_linear"],
          str(res_small["candidates"]))

    # ---------- 6~7. 批量标注（真实 SQLite；图谱注入桩模拟不可用） ----------
    _cleanup_existing_test_course()
    teacher = sql_db.ensure_default_teacher()
    other_teacher = _ensure_user("kp_other_teacher", "teacher", "别的老师")
    course_id = sql_db.create_course(TEST_COURSE_MARK, teacher)
    doc_id = sql_db.create_document(course_id, teacher, "标注测试文档.txt", "TXT", 10)

    q_a = QuestionService.create_question(teacher, course_id, {
        "document_id": str(doc_id), "kp_id": None, "q_type": "SINGLE",
        "stem": "线性表的两种存储结构分别是什么？",
        "options": [{"key": "A", "text": "顺序存储"}, {"key": "B", "text": "链式存储"}],
        "answer": "A", "analysis": "", "difficulty": 2,
    })["data"]["question_id"]
    q_b = QuestionService.create_question(teacher, course_id, {
        "document_id": str(doc_id), "kp_id": None, "q_type": "JUDGE",
        "stem": "本章内容到此结束。", "options": [], "answer": "true",
        "analysis": "", "difficulty": 1,
    })["data"]["question_id"]
    q_c = QuestionService.create_question(teacher, course_id, {
        "document_id": str(doc_id), "kp_id": None, "q_type": "SINGLE",
        "stem": "下列哪个不是线性表？",
        "options": [{"key": "A", "text": "栈"}, {"key": "B", "text": "树"}],
        "answer": "B", "analysis": "", "difficulty": 2,
    })["data"]["question_id"]
    sql_db.update_question(q_c, kp_id="kp_stack")          # 已挂知识点（验证不覆盖）
    sql_db.upsert_kp_text_batch(course_id,
                                [dict(it, document_id=doc_id) for it in KP_ITEMS])
    check("知识点文本缓存写入成功", sql_db.count_kp_text(course_id) == 3)

    original_db = labeler.db
    labeler.db = _BrokenGraph                              # 图谱不可用 → 走缓存
    # 显式注入「不可用 embedder」：让本脚本**完全离线且与环境无关**——否则配置了
    # EMBEDDING_API_KEY 的机器会真的去打 embedding 接口（既慢又要花钱），
    # 且 `embedding_configured` 的取值会随环境漂移（真实向量路由 eval_kp_labeling.py 覆盖）。
    dry = label_questions(course_id, document_id=doc_id, only_missing=True, apply=False, top_k=3,
                          embedder=_FakeEmbedder({}, available=False))
    # 断言「dry-run 不写库」必须在 apply 之前取快照（顺序敏感）
    kp_a_after_dry = sql_db.get_question(q_a)["kp_id"]
    applied = label_questions(course_id, document_id=doc_id, only_missing=True,
                              apply=True, apply_threshold=0.6, top_k=3,
                              embedder=_FakeEmbedder({}, available=False))
    allq = label_questions(course_id, document_id=doc_id, only_missing=False, top_k=3,
                           embedder=_FakeEmbedder({}, available=False))
    labeler.db = original_db

    check("批量标注：图谱不可用时降级到本地缓存",
          dry["data"]["meta"]["catalog_source"] == "cache"
          and dry["data"]["meta"]["catalog_size"] == 3, str(dry["data"]["meta"]))
    check("批量标注：默认只扫描未挂知识点的题（2 道）",
          dry["data"]["scanned"] == 2, str(dry["data"]["scanned"]))
    check("批量标注：dry-run 不写库",
          kp_a_after_dry in (None, "") and dry["data"]["applied"] == 0,
          f"kp_a_after_dry={kp_a_after_dry} applied={dry['data']['applied']}")
    check("批量标注：meta 暴露权重与降级标记",
          "weights" in dry["data"]["meta"]
          # 不再硬编码 False：本机若配了 EMBEDDING_API_KEY，向量路本就该是配置好的。
          # 该断言的原意是「如实暴露降级状态」，故改为与环境实际配置比对。
          and dry["data"]["meta"]["embedding_configured"] is bool(settings.EMBEDDING_API_KEY)
          and dry["data"]["meta"]["graph_available"] is False, str(dry["data"]["meta"]))
    check("批量标注：无候选的题如实返回空候选",
          any(it["question_id"] == q_b and not it["candidates"] for it in dry["data"]["items"]),
          str([(it["question_id"], it["candidates"]) for it in dry["data"]["items"]])[:200])
    check("批量标注：apply=True 只写达阈值候选",
          applied["data"]["applied"] == 1
          and sql_db.get_question(q_a)["kp_id"] == "kp_linear", str(applied["data"]["applied"]))
    check("批量标注：无候选的题不被写入", sql_db.get_question(q_b)["kp_id"] in (None, ""))
    check("批量标注：不覆盖已挂知识点的题（only_missing 跳过）",
          sql_db.get_question(q_c)["kp_id"] == "kp_stack")
    check("only_missing=False 时扫描全部题（3 道）",
          allq["data"]["scanned"] == 3, str(allq["data"]["scanned"]))
    check("越权调用批量标注被拒（4003）",
          QuestionService.auto_label(other_teacher, course_id)["code"] == 4003)
    check("文档不存在被拒（2002）",
          QuestionService.auto_label(teacher, course_id, document_id=999999)["code"] == 2002)

    # ---------- 8. L2：apply 的多知识点「合并」语义 ----------
    # 前置：q_a 已由上一段 apply 写入 kp_linear；q_c 手工挂 kp_stack（题干含「线性表」→ 有 kp_linear 候选）
    labeler.db = _BrokenGraph
    merged = label_questions(course_id, document_id=doc_id, only_missing=False,
                             apply=True, apply_threshold=0.6, top_k=3)
    merged_again = label_questions(course_id, document_id=doc_id, only_missing=False,
                                   apply=True, apply_threshold=0.6, top_k=3)
    labeler.db = original_db

    qc_rows = {r["kp_id"]: r for r in sql_db.get_question_kps(q_c)}
    qc_kps = list(qc_rows)
    check("L2：apply 把新候选**并入**已有知识点（q_c 得到 kp_linear）",
          "kp_linear" in qc_rows, str(qc_kps))
    check("L2：合并保留教师已挂的知识点（kp_stack 仍在）", "kp_stack" in qc_rows, str(qc_kps))
    check("L2：合并不夺走主知识点（仍为手工指定的 kp_stack）",
          next((k for k, v in qc_rows.items() if v["is_primary"]), None) == "kp_stack",
          str({k: v["is_primary"] for k, v in qc_rows.items()}))
    check("L2：q_c 变成一题多挂（2 个知识点）", len(qc_rows) == 2, str(qc_kps))
    check("L2：响应返回本次新增的 applied_kp_ids",
          any(it["question_id"] == q_c and it.get("applied_kp_ids") == ["kp_linear"]
              for it in merged["data"]["items"]),
          str([(it["question_id"], it.get("applied_kp_ids"))
               for it in merged["data"]["items"]])[:200])
    check("L2：apply_mode=merge，且 applied_kp_count 与实际新增一致",
          merged["data"].get("apply_mode") == "merge"
          and merged["data"].get("applied_kp_count") == 1
          and merged["data"]["applied"] == 1,
          f"mode={merged['data'].get('apply_mode')} "
          f"kp_count={merged['data'].get('applied_kp_count')} applied={merged['data']['applied']}")
    check("L2：来源保真——手工挂的仍为 MANUAL，AI 新增的标为 AI",
          qc_rows.get("kp_stack", {}).get("source") == "MANUAL"
          and qc_rows.get("kp_linear", {}).get("source") == "AI",
          str({k: v.get("source") for k, v in qc_rows.items()}))
    check("L2：重复 apply 幂等（第二次 0 新增、不产生重复行）",
          merged_again["data"]["applied"] == 0
          and merged_again["data"]["applied_kp_count"] == 0
          and len(sql_db.get_question_kps(q_c)) == 2,
          f"applied={merged_again['data']['applied']} "
          f"kp_count={merged_again['data']['applied_kp_count']} "
          f"rows={len(sql_db.get_question_kps(q_c))}")
    check("L2：已挂的 q_a 不被重复写入（仍为 1 行）",
          len(sql_db.get_question_kps(q_a)) == 1, str(sql_db.get_question_kps(q_a)))

    # ---------- 9. 题目向量缓存与清理 ----------
    # 先清掉可能残留的向量：配置 EMBEDDING_API_KEY 后，上面的批量标注可能已用**真 embedder**
    # 写过 q_a 的向量；那样下面 fake2 的「首次写入」会因指纹未变而被跳过，断言随环境漂移。
    sql_db._execute("DELETE FROM t_question_embedding WHERE question_id = ?", (q_a,))
    q_row = sql_db.get_question(q_a)
    fake2 = _FakeEmbedder({question_text(q_row): [0.5, 0.5]})
    # 上面批次标注的 label_questions 会用自己的 embedder 顺手写下题目向量——
    # 本机配了 EMBEDDING_API_KEY 时是真写库。要断言「首次写入」，须先清掉缓存，
    # 否则这里命中缓存返回 0（缓存命中本身由下一条 check 覆盖）。
    sql_db._execute("DELETE FROM t_question_embedding WHERE question_id = ?", (q_a,))
    check("题目向量写入成功",
          ensure_question_vectors([q_row], course_id, fake2) == 1
          and sql_db.get_question_embedding(q_a) is not None)
    check("指纹未变时不重复计算向量",
          ensure_question_vectors([sql_db.get_question(q_a)], course_id, fake2) == 0)
    sql_db.update_question(q_a, stem="线性表的顺序存储与链式存储有何区别？")
    check("题面变更后重新计算向量",
          ensure_question_vectors([sql_db.get_question(q_a)], course_id, fake2) == 1)
    check("embedding 未配置时不写向量（返回 0）",
          ensure_question_vectors([q_row], course_id, _FakeEmbedder({}, available=False)) == 0)

    sql_db.delete_course(course_id)
    check("课程删除后知识点文本缓存无孤儿", sql_db.count_kp_text(course_id) == 0)
    check("课程删除后题目向量无孤儿", sql_db.get_question_embedding(q_a) is None)

    print("=" * 68)
    passed = 0
    for name, ok, detail in checks:
        print(f"  {'✓' if ok else '✗'} {name}" + (f"  ({detail})" if not ok and detail else ""))
        passed += ok
    print(f"\n通过 {passed}/{len(checks)}")
    print("=" * 68)
    return passed == len(checks)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
