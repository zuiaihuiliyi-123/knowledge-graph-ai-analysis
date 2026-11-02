"""阶段 4 可靠性：重启后「卡在中间态」的文档必须收敛为 FAILED 并可重试。

背景：解析/抽取/建图都在进程内的 asyncio 任务里跑，状态只写进 SQLite。
进程重启后库里残留 `PARSING` / `EXTRACTING` / `BUILDING`：没有任何任务在跑，
前端却一直显示「处理中」，教师也拿不到可操作的结果。

本脚本守住：
1. 三种中间态都被收敛为 FAILED，并写入可读原因；
2. **已完成的阶段不回退**（PARSED/COMPLETED 不会被改坏）；
3. 已经 FAILED / 完全成功的文档不受影响；
4. 幂等：重复调用不会再改动任何行；
5. 真实启动路径（TestClient 触发 lifespan）会自动执行恢复；
6. `RECONCILE_ON_STARTUP=0` 时启动**不**动这些文档；
7. 收敛后 `retry_document` 放行（不再被「已完成」拒绝）。

运行：cd backend && python test_task_recovery.py
（临时库 + AUTO_BACKUP=0，不调 LLM / 不连 Neo4j）
"""
import os
import shutil
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="kg_recover_")
os.environ["SQLITE_DB_PATH"] = os.path.join(_TMP, "app.db")
os.environ["UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["AUTO_BACKUP"] = "0"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient                       # noqa: E402

from app.core.config import settings                            # noqa: E402
from app.core.security import hash_password                     # noqa: E402
from app.core.sql_database import sql_db                        # noqa: E402
from app.main import app                                        # noqa: E402
from app.services.document_service import DocumentService       # noqa: E402

PASSWORD = "pw123456"
failures = []
total = 0


def check(label, ok, detail=""):
    global total
    total += 1
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def make_doc(course_id, teacher_id, name, *, parse="UPLOADED", extract="PENDING",
             graph="PENDING"):
    did = sql_db.create_document(course_id, teacher_id, name, "TXT", 10)
    sql_db.update_document(did, parse_status=parse, extract_status=extract, graph_status=graph)
    return did


def status_of(doc_id):
    d = sql_db.get_document(doc_id)
    return d["parse_status"], d["extract_status"], (d.get("graph_status") or ""), d.get("error_message")


def main() -> int:
    sql_db.init_tables()
    teacher_id = sql_db.create_user("rc_teacher", hash_password(PASSWORD), role="teacher")
    course_id = sql_db.create_course("启动恢复回归课程", teacher_id)

    # 三种中间态 + 一个完全成功 + 一个已失败
    d_parsing = make_doc(course_id, teacher_id, "卡在解析.txt", parse="PARSING")
    d_extract = make_doc(course_id, teacher_id, "卡在抽取.txt", parse="PARSED",
                         extract="EXTRACTING")
    d_graph = make_doc(course_id, teacher_id, "卡在建图.txt", parse="PARSED",
                       extract="COMPLETED", graph="BUILDING")
    d_done = make_doc(course_id, teacher_id, "已完成.txt", parse="PARSED",
                      extract="COMPLETED", graph="COMPLETED")
    d_failed = make_doc(course_id, teacher_id, "已失败.txt", parse="FAILED", extract="FAILED")

    print("Step 1: 三种中间态收敛为 FAILED")
    result = DocumentService.reconcile_interrupted()
    check("扫描到 3 条中间态", result["scanned"] == 3, str(result))
    check("恢复 3 条", result["recovered"] == 3, str(result))

    p, e, g, msg = status_of(d_parsing)
    check("卡在解析 → parse=FAILED", p == "FAILED", f"{p}/{e}/{g}")
    p, e, g, msg = status_of(d_extract)
    check("卡在抽取 → extract=FAILED 且 parse 保持 PARSED",
          e == "FAILED" and p == "PARSED", f"{p}/{e}/{g}")
    p, e, g, msg = status_of(d_graph)
    check("卡在建图 → graph=FAILED 且前两阶段保持完成",
          g == "FAILED" and p == "PARSED" and e == "COMPLETED", f"{p}/{e}/{g}")
    check("写入可读原因", "重启" in (msg or ""), str(msg))

    print("\nStep 2: 已完成 / 已失败不受影响")
    check("完全成功的文档未被改动", status_of(d_done)[:3] == ("PARSED", "COMPLETED", "COMPLETED"),
          str(status_of(d_done)[:3]))
    check("已失败的文档未被改动", status_of(d_failed)[:3] == ("FAILED", "FAILED", "PENDING"),
          str(status_of(d_failed)[:3]))

    print("\nStep 3: 幂等")
    again = DocumentService.reconcile_interrupted()
    check("再次调用扫描 0 条", again["scanned"] == 0 and again["recovered"] == 0, str(again))

    print("\nStep 4: 真实启动路径会自动恢复")
    d_startup = make_doc(course_id, teacher_id, "启动前卡住.txt", parse="PARSED",
                         extract="EXTRACTING")
    with TestClient(app):
        # lifespan 已执行
        pass
    check("TestClient 启动后 extract=FAILED", status_of(d_startup)[1] == "FAILED",
          str(status_of(d_startup)[:3]))

    print("\nStep 5: RECONCILE_ON_STARTUP=0 时不收敛")
    d_skip = make_doc(course_id, teacher_id, "开关关闭时卡住.txt", parse="PARSING")
    original = settings.RECONCILE_ON_STARTUP
    settings.RECONCILE_ON_STARTUP = False
    try:
        with TestClient(app):
            pass
        check("开关关闭后仍保持 PARSING", status_of(d_skip)[0] == "PARSING",
              str(status_of(d_skip)[:3]))
    finally:
        settings.RECONCILE_ON_STARTUP = original

    print("\nStep 6: 收敛后可重试（不再被「已完成」拒绝）")
    res = __import__("asyncio").run(DocumentService.retry_document(d_parsing, teacher_id))
    check("重试未被「已完成」拒绝", res.get("code") != 4000, str(res)[:140])
    check("源文件缺失时给出 2003", res.get("code") == 2003, str(res.get("code")))

    print(f"\n通过 {total - len(failures)}/{total}" if not failures
          else f"\n失败 {len(failures)}/{total}：{failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
