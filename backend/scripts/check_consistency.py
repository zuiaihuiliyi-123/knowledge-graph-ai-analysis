"""跨库一致性巡检（**只读**，不修任何数据）。

背景：本系统是 SQLite + Neo4j 双存储，两者之间没有事务。历史上出现过两类漂移：

1. `t_document.graph_status=COMPLETED`，但 Neo4j 里该 (course_id, document_id) 一个节点
   都没有 —— 表现为「文档说建好图了，图谱页却是空的、问答恒答知识库为空」；
2. Neo4j 里有节点，但对应的 `t_document` 行已被删除 —— 孤儿图谱，向量/统计还会算上它。

本脚本把这两类漂移、以及「库里记录的文件已不存在」一并输出成报告，
供部署后自检或排查使用。

用法（在 backend/ 目录下）：
    python scripts/check_consistency.py                # 人类可读报告
    python scripts/check_consistency.py --json         # 机器可读（CI / 巡检任务）
    python scripts/check_consistency.py --skip-graph   # 只查 SQLite 侧，不连 Neo4j
    python scripts/check_consistency.py --limit 200    # 只抽检前 N 篇文档的图谱

退出码：0 = 未发现问题；1 = 发现问题；2 = 参数/环境错误。
Neo4j 不可用时**不会**把脚本判失败，而是在报告里标注 `graph_checked=false`。
"""
import argparse
import json
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.core.config import settings          # noqa: E402
from app.core.sql_database import sql_db      # noqa: E402
from app.core.storage import resolve_document_path  # noqa: E402

_MAX_LISTED = 50          # 每类问题最多列出多少条（报告不至于刷屏）


def _documents(limit: int = 0) -> list:
    sql = ("SELECT doc_id, course_id, file_name, file_path, parse_status, "
           "extract_status, graph_status FROM t_document ORDER BY doc_id")
    if limit and limit > 0:
        sql += f" LIMIT {int(limit)}"
    return sql_db._query(sql) or []


def check_files(docs: list) -> list:
    """库里有记录、但按解析规则找不到本机文件。"""
    issues = []
    for d in docs:
        path = resolve_document_path(d)
        if not path or not os.path.exists(path):
            issues.append({
                "type": "FILE_MISSING",
                "doc_id": d["doc_id"], "course_id": d["course_id"],
                "file_name": d.get("file_name"),
                "detail": f"记录路径 {d.get('file_path')!r}，上传目录 {settings.UPLOAD_DIR} 下未找到",
            })
    return issues


def check_graph(docs: list) -> tuple:
    """图谱侧：COMPLETED 却无节点（缺失），以及无主文档的节点（孤儿）。"""
    try:
        from app.core.database import db
        db.query("RETURN 1 AS ok")
    except Exception as exc:  # noqa: BLE001
        return [], False, f"Neo4j 不可用，跳过图谱检查：{type(exc).__name__}"

    issues = []
    completed = [d for d in docs if (d.get("graph_status") or "") == "COMPLETED"]
    for d in completed:
        rows = db.query(
            "MATCH (n:KnowledgePoint {course_id: $c, document_id: $d}) RETURN count(n) AS c",
            {"c": d["course_id"], "d": d["doc_id"]},
        )
        count = int(rows[0]["c"]) if rows else 0
        if count == 0:
            issues.append({
                "type": "GRAPH_MISSING",
                "doc_id": d["doc_id"], "course_id": d["course_id"],
                "file_name": d.get("file_name"),
                "detail": "库内标记 graph_status=COMPLETED，但该文档在 Neo4j 中 0 个节点",
            })

    # 孤儿图谱：图里有 (course_id, document_id)，但库里没有对应文档行
    rows = db.query(
        "MATCH (n:KnowledgePoint) "
        "RETURN DISTINCT n.course_id AS c, n.document_id AS d, count(n) AS n"
    )
    known = {(int(d["course_id"]), int(d["doc_id"])) for d in docs if d.get("doc_id") is not None}
    for r in rows:
        try:
            key = (int(r["c"]), int(r["d"]))
        except (TypeError, ValueError):
            continue
        if key not in known:
            issues.append({
                "type": "GRAPH_ORPHAN",
                "doc_id": key[1], "course_id": key[0],
                "detail": f"Neo4j 有 {r.get('n')} 个节点，但 t_document 中没有 (course_id={key[0]}, doc_id={key[1]}) 记录",
            })
    return issues, True, None


