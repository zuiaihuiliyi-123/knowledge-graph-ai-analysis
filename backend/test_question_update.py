"""阶段 2 数据一致性：题目更新时的向量缓存失效与局部编辑语义。

背景：`t_question_embedding` 是按 question_id 缓存的题目向量。它带 `text_hash`，
文本变了 `ensure_question_vectors` 会重算；但 **document_id 变了哈希不变** ——
缓存行会永久停留在旧作用域（按新文档检索查不到、ensure 也不会重写）。

本脚本守住：
1. 只改难度/解析 → **不**删向量缓存（避免每次小编辑都触发一次外部 embedding 调用）；
2. 改题干 → 删缓��；
3. 改选项 → 删缓存；
4. 改所属文档 → 删缓存（哈希兜不住的那一类）；
5. 局部编辑不塌缩其他字段；
6. 非本课程教师改题 → 4003，且不动缓存。

运行：cd backend && python test_question_update.py
（临时库 + AUTO_BACKUP=0，不调 embedding/LLM，不连 Neo4j）
"""
import os
import shutil
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="kg_qupdate_")
os.environ["SQLITE_DB_PATH"] = os.path.join(_TMP, "app.db")
os.environ["UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["AUTO_BACKUP"] = "0"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient                      # noqa: E402

from app.core.security import hash_password                    # noqa: E402
from app.core.sql_database import sql_db                       # noqa: E402
from app.main import app                                       # noqa: E402
from app.services.question_service import QuestionService      # noqa: E402

PASSWORD = "pw123456"
failures = []
total = 0


def check(label, ok, detail=""):
    global total
    total += 1
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def seed_vector(qid, course_id, doc_id):
    """模拟「自动标注已经算过向量」：直接写一行缓存。"""
    sql_db.upsert_question_embedding(qid, course_id, doc_id, "hash-old", [0.1, 0.2, 0.3])


def has_vector(qid) -> bool:
    return sql_db.get_question_embedding(qid) is not None


def payload(doc_id, stem="下列关于线性表的说法正确的是？", answer="A", difficulty=2):
    return {
        "document_id": doc_id, "kp_id": None, "q_type": "SINGLE", "stem": stem,
        "options": [{"key": "A", "text": "线性表是有限序列"},
                    {"key": "B", "text": "线性表是无限序列"}],
        "answer": answer, "analysis": "线性表是有限序列。", "difficulty": difficulty,
    }


def main() -> int:
    client = TestClient(app)
    with client:
        teacher_id = sql_db.create_user("qu_teacher", hash_password(PASSWORD), role="teacher")
        other_id = sql_db.create_user("qu_other", hash_password(PASSWORD), role="teacher")
        course_id = sql_db.create_course("题目更新回归课程", teacher_id)
        doc1 = sql_db.create_document(course_id, teacher_id, "a.txt", "TXT", 10)
        doc2 = sql_db.create_document(course_id, teacher_id, "b.txt", "TXT", 10)

        created = QuestionService.create_question(teacher_id, course_id, payload(doc1))
        check("准备：题目创建成功", created.get("ok") is True, str(created)[:160])
        qid = (created.get("data") or {}).get("question_id")

        print("\nStep 1: 只改难度/解析 → 保留向量缓存")
        seed_vector(qid, course_id, doc1)
        check("准备：缓存已写入", has_vector(qid))
        res = QuestionService.update_question(teacher_id, qid, {"difficulty": 5})
        check("改难度成功", res.get("ok") is True, str(res)[:120])
        check("缓存未被无谓删除", has_vector(qid))
        q = sql_db.get_question(qid)
        check("题干与选项未被改动", q["stem"] == "下列关于线性表的说法正确的是？" and bool(q.get("options")),
              f"stem={q['stem']!r}")
        check("难度已更新为 5", q["difficulty"] == 5, str(q["difficulty"]))

        res = QuestionService.update_question(teacher_id, qid, {"analysis": "补充解析"})
        check("改解析成功且缓存保留", res.get("ok") is True and has_vector(qid), str(res)[:120])

        print("\nStep 2: 改题干 → 删缓存")
        res = QuestionService.update_question(teacher_id, qid, {"stem": "新的题干：栈的特点是什么？"})
        check("改题干成功", res.get("ok") is True, str(res)[:160])
        check("缓存已删除", not has_vector(qid), str(sql_db.get_question_embedding(qid)))

        print("\nStep 3: 改选项 → 删缓存")
        seed_vector(qid, course_id, doc1)
        res = QuestionService.update_question(teacher_id, qid, {"options": [
            {"key": "A", "text": "先进先出"}, {"key": "B", "text": "后进先出"}]})
        check("改选项成功", res.get("ok") is True, str(res)[:160])
        check("缓存已删除", not has_vector(qid))

        print("\nStep 4: 改所属文档 → 删缓存（哈希兜不住的一类）")
        seed_vector(qid, course_id, doc1)
        res = QuestionService.update_question(teacher_id, qid, {"document_id": doc2})
        check("改文档成功", res.get("ok") is True, str(res)[:160])
        q = sql_db.get_question(qid)
        check("document_id 已变为 doc2", int(q["document_id"]) == int(doc2), str(q["document_id"]))
        check("缓存已删除（不残留旧作用域）", not has_vector(qid))

        print("\nStep 5: 非本课程教师改题 → 4003 且不动缓存")
        seed_vector(qid, course_id, doc2)
        res = QuestionService.update_question(other_id, qid, {"stem": "越权改题"})
        check("越权被拒 4003", res.get("code") == 4003 or res.get("ok") is False,
              str(res)[:120])
        check("缓存未被越权请求删除", has_vector(qid))

    print(f"\n通过 {total - len(failures)}/{total}" if not failures
          else f"\n失败 {len(failures)}/{total}：{failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
