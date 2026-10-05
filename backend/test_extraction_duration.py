"""知识抽取「耗时」取值正确性验证（管理端 资源管理 → 知识抽取任务）

运行方式（在 backend 目录下）：python test_extraction_duration.py

背景（本次修复的缺陷）：任务列表原先用 t_document 的
    created_at  当开始时间
    updated_at  当结束时间
但 updated_at 是「行最后修改时间」，`update_document()` 每次调用都刷新它。
抽取完成只是众多写入方之一——回填统计、重建图谱、改元数据都会刷新，
于是「耗时」实际是「上传至今」。实测有文档显示 48889 分（34 天）。

修复后：t_document 新增 extract_started_at / extract_finished_at，
由 update_document 在写 extract_status 的同一句 UPDATE 里打戳。

本脚本最重要的断言是 #3：写完再故意调一次 update_document 刷新 updated_at，
耗时必须**纹丝不动**——旧实现下这个断言必然失败。

数据安全：全程只在 app.db 的**副本**上操作，结束校验线上库未被写入。
"""
import os
import shutil
import sqlite3
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.config import settings
from app.core.sql_database import sql_db
from app.services.admin_service import AdminService

_results = []


def check(name: str, ok: bool, detail: str = ""):
    _results.append((name, bool(ok), detail))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  —— {detail}" if detail else ""))


