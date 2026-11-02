"""阶段 4：跨库一致性巡检脚本的行为（只读、可 machine-readable、Neo4j 不可用不误判）。

`backend/scripts/check_consistency.py` 是部署后自检/排查工具，必须满足：
1. 能查出「库里有记录但文件不存在」（FILE_MISSING）；
2. 无问题时退出码 0，有问题时退出码 1（便于接 CI / 巡检任务）；
3. `--json` 输出可被程序解析；`--skip-graph` 完全不碰 Neo4j；
4. Neo4j 不可用时**不把脚本判失败**，而是标注 `graph_checked=false` 并给出原因；
5. 全程只读：巡检前后 `t_document` 行数与内容不变。

运行：cd backend && python test_consistency_check.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="kg_consist_")
DB = os.path.join(_TMP, "app.db")
UPLOADS = os.path.join(_TMP, "uploads")
os.environ["SQLITE_DB_PATH"] = DB
os.environ["UPLOAD_DIR"] = UPLOADS
os.environ["AUTO_BACKUP"] = "0"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

from app.core.security import hash_password                    # noqa: E402
from app.core.sql_database import sql_db                       # noqa: E402
from app.core.storage import document_relative_path            # noqa: E402

SCRIPT = os.path.join(BACKEND_DIR, "scripts", "check_consistency.py")
failures = []
total = 0


def check(label, ok, detail=""):
    global total
    total += 1
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def run(*args):
    env = dict(os.environ)
    p = subprocess.run([sys.executable, "-X", "utf8", SCRIPT, *args],
                       cwd=BACKEND_DIR, env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout or "", p.stderr or ""


def main() -> int:
    sql_db.init_tables()
    teacher_id = sql_db.create_user("cc_teacher", hash_password("pw123456"), role="teacher")
    course_id = sql_db.create_course("巡检回归课程", teacher_id)

    # 文档 A：文件存在（不报问题）
    doc_ok = sql_db.create_document(course_id, teacher_id, "ok.txt", "TXT", 5)
    rel_ok = document_relative_path(course_id, doc_ok, "ok.txt")
    path_ok = os.path.join(UPLOADS, rel_ok)
    os.makedirs(os.path.dirname(path_ok), exist_ok=True)
    with open(path_ok, "w", encoding="utf-8") as f:
        f.write("ok")
    sql_db.update_document(doc_ok, file_path=rel_ok, parse_status="PARSED",
                           extract_status="COMPLETED", graph_status="COMPLETED")

    # 文档 B：记录在、文件不存在（应报 FILE_MISSING）
    doc_missing = sql_db.create_document(course_id, teacher_id, "lost.txt", "TXT", 5)
    sql_db.update_document(doc_missing, file_path=document_relative_path(
        course_id, doc_missing, "lost.txt"), parse_status="PARSED",
        extract_status="COMPLETED", graph_status="COMPLETED")

    before = sql_db._query("SELECT * FROM t_document ORDER BY doc_id")

    print("Step 1: 能查出 FILE_MISSING（--skip-graph 纯 SQLite）")
    code, out, err = run("--json", "--skip-graph")
    check("退出码 1（发现问题）", code == 1, f"rc={code} {err[-120:]}")
    report = {}
    try:
        report = json.loads(out)
    except Exception:
        report = {}
    check("JSON 可解析", bool(report), out[:160])
    check("graph_checked=False（用了 --skip-graph）", report.get("graph_checked") is False,
          str(report.get("graph_checked")))
    missing = [i for i in (report.get("issues") or []) if i.get("type") == "FILE_MISSING"]
    check("命中丢失文件的那篇文档", any(i.get("doc_id") == doc_missing for i in missing),
          str([i.get("doc_id") for i in missing]))
    check("未误报有文件的文档", all(i.get("doc_id") != doc_ok for i in missing),
          str([i.get("doc_id") for i in missing]))

    print("\nStep 2: 补上文件后应无问题（退出码 0）")
    path_missing = os.path.join(UPLOADS, document_relative_path(
        course_id, doc_missing, "lost.txt"))
    os.makedirs(os.path.dirname(path_missing), exist_ok=True)
    with open(path_missing, "w", encoding="utf-8") as f:
        f.write("restored")
    code2, out2, _ = run("--json", "--skip-graph")
    report2 = json.loads(out2) if out2.strip().startswith("{") else {}
    check("退出码 0", code2 == 0, f"rc={code2}")
    check("issue_count = 0", report2.get("issue_count") == 0, str(report2.get("issue_count")))

    print("\nStep 3: 不传 --skip-graph 时不会因为 Neo4j 不可用而崩")
    code3, out3, err3 = run("--json")
    check("退出码在 0/1 之间", code3 in (0, 1), f"rc={code3} {err3[-120:]}")
    report3 = json.loads(out3) if out3.strip().startswith("{") else {}
    if report3.get("graph_checked") is False:
        check("Neo4j 不可用时给出原因", bool(report3.get("graph_warning")),
              str(report3.get("graph_warning")))
    else:
        check("Neo4j 可用时标注已检查", report3.get("graph_checked") is True, "已检查")

    print("\nStep 3.5: 卡死的处理任务（心跳超时）会被报告")
    sql_db.upsert_document_task(doc_ok, course_id, stage="EXTRACT", status="RUNNING",
                                heartbeat_at="2000-01-01 00:00:00")
    code4, out4, _ = run("--json", "--skip-graph")
    report4 = json.loads(out4) if out4.strip().startswith("{") else {}
    stale = [i for i in (report4.get("issues") or []) if i.get("type") == "TASK_STALE"]
    check("退出码 1", code4 == 1, f"rc={code4}")
    check("命中卡死任务", any(i.get("doc_id") == doc_ok for i in stale), str(stale)[:160])
    code5, out5, _ = run("--json", "--skip-graph", "--stale-minutes", "100000000")
    report5 = json.loads(out5) if out5.strip().startswith("{") else {}
    check("阈值放大后不再报 TASK_STALE",
          not [i for i in (report5.get("issues") or []) if i.get("type") == "TASK_STALE"],
          str(report5.get("by_type")))

    print("\nStep 4: 巡检是只读的")
    after = sql_db._query("SELECT * FROM t_document ORDER BY doc_id")
    check("t_document 内容未被改动", before == after, f"{len(before)} -> {len(after)}")

    print(f"\n通过 {total - len(failures)}/{total}" if not failures
          else f"\n失败 {len(failures)}/{total}：{failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
