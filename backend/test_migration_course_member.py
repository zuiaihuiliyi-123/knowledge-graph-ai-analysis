"""课程中心改造：数据库迁移验证（幂等性 + 老数据不受损）

运行方式（在 backend 目录下执行）：
    python test_migration_course_member.py

重要：本脚本全程只操作 data/app.db 的【副本】，并在结束时校验线上库 MD5 未变，
      因此不会影响任何现有课程 / 文档 / 学习记录 / 收藏。
"""
import hashlib
import os
import shutil
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.config import settings
from app.core import sql_database as sd

# 受保护的既有表：迁移绝不允许改动它们的内容
PROTECTED_TABLES = ("t_user", "t_course", "t_document",
                    "t_learning_record", "t_student_favorite", "t_kp_embedding")

failures = []


def check(label, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def table_counts(db):
    return {t: db._query_one(f"SELECT count(*) AS c FROM {t}")["c"] for t in PROTECTED_TABLES}


def teacher_member_stats(db):
    """approved 教师成员统计，返回 (总行数, 去重后的课程数)。

    「一门课恰好一名教师」是旧假设——「协作教师可管理题库」上线后，一门课允许有
    多名 approved 教师（本机 course 63/64/66/67 各有一名协作教师，user=109），
    此时行数必然大于课程数。故「回填是否覆盖全部课程」只能用去重课程数来断言。
    """
    where = "WHERE role = 'teacher' AND status = 'approved'"
    total = db._query_one(f"SELECT count(*) AS c FROM t_course_member {where}")["c"]
    courses = db._query_one(
        f"SELECT count(DISTINCT course_id) AS c FROM t_course_member {where}")["c"]
    return total, courses


def main():
    live = settings.SQLITE_DB_PATH
    live_md5_before = md5(live)
    print("=" * 64)
    print("线上库:", live)
    print("MD5(前):", live_md5_before)
    print("=" * 64)

    # 线上库的行数快照（脚本结束再比一次，证明本脚本只写副本）
    live_tables_now = {r["name"] for r in sd.sql_db._query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    live_members_before = (sd.sql_db._query_one(
        "SELECT count(*) AS c FROM t_course_member")["c"]
        if "t_course_member" in live_tables_now else 0)
    live_courses_before = sd.sql_db._query_one("SELECT count(*) AS c FROM t_course")["c"]

    tmp_dir = tempfile.mkdtemp(prefix="kgu_migrate_")
    tmp_db = os.path.join(tmp_dir, "app_copy.db")
    shutil.copy2(live, tmp_db)
    print(f"副本: {tmp_db}\n")

    # 让新实例指向副本；模块级单例 sql_db 仍指向线上库，本脚本绝不使用它
    settings.SQLITE_DB_PATH = tmp_db
    dbo = sd.SQLDatabase()
    assert os.path.abspath(dbo.db_path) == os.path.abspath(tmp_db), "实例未指向副本，中止"

    before = table_counts(dbo)
    doc_paths_before = [r["file_path"] for r in dbo._query(
        "SELECT file_path FROM t_document ORDER BY doc_id")]

    print("Step 1: 连续执行 init_tables() 三次（第 2、3 次是幂等性测试）")
    dbo.init_tables()
    after_first = table_counts(dbo)
    # 第一次迁移后的教师成员行数：Step 4 的幂等断言以它为基准（后两次不得再插入）
    teachers_after_first = teacher_member_stats(dbo)[0]
    dbo.init_tables()
    dbo.init_tables()
    after_third = table_counts(dbo)

    print("\nStep 2: 表 / 索引 / 列 是否齐备")
    objs = {r["name"]: r["type"] for r in dbo._query(
        "SELECT name, type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
    for t in ("t_course_member", "t_course_invite", "t_user_profile"):
        check(f"表 {t} 已创建", objs.get(t) == "table")
    check("唯一索引 uq_course_join_code 已创建",
          objs.get("uq_course_join_code") == "index")
    for idx in ("idx_cm_course_status", "idx_cm_user_status", "idx_cm_course_role",
                "idx_invite_course", "idx_profile_student_no", "idx_profile_teacher_no"):
        check(f"索引 {idx} 已创建", objs.get(idx) == "index")

    cols = {r["name"] for r in dbo._query("PRAGMA table_info(t_course)")}
    for c in ("join_code", "join_mode", "organization", "category", "cover", "is_public"):
        check(f"t_course.{c} 已补列", c in cols)

    print("\nStep 3: 老课程回填结果")
    course_cnt = dbo._query_one("SELECT count(*) AS c FROM t_course")["c"]
    # 断言的意图是「没有课程漏掉教师回填」，故比的是被覆盖的课程数，不是成员行数
    teacher_total, teacher_covered = teacher_member_stats(dbo)
    check("每个课程都有 approved teacher 成员", teacher_covered == course_cnt,
          f"覆盖 {teacher_covered}/{course_cnt} 门课；教师成员共 {teacher_total} 行")

    null_codes = dbo._query_one(
        "SELECT count(*) AS c FROM t_course WHERE join_code IS NULL")["c"]
    check("没有 join_code 为空的课程", null_codes == 0, f"空码 {null_codes} 门")

    dup_codes = dbo._query_one(
        "SELECT count(*) AS c FROM (SELECT join_code FROM t_course "
        "GROUP BY join_code HAVING count(*) > 1)")["c"]
    check("join_code 无重复", dup_codes == 0, f"重复组 {dup_codes}")

    code_len_bad = dbo._query_one(
        "SELECT count(*) AS c FROM t_course "
        "WHERE length(join_code) < 6 OR length(join_code) > 8")["c"]
    check("join_code 长度均在 6-8 位", code_len_bad == 0)

    dup_members = dbo._query_one(
        "SELECT count(*) AS c FROM (SELECT course_id, user_id FROM t_course_member "
        "GROUP BY course_id, user_id HAVING count(*) > 1)")["c"]
    check("成员表无重复 (course_id,user_id)", dup_members == 0)

    # 加课码不得等于课程主键（对应「不要把主键直接当加入码」）
    pk_as_code = dbo._query_one(
        "SELECT count(*) AS c FROM t_course WHERE join_code = CAST(course_id AS TEXT)")["c"]
    check("join_code 不等于主键", pk_as_code == 0)

    print("\nStep 4: 既有数据是否被改动（核心）")
    for t in PROTECTED_TABLES:
        check(f"{t} 行数不变", before[t] == after_first[t] == after_third[t],
              f"{before[t]} -> {after_first[t]} -> {after_third[t]}")
    doc_paths_after = [r["file_path"] for r in dbo._query(
        "SELECT file_path FROM t_document ORDER BY doc_id")]
    check("t_document.file_path 未被重写（文档未重新关联）", doc_paths_before == doc_paths_after)

    # 三次迁移后 t_course_member 不应增长（证明 INSERT OR IGNORE 真正幂等）。
    # 幂等要证的是「第 2、3 次迁移没有再插入」，故以第 1 次迁移后的行数为基准比较，
    # 而不是跟课程数比——一门课允许有多名教师（协作教师），两者本就不相等。
    # 同时只统计 role='teacher'：成员表里还会有学生的加入/申请记录
    # （以及历史遗留的 status='removed' 行），混进来会误判。
    m1, _ = teacher_member_stats(dbo)
    check("三次迁移后教师成员无重复插入", m1 == teachers_after_first,
          f"第 1 次 {teachers_after_first} 行 -> 第 3 次 {m1} 行")

    print("\nStep 5: 线上库必须原封不动")
    live_members_after = (sd.sql_db._query_one(
        "SELECT count(*) AS c FROM t_course_member")["c"]
        if "t_course_member" in live_tables_now else 0)
    live_courses_after = sd.sql_db._query_one("SELECT count(*) AS c FROM t_course")["c"]
    live_md5_after = md5(live)
    check("线上 app.db MD5 未变", live_md5_before == live_md5_after,
          f"{live_md5_before[:12]}… -> {live_md5_after[:12]}…")
    # 不问「线上库是否已迁移」（应用启动时本来就会迁移），而是证明本脚本没有写线上库：
    # 成员/课程行数与脚本开始前完全一致。
    check("线上库成员行数未被本脚本改动", live_members_before == live_members_after,
          f"{live_members_before} -> {live_members_after}")
    check("线上库课程行数未被本脚本改动", live_courses_before == live_courses_after,
          f"{live_courses_before} -> {live_courses_after}")

    shutil.rmtree(tmp_dir, ignore_errors=True)

    print("\n" + "=" * 64)
    if failures:
        print(f"结果: 失败 {len(failures)} 项")
        for f in failures:
            print("   -", f)
        sys.exit(1)
    print("结果: 全部通过（迁移幂等，既有数据零改动）")
    print("=" * 64)


if __name__ == "__main__":
    main()