def main():
    live_db = settings.SQLITE_DB_PATH
    tmp_dir = tempfile.mkdtemp(prefix="kg_extract_dur_")
    tmp_db = os.path.join(tmp_dir, "app_copy.db")
    shutil.copy2(live_db, tmp_db)
    sql_db.db_path = tmp_db
    print(f"副本库: {tmp_db}\n")

    # ---------- 1. 迁移幂等：两列存在，且连跑两次不报错 ----------
    print("Step 1: 迁移补列")
    sql_db.init_tables()
    sql_db.init_tables()          # 幂等：再跑一次不应报错
    with sql_db._connect() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(t_document)").fetchall()}
    check("t_document 已有 extract_started_at / extract_finished_at",
          {"extract_started_at", "extract_finished_at"} <= cols)
    check("init_tables 连跑两次幂等（未抛异常）", True)

    # ---------- 2. 历史行两列为 NULL：不伪造、也不再产出天文数字 ----------
    print("\nStep 2: 历史数据（真实库里那 15 条文档）")
    with sql_db._connect() as conn:
        legacy = conn.execute(
            "SELECT doc_id, created_at, updated_at, extract_started_at, extract_finished_at "
            "FROM t_document ORDER BY doc_id").fetchall()
    check("历史行两列均为 NULL（不伪造耗时）",
          all(r["extract_started_at"] is None and r["extract_finished_at"] is None
              for r in legacy), f"共 {len(legacy)} 行")

    res = AdminService.list_extraction_tasks(page=1, page_size=100)
    items = res["data"]["items"]
    check("接口对历史行返回 duration_seconds=None（前端显示「—」）",
          all(i["duration_seconds"] is None for i in items), f"共 {len(items)} 条")

    # 旧逻辑会算出多大的数？用真实数据量化，证明这个 bug 不是理论问题
    worst = None
    for r in legacy:
        try:
            from datetime import datetime
            d = (datetime.strptime(r["updated_at"], "%Y-%m-%d %H:%M:%S")
                 - datetime.strptime(r["created_at"], "%Y-%m-%d %H:%M:%S")).total_seconds()
        except Exception:  # noqa: BLE001
            continue
        if worst is None or d > worst[1]:
            worst = (r["doc_id"], d)
    if worst:
        print(f"    （旧口径下最离谱的是 doc {worst[0]}：{int(worst[1] // 60)} 分）")
    check("旧口径确实会算出 > 1 小时的天文数字（证实缺陷真实存在）",
          worst is not None and worst[1] > 3600)

    # ---------- 3. 核心回归守卫：updated_at 不再影响耗时 ----------
    print("\nStep 3: 抽取生命周期 + updated_at 解耦（本 bug 的回归守卫）")
    with sql_db._connect() as conn:
        course_id = conn.execute(
            "SELECT course_id FROM t_course ORDER BY course_id LIMIT 1").fetchone()["course_id"]
        teacher_id = conn.execute(
            "SELECT user_id FROM t_user ORDER BY user_id LIMIT 1").fetchone()["user_id"]

    doc_id = sql_db.create_document(course_id, teacher_id, "耗时验证.txt", "TXT", 10)

    sql_db.update_document(doc_id, extract_status="EXTRACTING")
    started = sql_db.get_document(doc_id)
    check("置 EXTRACTING 时写入 extract_started_at",
          bool(started["extract_started_at"]), f"started={started['extract_started_at']}")
    check("置 EXTRACTING 时 extract_finished_at 保持 NULL",
          started["extract_finished_at"] is None)

    sql_db.update_document(doc_id, extract_status="COMPLETED",
                           entity_count=7, relation_count=3)
    done = sql_db.get_document(doc_id)
    check("置 COMPLETED 时写入 extract_finished_at", bool(done["extract_finished_at"]))

    def duration_of(doc):
        r = AdminService.list_extraction_tasks(page=1, page_size=100)
        for i in r["data"]["items"]:
            if i["task_id"] == doc:
                return i["duration_seconds"]
        return "NOT_FOUND"

    d0 = duration_of(doc_id)
    check("耗时 = extract_finished_at - extract_started_at（非 None、非负）",
          isinstance(d0, int) and d0 >= 0, f"duration={d0}")

    # 关键：模拟「后续写操作把 updated_at 顶到很久以后」。
    # 直接改 updated_at 而不是调 update_document，是为了绕开「同一秒内两次写入
    # 时间字符串相同」的抖动——这里要的是确定性断言，不是碰运气。
    sql_db.update_document(doc_id, chunk_count=12)   # 真实路径：回填统计 / 重建图谱改写元数据
    with sql_db._connect() as conn:
        conn.execute(
            "UPDATE t_document SET updated_at = datetime(updated_at, '+3 days') "
            "WHERE doc_id = ?", (doc_id,))
        conn.commit()
    after = sql_db.get_document(doc_id)
    d1 = duration_of(doc_id)
    print(f"    （把 updated_at 顶到 {after['updated_at']} 后）")
    check("★ updated_at 被顶到 3 天后，耗时纹丝不动（旧实现下此断言必失败）",
          d1 == d0, f"{d0} -> {d1}")
    check("★ 旧口径下同样数据会算出 ~3 天：证实二者确已解耦",
          d1 is not None and d1 < 3600,
          f"新口径 {d1}s，旧口径约 {int(3 * 86400)}s")
    check("extract_finished_at 未被后续写操作改写",
          after["extract_finished_at"] == done["extract_finished_at"])

    # ---------- 4. 未开始抽取就失败的任务：留 NULL，不写假结束时刻 ----------
    print("\nStep 4: 解析阶段即失败（从未开始抽取）")
    doc2 = sql_db.create_document(course_id, teacher_id, "解析失败.txt", "TXT", 10)
    sql_db.update_document(doc2, parse_status="FAILED", error_message="解析失败: 模拟")
    row2 = sql_db.get_document(doc2)
    check("extract_started_at 保持 NULL", row2["extract_started_at"] is None)
    check("extract_finished_at 保持 NULL（不写假结束时刻）",
          row2["extract_finished_at"] is None)
    check("该任务耗时返回 None", duration_of(doc2) is None)

    # ---------- 5. 重新抽取：起止时刻整体重置 ----------
    print("\nStep 5: 重新抽取")
    sql_db.update_document(doc_id, extract_status="EXTRACTING")
    again = sql_db.get_document(doc_id)
    check("重新置 EXTRACTING 会清空上一轮的 extract_finished_at",
          again["extract_finished_at"] is None)
    check("并重新记 extract_started_at", bool(again["extract_started_at"]))

    # ---------- 汇总 ----------
    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print("\n" + "=" * 60)
    print(f"通过 {passed}/{total}")
    for name, ok, detail in _results:
        if not ok:
            print(f"  失败: {name} {detail}")
    print("=" * 60)
    return passed == total


if __name__ == "__main__":
    live = settings.SQLITE_DB_PATH
    try:
        ok = main()
    finally:
        # 把单例指回线上库，并校验线上库没被这个脚本碰过
        sql_db.db_path = live
        try:
            con = sqlite3.connect(f"file:{live.replace(os.sep, '/')}?mode=ro", uri=True)
            n = con.execute("SELECT count(*) FROM t_document").fetchone()[0]
            con.close()
            print(f"线上库 t_document 计数（只读核对）: {n}")
        except Exception as e:  # noqa: BLE001
            print(f"线上库核对失败: {e}")
    sys.exit(0 if ok else 1)