def _minutes_ago(minutes: int) -> str:
    """SQLite 里时间戳统一是 `YYYY-MM-DD HH:MM:SS`（localtime），这里按同格式切片。"""
    from datetime import datetime, timedelta
    return (datetime.now() - timedelta(minutes=max(0, int(minutes)))).strftime(
        "%Y-%m-%d %H:%M:%S")


def check_tasks(stale_minutes: int = 30) -> list:
    """`t_document_task` 里「写着在跑、心跳却早就停了」的任务（阶段 4）。

    单进程部署下，`RUNNING` 且心跳超时的任务只可能来自崩溃，或线程被卡死。
    启动恢复只在启动时跑一次，中途卡死不会被发现——这类问题不会让数据不一致，
    但会让文档永远停在「处理中」，所以作为可观测性问题一并报告（不修数据）。
    """
    issues = []
    try:
        rows = sql_db.list_running_tasks(stale_before=_minutes_ago(stale_minutes))
    except Exception as exc:  # noqa: BLE001 - 旧库还没这张表时不该让巡检崩掉
        print(f"读取 t_document_task 失败（忽略该检查）：{exc}", file=sys.stderr)
        return issues
    for t in rows or []:
        issues.append({
            "type": "TASK_STALE",
            "doc_id": t.get("doc_id"), "course_id": t.get("course_id"),
            "file_name": None,
            "detail": (f"任务 stage={t.get('stage')} 状态 RUNNING，心跳 "
                       f"{t.get('heartbeat_at') or '从未'} 已超过 {stale_minutes} 分钟"),
        })
    return issues


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="跨库一致性巡检（只读）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--skip-graph", action="store_true", help="不连 Neo4j，只查 SQLite 侧")
    parser.add_argument("--limit", type=int, default=0, help="只抽检前 N 篇文档（0 = 全部）")
    parser.add_argument("--stale-minutes", type=int, default=30,
                        help="处理任务心跳超过多少分钟视为卡死（默认 30）")
    args = parser.parse_args(argv)

    try:
        docs = _documents(args.limit)
    except Exception as exc:  # noqa: BLE001
        print(f"读取 t_document 失败：{exc}", file=sys.stderr)
        return 2

    issues = check_files(docs)
    issues.extend(check_tasks(args.stale_minutes))
    graph_checked, graph_warning = False, None
    if not args.skip_graph:
        graph_issues, graph_checked, graph_warning = check_graph(docs)
        issues.extend(graph_issues)

    report = {
        "sqlite": settings.SQLITE_DB_PATH,
        "upload_dir": settings.UPLOAD_DIR,
        "documents_scanned": len(docs),
        "graph_checked": graph_checked,
        "graph_warning": graph_warning,
        "issue_count": len(issues),
        "by_type": {},
        "issues": issues[:_MAX_LISTED],
        "truncated": len(issues) > _MAX_LISTED,
    }
    for it in issues:
        report["by_type"][it["type"]] = report["by_type"].get(it["type"], 0) + 1

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("=" * 72)
        print("跨库一致性巡检（只读）")
        print("=" * 72)
        print(f"SQLite          : {report['sqlite']}")
        print(f"上传目录        : {report['upload_dir']}")
        print(f"扫描文档        : {report['documents_scanned']}")
        print(f"图谱检查        : {'已执行' if graph_checked else '未执行'}"
              + (f"（{graph_warning}）" if graph_warning else ""))
        print(f"任务检查        : 已执行（心跳阈值 {args.stale_minutes} 分钟）")
        print(f"发现问题        : {report['issue_count']} 条 {report['by_type'] or ''}")
        for it in report["issues"]:
            print(f"  - [{it['type']}] course={it['course_id']} doc={it['doc_id']} "
                  f"{it.get('file_name') or ''}：{it['detail']}")
        if report["truncated"]:
            print(f"  …（仅列出前 {_MAX_LISTED} 条）")
        print("=" * 72)

    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
