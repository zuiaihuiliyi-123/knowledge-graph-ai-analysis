"""阶段 2 数据一致性：文档导入题库（预览不写库 / 暂存语义 / 重复提交去重 / 越权）。

复现的真实缺陷：`import_commit` 原先**没有去重**——教师把同一份练习挂到两个文档、
或重复点一次「提交导入」，就会在题库里产生完全相同的题干，进而污染批改统计、
错题本与推荐配额。

本脚本守住：
1. 预览只读：不写 t_question；
2. 提交入库的是**暂存题**（is_active=0）并带 import_batch_id；
3. 重复提交同一批 items → 全部按「重复题目」跳过，题库总数不变；
4. 同批次内重复题干也只入一条；
5. 不合格条目（缺答案）被拒且有原因；
6. 非本课程教师提交 → 4003；
7. 批次明细可查回同批题目。

运行：cd backend && python test_question_import.py
（临时库 + 临时 UPLOAD_DIR；解析走确定性规则，不调 LLM / 不连 Neo4j）
"""
import asyncio
import os
import shutil
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="kg_qimport_")
os.environ["SQLITE_DB_PATH"] = os.path.join(_TMP, "app.db")
os.environ["UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["AUTO_BACKUP"] = "0"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient                     # noqa: E402

from app.core.security import hash_password                   # noqa: E402
from app.core.sql_database import sql_db                      # noqa: E402
from app.core.storage import document_relative_path           # noqa: E402
from app.main import app                                      # noqa: E402
from app.services.question_service import QuestionService     # noqa: E402

DEMO = os.path.join(os.path.dirname(BACKEND_DIR), "docs",
                    "演示用试题文档_第1章物理导论_含答案.txt")
PASSWORD = "pw123456"
failures = []
total = 0


def check(label, ok, detail=""):
    global total
    total += 1
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def count_questions(course_id: int) -> int:
    return sql_db._query_one(
        "SELECT count(*) AS c FROM t_question WHERE course_id = ?", (course_id,))["c"]


def main() -> int:
    client = TestClient(app)
    with client:
        teacher_id = sql_db.create_user("qi_teacher", hash_password(PASSWORD), role="teacher")
        other_id = sql_db.create_user("qi_other", hash_password(PASSWORD), role="teacher")
        course_id = sql_db.create_course("题目导入回归课程", teacher_id)

        with open(DEMO, encoding="utf-8") as f:
            text = f.read()
        doc_id = sql_db.create_document(course_id, teacher_id, "物理导论.txt", "TXT", len(text))
        rel = document_relative_path(course_id, doc_id, "物理导论.txt")
        abs_path = os.path.join(_TMP, "uploads", rel)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(text)
        sql_db.update_document(doc_id, file_path=rel)

        print("Step 1: 预览是只读的")
        preview = asyncio.run(QuestionService.import_preview(teacher_id, course_id, doc_id))
        items = (preview.get("data") or {}).get("items") or []
        check("预览成功", preview.get("ok") is True, str(preview)[:160])
        check("解析出题目（>= 5）", len(items) >= 5, str(len(items)))
        check("预览未写库", count_questions(course_id) == 0, str(count_questions(course_id)))

        print("\nStep 2: 提交入库为暂存题")
        res = QuestionService.import_commit(teacher_id, course_id, doc_id, items)
        data = res.get("data") or {}
        check("提交成功", res.get("ok") is True, str(res)[:160])
        check("全部入库", data.get("imported") == len(items), f"imported={data.get('imported')}")
        batch_id = data.get("batch_id")
        check("返回批次号", bool(batch_id), str(batch_id))
        rows = sql_db._query(
            "SELECT question_id, is_active, import_batch_id FROM t_question WHERE course_id = ?",
            (course_id,))
        check("题库条数与导入数一致", len(rows) == len(items), str(len(rows)))
        check("导入题默认停用（暂存区）", all(r["is_active"] == 0 for r in rows),
              str({r["is_active"] for r in rows}))
        check("都带上批次号", all(r["import_batch_id"] == batch_id for r in rows), str(batch_id))

        print("\nStep 3: 重复提交 → 全部跳过，题库不膨胀")
        again = QuestionService.import_commit(teacher_id, course_id, doc_id, items)
        d2 = again.get("data") or {}
        check("重复提交成功返回", again.get("ok") is True, str(again)[:120])
        check("第二次 imported = 0", d2.get("imported") == 0, str(d2.get("imported")))
        check("全部按重复跳过", len(d2.get("skipped") or []) == len(items),
              f"skipped={len(d2.get('skipped') or [])}")
        check("跳过原因含「重复」",
              all("重复" in (s.get("reason") or "") for s in (d2.get("skipped") or [])),
              str((d2.get("skipped") or [])[:1]))
        check("题库条数未变", count_questions(course_id) == len(items),
              str(count_questions(course_id)))

        print("\nStep 4: 同批次内重复题干只入一条")
        dup_item = {"q_type": "JUDGE", "stem": "同批次重复题干判定题。", "options": [],
                    "answer": "对", "analysis": "", "difficulty": 1}
        res3 = QuestionService.import_commit(teacher_id, course_id, doc_id, [dup_item, dup_item])
        d3 = res3.get("data") or {}
        check("只入一条", d3.get("imported") == 1, str(d3.get("imported")))
        check("另一条按重复跳过", len(d3.get("skipped") or []) == 1,
              str(len(d3.get("skipped") or [])))

        print("\nStep 5: 不合格条目被拒")
        bad = {"q_type": "SINGLE", "stem": "没有答案的单选题", "options": [
            {"key": "A", "text": "甲"}, {"key": "B", "text": "乙"}], "answer": None}
        before = count_questions(course_id)
        res4 = QuestionService.import_commit(teacher_id, course_id, doc_id, [bad])
        d4 = res4.get("data") or {}
        check("不合格条目 imported = 0", d4.get("imported") == 0, str(d4.get("imported")))
        check("给出跳过原因", bool(d4.get("skipped")), str(d4.get("skipped"))[:120])
        check("题库未新增", count_questions(course_id) == before, str(count_questions(course_id)))

        print("\nStep 6: 越权与批次明细")
        denied = QuestionService.import_commit(other_id, course_id, doc_id, items)
        check("非本课程教师 → 4003", (denied.get("code") == 4003), str(denied)[:120])

        detail = QuestionService.import_batch_detail(teacher_id, batch_id)
        check("批次明细可查", detail.get("ok") is True, str(detail)[:120])
        check("明细题目数正确", (detail.get("data") or {}).get("total") == len(items),
              str((detail.get("data") or {}).get("total")))

    print(f"\n通过 {total - len(failures)}/{total}" if not failures
          else f"\n失败 {len(failures)}/{total}：{failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
