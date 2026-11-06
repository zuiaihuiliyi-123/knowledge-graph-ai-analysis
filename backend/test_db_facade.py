"""Phase 1/4 回归：SQLite 门面契约 + Neo4j 驱动惰性化。

不连数据库、不连 Neo4j、不调外部 API；只做导入与 introspection。
运行：cd backend && python test_db_facade.py
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="kg_db_facade_")
os.environ.setdefault("SQLITE_DB_PATH", os.path.join(_TMP, "app.db"))
os.environ.setdefault("UPLOAD_DIR", os.path.join(_TMP, "uploads"))
os.environ.setdefault("AUTO_BACKUP", "0")
os.environ.setdefault("NEO4J_URI", "bolt://127.0.0.1:7688")

from app.core.sql_database import (  # noqa: E402
    ADMIN_ACTIONS, ARCHIVED_USERNAME_PREFIX, AUTO_GRADE_TYPES, BUSY_TIMEOUT_MS,
    COURSE_GOVERNANCE_STATUS, COURSE_VISIBLE_SQL, DEFAULT_ADMIN_USERNAME,
    DEFAULT_TEACHER_USERNAME, GENDERS, JOIN_MODES, JOURNAL_MODE,
    MANUAL_GRADE_TYPES, SQLDatabase, USER_ROLES, _blob_to_vec, _vec_to_blob,
    course_is_visible, generate_bootstrap_password, sql_db,
)
from app.core.database import db  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [通过] {label}")
    else:
        FAIL += 1
        print(f"  [失败] {label} {detail}")


REPRESENTATIVE_METHODS = """
__init__ _connect _apply_journal_mode pragma_status backup_to auto_backup
init_tables _migrate _migrate_course_center _migrate_question_types
ensure_default_teacher ensure_default_admin
create_user create_user_releasing_inactive get_user_by_username get_user_by_id
create_course list_courses_page update_course delete_course
get_membership upsert_membership list_members create_invite
create_document update_document upsert_document_task delete_document
upsert_learning_record add_favorite list_favorites_by_user_course
upsert_kp_embedding get_embedding_kp_ids delete_embeddings_by_document
create_question get_question set_question_kps list_questions
list_practice_questions add_answer_record grade_answer_record answer_grading_summary
fusion_scope_get insert_fusion_map list_fusion_candidates
list_users_admin list_courses_admin add_audit_log admin_platform_counts
""".split()


def main():
    print("=" * 60)
    print("SQLite 门面契约")
    print("=" * 60)
    for name in REPRESENTATIVE_METHODS:
        check(f"SQLDatabase.{name}", callable(getattr(SQLDatabase, name, None)))
    check("sql_db 是 SQLDatabase 实例", isinstance(sql_db, SQLDatabase))
    check("sql_db.db_path 可读", bool(getattr(sql_db, "db_path", "")))
    check("_execute 可调用", callable(getattr(sql_db, "_execute", None)))
    check("_query 可调用", callable(getattr(sql_db, "_query", None)))
    check("_query_one 可调用", callable(getattr(sql_db, "_query_one", None)))

    print("\n" + "=" * 60)
    print("兼容导出（core.sql_database）")
    print("=" * 60)
    check("USER_ROLES", USER_ROLES == ("admin", "teacher", "student"))
    check("ARCHIVED_USERNAME_PREFIX", ARCHIVED_USERNAME_PREFIX.startswith("__deleted_user_"))
    check("COURSE_VISIBLE_SQL", "status = 1" in COURSE_VISIBLE_SQL)
    check("course_is_visible", callable(course_is_visible))
    check("_vec_to_blob/_blob_to_vec", _blob_to_vec(_vec_to_blob([1.5])) == [1.5])
    check("JOURNAL_MODE/BUSY_TIMEOUT_MS", isinstance(JOURNAL_MODE, str)
          and isinstance(BUSY_TIMEOUT_MS, int))
    check("DEFAULT_* / generate_bootstrap_password",
          DEFAULT_ADMIN_USERNAME and DEFAULT_TEACHER_USERNAME
          and callable(generate_bootstrap_password))
    check("枚举常量", all((ADMIN_ACTIONS, AUTO_GRADE_TYPES, COURSE_GOVERNANCE_STATUS,
                           GENDERS, JOIN_MODES, MANUAL_GRADE_TYPES)))

    print("\n" + "=" * 60)
    print("Neo4j 驱动惰性化")
    print("=" * 60)
    check("导入后未创建驱动", db._driver is None)
    db.close()
    check("close 幂等且不抛错", db._driver is None)

    print("\n" + "=" * 60)
    print(f"结果：通过 {PASS}/{PASS + FAIL}")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
