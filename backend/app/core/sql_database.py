"""SQLite 数据访问兼容入口。

Phase 1 起真实实现位于 app/db/；本模块只做显式 re-export，保证
`from app.core.sql_database import sql_db, ...` 的既有调用点与测试脚本无需改动。
"""
from ..db.bootstrap import (DEFAULT_ADMIN_USERNAME, DEFAULT_TEACHER_USERNAME, LEGACY_WEAK_PASSWORDS, generate_bootstrap_password)
from ..db.connection import (BUSY_TIMEOUT_MS, JOURNAL_MODE, _blob_to_vec, _now, _vec_to_blob)
from ..db.database import SQLDatabase, sql_db
from ..db.schema import (ADMIN_ACTIONS, ANSWER_GRADE_SOURCE, ANSWER_GRADE_STATUS, ARCHIVED_USERNAME_PREFIX, AUTO_GRADE_TYPES, COURSE_GOVERNANCE_STATUS, COURSE_VISIBLE_SQL, DOC_EXTRACT_STATUS, DOC_FILE_TYPES, DOC_PARSE_STATUS, GENDERS, INVITE_STATUS, JOIN_MODES, MANUAL_GRADE_TYPES, MEMBER_JOIN_SOURCE, MEMBER_ROLES, MEMBER_STATUS, QUESTION_SOURCE, QUESTION_TYPES, RECORD_SOURCE, RECORD_STATUS, USER_ACTIVE_STATUS, USER_ROLES, _SCHEMA_SQL, _T_QUESTION_DDL, course_is_visible)

__all__ = [
    "ADMIN_ACTIONS",
    "ANSWER_GRADE_SOURCE",
    "ANSWER_GRADE_STATUS",
    "ARCHIVED_USERNAME_PREFIX",
    "AUTO_GRADE_TYPES",
    "BUSY_TIMEOUT_MS",
    "COURSE_GOVERNANCE_STATUS",
    "COURSE_VISIBLE_SQL",
    "DEFAULT_ADMIN_USERNAME",
    "DEFAULT_TEACHER_USERNAME",
    "DOC_EXTRACT_STATUS",
    "DOC_FILE_TYPES",
    "DOC_PARSE_STATUS",
    "GENDERS",
    "INVITE_STATUS",
    "JOIN_MODES",
    "JOURNAL_MODE",
    "LEGACY_WEAK_PASSWORDS",
    "MANUAL_GRADE_TYPES",
    "MEMBER_JOIN_SOURCE",
    "MEMBER_ROLES",
    "MEMBER_STATUS",
    "QUESTION_SOURCE",
    "QUESTION_TYPES",
    "RECORD_SOURCE",
    "RECORD_STATUS",
    "SQLDatabase",
    "USER_ACTIVE_STATUS",
    "USER_ROLES",
    "_SCHEMA_SQL",
    "_T_QUESTION_DDL",
    "_blob_to_vec",
    "_now",
    "_vec_to_blob",
    "course_is_visible",
    "generate_bootstrap_password",
    "sql_db",
]
