"""
SQLite 关系型数据库访问层（对齐规划文档 4.2 节：t_user / t_course / t_document / t_learning_record）

设计说明：
- 第一阶段用 SQLite（零部署、Python 标准库），第二阶段迁移 MySQL 时仅需替换本模块底层连接实现，
  上层方法签名、表名、字段名全部保持不变，业务代码零改动。
- MySQL -> SQLite 类型映射（SQLite 无 ENUM / UNSIGNED / ON UPDATE 语法，用等价手段保留语义）：
    BIGINT/INT/TINYINT UNSIGNED -> INTEGER
    VARCHAR / CHAR / TEXT / ENUM -> TEXT（ENUM 取值用 CHECK 约束保留）
    DATETIME                     -> TEXT（存 "YYYY-MM-DD HH:MM:SS"，与 MySQL DATETIME 字符串一致）
    AUTO_INCREMENT               -> INTEGER PRIMARY KEY AUTOINCREMENT
    DEFAULT CURRENT_TIMESTAMP    -> DEFAULT (datetime('now','localtime'))
    ON UPDATE CURRENT_TIMESTAMP  -> 应用层在 UPDATE 时显式写入 updated_at（见 _update 相关方法）
"""
import os
import json
import logging
import secrets
import sqlite3
import string
import uuid
from datetime import datetime

from .config import settings
from .security import hash_password, verify_password
from .codes import gen_join_code

_logger = logging.getLogger(__name__)

# 枚举取值（与规划文档表格 8/9/10/11 的 ENUM 定义一致，供应用层校验）
# admin 为管理员端新增角色（平台治理，不属于任何课程的教学角色）。
# 注意：注册接口（api/auth.py）刻意不接受 "admin"，管理员只能由既有管理员在后台指派。
USER_ROLES = ("admin", "teacher", "student")
DOC_FILE_TYPES = ("PDF", "TXT", "DOCX", "MD")
DOC_PARSE_STATUS = ("UPLOADED", "PARSING", "PARSED", "FAILED")
DOC_EXTRACT_STATUS = ("PENDING", "EXTRACTING", "COMPLETED", "FAILED")
RECORD_STATUS = ("MASTERED", "LEARNING", "RECOMMENDED")
RECORD_SOURCE = ("MANUAL", "SYSTEM")

# 课程中心枚举（应用层校验；表内以 CHECK 约束保留同一取值集合）
MEMBER_ROLES = ("teacher", "student")
MEMBER_STATUS = ("pending", "approved", "rejected", "removed")
MEMBER_JOIN_SOURCE = ("create", "code", "invite", "apply", "import")
JOIN_MODES = ("auto", "approval", "closed")          # 直接加入 / 审核后加入 / 关闭加入
INVITE_STATUS = ("active", "used", "revoked")
GENDERS = ("male", "female", "other", "unknown")
# 题库枚举（Scope B：三型客观题自动判分 + 两型主观题由教师批改）
QUESTION_TYPES = ("SINGLE", "MULTI", "JUDGE", "FILL", "ESSAY")
# 判分边界：客观题提交即判分（grade_source=AUTO）；主观题提交只落库、由教师批改（TEACHER）
AUTO_GRADE_TYPES = ("SINGLE", "MULTI", "JUDGE")
MANUAL_GRADE_TYPES = ("FILL", "ESSAY")
# 答题记录批改状态：PENDING=待教师批改（不判分），GRADED=已判分（自动或人工）
ANSWER_GRADE_STATUS = ("PENDING", "GRADED")
QUESTION_SOURCE = ("MANUAL", "AI", "IMPORT")
ANSWER_GRADE_SOURCE = ("AUTO", "LLM", "TEACHER")

# 管理员端枚举
# 课程治理状态：normal=正常 / hidden=已下架（学生不可见，课程数据保留）/ archived=已归档（冻结）
COURSE_GOVERNANCE_STATUS = ("normal", "hidden", "archived")

# 「课程对学生可见（可发现 / 可申请 / 可用加课码加入）」的**唯一**定义。
#
# 课程状态是两个正交维度，必须同时成立才算可见：
#   status = 1                     业务状态：教师或管理员显式开放（治理动作不写这一列）
#   governance_status = 'normal'   治理状态：未被平台下架 / 归档
# 只看 status，「下架」对发现页完全无效；只看 governance_status，
# 「被教师关闭的课程」仍会出现在发现页与加课码入口。
#
# 两处消费点必须保持一致，改这里时同步 core/permissions.py 的 course_is_visible：
#   1. SQL：admin_platform_counts 的公开课程数
#   2. Python：Permissions._relation 的 PUBLIC 判定、CourseService.list_discover、
#      MemberService.apply_to_course / join_by_code
COURSE_VISIBLE_SQL = "status = 1 AND COALESCE(governance_status, 'normal') = 'normal'"


def course_is_visible(course: dict) -> bool:
    """COURSE_VISIBLE_SQL 的行级等价实现（Python 侧过滤用）。

    两个维度同时成立才算可见，判定语义与上面那段 SQL 完全一致。
    """
    if not course:
        return False
    if course.get("status") != 1:
        return False
    return (course.get("governance_status") or "normal") == "normal"
# 用户账号状态筛选值（映射 t_user.is_active）
USER_ACTIVE_STATUS = ("active", "disabled")
# 审计日志动作名（与 api/admin.py 的调用点一一对应，便于筛选与统计）
ADMIN_ACTIONS = (
    "admin.login",             # 管理员登录
    "user.update",             # 编辑用户资料
    "user.enable",             # 启用账号
    "user.disable",            # 禁用账号
    "user.role_change",        # 修改角色
    "user.reset_password",     # 重置密码
    "user.delete",             # 删除用户
    "course.hide",             # 平台下架（改治理状态，不动业务状态）
    "course.restore",          # 撤销下架
    "course.archive",          # 归档课程
    "course.unarchive",        # 取消归档
    "course.close",            # 管理员代教师关闭课程（只改业务状态）
    "course.reopen",           # 撤销关闭
    "course.transfer",         # 转移课程负责人
    "course.delete",           # 删除课程
    "resource.delete",         # 删除文档资源
    "settings.update",         # 修改平台设置
)

# 引导账号（首次启动自动创建）的凭据策略。
#
# **代码里不再内置任何默认密码。** 历史版本曾把 admin123 写成 DEFAULT_ADMIN_PASSWORD 的兜底值，
# 那等于给每份部署装了一个公开口令：任何人 clone 仓库就知道管理员密码。
# 现在的规则（ensure_default_admin 执行）：
#   1. 环境变量 DEFAULT_ADMIN_PASSWORD 有值  -> 用它（部署方自己负责保密），不强制改密；
#   2. 未配置                              -> 现场生成随机强密码，**只在启动日志里打印一次**，
#      并给该账号打上 must_change_password=1（首次登录必须改密，日志泄漏也只有一次性价值）。
#
# 刻意**不**复用已有的 "admin" 用户名：那个账号是 role=teacher 的演示教师，且是课程 5 的
# 创建者，把它改成管理员会让「教师登录」与权限回归测试全线受影响。
DEFAULT_ADMIN_USERNAME = os.getenv("DEFAULT_ADMIN_USERNAME", "sysadmin")

# 演示教师账号的初始密码。同样不内置默认值，未配置时随机生成并打印一次。
# 注意：仅在**该账号尚不存在**时生效；已存在的账号密码不会被覆盖。
DEFAULT_TEACHER_USERNAME = "admin"

# 历史版本内置过的引导密码。启动时若发现引导账号仍能用其中任何一个登录，
# 打一条 WARNING 提醒轮换——这是「已知弱口令清单」，不是可用的默认值。
LEGACY_WEAK_PASSWORDS = ("admin123",)


def generate_bootstrap_password(length: int = 16) -> str:
    """生成随机引导密码（去易混字符，便于从日志里准确抄录）"""
    alphabet = string.ascii_letters.replace("l", "").replace("I", "").replace("O", "") + "23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _now() -> str:
    """返回当前本地时间字符串（MySQL DATETIME 兼容格式）"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# t_question 建表 DDL（抽成模块常量：Scope B 迁移需用它重建新表——SQLite 无法修改 CHECK 约束——
# 抽出来可避免「建表 DDL」与「迁移 DDL」两份定义各自漂移）。
_T_QUESTION_DDL = """
CREATE TABLE IF NOT EXISTS t_question (
    question_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id    INTEGER NOT NULL,
    document_id  INTEGER,
    kp_id        TEXT,
    q_type       TEXT NOT NULL CHECK (q_type IN ('SINGLE', 'MULTI', 'JUDGE', 'FILL', 'ESSAY')),
    stem         TEXT NOT NULL,
    options      TEXT,
    answer       TEXT NOT NULL,
    analysis     TEXT,
    difficulty   INTEGER NOT NULL DEFAULT 3 CHECK (difficulty BETWEEN 1 AND 5),
    source       TEXT NOT NULL DEFAULT 'MANUAL' CHECK (source IN ('MANUAL', 'AI', 'IMPORT')),
    created_by   INTEGER NOT NULL,
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    -- Scope D：导入批次与暂存状态（READY / NEEDS_REVIEW / ANSWER_MISSING）
    -- 旧库由 _migrate_question_bank 用 ADD COLUMN 补齐（SQLite 的 ADD COLUMN 不能带 CHECK，应用层校验）
    import_batch_id TEXT,
    import_status   TEXT DEFAULT 'READY',
    created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (course_id) REFERENCES t_course(course_id),
    FOREIGN KEY (created_by) REFERENCES t_user(user_id)
)
"""

# 建表 DDL（含索引、CHECK 约束、外键）。顺序敏感：先建被引用的父表。
_SCHEMA_SQL = [
    # 4.2.1 用户表
    # role 的 CHECK 含 'admin'（管理员端）。旧库的 CHECK 只有 teacher/student，
    # SQLite 无法修改 CHECK，由 _migrate_admin_role() 重建表补齐。
    """
    CREATE TABLE IF NOT EXISTS t_user (
        user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
        username      TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role          TEXT NOT NULL DEFAULT 'student'
                      CHECK (role IN ('admin', 'teacher', 'student')),
        display_name  TEXT,
        email         TEXT,
        is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
        must_change_password INTEGER NOT NULL DEFAULT 0
                      CHECK (must_change_password IN (0, 1)),
        created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        updated_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_role ON t_user(role);",

    # 4.2.2 课程表
    """
    CREATE TABLE IF NOT EXISTS t_course (
        course_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        course_code TEXT UNIQUE,
        course_name TEXT NOT NULL,
        description TEXT,
        teacher_id  INTEGER NOT NULL,
        status      INTEGER NOT NULL DEFAULT 1 CHECK (status IN (0, 1)),
        created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        FOREIGN KEY (teacher_id) REFERENCES t_user(user_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_teacher_id ON t_course(teacher_id);",

    # 4.2.3 文档表
    """
    CREATE TABLE IF NOT EXISTS t_document (
        doc_id            INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id         INTEGER NOT NULL,
        uploader_id       INTEGER NOT NULL,
        file_name         TEXT NOT NULL,
        file_type         TEXT NOT NULL CHECK (file_type IN ('PDF', 'TXT', 'DOCX', 'MD')),
        file_size         INTEGER NOT NULL,
        file_sha256       TEXT,
        file_path         TEXT,
        parse_status      TEXT NOT NULL DEFAULT 'UPLOADED'
                          CHECK (parse_status IN ('UPLOADED', 'PARSING', 'PARSED', 'FAILED')),
        extract_status    TEXT NOT NULL DEFAULT 'PENDING'
                          CHECK (extract_status IN ('PENDING', 'EXTRACTING', 'COMPLETED', 'FAILED')),
        error_message     TEXT,
        chunk_count       INTEGER NOT NULL DEFAULT 0,
        entity_count      INTEGER NOT NULL DEFAULT 0,
        relation_count    INTEGER NOT NULL DEFAULT 0,
        vector_collection TEXT,
        created_at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        updated_at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        FOREIGN KEY (course_id) REFERENCES t_course(course_id),
        FOREIGN KEY (uploader_id) REFERENCES t_user(user_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_course_id ON t_document(course_id);",
    "CREATE INDEX IF NOT EXISTS idx_uploader_id ON t_document(uploader_id);",
    "CREATE INDEX IF NOT EXISTS idx_parse_status ON t_document(parse_status);",
    "CREATE INDEX IF NOT EXISTS idx_extract_status ON t_document(extract_status);",

    # 4.2.4 学习记录表（kp_id 为逻辑外键，关联 Neo4j KnowledgePoint，不建物理外键）
    """
    CREATE TABLE IF NOT EXISTS t_learning_record (
        record_id       INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id         INTEGER NOT NULL,
        course_id       INTEGER NOT NULL,
        document_id     INTEGER,
        kp_id           TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'MASTERED'
                        CHECK (status IN ('MASTERED', 'LEARNING', 'RECOMMENDED')),
        mastery_level   INTEGER NOT NULL DEFAULT 100 CHECK (mastery_level BETWEEN 0 AND 100),
        source          TEXT NOT NULL DEFAULT 'MANUAL' CHECK (source IN ('MANUAL', 'SYSTEM')),
        last_learned_at TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        FOREIGN KEY (user_id) REFERENCES t_user(user_id),
        FOREIGN KEY (course_id) REFERENCES t_course(course_id),
        UNIQUE (user_id, course_id, kp_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_user_course ON t_learning_record(user_id, course_id);",
    "CREATE INDEX IF NOT EXISTS idx_course_kp ON t_learning_record(course_id, kp_id);",

    # 4.2.5 知识点向量表（RAG 向量检索；embedding 存 JSON 数组文本）
    """
    CREATE TABLE IF NOT EXISTS t_kp_embedding (
        course_id    INTEGER NOT NULL,
        document_id  INTEGER,
        kp_id        TEXT NOT NULL,
        embedding  TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        PRIMARY KEY (course_id, kp_id)
    )
    """,

    # 4.2.6 学生收藏表（收藏 = 学生个人知识点书签，独立于学习状态；kp_id 为逻辑外键指向 Neo4j）
    """
    CREATE TABLE IF NOT EXISTS t_student_favorite (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        course_id   INTEGER NOT NULL,
        document_id INTEGER,
        kp_id       TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        FOREIGN KEY (user_id) REFERENCES t_user(user_id),
        FOREIGN KEY (course_id) REFERENCES t_course(course_id),
        UNIQUE (user_id, course_id, kp_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_fav_user_course ON t_student_favorite(user_id, course_id);",

    # ---------- 课程中心改造（课程成员 / 邀请 / 用户资料） ----------
    # 说明：t_course 的新增列（join_code / join_mode / organization / category /
    # cover / is_public）刻意不写在上面的 CREATE TABLE 里，而是统一由 _migrate()
    # 补列，避免「新建库」与「迁移库」的表结构分叉。

    # 课程成员表（用户 ↔ 课程 多对多，替代「学生硬编码在课程表」的做法）
    # UNIQUE(course_id, user_id) 保证同一用户在同一课程至多一条关系，重复加入不会产生脏数据。
    """
    CREATE TABLE IF NOT EXISTS t_course_member (
        member_id      INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id      INTEGER NOT NULL,
        user_id        INTEGER NOT NULL,
        role           TEXT NOT NULL DEFAULT 'student'
                       CHECK (role IN ('teacher', 'student')),
        status         TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'approved', 'rejected', 'removed')),
        join_source    TEXT NOT NULL DEFAULT 'code'
                       CHECK (join_source IN ('create', 'code', 'invite', 'apply', 'import')),
        applied_reason TEXT,
        reviewed_by    INTEGER,
        reviewed_at    TEXT,
        review_comment TEXT,
        joined_at      TEXT,
        created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        updated_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        FOREIGN KEY (course_id) REFERENCES t_course(course_id),
        FOREIGN KEY (user_id) REFERENCES t_user(user_id),
        UNIQUE (course_id, user_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cm_course_status ON t_course_member(course_id, status);",
    "CREATE INDEX IF NOT EXISTS idx_cm_user_status ON t_course_member(user_id, status);",
    "CREATE INDEX IF NOT EXISTS idx_cm_course_role ON t_course_member(course_id, role);",

    # 课程邀请令牌（单次使用；token 为随机串，与 course_id / user_id 无推导关系）
    """
    CREATE TABLE IF NOT EXISTS t_course_invite (
        invite_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id   INTEGER NOT NULL,
        token       TEXT NOT NULL UNIQUE,
        invited_by  INTEGER NOT NULL,
        role        TEXT NOT NULL DEFAULT 'student'
                    CHECK (role IN ('teacher', 'student')),
        status      TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'used', 'revoked')),
        used_by     INTEGER,
        used_at     TEXT,
        expires_at  TEXT,
        created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        FOREIGN KEY (course_id) REFERENCES t_course(course_id),
        FOREIGN KEY (invited_by) REFERENCES t_user(user_id),
        FOREIGN KEY (used_by) REFERENCES t_user(user_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_invite_course ON t_course_invite(course_id, status);",

    # 用户资料表（1:1，主键即 user_id；字段全部可空，与认证列完全分离）
    # 只读不写 t_user 的 username / password_hash / role，认证逻辑零影响。
    """
    CREATE TABLE IF NOT EXISTS t_user_profile (
        user_id       INTEGER PRIMARY KEY,
        avatar_url    TEXT,
        real_name     TEXT,
        nickname      TEXT,
        gender        TEXT CHECK (gender IN ('male', 'female', 'other', 'unknown')),
        school        TEXT,
        college       TEXT,
        bio           TEXT,
        student_no    TEXT,
        major         TEXT,
        grade         TEXT,
        class_name    TEXT,
        teacher_no    TEXT,
        title         TEXT,
        research_area TEXT,
        created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        updated_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        FOREIGN KEY (user_id) REFERENCES t_user(user_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_profile_student_no ON t_user_profile(student_no);",
    "CREATE INDEX IF NOT EXISTS idx_profile_teacher_no ON t_user_profile(teacher_no);",

    # ---------- 题库（合作者 PR #3：教师出题 / 学生练习） ----------
    # 4.2.7 题目表（题库唯一事实来源；kp_id 为逻辑外键 → Neo4j KnowledgePoint）
    # options / answer 用 JSON 文本存储：题型差异大（选择/判断/填空/解答），拆表会产生大量空列；
    # 与 t_kp_embedding.embedding 存 JSON 同一先例，迁 MySQL 时 TEXT/JSON 均可，业务代码零改动。
    _T_QUESTION_DDL,
    "CREATE INDEX IF NOT EXISTS idx_q_course_doc ON t_question(course_id, document_id);",
    "CREATE INDEX IF NOT EXISTS idx_q_kp ON t_question(kp_id);",
    "CREATE INDEX IF NOT EXISTS idx_q_type ON t_question(course_id, q_type);",

    # 4.2.8 学生答题记录（错题本 + 正确率统计的唯一来源）
    # 追加式（不建 UNIQUE）：同一题可多次作答，错题本取「每题最近一次」；
    # 这样既能统计正确率趋势，又不会覆盖历史作答。
    """
    CREATE TABLE IF NOT EXISTS t_answer_record (
        record_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL,
        course_id    INTEGER NOT NULL,
        document_id  INTEGER,
        question_id  INTEGER NOT NULL,
        user_answer  TEXT,
        is_correct   INTEGER NOT NULL DEFAULT 0 CHECK (is_correct IN (0, 1)),
        score        REAL NOT NULL DEFAULT 0,
        grade_source TEXT NOT NULL DEFAULT 'AUTO' CHECK (grade_source IN ('AUTO', 'LLM', 'TEACHER')),
        -- Scope B：主观题提交即 PENDING（不判分），教师批改后就地更新为 GRADED；
        -- 默认值取 GRADED，保证历史记录与三类客观题行为完全不变（回归零差异的关键）
        grade_status TEXT NOT NULL DEFAULT 'GRADED' CHECK (grade_status IN ('PENDING', 'GRADED')),
        graded_by    INTEGER,   -- 批改教师 user_id（逻辑外键，不设 FK：教师注销不应破坏答题记录）
        graded_at    TEXT,
        comment      TEXT,      -- 教师评语
        answered_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        FOREIGN KEY (user_id) REFERENCES t_user(user_id),
        FOREIGN KEY (course_id) REFERENCES t_course(course_id),
        FOREIGN KEY (question_id) REFERENCES t_question(question_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ans_user_q ON t_answer_record(user_id, question_id);",
    "CREATE INDEX IF NOT EXISTS idx_ans_user_c ON t_answer_record(user_id, course_id, document_id);",

    # 4.2.9 学生题目收藏（独立于 t_student_favorite 的知识点收藏：
    # 后者 kp_id NOT NULL 且语义为「知识点书签」，混用会污染两侧统计口径）
    """
    CREATE TABLE IF NOT EXISTS t_question_favorite (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        course_id   INTEGER NOT NULL,
        question_id INTEGER NOT NULL,
        created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        UNIQUE (user_id, question_id),
        FOREIGN KEY (user_id) REFERENCES t_user(user_id),
        FOREIGN KEY (course_id) REFERENCES t_course(course_id),
        FOREIGN KEY (question_id) REFERENCES t_question(question_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_qfav_user_course ON t_question_favorite(user_id, course_id);",

    # ---------- Scope C：试题知识点自动标注（题目向量 + 知识点文本缓存） ----------
    # 4.2.13 题目向量表（题干+选项的 embedding；与 t_kp_embedding 同构：向量存 JSON 文本）
    # text_hash 用于判断题面是否变更（变了才需要重算向量），避免每次标注都调 embedding。
    """
    CREATE TABLE IF NOT EXISTS t_question_embedding (
        question_id INTEGER PRIMARY KEY,
        course_id   INTEGER NOT NULL,
        document_id INTEGER,
        text_hash   TEXT NOT NULL,
        embedding   TEXT NOT NULL,
        updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_q_emb_scope ON t_question_embedding(course_id, document_id);",

    # 4.2.14 知识点文本缓存（图谱不可用时的降级数据源：名称/类别/描述）
    # 每次从 Neo4j 成功取到知识点清单后覆盖写入，使「字面匹配」路在无图库时仍可用。
    # 主键与 t_kp_embedding 保持同一先例：(course_id, kp_id)；document_id 为普通列（避免 NULL 进主键）。
    """
    CREATE TABLE IF NOT EXISTS t_kp_text (
        course_id   INTEGER NOT NULL,
        kp_id       TEXT NOT NULL,
        document_id INTEGER,
        name        TEXT NOT NULL,
        category    TEXT,
        description TEXT,
        updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        PRIMARY KEY (course_id, kp_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_kp_text_scope ON t_kp_text(course_id, document_id);",

    # ---------- Scope D：试题文档导入（批次 + 暂存题） ----------
    # 4.2.15 导入批次表：一次"从文档导入题目"的记录（预览→提交→复核都挂在批次上）
    """
    CREATE TABLE IF NOT EXISTS t_question_import_batch (
        batch_id    TEXT PRIMARY KEY,
        course_id   INTEGER NOT NULL,
        document_id INTEGER,
        file_name   TEXT,
        source      TEXT NOT NULL DEFAULT 'RULE' CHECK (source IN ('RULE', 'LLM', 'MIXED')),
        total       INTEGER NOT NULL DEFAULT 0,
        imported    INTEGER NOT NULL DEFAULT 0,
        needs_review INTEGER NOT NULL DEFAULT 0,
        status      TEXT NOT NULL DEFAULT 'PREVIEW' CHECK (status IN ('PREVIEW', 'COMMITTED')),
        created_by  INTEGER,
        created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        meta        TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_import_batch_course ON t_question_import_batch(course_id, document_id);",

    # ---------- 管理员端：审计日志 ----------
    # 4.2.16 管理员操作审计（管理员端新增）
    # 只追加、不修改：管理员的关键操作一律先写库再返回，日志本身不提供删除接口。
    # operator_name 冗余存一份用户名：管理员账号后续被改名/删除后，历史日志仍可读。
    # operator_role 记录「操作发生时的角色」，便于追溯角色变更前后的行为。
    """
    CREATE TABLE IF NOT EXISTS t_admin_audit_log (
        log_id        INTEGER PRIMARY KEY AUTOINCREMENT,
        operator_id   INTEGER,
        operator_name TEXT,
        operator_role TEXT,
        action        TEXT NOT NULL,
        target_type   TEXT,
        target_id     TEXT,
        result        TEXT NOT NULL DEFAULT 'success'
                      CHECK (result IN ('success', 'failure')),
        detail        TEXT,
        ip            TEXT,
        user_agent    TEXT,
        created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_created ON t_admin_audit_log(created_at);",
    "CREATE INDEX IF NOT EXISTS idx_audit_operator ON t_admin_audit_log(operator_id);",
    "CREATE INDEX IF NOT EXISTS idx_audit_action ON t_admin_audit_log(action);",
    "CREATE INDEX IF NOT EXISTS idx_audit_target ON t_admin_audit_log(target_type, target_id);",
]


class SQLDatabase:
    """SQLite 数据库管理类（第一阶段；第二阶段由 MySQL 实现替换底层连接）"""

    def __init__(self):
        self.db_path = settings.SQLITE_DB_PATH
        # 确保数据库文件所在目录存在
        parent = os.path.dirname(os.path.abspath(self.db_path))
        os.makedirs(parent, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        """新建连接：开启外键约束 + 行字典工厂"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_tables(self):
        """初始化表结构（幂等，可重复调用）"""
        with self._connect() as conn:
            for stmt in _SCHEMA_SQL:
                conn.execute(stmt)
            conn.commit()
        self._migrate()
        # Scope B 题型扩展：t_question 的 q_type CHECK 需重建表（SQLite 不能改 CHECK）。
        # 该步骤要用「关闭外键的独立连接」执行，故放在 _migrate（共用连接）之外。
        self._migrate_question_types()
        # 管理员端：t_user.role 的 CHECK 需加入 'admin'，同样是「重建表」类迁移。
        self._migrate_admin_role()

    def _migrate(self):
        """幂等迁移：为旧库补齐 document_id 列并回填（文档作用域改造）。

        第一阶段历史数据「一个课程 == 一个文档」，故每个 course_id 至多映射一个 doc_id，
        可安全回填；仅回填 document_id IS NULL 的行，重复执行无副作用。
        新库由 _SCHEMA_SQL 直接建出含 document_id 的表，本方法对空表无影响。

        说明：document_id 列故意不设 NOT NULL，以保证「改列阶段」旧写入路径
        （尚未传 document_id）不报错；Phase 5/8 后所有写入都会显式提供 document_id。
        """
        doc_scoped_tables = (
            ("t_learning_record", "course_id"),
            ("t_student_favorite", "course_id"),
            ("t_kp_embedding", "course_id"),
        )
        with self._connect() as conn:
            for table, fk in doc_scoped_tables:
                columns = {row["name"] for row in
                           conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if "document_id" not in columns:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN document_id INTEGER")
                conn.execute(
                    f"UPDATE {table} SET document_id = ("
                    f"SELECT d.doc_id FROM t_document d WHERE d.course_id = {table}.{fk} LIMIT 1"
                    f") WHERE document_id IS NULL"
                )
            # 向量取数一律按 (course_id, document_id) 过滤，但 t_kp_embedding 的主键是
            # (course_id, kp_id)，document_id 上没有索引 → 只能走主键前缀命中 course_id，
            # 再逐行过滤 document_id（course 5 有 117 行，其中目标文档只占 69 行，多扫的
            # 48 行全是待反序列化的向量文本）。补一个作用域索引消除这部分扫描。
            # 必须建在 _migrate 而非 _SCHEMA_SQL：旧库的 document_id 列由上面这个循环
            # ALTER 补齐，而 _SCHEMA_SQL 先于 _migrate 执行，索引会因列不存在而报错。
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_kp_emb_scope "
                "ON t_kp_embedding(course_id, document_id)"
            )
            self._migrate_question_bank(conn)
            self._migrate_course_center(conn)
            self._migrate_admin(conn)
            self._migrate_admin_user_columns(conn)
            conn.commit()

    # t_course 在课程中心改造中新增的列（全部可空或带默认值，历史行不受影响）
    _COURSE_NEW_COLUMNS = (
        ("join_code", "TEXT"),
        ("join_mode", "TEXT NOT NULL DEFAULT 'approval'"),
        ("organization", "TEXT"),
        ("category", "TEXT"),
        ("cover", "TEXT"),
        ("is_public", "INTEGER NOT NULL DEFAULT 1"),
    )

    def _migrate_course_center(self, conn: sqlite3.Connection):
        """幂等迁移：课程中心改造（t_course 补列 + 加课码 + 老课程教师成员回填）。

        四步全部可重复执行，重启任意次结果一致：
        1) PRAGMA table_info 守卫补列——SQLite 无 ADD COLUMN IF NOT EXISTS；
           注意 ALTER TABLE ADD COLUMN 不允许带 UNIQUE / PRIMARY KEY，因此
           join_code 只加成普通可空列，唯一性交给下面的独立唯一索引。
        2) 唯一索引（SQLite 唯一索引允许多个 NULL，可与历史 NULL 行共存）。
        3) 仅为 join_code IS NULL 的行回填加课码——教师已发出的码不会被重新生成。
        4) 每个已存在课程的 teacher_id 自动成为 role=teacher/status=approved 成员，
           靠 UNIQUE(course_id, user_id) + INSERT OR IGNORE 保证幂等，且不会覆盖
           教师后续手动改动过的成员行。

        历史课程不猜测学生成员关系：学生需通过加课码 / 邀请 / 申请重新加入。
        """
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(t_course)").fetchall()}
        for name, decl in self._COURSE_NEW_COLUMNS:
            if name not in cols:
                conn.execute(f"ALTER TABLE t_course ADD COLUMN {name} {decl}")

        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_course_join_code ON t_course(join_code)"
        )

        pending = conn.execute(
            "SELECT course_id FROM t_course WHERE join_code IS NULL"
        ).fetchall()
        for row in pending:
            for _ in range(20):  # 冲突重试；8 位随机码在实际规模下几乎不会冲突
                code = gen_join_code()
                exists = conn.execute(
                    "SELECT 1 FROM t_course WHERE join_code = ?", (code,)
                ).fetchone()
                if not exists:
                    conn.execute(
                        "UPDATE t_course SET join_code = ? "
                        "WHERE course_id = ? AND join_code IS NULL",
                        (code, row["course_id"]),
                    )
                    break

        conn.execute(
            """
            INSERT OR IGNORE INTO t_course_member
                (course_id, user_id, role, status, join_source, joined_at, created_at, updated_at)
            SELECT c.course_id, c.teacher_id, 'teacher', 'approved', 'create',
                   COALESCE(c.created_at, datetime('now', 'localtime')),
                   COALESCE(c.created_at, datetime('now', 'localtime')),
                   datetime('now', 'localtime')
            FROM t_course c
            WHERE c.teacher_id IS NOT NULL
            """
        )

    # t_answer_record 在 Scope B（主观题）新增的列（全部可空或带默认值，历史行不受影响）
    _ANSWER_NEW_COLUMNS = (
        ("grade_status", "TEXT NOT NULL DEFAULT 'GRADED'"),
        ("graded_by", "INTEGER"),
        ("graded_at", "TEXT"),
        ("comment", "TEXT"),
    )

    # t_question 在 Scope D（试题文档导入）新增的列
    _QUESTION_NEW_COLUMNS = (
        ("import_batch_id", "TEXT"),
        ("import_status", "TEXT DEFAULT 'READY'"),
    )

    def _migrate_question_bank(self, conn: sqlite3.Connection):
        """幂等迁移：题库 Scope B —— 为 t_answer_record 补「批改」相关列。

        为什么默认值是 'GRADED'：历史记录（已判分的客观题）必须保持原口径，
        只有主观题提交时才会显式写 'PENDING'；否则正确率 / 错题本 / 掌握度全线漂移。

        注意：SQLite 的 ALTER TABLE ADD COLUMN 不允许带 CHECK 约束，旧库补出来的
        grade_status 没有 CHECK（新库由 _SCHEMA_SQL 建出带 CHECK 的列），
        取值合法性由应用层 ANSWER_GRADE_STATUS 白名单兜底。
        """
        cols = {row["name"] for row in
                conn.execute("PRAGMA table_info(t_answer_record)").fetchall()}
        for name, decl in self._ANSWER_NEW_COLUMNS:
            if name not in cols:
                conn.execute(f"ALTER TABLE t_answer_record ADD COLUMN {name} {decl}")

        # Scope D：t_question 的导入批次/暂存状态列（先于 _migrate_question_types 执行，
        # 这样重建表时能把这些列一起拷过去，见下面的动态列清单）
        qcols = {row["name"] for row in conn.execute("PRAGMA table_info(t_question)").fetchall()}
        for name, decl in self._QUESTION_NEW_COLUMNS:
            if name not in qcols:
                conn.execute(f"ALTER TABLE t_question ADD COLUMN {name} {decl}")

    # 重建 t_question 时保留的列（显式列名 INSERT…SELECT，避免历史列序差异）
    _QUESTION_COLUMNS = (
        "question_id", "course_id", "document_id", "kp_id", "q_type", "stem",
        "options", "answer", "analysis", "difficulty", "source", "created_by",
        "is_active", "created_at", "updated_at",
    )

    def _migrate_question_types(self) -> bool:
        """幂等迁移：把 t_question.q_type 的 CHECK 从 3 类扩到 5 类（+FILL/ESSAY）。

        为什么必须重建表：SQLite 不支持修改 CHECK 约束。
        为什么用独立连接且关闭外键：t_question 是 t_answer_record 与 t_question_favorite
        的父表（两个外键指向它），而 _connect() 默认 `PRAGMA foreign_keys = ON`，
        直接 DROP 父表会触发外键检查而失败；另外 `PRAGMA foreign_keys` 在事务内是空操作，
        必须在 BEGIN 之前设置，故这里自建连接并显式控制事务。

        流程（对齐 SQLite 官方 rebuild-table 建议）：
          关外键 → BEGIN → 清理残留新表 → 建新表 → 拷数据（行数校验）→ 删旧表 → 改名 →
          重建 3 个索引 → 修正 AUTOINCREMENT 续号 → foreign_key_check → COMMIT。
        任一步失败即 ROLLBACK，原表原样保留。

        返回 True = 本次执行了重建；False = 已是最新（幂等跳过）。
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 't_question'"
            ).fetchone()
        if row is None or not row["sql"]:
            return False                     # 空库：首次由 _SCHEMA_SQL 直接建出 5 类 CHECK
        if "'FILL'" in row["sql"] or '"FILL"' in row["sql"]:
            return False                     # 已迁移（CHECK 文本里能看到 FILL）

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.isolation_level = None          # 显式控制事务，保证 PRAGMA 生效时机可控
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN IMMEDIATE")
            # 先记下旧续号：重建后必须保留，见下方「续号取 max(旧续号, 最大 id)」
            old_seq_row = conn.execute(
                "SELECT seq FROM sqlite_sequence WHERE name = 't_question'"
            ).fetchone()
            old_seq = old_seq_row["seq"] if old_seq_row else 0
            conn.execute("DROP TABLE IF EXISTS t_question_new")
            conn.execute(_T_QUESTION_DDL.replace(
                "CREATE TABLE IF NOT EXISTS t_question (",
                "CREATE TABLE t_question_new (",
            ))
            # 动态列清单：只拷贝「当前表实际存在的列」，使重建对列增删保持健壮
            # （_migrate_question_bank 已先补 Scope D 的新列，故这里能一起拷过去）
            existing = {r["name"] for r in
                        conn.execute("PRAGMA table_info(t_question)").fetchall()}
            wanted = list(self._QUESTION_COLUMNS) + [n for n, _ in self._QUESTION_NEW_COLUMNS]
            cols = ", ".join(c for c in wanted if c in existing)
            conn.execute(f"INSERT INTO t_question_new ({cols}) SELECT {cols} FROM t_question")
            old_cnt = conn.execute("SELECT count(*) AS c FROM t_question").fetchone()["c"]
            new_cnt = conn.execute("SELECT count(*) AS c FROM t_question_new").fetchone()["c"]
            if old_cnt != new_cnt:
                raise RuntimeError(
                    f"t_question 重建行数不一致：旧 {old_cnt} 行 / 新 {new_cnt} 行"
                )
            conn.execute("DROP TABLE t_question")
            conn.execute("ALTER TABLE t_question_new RENAME TO t_question")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_q_course_doc ON t_question(course_id, document_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_q_kp ON t_question(kp_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_q_type ON t_question(course_id, q_type)")
            # AUTOINCREMENT 续号：必须取 max(旧续号, 当前最大 id)。
            # 单取 max(question_id) 是错的——历史上有物理删除过题目时，旧续号会更大，
            # 那样新题就会复用「已删除的 question_id」，历史遗留引用会静默串到新题上。
            max_id = conn.execute(
                "SELECT COALESCE(MAX(question_id), 0) AS m FROM t_question"
            ).fetchone()["m"]
            next_seq = old_seq if old_seq >= max_id else max_id
            seq = conn.execute(
                "SELECT seq FROM sqlite_sequence WHERE name = 't_question'"
            ).fetchone()
            if seq is None:
                conn.execute(
                    "INSERT INTO sqlite_sequence (name, seq) VALUES ('t_question', ?)",
                    (next_seq,),
                )
            elif seq["seq"] != next_seq:
                conn.execute(
                    "UPDATE sqlite_sequence SET seq = ? WHERE name = 't_question'",
                    (next_seq,),
                )
            issues = conn.execute("PRAGMA foreign_key_check").fetchall()
            if issues:
                raise RuntimeError(f"外键校验失败，已回滚：{[tuple(r) for r in issues]}")
            conn.execute("COMMIT")
            return True
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    # 重建 t_user 时保留的列（显式列名 INSERT…SELECT，避免历史列序差异）
    _USER_COLUMNS = (
        "user_id", "username", "password_hash", "role", "display_name",
        "email", "is_active", "must_change_password", "created_at", "updated_at",
    )

    def _migrate_admin_user_columns(self, conn: sqlite3.Connection):
        """幂等迁移：t_user 补 must_change_password 列（管理员端强制改密）。

        历史行取默认值 0 —— 既有账号（包括已用过一段时间的 sysadmin）不会被强制改密，
        避免一次升级把所有在线用户挡在改密页上。该标记只对**新引导创建**的账号
        以及被管理员重置过密码的账号置 1。

        用 PRAGMA table_info 守卫而非 ALTER ... IF NOT EXISTS：SQLite 没有后者。
        """
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(t_user)").fetchall()}
        if "must_change_password" not in cols:
            conn.execute(
                "ALTER TABLE t_user ADD COLUMN must_change_password "
                "INTEGER NOT NULL DEFAULT 0"
            )

    def _migrate_admin_role(self) -> bool:
        """幂等迁移：把 t_user.role 的 CHECK 从 teacher/student 扩到含 admin。

        为什么必须重建表：SQLite 不支持修改 CHECK 约束（与 _migrate_question_types 同理）。
        为什么用独立连接且关闭外键：t_user 是 t_course / t_document / t_learning_record /
        t_course_member / t_course_invite / t_user_profile / t_question / t_answer_record 等
        多张表的父表，_connect() 默认开外键，直接 DROP 父表会触发外键检查而失败；
        且 `PRAGMA foreign_keys` 在事务内是空操作，必须在 BEGIN 之前设置。

        流程与 _migrate_question_types 完全一致：关外键 → BEGIN → 建新表 → 拷数据（行数校验）
        → 删旧表 → 改名 → 重建索引 → 修正 AUTOINCREMENT 续号 → foreign_key_check → COMMIT。
        任一步失败即 ROLLBACK，原表原样保留（旧库不会被改坏）。

        返回 True = 本次执行了重建；False = 已是最新（幂等跳过）。
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 't_user'"
            ).fetchone()
        if row is None or not row["sql"]:
            return False                     # 空库：首次由 _SCHEMA_SQL 直接建出含 admin 的 CHECK
        if "'admin'" in row["sql"] or '"admin"' in row["sql"]:
            return False                     # 已迁移（CHECK 文本里能看到 admin）

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.isolation_level = None          # 显式控制事务，保证 PRAGMA 生效时机可控
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN IMMEDIATE")
            old_seq_row = conn.execute(
                "SELECT seq FROM sqlite_sequence WHERE name = 't_user'"
            ).fetchone()
            old_seq = old_seq_row["seq"] if old_seq_row else 0
            conn.execute("DROP TABLE IF EXISTS t_user_new")
            conn.execute(
                """
                CREATE TABLE t_user_new (
                    user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    username      TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role          TEXT NOT NULL DEFAULT 'student'
                                  CHECK (role IN ('admin', 'teacher', 'student')),
                    display_name  TEXT,
                    email         TEXT,
                    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
                    must_change_password INTEGER NOT NULL DEFAULT 0
                                  CHECK (must_change_password IN (0, 1)),
                    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                    updated_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                )
                """
            )
            existing = {r["name"] for r in
                        conn.execute("PRAGMA table_info(t_user)").fetchall()}
            cols = ", ".join(c for c in self._USER_COLUMNS if c in existing)
            conn.execute(f"INSERT INTO t_user_new ({cols}) SELECT {cols} FROM t_user")
            old_cnt = conn.execute("SELECT count(*) AS c FROM t_user").fetchone()["c"]
            new_cnt = conn.execute("SELECT count(*) AS c FROM t_user_new").fetchone()["c"]
            if old_cnt != new_cnt:
                raise RuntimeError(f"t_user 重建行数不一致：旧 {old_cnt} 行 / 新 {new_cnt} 行")
            conn.execute("DROP TABLE t_user")
            conn.execute("ALTER TABLE t_user_new RENAME TO t_user")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_role ON t_user(role)")
            # AUTOINCREMENT 续号取 max(旧续号, 当前最大 id)：单取 max(user_id) 会在历史上
            # 物理删除过用户时复用已删除的 user_id，导致历史引用静默串到新用户上。
            max_id = conn.execute(
                "SELECT COALESCE(MAX(user_id), 0) AS m FROM t_user"
            ).fetchone()["m"]
            next_seq = old_seq if old_seq >= max_id else max_id
            seq = conn.execute(
                "SELECT seq FROM sqlite_sequence WHERE name = 't_user'"
            ).fetchone()
            if seq is None:
                conn.execute(
                    "INSERT INTO sqlite_sequence (name, seq) VALUES ('t_user', ?)", (next_seq,)
                )
            elif seq["seq"] != next_seq:
                conn.execute(
                    "UPDATE sqlite_sequence SET seq = ? WHERE name = 't_user'", (next_seq,)
                )
            issues = conn.execute("PRAGMA foreign_key_check").fetchall()
            if issues:
                raise RuntimeError(f"外键校验失败，已回滚：{[tuple(r) for r in issues]}")
            conn.execute("COMMIT")
            return True
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    # t_course 在管理员端新增的列。
    # governance_status 刻意与既有 status（0=停用 / 1=启用，课程中心与发现课程都在用）分开：
    #   status 是「课程是否启用」的业务开关，管理员下架/恢复同时改 status，
    #   因为发现课程（is_public=1 AND status=1）与 Permissions 的公开课判定都只看 status，
    #   只加新列不动 status 的话「下架」对现有查询完全无效。
    #   governance_status 记录治理语义（normal / hidden / archived），供管理员端筛选与展示。
    _ADMIN_COURSE_COLUMNS = (
        ("governance_status", "TEXT NOT NULL DEFAULT 'normal'"),
        ("governance_note", "TEXT"),
        ("archived_at", "TEXT"),
    )

    def _migrate_admin(self, conn: sqlite3.Connection):
        """幂等迁移：管理员端（t_course 补治理列）。

        仅为「不存在该列」的课程表补列，历史行取默认值 'normal'，
        所有既有课程的 status / is_public 原样不动 —— 旧课程数据零影响。

        注：ADD COLUMN 不允许带 CHECK，governance_status 的取值合法性由应用层
        COURSE_GOVERNANCE_STATUS 白名单兜底（新库同样走这里，故不写进 _SCHEMA_SQL，
        以免出现「新建库」与「迁移库」两套表结构）。
        """
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(t_course)").fetchall()}
        for name, decl in self._ADMIN_COURSE_COLUMNS:
            if name not in cols:
                conn.execute(f"ALTER TABLE t_course ADD COLUMN {name} {decl}")

    def _execute(self, sql: str, params: tuple = ()) -> int:
        """执行写操作，返回 lastrowid（INSERT 时的自增主键）"""
        with self._connect() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid

    def _query(self, sql: str, params: tuple = ()) -> list:
        """执行查询，返回字典列表"""
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def _query_one(self, sql: str, params: tuple = ()) -> dict:
        """执行查询，返回单行字典；无结果返回 None"""
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    # ---------- 用户 ----------

    def create_user(self, username: str, password_hash: str, role: str = "student",
                    display_name: str = None, email: str = None) -> int:
        return self._execute(
            "INSERT INTO t_user (username, password_hash, role, display_name, email) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, password_hash, role, display_name, email),
        )

    def get_user_by_username(self, username: str) -> dict:
        return self._query_one("SELECT * FROM t_user WHERE username = ?", (username,))

    def get_user_by_id(self, user_id: int) -> dict:
        return self._query_one("SELECT * FROM t_user WHERE user_id = ?", (user_id,))

    def update_password(self, user_id: int, password_hash: str) -> int:
        """更新指定用户密码哈希（仅本人修改密码使用）"""
        return self._execute(
            "UPDATE t_user SET password_hash = ? WHERE user_id = ?",
            (password_hash, user_id),
        )

    def deactivate_user(self, user_id: int) -> int:
        """注销（软停用）指定用户：置 is_active=0，保留历史数据；登录时会被拒绝"""
        return self._execute(
            "UPDATE t_user SET is_active = 0 WHERE user_id = ?",
            (user_id,),
        )

    def list_users(self) -> list:
        return self._query("SELECT * FROM t_user ORDER BY user_id")

    def ensure_default_teacher(self) -> int:
        """确保存在演示教师账号 admin，返回其 user_id。

        幂等：已存在则原样返回，**不覆盖密码**。
        新库首次创建时：优先取环境变量 DEFAULT_TEACHER_PASSWORD；
        未配置则随机生成并只在启动日志打印一次（不再内置 admin123 这类公开口令）。
        """
        existing = self.get_user_by_username(DEFAULT_TEACHER_USERNAME)
        if existing:
            # 迁移：旧版占位密码 "<not-implemented>" 无法通过校验，替换为真实哈希
            if existing["password_hash"] == "<not-implemented>":
                password, _ = self._bootstrap_password("DEFAULT_TEACHER_PASSWORD")
                self._execute(
                    "UPDATE t_user SET password_hash = ? WHERE user_id = ?",
                    (hash_password(password), existing["user_id"]),
                )
                _logger.warning(
                    "演示教师 %s 的历史占位密码已替换为随机密码，请用重置密码流程重新获取",
                    DEFAULT_TEACHER_USERNAME)
                existing = self.get_user_by_username(DEFAULT_TEACHER_USERNAME)
            self._warn_if_legacy_weak_password(existing)
            return existing["user_id"]

        password, generated = self._bootstrap_password("DEFAULT_TEACHER_PASSWORD")
        user_id = self.create_user(
            username=DEFAULT_TEACHER_USERNAME, password_hash=hash_password(password),
            role="teacher", display_name="默认教师",
        )
        if generated:
            self._print_bootstrap_credentials(DEFAULT_TEACHER_USERNAME, password, "演示教师")
        return user_id

    def ensure_default_admin(self) -> int:
        """确保存在管理员引导账号（管理员端首次可用），返回其 user_id。

        幂等：已存在则原样返回，**不覆盖密码、不改角色**——管理员上线后自行改过密码，
        重启服务不应该把它重置回默认值。用户名见 DEFAULT_ADMIN_USERNAME。

        密码来源（见文件头 DEFAULT_ADMIN_* 注释）：
        - 环境变量 DEFAULT_ADMIN_PASSWORD 有值 -> 用它，不强制改密；
        - 未配置 -> 现场生成随机密码，只在启动日志打印一次，并置 must_change_password=1。
        """
        existing = self.get_user_by_username(DEFAULT_ADMIN_USERNAME)
        if existing:
            self._warn_if_legacy_weak_password(existing)
            return existing["user_id"]

        password, generated = self._bootstrap_password("DEFAULT_ADMIN_PASSWORD")
        user_id = self.create_user(
            username=DEFAULT_ADMIN_USERNAME,
            password_hash=hash_password(password),
            role="admin", display_name="系统管理员",
        )
        if generated:
            # 随机密码是打印在日志里的，日志可能被留存/转发，故首次登录强制改密。
            self.set_must_change_password(user_id, True)
            self._print_bootstrap_credentials(DEFAULT_ADMIN_USERNAME, password, "管理员")
        return user_id

    @staticmethod
    def _bootstrap_password(env_key: str) -> tuple:
        """引导密码来源：(password, generated)。

        环境变量优先；未配置则随机生成。生成时必须由调用方安排「只打印一次」，
        generated=True 也用于决定是否强制首次改密。
        """
        configured = (os.getenv(env_key) or "").strip()
        if configured:
            return configured, False
        return generate_bootstrap_password(), True

    @staticmethod
    def _print_bootstrap_credentials(username: str, password: str, label: str) -> None:
        """把随机生成的引导凭据打印到启动日志（仅此一次，之后无法再查）。"""
        banner = "=" * 68
        print(f"\n{banner}\n"
              f"  已创建{label}引导账号（随机密码，仅本次显示，请立即记录并在首次登录后修改）\n"
              f"    用户名：{username}\n"
              f"    密  码：{password}\n"
              f"  如需自行指定，请在启动前设置环境变量：\n"
              f"    DEFAULT_ADMIN_USERNAME / DEFAULT_ADMIN_PASSWORD\n"
              f"{banner}\n", flush=True)

    @staticmethod
    def _warn_if_legacy_weak_password(user: dict) -> None:
        """引导账号仍在使用历史内置弱口令时，每次启动都提醒轮换。

        只提示、不改动：把密码改掉可能让正在使用该账号的人被锁在外面。
        """
        try:
            if any(verify_password(p, user["password_hash"]) for p in LEGACY_WEAK_PASSWORDS):
                _logger.warning(
                    "安全提醒：引导账号 %s 仍在使用内置的历史弱口令 %s，请尽快通过"
                    "「修改密码」或管理员端「重置密码」轮换。",
                    user["username"], "/".join(LEGACY_WEAK_PASSWORDS),
                )
        except Exception:                       # 哈希格式异常不应阻断启动
            pass

    def set_must_change_password(self, user_id: int, required: bool) -> int:
        """设置/清除「首次登录必须修改密码」标记"""
        return self._execute(
            "UPDATE t_user SET must_change_password = ? WHERE user_id = ?",
            (1 if required else 0, user_id),
        )

    # ---------- 课程 ----------

    def create_course(self, course_name: str, teacher_id: int,
                      course_code: str = None, description: str = None,
                      join_code: str = None, join_mode: str = "approval",
                      organization: str = None, category: str = None,
                      cover: str = None, is_public: int = 1) -> int:
        """新建课程（课程中心改造新增 join_code / join_mode / 组织 / 分类 / 封面 / 是否公开）。

        前 4 个参数保持原有位置与默认值不变，历史调用方无需改动。
        """
        return self._execute(
            "INSERT INTO t_course "
            "(course_name, teacher_id, course_code, description, join_code, join_mode, "
            " organization, category, cover, is_public) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (course_name, teacher_id, course_code, description, join_code, join_mode,
             organization, category, cover, is_public),
        )

    def get_course(self, course_id: int) -> dict:
        return self._query_one("SELECT * FROM t_course WHERE course_id = ?", (course_id,))

    def list_courses(self) -> list:
        return self._query("SELECT * FROM t_course ORDER BY course_id")

    def get_course_by_name(self, course_name: str) -> dict:
        return self._query_one("SELECT * FROM t_course WHERE course_name = ?", (course_name,))

    def get_course_by_code(self, course_code: str) -> dict:
        return self._query_one("SELECT * FROM t_course WHERE course_code = ?", (course_code,))

    def get_course_by_join_code(self, join_code: str) -> dict:
        """按加课码查课程（走 uq_course_join_code 唯一索引）"""
        return self._query_one("SELECT * FROM t_course WHERE join_code = ?", (join_code,))

    def list_courses_page(self, page: int = 1, page_size: int = 10,
                          teacher_id: int = None, keyword: str = None,
                          category: str = None, is_public: int = None,
                          join_mode: str = None,
                          course_ids: list = None,
                          exclude_course_ids: list = None):
        """分页查询课程（LEFT JOIN 教师表取教师名），返回 (total, rows)。

        课程中心改造新增过滤条件：
        - course_ids：白名单（权限范围，见 Permissions.allowed_course_ids）
        - exclude_course_ids：排除已加入/已申请的课程（发现课程页）
        - category / is_public / join_mode：发现课程页的筛选
        空列表语义：course_ids=[] 表示「无可见课程」直接返回空，绝不生成 IN ()；
        exclude_course_ids=[] 等价于不过滤。
        """
        if course_ids is not None and len(course_ids) == 0:
            return 0, []

        where, params = [], []
        if teacher_id is not None:
            where.append("c.teacher_id = ?")
            params.append(teacher_id)
        if keyword:
            where.append("(c.course_name LIKE ? OR c.description LIKE ?)")
            params.append(f"%{keyword}%")
            params.append(f"%{keyword}%")
        if category:
            where.append("c.category = ?")
            params.append(category)
        if is_public is not None:
            where.append("c.is_public = ?")
            params.append(is_public)
        if join_mode:
            where.append("c.join_mode = ?")
            params.append(join_mode)
        if course_ids:
            where.append(f"c.course_id IN ({','.join('?' * len(course_ids))})")
            params.extend(course_ids)
        if exclude_course_ids:
            where.append(f"c.course_id NOT IN ({','.join('?' * len(exclude_course_ids))})")
            params.extend(exclude_course_ids)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        total = self._query_one(
            f"SELECT count(*) AS cnt FROM t_course c {where_sql}", tuple(params)
        )["cnt"]

        rows = self._query(
            f"""
            SELECT c.*, COALESCE(u.display_name, u.username, '') AS teacher_name
            FROM t_course c LEFT JOIN t_user u ON c.teacher_id = u.user_id
            {where_sql}
            ORDER BY c.course_id DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params + [page_size, (page - 1) * page_size]),
        )
        return total, rows

    def update_course(self, course_id: int, **fields) -> None:
        """更新课程字段（白名单，None 跳过表示不修改），自动刷新 updated_at。

        join_code 也在白名单内，但仅由 CourseService.refresh_join_code 调用
        （刷新即覆盖旧码）；普通课程设置接口不允许手填加课码。
        """
        allowed = {"course_name", "course_code", "description", "status",
                   "join_code", "join_mode", "organization", "category",
                   "cover", "is_public"}
        sets, params = [], []
        for key, val in fields.items():
            if key not in allowed or val is None:
                continue
            sets.append(f"{key} = ?")
            params.append(val)
        if not sets:
            return
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(course_id)
        self._execute(f"UPDATE t_course SET {', '.join(sets)} WHERE course_id = ?", tuple(params))

    def delete_course(self, course_id: int) -> int:
        """删除课程及其文档、学习记录、收藏、向量、题库（按子表->父表顺序满足外键），返回删除的文档数。

        Phase 9（技术债修复）：补充清理 t_kp_embedding——该表无外键、不参与级联，
        历史实现整课删除会残留孤儿向量，故在此显式删除。
        题库（题目/答题记录/题目收藏）同样无级联，必须在删 t_course 前显式清理，
        否则会留下指向已删课程的孤儿题目。
        Scope C 的 t_question_embedding（题目向量）与 t_kp_text（知识点文本缓存）同理。
        """
        doc_count = self.count_documents_by_course(course_id)
        with self._connect() as conn:
            conn.execute("DELETE FROM t_kp_embedding WHERE course_id = ?", (course_id,))
            # Scope C：题目向量与知识点文本缓存同样无外键，需显式清理（否则留孤儿）
            conn.execute(
                "DELETE FROM t_question_embedding WHERE question_id IN "
                "(SELECT question_id FROM t_question WHERE course_id = ?)", (course_id,),
            )
            conn.execute("DELETE FROM t_question_import_batch WHERE course_id = ?", (course_id,))
            conn.execute("DELETE FROM t_kp_text WHERE course_id = ?", (course_id,))
            conn.execute("DELETE FROM t_student_favorite WHERE course_id = ?", (course_id,))
            conn.execute("DELETE FROM t_learning_record WHERE course_id = ?", (course_id,))
            conn.execute("DELETE FROM t_course_invite WHERE course_id = ?", (course_id,))
            conn.execute("DELETE FROM t_course_member WHERE course_id = ?", (course_id,))
            # 题库：先删题目收藏与答题记录（引用 t_question），再删题目本身
            conn.execute(
                "DELETE FROM t_question_favorite WHERE question_id IN "
                "(SELECT question_id FROM t_question WHERE course_id = ?)", (course_id,),
            )
            conn.execute(
                "DELETE FROM t_answer_record WHERE question_id IN "
                "(SELECT question_id FROM t_question WHERE course_id = ?)", (course_id,),
            )
            conn.execute("DELETE FROM t_question WHERE course_id = ?", (course_id,))
            conn.execute("DELETE FROM t_document WHERE course_id = ?", (course_id,))
            conn.execute("DELETE FROM t_course WHERE course_id = ?", (course_id,))
            conn.commit()
        return doc_count

    def update_course_join_code(self, course_id: int, join_code: str) -> int:
        """刷新加课码：单条 UPDATE 直接覆盖，旧码立即失效；返回影响行数"""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE t_course SET join_code = ?, updated_at = ? WHERE course_id = ?",
                (join_code, _now(), course_id),
            )
            conn.commit()
            return cur.rowcount

    def list_accessible_course_ids(self, user_id: int, role: str) -> list:
        """该用户可读的课程 id 列表：教师=自己创建的 + 被邀请协作的；学生=已通过审核的成员课程。

        供 Permissions 计算「未指定课程时」的读作用域，以及数据总览/问答的范围收敛。
        """
        if role == "teacher":
            rows = self._query(
                "SELECT course_id FROM t_course WHERE teacher_id = ? "
                "UNION "
                "SELECT course_id FROM t_course_member "
                "WHERE user_id = ? AND status = 'approved'",
                (user_id, user_id),
            )
        else:
            rows = self._query(
                "SELECT course_id FROM t_course_member "
                "WHERE user_id = ? AND status = 'approved'",
                (user_id,),
            )
        return [r["course_id"] for r in rows]

    # ---------- 文档 ----------

    def create_document(self, course_id: int, uploader_id: int, file_name: str,
                        file_type: str, file_size: int,
                        file_sha256: str = None, file_path: str = None) -> int:
        return self._execute(
            "INSERT INTO t_document "
            "(course_id, uploader_id, file_name, file_type, file_size, file_sha256, file_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (course_id, uploader_id, file_name, file_type, file_size, file_sha256, file_path),
        )

    def update_document(self, doc_id: int, **fields) -> None:
        """
        更新文档表字段（字段白名单防注入），自动刷新 updated_at。
        示例：update_document(doc_id, parse_status='PARSED', entity_count=12)
        """
        allowed = {"parse_status", "extract_status", "error_message", "chunk_count",
                   "entity_count", "relation_count", "vector_collection", "file_path"}
        sets, params = [], []
        for key, val in fields.items():
            if key not in allowed:
                continue
            sets.append(f"{key} = ?")
            params.append(val)
        if not sets:
            return
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(doc_id)
        self._execute(f"UPDATE t_document SET {', '.join(sets)} WHERE doc_id = ?", tuple(params))

    def get_document(self, doc_id: int) -> dict:
        return self._query_one("SELECT * FROM t_document WHERE doc_id = ?", (doc_id,))

    def list_documents_by_course(self, course_id: int) -> list:
        return self._query(
            "SELECT * FROM t_document WHERE course_id = ? ORDER BY doc_id", (course_id,),
        )

    def list_all_documents(self) -> list:
        """全部文档记录（按课程 + 文档号排序）。

        供跨课程的批量维护脚本使用（如 scripts/rebuild_missing_graphs.py
        扫描「记录说抽取完成、Neo4j 里却没有节点」的文档）。
        """
        return self._query("SELECT * FROM t_document ORDER BY course_id, doc_id")

    def count_documents_by_course(self, course_id: int) -> int:
        return self._query_one(
            "SELECT count(*) AS cnt FROM t_document WHERE course_id = ?", (course_id,),
        )["cnt"]

    def count_documents_grouped(self) -> dict:
        """按课程统计文档数，返回 {course_id: count}"""
        rows = self._query("SELECT course_id, count(*) AS cnt FROM t_document GROUP BY course_id")
        return {r["course_id"]: r["cnt"] for r in rows}

    def delete_document(self, doc_id: int) -> int:
        """删除单个文档记录（仅删除 t_document 行；图谱/向量/学习/收藏清理由上层 Phase 8/9 完成）"""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM t_document WHERE doc_id = ?", (doc_id,))
            conn.commit()
            return cur.rowcount

    # ---------- 学习记录 ----------

    def upsert_learning_record(self, user_id: int, course_id: int, document_id, kp_id: str,
                               status: str = "MASTERED", mastery_level: int = 100,
                               source: str = "MANUAL", last_learned_at: str = None) -> int:
        """
        写入学习记录；依赖 UNIQUE(user_id, course_id, kp_id) 冲突时更新，
        保证同一学生、同一课程、同一知识点仅一条记录。

        Phase 8B：document_id 作为业务作用域字段写入（kp_id 全局唯一，UNIQUE 无需改为四列；
        具体见规划文档「数据库约束」）。注：ON CONFLICT DO UPDATE 为 SQLite 语法，
        迁 MySQL 时改为 ON DUPLICATE KEY UPDATE。
        """
        now = _now()
        sql = """
        INSERT INTO t_learning_record
            (user_id, course_id, document_id, kp_id, status, mastery_level, source, last_learned_at,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, course_id, kp_id) DO UPDATE SET
            document_id = excluded.document_id,
            status = excluded.status,
            mastery_level = excluded.mastery_level,
            source = excluded.source,
            last_learned_at = excluded.last_learned_at,
            updated_at = excluded.updated_at
        """
        return self._execute(sql, (user_id, course_id, document_id, kp_id, status, mastery_level,
                                   source, last_learned_at, now, now))

    def list_records_by_user_course(self, user_id: int, course_id: int, document_id=None) -> list:
        """查询某用户某文档的学习记录；document_id 为 None 时退化为课程级（教师监测汇总）"""
        if document_id is not None:
            return self._query(
                "SELECT * FROM t_learning_record WHERE user_id = ? AND course_id = ? "
                "AND document_id = ? ORDER BY record_id",
                (user_id, course_id, document_id),
            )
        return self._query(
            "SELECT * FROM t_learning_record WHERE user_id = ? AND course_id = ? "
            "ORDER BY record_id",
            (user_id, course_id),
        )

    def list_records_by_user(self, user_id: int) -> list:
        return self._query(
            "SELECT * FROM t_learning_record WHERE user_id = ? ORDER BY course_id, record_id",
            (user_id,),
        )

    def list_records_by_course(self, course_id: int) -> list:
        """查询某课程下全部学习记录（教师查看班级学习情况）"""
        return self._query(
            "SELECT * FROM t_learning_record WHERE course_id = ? ORDER BY user_id, record_id",
            (course_id,),
        )

    def delete_learning_record(self, user_id: int, course_id: int, document_id, kp_id: str) -> int:
        """删除某用户某文档某知识点的学习记录（用于取消掌握标记）"""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM t_learning_record "
                "WHERE user_id = ? AND course_id = ? AND document_id = ? AND kp_id = ?",
                (user_id, course_id, document_id, kp_id),
            )
            conn.commit()
            return cur.rowcount

    # ---------- 学生收藏（收藏 = 个人知识点书签，独立于学习状态） ----------

    def add_favorite(self, user_id: int, course_id: int, document_id, kp_id: str) -> bool:
        """新增收藏（INSERT OR IGNORE 幂等）；返回是否新插入（True=新增，False=已存在未重复）。

        Phase 8B：document_id 作为业务作用域字段写入（kp_id 全局唯一，UNIQUE 无需改为四列）。
        """
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO t_student_favorite (user_id, course_id, document_id, kp_id) "
                "VALUES (?, ?, ?, ?)",
                (user_id, course_id, document_id, kp_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def remove_favorite(self, user_id: int, course_id: int, document_id, kp_id: str) -> int:
        """取消收藏，返回删除条数"""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM t_student_favorite "
                "WHERE user_id = ? AND course_id = ? AND document_id = ? AND kp_id = ?",
                (user_id, course_id, document_id, kp_id),
            )
            conn.commit()
            return cur.rowcount

    def list_favorites_by_user_course(self, user_id: int, course_id: int, document_id=None) -> list:
        """查询某用户某文档的收藏（按收藏时间倒序）；document_id 为 None 时退化为课程级"""
        if document_id is not None:
            return self._query(
                "SELECT kp_id, course_id, document_id, created_at FROM t_student_favorite "
                "WHERE user_id = ? AND course_id = ? AND document_id = ? ORDER BY id DESC",
                (user_id, course_id, document_id),
            )
        return self._query(
            "SELECT kp_id, course_id, created_at FROM t_student_favorite "
            "WHERE user_id = ? AND course_id = ? ORDER BY id DESC",
            (user_id, course_id),
        )

    def list_favorites_by_user(self, user_id: int) -> list:
        """查询某用户全部课程的收藏（按课程 + 收藏时间倒序）"""
        return self._query(
            "SELECT kp_id, course_id, created_at FROM t_student_favorite "
            "WHERE user_id = ? ORDER BY course_id, id DESC",
            (user_id,),
        )

    def count_favorites_by_course(self, course_id: int) -> dict:
        """按用户统计某课程的收藏数，返回 {user_id: count}（教师查看学生收藏情况）"""
        rows = self._query(
            "SELECT user_id, count(*) AS cnt FROM t_student_favorite "
            "WHERE course_id = ? GROUP BY user_id",
            (course_id,),
        )
        return {r["user_id"]: r["cnt"] for r in rows}

    # ---------- 知识点向量（RAG 向量检索） ----------

    def upsert_kp_embedding(self, course_id: int, document_id, kp_id: str, embedding: list) -> int:
        """写入/更新知识点向量（embedding 序列化为 JSON 文本；Phase 8C 保存 document_id）"""
        return self._execute(
            "INSERT INTO t_kp_embedding (course_id, document_id, kp_id, embedding, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(course_id, kp_id) DO UPDATE SET "
            "document_id = excluded.document_id, "
            "embedding = excluded.embedding, updated_at = excluded.updated_at",
            (course_id, document_id, kp_id, json.dumps(embedding), _now()),
        )

    def get_embeddings_by_document(self, course_id: int, document_id) -> list:
        """返回文档全部知识点向量，[{kp_id, embedding(list[float])}]"""
        rows = self._query(
            "SELECT kp_id, embedding FROM t_kp_embedding WHERE course_id = ? AND document_id = ?",
            (course_id, document_id),
        )
        result = []
        for r in rows:
            try:
                vec = json.loads(r["embedding"])
            except (ValueError, TypeError):
                continue
            result.append({"kp_id": r["kp_id"], "embedding": vec})
        return result

    def get_embedding_kp_ids(self, course_id: int, document_id) -> set:
        """该文档已存向量的 kp_id 集合（向量索引新鲜度比对专用）。

        与 get_embeddings_by_document 的区别：不读 embedding 列。索引新鲜度只需要
        比对 kp_id 集合，若为此整表取回向量文本并反序列化，纯属浪费（实测 69 条
        向量下，反序列化占该步耗时的约 70%）。
        """
        rows = self._query(
            "SELECT kp_id FROM t_kp_embedding WHERE course_id = ? AND document_id = ?",
            (course_id, document_id),
        )
        return {r["kp_id"] for r in rows}

    def delete_embeddings_by_document(self, course_id: int, document_id) -> int:
        """删除文档全部知识点向量，返回删除条数"""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM t_kp_embedding WHERE course_id = ? AND document_id = ?",
                (course_id, document_id),
            )
            conn.commit()
            return cur.rowcount

    def get_embeddings_by_course(self, course_id: int) -> list:
        """返回该课程全部知识点向量（跨文档），[{kp_id, document_id, embedding(list)}]"""
        rows = self._query(
            "SELECT kp_id, document_id, embedding FROM t_kp_embedding WHERE course_id = ?",
            (course_id,),
        )
        for r in rows:
            r["embedding"] = self._loads_json(r["embedding"])
        return rows

    def count_embeddings_by_course(self, course_id: int) -> int:
        """某课程全部知识点向量数量（供整课删除前统计与报告）"""
        return self._query_one(
            "SELECT count(*) AS cnt FROM t_kp_embedding WHERE course_id = ?", (course_id,),
        )["cnt"]

    def delete_learning_records_by_document(self, course_id: int, document_id) -> int:
        """删除文档全部学习记录，返回删除条数（Phase 8C 删除文档级清理）"""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM t_learning_record WHERE course_id = ? AND document_id = ?",
                (course_id, document_id),
            )
            conn.commit()
            return cur.rowcount

    def delete_favorites_by_document(self, course_id: int, document_id) -> int:
        """删除文档全部收藏，返回删除条数（Phase 8C 删除文档级清理）"""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM t_student_favorite WHERE course_id = ? AND document_id = ?",
                (course_id, document_id),
            )
            conn.commit()
            return cur.rowcount

    # ---------- 课程成员（课程中心） ----------

    def get_membership(self, course_id: int, user_id: int) -> dict:
        return self._query_one(
            "SELECT * FROM t_course_member WHERE course_id = ? AND user_id = ?",
            (course_id, user_id),
        )

    def get_course_with_membership(self, course_id: int, user_id: int) -> dict:
        """一次查询取回课程 + 该用户在该课程上的成员关系（权限判定的唯一数据来源）"""
        return self._query_one(
            """
            SELECT c.*,
                   m.role    AS member_role,
                   m.status  AS member_status,
                   m.join_source AS member_join_source,
                   m.joined_at   AS member_joined_at
            FROM t_course c
            LEFT JOIN t_course_member m
                   ON m.course_id = c.course_id AND m.user_id = ?
            WHERE c.course_id = ?
            """,
            (user_id, course_id),
        )

    def upsert_membership(self, course_id: int, user_id: int, role: str = "student",
                          status: str = "pending", join_source: str = "code",
                          applied_reason: str = None) -> int:
        """写入/覆盖成员关系（依赖 UNIQUE(course_id, user_id)）。

        覆盖时清空上一轮的审核痕迹（reviewed_by/at/comment），避免「上次拒绝理由」
        残留在本次待审核记录上；joined_at 一旦有值不再被覆盖（保留最早加入时间）。
        """
        now = _now()
        joined_at = now if status == "approved" else None
        sql = """
        INSERT INTO t_course_member
            (course_id, user_id, role, status, join_source, applied_reason,
             joined_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(course_id, user_id) DO UPDATE SET
            role = excluded.role,
            status = excluded.status,
            join_source = excluded.join_source,
            applied_reason = excluded.applied_reason,
            joined_at = CASE WHEN excluded.status = 'approved'
                             THEN COALESCE(t_course_member.joined_at, excluded.joined_at)
                             ELSE t_course_member.joined_at END,
            reviewed_by = NULL,
            reviewed_at = NULL,
            review_comment = NULL,
            updated_at = excluded.updated_at
        """
        return self._execute(sql, (course_id, user_id, role, status, join_source,
                                   applied_reason, joined_at, now, now))

    def set_membership_status(self, course_id: int, user_id: int, status: str,
                              reviewed_by: int = None, comment: str = None) -> int:
        """审核/移除：仅改状态，返回影响行数（0 表示成员记录不存在）。

        移除（removed）只翻状态，不删该学生的学习记录与收藏——那是学生自己的数据。
        """
        now = _now()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE t_course_member SET "
                "status = ?, reviewed_by = ?, reviewed_at = ?, review_comment = ?, "
                "joined_at = CASE WHEN ? = 'approved' THEN COALESCE(joined_at, ?) "
                "ELSE joined_at END, "
                "updated_at = ? "
                "WHERE course_id = ? AND user_id = ?",
                (status, reviewed_by, now, comment, status, now, now, course_id, user_id),
            )
            conn.commit()
            return cur.rowcount

    # ---------- Scope C：知识点文本缓存 + 题目向量（试题知识点自动标注） ----------

    def upsert_kp_text_batch(self, course_id: int, items: list) -> int:
        """批量写入/更新知识点文本缓存，返回写入条数。

        items: [{"kp_id", "name", "category", "description", "document_id"}]
        每次从图谱成功取到清单后调用（覆盖式更新），供图谱不可用时降级使用。
        """
        rows = [
            (course_id, it.get("kp_id"), it.get("document_id"), it.get("name") or "",
             it.get("category") or "", it.get("description") or "", _now())
            for it in items
            if it.get("kp_id")
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO t_kp_text (course_id, kp_id, document_id, name, category, "
                "description, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(course_id, kp_id) DO UPDATE SET "
                "document_id = excluded.document_id, name = excluded.name, "
                "category = excluded.category, description = excluded.description, "
                "updated_at = excluded.updated_at",
                rows,
            )
            conn.commit()
        return len(rows)

    def list_kp_text(self, course_id: int, document_id=None) -> list:
        """读取知识点文本缓存；document_id 传入时只取该文档（缓存无 document_id 的也一并返回）"""
        where, params = ["course_id = ?"], [course_id]
        if document_id is not None:
            where.append("(document_id = ? OR document_id IS NULL)")
            params.append(document_id)
        return self._query(
            f"SELECT kp_id, document_id, name, category, description FROM t_kp_text "
            f"WHERE {' AND '.join(where)} ORDER BY name",
            tuple(params),
        )

    def count_kp_text(self, course_id: int) -> int:
        return self._query_one(
            "SELECT count(*) AS cnt FROM t_kp_text WHERE course_id = ?", (course_id,),
        )["cnt"]

    def upsert_question_embedding(self, question_id: int, course_id: int, document_id,
                                  text_hash: str, embedding: list) -> int:
        """写入/更新题目向量（embedding 序列化为 JSON 文本）"""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO t_question_embedding "
                "(question_id, course_id, document_id, text_hash, embedding, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(question_id) DO UPDATE SET "
                "course_id = excluded.course_id, document_id = excluded.document_id, "
                "text_hash = excluded.text_hash, embedding = excluded.embedding, "
                "updated_at = excluded.updated_at",
                (question_id, course_id, document_id, text_hash,
                 self._json_text(embedding), _now()),
            )
            conn.commit()
        return 1

    def get_question_embedding(self, question_id: int) -> dict:
        """取单题向量行（含 text_hash，供判断是否需要重算）"""
        row = self._query_one(
            "SELECT question_id, course_id, document_id, text_hash, embedding "
            "FROM t_question_embedding WHERE question_id = ?", (question_id,),
        )
        if row is None:
            return None
        row["embedding"] = self._loads_json(row["embedding"])
        return row

    @staticmethod
    def _loads_json(value):
        """把库内 JSON 文本解析回对象（解析失败返回空列表）"""
        if isinstance(value, (list, dict)):
            return value
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return []

    # ---------- Scope D：试题文档导入（批次） ----------

    def create_import_batch(self, course_id: int, document_id, file_name: str,
                            source: str = "RULE", total: int = 0,
                            created_by: int = None, meta: dict = None) -> str:
        """创建导入批次，返回 batch_id（形如 imp_xxxxxxxxxxxx）"""
        batch_id = f"imp_{uuid.uuid4().hex[:12]}"
        self._execute(
            "INSERT INTO t_question_import_batch "
            "(batch_id, course_id, document_id, file_name, source, total, created_by, meta) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (batch_id, course_id, document_id, file_name, source, total, created_by,
             json.dumps(meta or {}, ensure_ascii=False)),
        )
        return batch_id

    def get_import_batch(self, batch_id: str) -> dict:
        return self._query_one(
            "SELECT * FROM t_question_import_batch WHERE batch_id = ?", (batch_id,)
        )

    def update_import_batch(self, batch_id: str, **fields) -> int:
        """更新批次（白名单字段）"""
        allowed = {"imported", "needs_review", "status", "total", "meta", "source"}
        sets, params = [], []
        for key, val in fields.items():
            if key not in allowed:
                continue
            if key == "meta" and not isinstance(val, str):
                val = json.dumps(val, ensure_ascii=False)
            sets.append(f"{key} = ?")
            params.append(val)
        if not sets:
            return 0
        params.append(batch_id)
        return self._execute(
            f"UPDATE t_question_import_batch SET {', '.join(sets)} WHERE batch_id = ?",
            tuple(params),
        )

    def list_import_batches(self, course_id: int, document_id=None, limit: int = 20) -> list:
        where, params = ["course_id = ?"], [course_id]
        if document_id is not None:
            where.append("document_id = ?")
            params.append(document_id)
        params.append(limit)
        return self._query(
            f"SELECT * FROM t_question_import_batch WHERE {' AND '.join(where)} "
            f"ORDER BY created_at DESC, batch_id DESC LIMIT ?",
            tuple(params),
        )

    def list_questions_by_batch(self, batch_id: str, page: int = 1, page_size: int = 200):
        """批次内已入库的题目（分页），返回 (total, rows)"""
        total = self._query_one(
            "SELECT count(*) AS cnt FROM t_question WHERE import_batch_id = ?", (batch_id,),
        )["cnt"]
        rows = self._query(
            "SELECT * FROM t_question WHERE import_batch_id = ? ORDER BY question_id LIMIT ? OFFSET ?",
            (batch_id, page_size, (page - 1) * page_size),
        )
        return total, rows

    def count_questions_by_import_status(self, course_id: int) -> dict:
        """课件/课程级：按导入状态统计（教师端"待复核导入题"角标）"""
        rows = self._query(
            "SELECT import_status, count(*) AS cnt FROM t_question "
            "WHERE course_id = ? AND import_batch_id IS NOT NULL GROUP BY import_status",
            (course_id,),
        )
        return {r["import_status"] or "READY": r["cnt"] for r in rows}


    @staticmethod
    def _json_text(value):
        """把 options/answer 一律序列化为 JSON 文本（None 原样返回）。

        为什么「一律 dumps」而不是「字符串原样返回」：
        - 单选答案 "A" 这类字符串不是合法 JSON，原样存库后回读 json.loads 会失败；
        - 判断题答案 "true" 恰好是合法 JSON，原样存库后回读会变成布尔 True；
        两种题型行为不一致（单选永远判错）。统一 json.dumps 后 _loads 可无损还原：
        "A" -> '"A"' -> "A"，"true" -> '"true"' -> "true"。
        """
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False)

    def create_question(self, course_id: int, document_id, kp_id, q_type: str,
                        stem: str, options, answer, analysis: str = None,
                        difficulty: int = 3, created_by: int = None,
                        source: str = "MANUAL", is_active: int = 1,
                        import_batch_id: str = None,
                        import_status: str = None) -> int:
        """新增题目，返回 question_id（options/answer 自动序列化为 JSON 文本）。

        Scope D：导入的题目一律 is_active=0 进「暂存区」，并带上 import_batch_id /
        import_status（READY / NEEDS_REVIEW / ANSWER_MISSING），教师复核后才启用。
        """
        return self._execute(
            "INSERT INTO t_question "
            "(course_id, document_id, kp_id, q_type, stem, options, answer, analysis, "
            " difficulty, source, created_by, is_active, import_batch_id, import_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (course_id, document_id, kp_id, q_type, stem,
             self._json_text(options), self._json_text(answer),
             analysis, difficulty, source, created_by, 1 if is_active else 0,
             import_batch_id, import_status or "READY"),
        )

    def get_question(self, question_id: int) -> dict:
        return self._query_one("SELECT * FROM t_question WHERE question_id = ?", (question_id,))

    def update_question(self, question_id: int, **fields) -> None:
        """更新题目字段（白名单，None 跳过表示不修改；options/answer 自动序列化），刷新 updated_at"""
        allowed = {"document_id", "kp_id", "q_type", "stem", "options", "answer",
                   "analysis", "difficulty", "source", "is_active"}
        sets, params = [], []
        for key, val in fields.items():
            if key not in allowed or val is None:
                continue
            if key in ("options", "answer"):
                val = self._json_text(val)
            sets.append(f"{key} = ?")
            params.append(val)
        if not sets:
            return
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(question_id)
        self._execute(f"UPDATE t_question SET {', '.join(sets)} WHERE question_id = ?", tuple(params))

    def set_question_document(self, question_id: int, document_id) -> int:
        """把题目挂到指定文档（document_id=None 表示改为「课程通用题」）。

        单独提供该方法是必要的：update_question 对 None 的语义是「不修改」，
        无法表达「清空 document_id」，而「改为课程通用题」是教师端的真实编辑动作。
        """
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE t_question SET document_id = ?, updated_at = ? WHERE question_id = ?",
                (document_id, _now(), question_id),
            )
            conn.commit()
            return cur.rowcount

    def list_members(self, course_id: int, status: str = None, role: str = None,
                     keyword: str = None, page: int = 1, page_size: int = 20):
        """分页查询课程成员（联表带出用户基础信息与资料），返回 (total, rows)。

        keyword 支持按用户名 / 姓名 / 昵称 / 学号搜索（对应「学生管理」的搜索框）。
        """
        where = ["m.course_id = ?"]
        params = [course_id]
        if status:
            where.append("m.status = ?")
            params.append(status)
        if role:
            where.append("m.role = ?")
            params.append(role)
        if keyword:
            where.append("(u.username LIKE ? OR p.real_name LIKE ? "
                         "OR p.nickname LIKE ? OR p.student_no LIKE ? OR p.teacher_no LIKE ?)")
            params.extend([f"%{keyword}%"] * 5)
        where_sql = "WHERE " + " AND ".join(where)

        total = self._query_one(
            f"""
            SELECT count(*) AS cnt FROM t_course_member m
            LEFT JOIN t_user u ON u.user_id = m.user_id
            LEFT JOIN t_user_profile p ON p.user_id = m.user_id
            {where_sql}
            """,
            tuple(params),
        )["cnt"]

        rows = self._query(
            f"""
            SELECT m.*, u.username, u.display_name, u.email, u.is_active,
                   p.avatar_url, p.real_name, p.nickname, p.gender, p.school,
                   p.college, p.major, p.grade, p.class_name,
                   p.student_no, p.teacher_no, p.title, p.research_area
            FROM t_course_member m
            LEFT JOIN t_user u ON u.user_id = m.user_id
            LEFT JOIN t_user_profile p ON p.user_id = m.user_id
            {where_sql}
            ORDER BY CASE m.status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
                     m.member_id DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params + [page_size, (page - 1) * page_size]),
        )
        return total, rows

    def set_question_active(self, question_id: int, is_active: bool) -> int:
        """启用/停用题目（软删：保留学生答题记录，仅从出题池移除），返回受影响行数"""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE t_question SET is_active = ?, updated_at = ? WHERE question_id = ?",
                (1 if is_active else 0, _now(), question_id),
            )
            conn.commit()
            return cur.rowcount

    def delete_question(self, question_id: int) -> int:
        """物理删除题目及其全部收藏/答题记录（先删子表再删主表，满足外键顺序）"""
        with self._connect() as conn:
            conn.execute("DELETE FROM t_question_favorite WHERE question_id = ?", (question_id,))
            conn.execute("DELETE FROM t_answer_record WHERE question_id = ?", (question_id,))
            cur = conn.execute("DELETE FROM t_question WHERE question_id = ?", (question_id,))
            conn.commit()
            return cur.rowcount

    # ---------- 题库：题目查询（管理列表 / 出题池） ----------

    def list_questions(self, course_id: int, document_id=None, kp_id: str = None,
                       q_type: str = None, keyword: str = None, is_active=None,
                       page: int = 1, page_size: int = 10,
                       include_course_level: bool = True,
                       auto_grade_only: bool = False):
        """分页查询题目（LEFT JOIN 取创建人姓名），返回 (total, rows)。

        作用域规则：传入 document_id 时默认同时包含「该文档题目」与「课程通用题
        （document_id IS NULL，即题目挂课程不挂具体文档）」；include_course_level=False
        时退化为精确匹配该文档（用于文档级清理/统计）。

        auto_grade_only=True 时排除主观题（FILL/ESSAY）——推荐器候选池用该口径实现
        "自动组卷不硬插入主观题"；教师端题库列表保持 False（要能看到主观题）。

        注意：WHERE 条件里的列一律带 `q.` 前缀——`t_user` 也有 `is_active` 列，
        裸写会报 `ambiguous column name: is_active`（教师端「启用状态」筛选曾因此报错）。
        """
        where, params = ["q.course_id = ?"], [course_id]
        if document_id is not None:
            if include_course_level:
                where.append("(q.document_id = ? OR q.document_id IS NULL)")
            else:
                where.append("q.document_id = ?")
            params.append(document_id)
        if kp_id:
            where.append("q.kp_id = ?")
            params.append(kp_id)
        if q_type:
            where.append("q.q_type = ?")
            params.append(q_type)
        elif auto_grade_only:
            marks = ", ".join("?" for _ in MANUAL_GRADE_TYPES)
            where.append(f"q.q_type NOT IN ({marks})")
            params.extend(MANUAL_GRADE_TYPES)
        if keyword:
            where.append("q.stem LIKE ?")
            params.append(f"%{keyword}%")
        if is_active is not None:
            where.append("q.is_active = ?")
            params.append(1 if is_active else 0)
        where_sql = "WHERE " + " AND ".join(where)

        total = self._query_one(
            f"SELECT count(*) AS cnt FROM t_question q {where_sql}", tuple(params),
        )["cnt"]
        rows = self._query(
            f"""
            SELECT q.*, COALESCE(u.display_name, u.username, '') AS creator_name
            FROM t_question q LEFT JOIN t_user u ON q.created_by = u.user_id
            {where_sql}
            ORDER BY q.question_id DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params + [page_size, (page - 1) * page_size]),
        )
        return total, rows

    def list_memberships_by_user(self, user_id: int, statuses: tuple = None) -> list:
        """某用户的全部课程成员关系（可按状态过滤），用于「我的课程」标注我的角色与状态"""
        if statuses:
            marks = ",".join("?" * len(statuses))
            return self._query(
                f"SELECT * FROM t_course_member WHERE user_id = ? AND status IN ({marks}) "
                f"ORDER BY member_id DESC",
                tuple([user_id] + list(statuses)),
            )
        return self._query(
            "SELECT * FROM t_course_member WHERE user_id = ? ORDER BY member_id DESC",
            (user_id,),
        )

    def count_members_by_status(self, course_id: int) -> dict:
        """某课程各状态成员数，返回 {status: count}"""
        rows = self._query(
            "SELECT status, count(*) AS cnt FROM t_course_member "
            "WHERE course_id = ? GROUP BY status",
            (course_id,),
        )
        return {r["status"]: r["cnt"] for r in rows}

    def member_counts_by_course(self, course_ids: list) -> dict:
        """批量统计多门课程的成员数，返回 {course_id: {status: count}}（课程卡片角标用）"""
        if not course_ids:
            return {}
        rows = self._query(
            f"SELECT course_id, status, count(*) AS cnt FROM t_course_member "
            f"WHERE course_id IN ({','.join('?' * len(course_ids))}) "
            f"GROUP BY course_id, status",
            tuple(course_ids),
        )
        result = {}
        for r in rows:
            result.setdefault(r["course_id"], {})[r["status"]] = r["cnt"]
        return result

    def list_member_user_ids(self, course_id: int, status: str = "approved",
                             role: str = None) -> list:
        """某课程指定状态（可按角色过滤）的成员 user_id 列表。

        教学监测的「学生集合」= role='student' 的已通过成员 ∪ 有学习记录者——
        必须排除 role='teacher' 的成员，否则课程创建者会出现在自己的学生名单里。
        """
        if role:
            rows = self._query(
                "SELECT user_id FROM t_course_member "
                "WHERE course_id = ? AND status = ? AND role = ?",
                (course_id, status, role),
            )
        else:
            rows = self._query(
                "SELECT user_id FROM t_course_member WHERE course_id = ? AND status = ?",
                (course_id, status),
            )
        return [r["user_id"] for r in rows]

    # ---------- 课程邀请 ----------

    def create_invite(self, course_id: int, token: str, invited_by: int,
                      role: str = "student", expires_at: str = None) -> int:
        return self._execute(
            "INSERT INTO t_course_invite (course_id, token, invited_by, role, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (course_id, token, invited_by, role, expires_at),
        )

    def get_invite_by_token(self, token: str) -> dict:
        return self._query_one("SELECT * FROM t_course_invite WHERE token = ?", (token,))

    def list_invites_by_course(self, course_id: int) -> list:
        return self._query(
            """
            SELECT i.*,
                   COALESCE(inv.display_name, inv.username, '') AS inviter_name,
                   COALESCE(ub.display_name, ub.username, '')   AS used_by_name
            FROM t_course_invite i
            LEFT JOIN t_user inv ON inv.user_id = i.invited_by
            LEFT JOIN t_user ub  ON ub.user_id  = i.used_by
            WHERE i.course_id = ?
            ORDER BY i.invite_id DESC
            """,
            (course_id,),
        )

    def mark_invite_used(self, invite_id: int, used_by: int) -> int:
        """标记邀请已使用；rowcount==1 才是本次真正消费掉该邀请。

        条件里带 status='active'，因此「并发点击 / 重复点击」只有一个请求能拿到 1，
        无需先读后写，从根上避免邀请被重复使用。
        """
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE t_course_invite SET status = 'used', used_by = ?, used_at = ? "
                "WHERE invite_id = ? AND status = 'active'",
                (used_by, _now(), invite_id),
            )
            conn.commit()
            return cur.rowcount

    def revoke_invite(self, invite_id: int, course_id: int) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE t_course_invite SET status = 'revoked' "
                "WHERE invite_id = ? AND course_id = ? AND status = 'active'",
                (invite_id, course_id),
            )
            conn.commit()
            return cur.rowcount

    # ---------- 用户资料（个人中心） ----------

    _PROFILE_FIELDS = ("avatar_url", "real_name", "nickname", "gender", "school",
                       "college", "bio", "student_no", "major", "grade",
                       "class_name", "teacher_no", "title", "research_area")

    def get_user_profile(self, user_id: int) -> dict:
        """取用户身份信息 + 资料（资料行不存在时右侧字段为 NULL，不算错误）"""
        return self._query_one(
            """
            SELECT u.user_id, u.username, u.role, u.display_name, u.email,
                   u.is_active, u.created_at AS user_created_at,
                   p.avatar_url, p.real_name, p.nickname, p.gender, p.school,
                   p.college, p.bio, p.student_no, p.major, p.grade, p.class_name,
                   p.teacher_no, p.title, p.research_area,
                   p.updated_at AS profile_updated_at
            FROM t_user u
            LEFT JOIN t_user_profile p ON p.user_id = u.user_id
            WHERE u.user_id = ?
            """,
            (user_id,),
        )

    def upsert_user_profile(self, user_id: int, **fields) -> None:
        """写入/更新资料（仅白名单字段；None 表示清空该字段，与「不修改」由服务层区分）"""
        cols = [k for k in fields if k in self._PROFILE_FIELDS]
        if not cols:
            return
        now = _now()
        placeholders = ", ".join("?" * len(cols))
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols)
        self._execute(
            f"INSERT INTO t_user_profile (user_id, {', '.join(cols)}, updated_at) "
            f"VALUES (?, {placeholders}, ?) "
            f"ON CONFLICT(user_id) DO UPDATE SET {updates}, updated_at = excluded.updated_at",
            tuple([user_id] + [fields[c] for c in cols] + [now]),
        )

    def list_user_profiles(self, user_ids: list) -> dict:
        """批量取资料，返回 {user_id: {nickname, real_name, avatar_url, school, ...}}（成员列表用）"""
        if not user_ids:
            return {}
        rows = self._query(
            f"""
            SELECT u.user_id, u.username, u.display_name,
                   p.avatar_url, p.real_name, p.nickname, p.school, p.college,
                   p.major, p.grade, p.class_name, p.student_no, p.teacher_no, p.title
            FROM t_user u
            LEFT JOIN t_user_profile p ON p.user_id = u.user_id
            WHERE u.user_id IN ({','.join('?' * len(user_ids))})
            """,
            tuple(user_ids),
        )
        return {r["user_id"]: dict(r) for r in rows}

    def count_user_profiles(self) -> int:
        return self._query_one("SELECT count(*) AS cnt FROM t_user_profile")["cnt"]
    def list_practice_questions(self, course_id: int, document_id=None, kp_id: str = None,
                                q_type: str = None, limit: int = 10,
                                exclude_ids=None, auto_grade_only: bool = True) -> list:
        """出题查询：仅取启用题目，随机排序；document_id 传入时含「该文档题 + 课程通用题」。

        auto_grade_only=True（默认）：主观题（FILL/ESSAY）不进随机出题池——它们要教师批改、
        作答耗时长，混进随机卷会拉垮体验；只有显式指定 q_type 时才取（"不硬插入"策略）。
        """
        where, params = ["course_id = ?", "is_active = 1"], [course_id]
        if document_id is not None:
            where.append("(document_id = ? OR document_id IS NULL)")
            params.append(document_id)
        if kp_id:
            where.append("kp_id = ?")
            params.append(kp_id)
        if q_type:
            where.append("q_type = ?")
            params.append(q_type)
        elif auto_grade_only:
            marks = ", ".join("?" for _ in MANUAL_GRADE_TYPES)
            where.append(f"q_type NOT IN ({marks})")
            params.extend(MANUAL_GRADE_TYPES)
        if exclude_ids:
            placeholders = ",".join("?" for _ in exclude_ids)
            where.append(f"question_id NOT IN ({placeholders})")
            params.extend(list(exclude_ids))
        params.append(limit)
        return self._query(
            f"SELECT * FROM t_question WHERE {' AND '.join(where)} "
            f"ORDER BY RANDOM() LIMIT ?",
            tuple(params),
        )

    def count_questions_by_course(self, course_id: int) -> int:
        return self._query_one(
            "SELECT count(*) AS cnt FROM t_question WHERE course_id = ?", (course_id,),
        )["cnt"]

    def count_questions_by_document(self, course_id: int, document_id) -> int:
        """该文档精确挂载的题目数（不含课程通用题），供文档删除报告使用"""
        return self._query_one(
            "SELECT count(*) AS cnt FROM t_question WHERE course_id = ? AND document_id = ?",
            (course_id, document_id),
        )["cnt"]

    def count_questions_grouped_by_kp(self, course_id: int, document_id=None,
                                      include_course_level: bool = True,
                                      only_active: bool = False):
        """按知识点统计题目数，返回 (grouped, unlinked)。

        - grouped：{kp_id: 题目数}，只含**挂了知识点**的题；
        - unlinked：kp_id 为空（未挂知识点）的题目数。

        作用域口径与 list_questions 一致：document_id 传入时默认含课程通用题
        （document_id IS NULL）；only_active=True 时只数启用中的题（覆盖率报表用）。
        """
        where, params = ["course_id = ?"], [course_id]
        if document_id is not None:
            if include_course_level:
                where.append("(document_id = ? OR document_id IS NULL)")
            else:
                where.append("document_id = ?")
            params.append(document_id)
        if only_active:
            where.append("is_active = 1")

        rows = self._query(
            f"SELECT kp_id, count(*) AS c FROM t_question WHERE {' AND '.join(where)} "
            f"GROUP BY kp_id",
            tuple(params),
        )
        grouped, unlinked = {}, 0
        for r in rows:
            if r["kp_id"]:
                grouped[r["kp_id"]] = r["c"]
            else:
                unlinked = r["c"]              # 唯一一行：kp_id IS NULL
        return grouped, unlinked

    def question_answer_stats(self, course_id: int, graded_only: bool = True) -> dict:
        """按题统计作答人次与正确数，返回 {question_id: {"attempts": n, "correct": n}}。

        graded_only=True（默认）只统计已判分的记录：主观题提交后处于 PENDING 且
        is_correct=0，若不排除会把"未批改"误算成"答错"，污染推荐器的区分度信号与正确率。
        """
        sql = ("SELECT question_id, count(*) AS attempts, sum(is_correct) AS correct "
               "FROM t_answer_record WHERE course_id = ?")
        if graded_only:
            sql += " AND grade_status = 'GRADED'"
        sql += " GROUP BY question_id"
        rows = self._query(sql, (course_id,))
        if not rows:
            return {}
        return {
            r["question_id"]: {"attempts": r["attempts"], "correct": r["correct"] or 0}
            for r in rows
        }

    # ---------- 题库：学生答题记录 ----------

    def add_answer_record(self, user_id: int, course_id: int, document_id, question_id: int,
                          user_answer, is_correct: bool, score: float = 0,
                          grade_source: str = "AUTO", grade_status: str = "GRADED") -> int:
        """追加一条答题记录（不覆盖历史，同一题可多次作答）。

        grade_status 默认 'GRADED'（客观题提交即判分，与历史行为一致）；
        主观题（FILL/ESSAY）由上层显式传 'PENDING'——提交只落库、不判分，等待教师批改。
        """
        if grade_status not in ANSWER_GRADE_STATUS:
            grade_status = "GRADED"
        return self._execute(
            "INSERT INTO t_answer_record "
            "(user_id, course_id, document_id, question_id, user_answer, is_correct, "
            " score, grade_source, grade_status, answered_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, course_id, document_id, question_id, self._json_text(user_answer),
             1 if is_correct else 0, score, grade_source, grade_status, _now()),
        )

    def list_answer_records(self, user_id: int, course_id: int = None, document_id=None,
                            only_wrong: bool = False, include_pending: bool = True) -> list:
        """查询答题记录（时间倒序）；only_wrong=True 仅返回答错的记录（错题本原始数据）。

        include_pending=False 时排除「待批改」的主观题记录——错题本、正确率、掌握度
        都必须用这个口径，否则"交了但老师还没批"会被当成答错。
        """
        where, params = ["user_id = ?"], [user_id]
        if course_id is not None:
            where.append("course_id = ?")
            params.append(course_id)
        if document_id is not None:
            where.append("document_id = ?")
            params.append(document_id)
        if only_wrong:
            where.append("is_correct = 0")
        if not include_pending:
            where.append("grade_status = 'GRADED'")
        return self._query(
            f"SELECT * FROM t_answer_record WHERE {' AND '.join(where)} ORDER BY record_id DESC",
            tuple(params),
        )

    def list_answer_records_by_course(self, course_id: int) -> list:
        """某课程全部答题记录（教师端统计学生练习情况）"""
        return self._query(
            "SELECT * FROM t_answer_record WHERE course_id = ? ORDER BY user_id, record_id",
            (course_id,),
        )

    def count_answers_by_course(self, course_id: int) -> int:
        return self._query_one(
            "SELECT count(*) AS cnt FROM t_answer_record WHERE course_id = ?", (course_id,),
        )["cnt"]

    def count_answers_by_question(self, question_id: int) -> int:
        """某题被作答次数（题目是否可物理删除的判据：已作答过则只允许软删）"""
        return self._query_one(
            "SELECT count(*) AS cnt FROM t_answer_record WHERE question_id = ?", (question_id,),
        )["cnt"]

    def get_answer_record(self, record_id: int) -> dict:
        """取单条作答记录（批改前校验归属与题型用）"""
        return self._query_one(
            "SELECT * FROM t_answer_record WHERE record_id = ?", (record_id,)
        )

    # ---------- 题库：主观题批改（Scope B，就地更新 t_answer_record） ----------

    def grade_answer_record(self, record_id: int, score: float, is_correct: bool,
                            graded_by: int, comment: str = None) -> int:
        """教师批改单条作答：就地更新（分数/对错/评语/批改痕迹），返回影响行数。

        - grade_status 置 GRADED、grade_source 置 TEACHER：与客观题（AUTO）可区分；
        - 允许重批（覆盖旧分与旧评语）——这是「就地更新」方案的既定语义（不留痕）。
        """
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE t_answer_record SET score = ?, is_correct = ?, grade_status = 'GRADED', "
                "grade_source = 'TEACHER', graded_by = ?, graded_at = ?, comment = ? "
                "WHERE record_id = ?",
                (score, 1 if is_correct else 0, graded_by, _now(), comment, record_id),
            )
            conn.commit()
            return cur.rowcount

    def grade_answer_records_batch(self, record_ids: list, score: float, is_correct: bool,
                                   graded_by: int, comment: str = None) -> int:
        """批量批改（同一分数与评语），返回实际更新条数；空列表返回 0"""
        if not record_ids:
            return 0
        marks = ", ".join("?" for _ in record_ids)
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE t_answer_record SET score = ?, is_correct = ?, grade_status = 'GRADED', "
                f"grade_source = 'TEACHER', graded_by = ?, graded_at = ?, comment = ? "
                f"WHERE record_id IN ({marks})",
                (score, 1 if is_correct else 0, graded_by, _now(), comment) + tuple(record_ids),
            )
            conn.commit()
            return cur.rowcount

    def list_pending_answer_records(self, course_id: int, document_id=None, kp_id: str = None,
                                    student_id: int = None, limit: int = 50,
                                    offset: int = 0, status: str = "PENDING") -> list:
        """批改台列表（教师视角，联表带出题面 / 参考答案 / 学生信息）。

        - status='PENDING'（默认）：只取待批改，排序按 record_id 升序 = 先交先批；
        - status='GRADED'：取已批改（**限定主观题**，客观题由系统判分不属于人工批改台），
          排序按 graded_at 倒序 = 最近批改在前，供教师复查与改判；
        - status='ALL'：不过滤状态（仍按 record_id 升序）。
        document_id 口径与题目列表一致（含课程通用题）。
        参考答案随该接口下发给教师，学生端绝不可复用本方法。
        """
        where, params = ["r.course_id = ?"], [course_id]
        if status == "GRADED":
            where.append("r.grade_status = 'GRADED'")
            where.append(f"q.q_type IN ({', '.join('?' for _ in MANUAL_GRADE_TYPES)})")
            params.extend(MANUAL_GRADE_TYPES)
        elif status == "ALL":
            pass
        else:
            where.append("r.grade_status = 'PENDING'")
        if document_id is not None:
            where.append("(r.document_id = ? OR r.document_id IS NULL)")
            params.append(document_id)
        if kp_id:
            where.append("q.kp_id = ?")
            params.append(kp_id)
        if student_id is not None:
            where.append("r.user_id = ?")
            params.append(student_id)
        order_by = "r.graded_at DESC, r.record_id DESC" if status == "GRADED" else "r.record_id ASC"
        params.extend([limit, offset])
        return self._query(
            f"""
            SELECT r.*, q.stem AS stem, q.q_type AS q_type, q.kp_id AS kp_id,
                   q.options AS question_options,
                   q.answer AS reference_answer, q.analysis AS analysis,
                   q.difficulty AS difficulty, q.document_id AS question_document_id,
                   COALESCE(u.display_name, u.username, '') AS student_name,
                   u.username AS student_username
            FROM t_answer_record r
            JOIN t_question q ON q.question_id = r.question_id
            LEFT JOIN t_user u ON u.user_id = r.user_id
            WHERE {' AND '.join(where)}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
            """,
            tuple(params),
        )

    def count_pending_answer_records(self, course_id: int, document_id=None, kp_id: str = None,
                                     student_id: int = None, status: str = "PENDING") -> int:
        """批改台条数（与 list_pending_answer_records 同一口径，含 status 筛选）"""
        where, params = ["r.course_id = ?"], [course_id]
        if status == "GRADED":
            where.append("r.grade_status = 'GRADED'")
            where.append(f"q.q_type IN ({', '.join('?' for _ in MANUAL_GRADE_TYPES)})")
            params.extend(MANUAL_GRADE_TYPES)
        elif status == "ALL":
            pass
        else:
            where.append("r.grade_status = 'PENDING'")
        if document_id is not None:
            where.append("(r.document_id = ? OR r.document_id IS NULL)")
            params.append(document_id)
        if kp_id:
            where.append("q.kp_id = ?")
            params.append(kp_id)
        if student_id is not None:
            where.append("r.user_id = ?")
            params.append(student_id)
        return self._query_one(
            f"SELECT count(*) AS cnt FROM t_answer_record r "
            f"JOIN t_question q ON q.question_id = r.question_id "
            f"WHERE {' AND '.join(where)}",
            tuple(params),
        )["cnt"]

    def answer_grading_summary(self, course_id: int) -> dict:
        """批改进度汇总：{pending, graded, manual_total, auto_total, avg_score}

        - manual_total = 主观题（FILL/ESSAY）作答总数，auto_total = 客观题作答总数；
        - avg_score 只统计已批改记录：未批改记录的 0 分是占位值，不能拉低平均分。
        """
        rows = self._query(
            """
            SELECT q.q_type AS q_type, r.grade_status AS grade_status,
                   count(*) AS cnt, AVG(r.score) AS avg_score
            FROM t_answer_record r
            JOIN t_question q ON q.question_id = r.question_id
            WHERE r.course_id = ?
            GROUP BY q.q_type, r.grade_status
            """,
            (course_id,),
        )
        summary = {"pending": 0, "graded": 0, "manual_total": 0, "auto_total": 0,
                   "avg_score": 0.0}
        weighted, scored = 0.0, 0
        for r in rows:
            if r["grade_status"] == "PENDING":
                summary["pending"] += r["cnt"]
            else:
                summary["graded"] += r["cnt"]
                if r["avg_score"] is not None:
                    weighted += r["avg_score"] * r["cnt"]
                    scored += r["cnt"]
            if r["q_type"] in MANUAL_GRADE_TYPES:
                summary["manual_total"] += r["cnt"]
            else:
                summary["auto_total"] += r["cnt"]
        summary["avg_score"] = round(weighted / scored, 1) if scored else 0.0
        return summary

    def count_answers_grouped_by_course(self) -> dict:
        """按课程统计答题总数，返回 {course_id: count}"""
        rows = self._query(
            "SELECT course_id, count(*) AS cnt FROM t_answer_record GROUP BY course_id",
        )
        return {r["course_id"]: r["cnt"] for r in rows}

    # ---------- 题库：学生题目收藏（独立于知识点收藏 t_student_favorite） ----------

    def add_question_favorite(self, user_id: int, course_id: int, question_id: int) -> bool:
        """新增题目收藏（INSERT OR IGNORE 幂等）；返回是否新插入（True=新增，False=已存在）"""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO t_question_favorite (user_id, course_id, question_id) "
                "VALUES (?, ?, ?)",
                (user_id, course_id, question_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def remove_question_favorite(self, user_id: int, course_id: int, question_id: int) -> int:
        """取消题目收藏，返回删除条数"""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM t_question_favorite "
                "WHERE user_id = ? AND course_id = ? AND question_id = ?",
                (user_id, course_id, question_id),
            )
            conn.commit()
            return cur.rowcount

    def list_question_favorites(self, user_id: int, course_id: int) -> list:
        """查询某用户某课程的题目收藏（按收藏时间倒序）"""
        return self._query(
            "SELECT question_id, course_id, created_at FROM t_question_favorite "
            "WHERE user_id = ? AND course_id = ? ORDER BY id DESC",
            (user_id, course_id),
        )

    def list_question_favorite_ids(self, user_id: int, course_id: int) -> set:
        """某用户某课程已收藏的 question_id 集合（出题时回填 is_favorited 标记）"""
        rows = self._query(
            "SELECT question_id FROM t_question_favorite WHERE user_id = ? AND course_id = ?",
            (user_id, course_id),
        )
        return {r["question_id"] for r in rows}

    def count_question_favorites_grouped(self, course_id: int) -> dict:
        """按题统计某课程的收藏数，返回 {question_id: count}（教师端「题目收藏情况」）"""
        rows = self._query(
            "SELECT question_id, count(*) AS cnt FROM t_question_favorite "
            "WHERE course_id = ? GROUP BY question_id",
            (course_id,),
        )
        return {r["question_id"]: r["cnt"] for r in rows}

    def list_question_favorite_users(self, course_id: int, question_id: int = None) -> list:
        """某课程（或某题）的收藏明细：谁收藏了哪道题，[{user_id, question_id, created_at}]"""
        if question_id is not None:
            return self._query(
                "SELECT user_id, question_id, created_at FROM t_question_favorite "
                "WHERE course_id = ? AND question_id = ? ORDER BY id DESC",
                (course_id, question_id),
            )
        return self._query(
            "SELECT user_id, question_id, created_at FROM t_question_favorite "
            "WHERE course_id = ? ORDER BY id DESC",
            (course_id,),
        )

    # ---------- 题库：级联清理（防孤儿数据，顺序敏感：子表 -> 主表） ----------

    def delete_questions_by_course(self, course_id: int) -> int:
        """删除课程全部题目及其收藏/答题记录，返回删除的题目数（整课删除时调用）"""
        with self._connect() as conn:
            count = conn.execute(
                "SELECT count(*) AS cnt FROM t_question WHERE course_id = ?", (course_id,),
            ).fetchone()["cnt"]
            conn.execute(
                "DELETE FROM t_question_favorite WHERE question_id IN "
                "(SELECT question_id FROM t_question WHERE course_id = ?)", (course_id,),
            )
            conn.execute(
                "DELETE FROM t_answer_record WHERE question_id IN "
                "(SELECT question_id FROM t_question WHERE course_id = ?)", (course_id,),
            )
            conn.execute("DELETE FROM t_question WHERE course_id = ?", (course_id,))
            conn.commit()
            return count

    def delete_questions_by_document(self, course_id: int, document_id) -> int:
        """删除某文档精确挂载的题目（课程通用题 document_id IS NULL 刻意保留），返回题目数。

        防御性处理：若题目在「已被作答之后」才被改挂到别的文档，其答题记录的 document_id
        可能与题目当前 document_id 不一致，故这里先按 question_id 子查询清子表，再删题目，
        避免触发 t_answer_record / t_question_favorite 的外键约束。

        题目向量表（t_question_embedding）同属「按 question_id 挂在题目上」的子表，且无外键、
        不参与级联，必须在这里一并清理——漏掉会留下指向已删题目的孤儿向量。清理必须在
        DELETE FROM t_question 之前，否则子查询已取不到 question_id。
        """
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM t_question_favorite WHERE question_id IN "
                "(SELECT question_id FROM t_question WHERE course_id = ? AND document_id = ?)",
                (course_id, document_id),
            )
            conn.execute(
                "DELETE FROM t_answer_record WHERE question_id IN "
                "(SELECT question_id FROM t_question WHERE course_id = ? AND document_id = ?)",
                (course_id, document_id),
            )
            conn.execute(
                "DELETE FROM t_question_embedding WHERE question_id IN "
                "(SELECT question_id FROM t_question WHERE course_id = ? AND document_id = ?)",
                (course_id, document_id),
            )
            cur = conn.execute(
                "DELETE FROM t_question WHERE course_id = ? AND document_id = ?",
                (course_id, document_id),
            )
            conn.commit()
            return cur.rowcount

    def delete_question_favorites_by_document(self, course_id: int, document_id) -> int:
        """删除某文档题目的收藏记录（须先于题目删除调用），返回条数"""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM t_question_favorite WHERE question_id IN "
                "(SELECT question_id FROM t_question WHERE course_id = ? AND document_id = ?)",
                (course_id, document_id),
            )
            conn.commit()
            return cur.rowcount

    def delete_answers_by_document(self, course_id: int, document_id) -> int:
        """删除某文档的答题记录，返回条数"""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM t_answer_record WHERE course_id = ? AND document_id = ?",
                (course_id, document_id),
            )
            conn.commit()
            return cur.rowcount

    # ---------- 统计（数据总览） ----------

    def count_courses(self) -> int:
        """课程总数"""
        return self._query_one("SELECT count(*) AS cnt FROM t_course")["cnt"]

    def count_users_by_role(self, role: str) -> int:
        """按角色统计用户数（teacher / student）"""
        return self._query_one(
            "SELECT count(*) AS cnt FROM t_user WHERE role = ?", (role,),
        )["cnt"]

    def count_documents(self) -> int:
        """文档总数"""
        return self._query_one("SELECT count(*) AS cnt FROM t_document")["cnt"]

    # ---------- 管理员端：用户管理 ----------

    # 「最近活动」的取数口径：学习记录 / 答题 / 上传文档 / 加入课程 四类行为的时间最大值。
    # 项目里没有统一的用户活跃字段，也没有登录日志表，故用这四张已有的表聚合，
    # 取不到（从未产生上述行为，如刚注册的管理员）就返回 NULL，前端显示「—」而不是伪造时间。
    _LAST_ACTIVE_SQL = """
        (SELECT MAX(t) FROM (
            SELECT MAX(updated_at) AS t FROM t_learning_record WHERE user_id = u.user_id
            UNION ALL SELECT MAX(answered_at) FROM t_answer_record WHERE user_id = u.user_id
            UNION ALL SELECT MAX(created_at) FROM t_document WHERE uploader_id = u.user_id
            UNION ALL SELECT MAX(created_at) FROM t_course_member WHERE user_id = u.user_id
        )) AS last_active
    """

    _ADMIN_USER_SORT = {
        "user_id": "u.user_id",
        "username": "u.username",
        "role": "u.role",
        "created_at": "u.created_at",
        "last_active": "last_active",
    }

    def list_users_admin(self, keyword: str = None, role: str = None, status: str = None,
                         page: int = 1, page_size: int = 20,
                         sort_by: str = None, sort_order: str = None):
        """管理员端用户列表（分页 + 搜索 + 角色/状态筛选 + 排序），返回 (total, rows)。

        status：active / disabled（映射 t_user.is_active）。
        LEFT JOIN t_user_profile 取真实姓名/学号/工号参与搜索，缺失资料的用户不会因此丢失。
        """
        where, params = [], []
        if keyword:
            like = f"%{keyword}%"
            where.append(
                "(u.username LIKE ? OR u.display_name LIKE ? OR u.email LIKE ? "
                "OR p.real_name LIKE ? OR p.nickname LIKE ? "
                "OR p.student_no LIKE ? OR p.teacher_no LIKE ?)"
            )
            params.extend([like] * 7)
        if role:
            where.append("u.role = ?")
            params.append(role)
        if status == "active":
            where.append("u.is_active = 1")
        elif status == "disabled":
            where.append("u.is_active = 0")
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        base = f"FROM t_user u LEFT JOIN t_user_profile p ON p.user_id = u.user_id {where_sql}"
        total = self._query_one(f"SELECT count(*) AS cnt {base}", tuple(params))["cnt"]

        order_col = self._ADMIN_USER_SORT.get(sort_by or "", "u.user_id")
        direction = "ASC" if str(sort_order or "").lower() == "asc" else "DESC"
        rows = self._query(
            f"""
            SELECT u.user_id, u.username, u.role, u.display_name, u.email,
                   u.is_active, u.created_at,
                   p.real_name, p.nickname, p.school, p.college,
                   p.student_no, p.teacher_no, p.avatar_url,
                   {self._LAST_ACTIVE_SQL}
            {base}
            ORDER BY {order_col} {direction}
            LIMIT ? OFFSET ?
            """,
            tuple(params + [page_size, (page - 1) * page_size]),
        )
        return total, rows

    def get_user_admin_detail(self, user_id: int) -> dict:
        """用户详情：t_user + t_user_profile 全字段（不含 password_hash）。

        刻意不 SELECT u.*：避免把 password_hash 带进 API 层。管理员永远看不到密码哈希。
        """
        return self._query_one(
            """
            SELECT u.user_id, u.username, u.role, u.display_name, u.email,
                   u.is_active, u.created_at, u.updated_at,
                   p.avatar_url, p.real_name, p.nickname, p.gender, p.school, p.college,
                   p.bio, p.student_no, p.major, p.grade, p.class_name,
                   p.teacher_no, p.title, p.research_area
            FROM t_user u LEFT JOIN t_user_profile p ON p.user_id = u.user_id
            WHERE u.user_id = ?
            """,
            (user_id,),
        )

    def get_user_admin_stats(self, user_id: int) -> dict:
        """用户维度的统计（课程数 / 待批改 / 学习记录 / 收藏 / 上传文档数）"""
        return self._query_one(
            """
            SELECT
                (SELECT count(*) FROM t_course WHERE teacher_id = ?) AS owned_course_count,
                (SELECT count(*) FROM t_course_member
                  WHERE user_id = ? AND status = 'approved') AS joined_course_count,
                (SELECT count(*) FROM t_document WHERE uploader_id = ?) AS document_count,
                (SELECT count(*) FROM t_learning_record WHERE user_id = ?) AS learning_record_count,
                (SELECT count(*) FROM t_student_favorite WHERE user_id = ?) AS favorite_count,
                (SELECT count(*) FROM t_answer_record WHERE user_id = ?) AS answer_count
            """,
            (user_id, user_id, user_id, user_id, user_id, user_id),
        )

    def list_user_courses_admin(self, user_id: int) -> list:
        """用户所在课程列表（区分「自己创建」与「加入的」，供用户详情抽屉展示）"""
        return self._query(
            """
            SELECT c.course_id, c.course_name, c.status, c.archived_at,
                   COALESCE(c.governance_status, 'normal') AS governance_status,
                   c.created_at,
                   CASE WHEN c.teacher_id = ? THEN 'owner' ELSE COALESCE(m.role, 'student') END AS rel_role,
                   COALESCE(m.status, 'approved') AS member_status
            FROM t_course c
            LEFT JOIN t_course_member m ON m.course_id = c.course_id AND m.user_id = ?
            WHERE c.teacher_id = ? OR m.member_id IS NOT NULL
            ORDER BY c.course_id DESC
            """,
            (user_id, user_id, user_id),
        )

    def set_user_active(self, user_id: int, is_active: bool) -> int:
        """启用 / 禁用账号（管理员操作；与「用户自助注销」共用 is_active 字段）"""
        return self._execute(
            "UPDATE t_user SET is_active = ?, updated_at = ? WHERE user_id = ?",
            (1 if is_active else 0, _now(), user_id),
        )

    def set_user_role(self, user_id: int, role: str) -> int:
        """修改用户角色（管理员操作）。调用方必须先用 USER_ROLES 白名单校验。"""
        return self._execute(
            "UPDATE t_user SET role = ?, updated_at = ? WHERE user_id = ?",
            (role, _now(), user_id),
        )

    def delete_user(self, user_id: int) -> int:
        """物理删除用户（仅在没有留下任何业务数据时允许，由 AdminService 前置校验）。

        这里只删 t_user 与其资料行：t_user 被 t_course / t_document / t_learning_record
        等表引用，若仍有引用行会因外键约束失败而报错——这正是我们想要的「拒绝删除」。
        """
        with self._connect() as conn:
            conn.execute("DELETE FROM t_user_profile WHERE user_id = ?", (user_id,))
            cur = conn.execute("DELETE FROM t_user WHERE user_id = ?", (user_id,))
            conn.commit()
            return cur.rowcount

    def count_user_references(self, user_id: int) -> dict:
        """用户被业务数据引用的数量（删除前置校验用）"""
        return self._query_one(
            """
            SELECT
                (SELECT count(*) FROM t_course WHERE teacher_id = ?) AS owned_courses,
                (SELECT count(*) FROM t_document WHERE uploader_id = ?) AS documents,
                (SELECT count(*) FROM t_learning_record WHERE user_id = ?) AS learning_records,
                (SELECT count(*) FROM t_student_favorite WHERE user_id = ?) AS favorites,
                (SELECT count(*) FROM t_course_member WHERE user_id = ?) AS memberships,
                (SELECT count(*) FROM t_answer_record WHERE user_id = ?) AS answers,
                (SELECT count(*) FROM t_question WHERE created_by = ?) AS questions
            """,
            (user_id,) * 7,
        )

    # ---------- 管理员端：课程管理 ----------

    _COURSE_AGGREGATE_SQL = """
        (SELECT count(*) FROM t_course_member m
          WHERE m.course_id = c.course_id AND m.status = 'approved') AS member_count,
        (SELECT count(*) FROM t_course_member m
          WHERE m.course_id = c.course_id AND m.status = 'pending') AS pending_member_count,
        (SELECT count(*) FROM t_document d
          WHERE d.course_id = c.course_id) AS document_count,
        (SELECT COALESCE(SUM(d.entity_count), 0) FROM t_document d
          WHERE d.course_id = c.course_id) AS entity_count,
        (SELECT COALESCE(SUM(d.relation_count), 0) FROM t_document d
          WHERE d.course_id = c.course_id) AS relation_count
    """

    _ADMIN_COURSE_SORT = {
        "course_id": "c.course_id",
        "course_name": "c.course_name",
        "created_at": "c.created_at",
        "updated_at": "c.updated_at",
        "member_count": "member_count",
        "document_count": "document_count",
    }

    def list_courses_admin(self, keyword: str = None, teacher_id: int = None,
                           category: str = None, is_public: int = None,
                           status: int = None, governance_status: str = None,
                           only_without_teacher: bool = False,
                           inactive_days: int = None,
                           page: int = 1, page_size: int = 20,
                           sort_by: str = None, sort_order: str = None):
        """管理员端课程列表（全平台课程，不只「我的课程」），返回 (total, rows)。

        - keyword：课程名 / 描述 / 课程号 / 加课码
        - is_public / status / governance_status：公开状态、启用状态、治理状态筛选
        - only_without_teacher：无教师课程（teacher_id 为空或指向已不存在的用户）
        - inactive_days：长期无活动课程（updated_at 早于 N 天前）

        member_count / document_count / entity_count / relation_count 由子查询聚合，
        刻意不在 Python 里逐课程再查一次（N+1）。
        """
        where, params = [], []
        if keyword:
            like = f"%{keyword}%"
            where.append(
                "(c.course_name LIKE ? OR c.description LIKE ? "
                "OR c.course_code LIKE ? OR c.join_code LIKE ?)"
            )
            params.extend([like] * 4)
        if teacher_id is not None:
            where.append("c.teacher_id = ?")
            params.append(teacher_id)
        if category:
            where.append("c.category = ?")
            params.append(category)
        if is_public is not None:
            where.append("c.is_public = ?")
            params.append(is_public)
        if status is not None:
            where.append("c.status = ?")
            params.append(status)
        if governance_status:
            where.append("COALESCE(c.governance_status, 'normal') = ?")
            params.append(governance_status)
        if only_without_teacher:
            where.append(
                "(c.teacher_id IS NULL OR NOT EXISTS "
                " (SELECT 1 FROM t_user u2 WHERE u2.user_id = c.teacher_id))"
            )
        if inactive_days:
            where.append(
                "c.updated_at < datetime('now', 'localtime', ?)"
            )
            params.append(f"-{int(inactive_days)} days")
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        base = f"FROM t_course c LEFT JOIN t_user u ON c.teacher_id = u.user_id {where_sql}"
        total = self._query_one(f"SELECT count(*) AS cnt {base}", tuple(params))["cnt"]

        order_col = self._ADMIN_COURSE_SORT.get(sort_by or "", "c.course_id")
        direction = "ASC" if str(sort_order or "").lower() == "asc" else "DESC"
        rows = self._query(
            f"""
            SELECT c.course_id, c.course_name, c.course_code, c.description,
                   c.teacher_id, c.status, c.is_public, c.join_mode, c.category,
                   c.organization, c.join_code, c.created_at, c.updated_at,
                   COALESCE(c.governance_status, 'normal') AS governance_status,
                   c.governance_note, c.archived_at,
                   COALESCE(u.display_name, u.username, '') AS teacher_name,
                   u.is_active AS teacher_active,
                   {self._COURSE_AGGREGATE_SQL}
            {base}
            ORDER BY {order_col} {direction}
            LIMIT ? OFFSET ?
            """,
            tuple(params + [page_size, (page - 1) * page_size]),
        )
        return total, rows

    def get_course_admin_detail(self, course_id: int) -> dict:
        """管理员端课程详情（含教师名与聚合计数；不含内容级数据，内容走各自的 admin 接口）"""
        return self._query_one(
            f"""
            SELECT c.*, COALESCE(u.display_name, u.username, '') AS teacher_name,
                   u.username AS teacher_username, u.is_active AS teacher_active,
                   {self._COURSE_AGGREGATE_SQL}
            FROM t_course c LEFT JOIN t_user u ON c.teacher_id = u.user_id
            WHERE c.course_id = ?
            """,
            (course_id,),
        )

    def count_courses_by_governance(self) -> dict:
        """按治理状态统计课程数（工作台的课程治理概览）"""
        rows = self._query(
            "SELECT COALESCE(governance_status, 'normal') AS gs, count(*) AS cnt "
            "FROM t_course GROUP BY COALESCE(governance_status, 'normal')"
        )
        result = {k: 0 for k in COURSE_GOVERNANCE_STATUS}
        for r in rows:
            result[r["gs"]] = r["cnt"]
        return result

    def set_course_governance(self, course_id: int, governance_status: str,
                             note: str = None, archived_at: str = None) -> int:
        """设置课程治理状态（管理员操作）——**只动治理字段，绝不写 status**。

        为什么刻意不提供 status 参数（早期版本有过，已移除）：
        status 是课程的**业务状态**（教师/管理员显式设置的 0=关闭 / 1=开放），
        治理状态是**平台治理结论**（normal / hidden / archived），两者是正交的两维。
        一旦让下架去写 status=0、恢复去写 status=1，恢复就会把「教师原本就关闭的课程」
        一并打开——治理动作覆盖了业务事实，且事后无法区分。移除该参数后这种覆盖
        在类型层面就不可能发生，无需额外字段记录「治理前的状态」。

        代价是「下架」的可见性效果不再靠 status 生效，而由 COURSE_VISIBLE_TO_STUDENT
        这个组合谓词在各消费点统一判定（见 core/permissions.py 的 course_is_visible）。

        note 传 None 表示「不修改备注」，传空串表示「清空备注」。
        """
        sets = ["governance_status = ?", "updated_at = ?"]
        params = [governance_status, _now()]
        if archived_at is not None:
            sets.append("archived_at = ?")
            params.append(archived_at or None)
        if note is not None:
            sets.append("governance_note = ?")
            params.append(note or None)
        params.append(course_id)
        return self._execute(
            f"UPDATE t_course SET {', '.join(sets)} WHERE course_id = ?", tuple(params)
        )

    def transfer_course_owner(self, course_id: int, old_teacher_id: int, new_teacher_id: int) -> None:
        """转移课程负责人（管理员操作）。

        三步在同一事务内完成，避免出现「课程没有负责人」的中间态：
        1) t_course.teacher_id 指向新负责人；
        2) 新负责人写入 role=teacher/status=approved 的成员行（已存在则升级为教师并置回已通过）；
        3) 原负责人保留为协作教师成员（不删除其成员行，避免连带失去课程访问权）。
        """
        with self._connect() as conn:
            conn.execute(
                "UPDATE t_course SET teacher_id = ?, updated_at = ? WHERE course_id = ?",
                (new_teacher_id, _now(), course_id),
            )
            conn.execute(
                """
                INSERT INTO t_course_member
                    (course_id, user_id, role, status, join_source, joined_at, created_at, updated_at)
                VALUES (?, ?, 'teacher', 'approved', 'invite', ?, ?, ?)
                ON CONFLICT(course_id, user_id) DO UPDATE SET
                    role = 'teacher', status = 'approved', updated_at = excluded.updated_at
                """,
                (course_id, new_teacher_id, _now(), _now(), _now()),
            )
            if old_teacher_id and old_teacher_id != new_teacher_id:
                conn.execute(
                    """
                    INSERT INTO t_course_member
                        (course_id, user_id, role, status, join_source, joined_at, created_at, updated_at)
                    VALUES (?, ?, 'teacher', 'approved', 'create', ?, ?, ?)
                    ON CONFLICT(course_id, user_id) DO UPDATE SET
                        role = 'teacher', updated_at = excluded.updated_at
                    """,
                    (course_id, old_teacher_id, _now(), _now(), _now()),
                )
            conn.commit()

    # ---------- 管理员端：资源（文档）管理 ----------

    def list_documents_admin(self, keyword: str = None, course_id: int = None,
                             file_type: str = None, parse_status: str = None,
                             extract_status: str = None, uploader_id: int = None,
                             page: int = 1, page_size: int = 20,
                             sort_by: str = None, sort_order: str = None):
        """管理员端文档列表（全平台文档），返回 (total, rows)。

        系统监控与审计之外，「解析 / 抽取状态」是本系统最需要管理员关注的资源属性，
        故 parse_status / extract_status 都可单独筛选。
        """
        where, params = [], []
        if keyword:
            like = f"%{keyword}%"
            where.append("(d.file_name LIKE ? OR c.course_name LIKE ?)")
            params.extend([like, like])
        if course_id is not None:
            where.append("d.course_id = ?")
            params.append(course_id)
        if file_type:
            where.append("d.file_type = ?")
            params.append(file_type)
        if parse_status:
            where.append("d.parse_status = ?")
            params.append(parse_status)
        if extract_status:
            where.append("d.extract_status = ?")
            params.append(extract_status)
        if uploader_id is not None:
            where.append("d.uploader_id = ?")
            params.append(uploader_id)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        base = (
            "FROM t_document d "
            "LEFT JOIN t_course c ON d.course_id = c.course_id "
            "LEFT JOIN t_user u ON d.uploader_id = u.user_id "
            f"{where_sql}"
        )
        total = self._query_one(f"SELECT count(*) AS cnt {base}", tuple(params))["cnt"]

        sort_cols = {
            "doc_id": "d.doc_id", "created_at": "d.created_at",
            "file_size": "d.file_size", "file_name": "d.file_name",
        }
        order_col = sort_cols.get(sort_by or "", "d.doc_id")
        direction = "ASC" if str(sort_order or "").lower() == "asc" else "DESC"
        rows = self._query(
            f"""
            SELECT d.doc_id, d.course_id, d.uploader_id, d.file_name, d.file_type,
                   d.file_size, d.parse_status, d.extract_status, d.error_message,
                   d.chunk_count, d.entity_count, d.relation_count, d.created_at, d.updated_at,
                   COALESCE(c.course_name, '') AS course_name,
                   COALESCE(u.display_name, u.username, '') AS uploader_name
            {base}
            ORDER BY {order_col} {direction}
            LIMIT ? OFFSET ?
            """,
            tuple(params + [page_size, (page - 1) * page_size]),
        )
        return total, rows

    def count_documents_by_status(self) -> dict:
        """按解析/抽取状态统计文档数（系统监控的资源概览）"""
        parse_rows = self._query(
            "SELECT parse_status AS s, count(*) AS cnt FROM t_document GROUP BY parse_status"
        )
        extract_rows = self._query(
            "SELECT extract_status AS s, count(*) AS cnt FROM t_document GROUP BY extract_status"
        )
        return {
            "parse": {r["s"]: r["cnt"] for r in parse_rows},
            "extract": {r["s"]: r["cnt"] for r in extract_rows},
        }

    def list_extraction_tasks(self, course_id: int = None, status: str = None,
                              keyword: str = None, page: int = 1, page_size: int = 20):
        """知识抽取任务列表，返回 (total, rows)。

        本系统没有独立的异步任务表：一次「知识抽取」与一份文档一一对应
        （上传接口内同步解析 + 抽取，状态写在 t_document 的 parse_status /
        extract_status / entity_count / relation_count / error_message 上）。
        故这里以 t_document 为任务事实来源直接派生任务视图，不新建任务表、
        也不引入与现有同步流程并行的第二套状态机（避免两处状态不一致）。

        状态映射（status 参数取 processing / success / failed / pending）：
          PENDING                         -> pending    等待中
          PARSING / EXTRACTING            -> processing 处理中
          COMPLETED                       -> success    成功
          FAILED                          -> failed     失败
        失败优先：parse_status 或 extract_status 任一为 FAILED 即归入 failed。
        """
        case_status = """
            CASE
                WHEN d.parse_status = 'FAILED' OR d.extract_status = 'FAILED' THEN 'failed'
                WHEN d.parse_status IN ('PARSING') OR d.extract_status = 'EXTRACTING' THEN 'processing'
                WHEN d.parse_status = 'PARSED' AND d.extract_status = 'COMPLETED' THEN 'success'
                ELSE 'pending'
            END
        """
        where, params = [], []
        if course_id is not None:
            where.append("d.course_id = ?")
            params.append(course_id)
        if keyword:
            like = f"%{keyword}%"
            where.append("(d.file_name LIKE ? OR c.course_name LIKE ?)")
            params.extend([like, like])
        if status:
            where.append(f"{case_status} = ?")
            params.append(status)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        base = (
            "FROM t_document d LEFT JOIN t_course c ON d.course_id = c.course_id "
            f"{where_sql}"
        )
        total = self._query_one(f"SELECT count(*) AS cnt {base}", tuple(params))["cnt"]
        rows = self._query(
            f"""
            SELECT d.doc_id AS task_id, d.course_id, d.file_name,
                   COALESCE(c.course_name, '') AS course_name,
                   d.parse_status, d.extract_status, d.error_message,
                   d.chunk_count, d.entity_count, d.relation_count,
                   d.created_at AS started_at,
                   CASE WHEN d.extract_status IN ('COMPLETED', 'FAILED')
                        THEN d.updated_at ELSE NULL END AS finished_at,
                   {case_status} AS task_status
            {base}
            ORDER BY d.doc_id DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params + [page_size, (page - 1) * page_size]),
        )
        return total, rows

    def count_extraction_tasks(self) -> dict:
        """抽取任务状态分布（工作台 / 系统监控用）"""
        row = self._query_one(
            """
            SELECT
                sum(CASE WHEN parse_status = 'FAILED' OR extract_status = 'FAILED'
                         THEN 1 ELSE 0 END) AS failed,
                sum(CASE WHEN parse_status = 'PARSING' OR extract_status = 'EXTRACTING'
                         THEN 1 ELSE 0 END) AS processing,
                sum(CASE WHEN parse_status = 'PARSED' AND extract_status = 'COMPLETED'
                         THEN 1 ELSE 0 END) AS success,
                count(*) AS total
            FROM t_document
            """
        ) or {}
        total = row.get("total") or 0
        failed = row.get("failed") or 0
        processing = row.get("processing") or 0
        success = row.get("success") or 0
        return {
            "total": total, "success": success, "failed": failed,
            "processing": processing, "pending": max(total - success - failed - processing, 0),
        }

    # ---------- 管理员端：审计日志 ----------

    def add_audit_log(self, operator_id: int, operator_name: str, operator_role: str,
                      action: str, target_type: str = None, target_id=None,
                      result: str = "success", detail: str = None,
                      ip: str = None, user_agent: str = None) -> int:
        """写入一条管理员操作审计日志（只追加，不提供更新/删除接口）"""
        return self._execute(
            "INSERT INTO t_admin_audit_log "
            "(operator_id, operator_name, operator_role, action, target_type, target_id, "
            " result, detail, ip, user_agent) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (operator_id, operator_name, operator_role, action, target_type,
             None if target_id is None else str(target_id), result, detail, ip, user_agent),
        )

    def list_audit_logs(self, operator_id: int = None, action: str = None,
                        target_type: str = None, target_id=None, result: str = None,
                        keyword: str = None, start_time: str = None, end_time: str = None,
                        page: int = 1, page_size: int = 20):
        """审计日志查询（时间 / 操作人 / 操作动作 / 目标类型 / 目标 ID / 结果 / 关键词 + 分页）

        target_id 与 target_type 配合使用，用于「某个对象的操作记录」这类定向查询
        （如用户详情抽屉里的相关操作）。target_id 在库中是 TEXT，故按字符串比较。
        """
        where, params = [], []
        if operator_id is not None:
            where.append("operator_id = ?")
            params.append(operator_id)
        if action:
            where.append("action = ?")
            params.append(action)
        if target_type:
            where.append("target_type = ?")
            params.append(target_type)
        if target_id is not None:
            where.append("target_id = ?")
            params.append(str(target_id))
        if result:
            where.append("result = ?")
            params.append(result)
        if keyword:
            like = f"%{keyword}%"
            where.append(
                "(detail LIKE ? OR operator_name LIKE ? OR target_id LIKE ? OR action LIKE ?)"
            )
            params.extend([like] * 4)
        # 时间筛选统一用「日期或日期时间字符串」比较：SQLite 的 TEXT 时间按字典序比较，
        # 'YYYY-MM-DD HH:MM:SS' 的字典序与时间序一致，故可直接用 >= / <=。
        if start_time:
            where.append("created_at >= ?")
            params.append(start_time)
        if end_time:
            where.append("created_at <= ?")
            params.append(end_time)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        total = self._query_one(
            f"SELECT count(*) AS cnt FROM t_admin_audit_log {where_sql}", tuple(params)
        )["cnt"]
        rows = self._query(
            f"""
            SELECT log_id, operator_id, operator_name, operator_role, action,
                   target_type, target_id, result, detail, ip, user_agent, created_at
            FROM t_admin_audit_log {where_sql}
            ORDER BY log_id DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params + [page_size, (page - 1) * page_size]),
        )
        return total, rows

    def count_audit_logs(self) -> int:
        return self._query_one("SELECT count(*) AS cnt FROM t_admin_audit_log")["cnt"]

    # ---------- 管理员端：平台总览 ----------

    def admin_platform_counts(self) -> dict:
        """平台核心计数（管理员工作台顶部统计）。

        用户/课程/文档口径复用已有的 count_* 方法；admin_count 直接按 role 统计。
        """
        return {
            "user_count": self._query_one("SELECT count(*) AS c FROM t_user")["c"],
            "admin_count": self.count_users_by_role("admin"),
            "teacher_count": self.count_users_by_role("teacher"),
            "student_count": self.count_users_by_role("student"),
            "active_user_count": self._query_one(
                "SELECT count(*) AS c FROM t_user WHERE is_active = 1")["c"],
            "disabled_user_count": self._query_one(
                "SELECT count(*) AS c FROM t_user WHERE is_active = 0")["c"],
            "course_count": self.count_courses(),
            # 公开课程数：与 COURSE_VISIBLE_SQL 同一口径——已下架/归档的课程不算「公开可加入」
            "public_course_count": self._query_one(
                f"SELECT count(*) AS c FROM t_course WHERE is_public = 1 AND {COURSE_VISIBLE_SQL}")["c"],
            "document_count": self.count_documents(),
            "member_count": self._query_one(
                "SELECT count(*) AS c FROM t_course_member WHERE status = 'approved'")["c"],
            "question_count": self._query_one("SELECT count(*) AS c FROM t_question")["c"],
        }

    def admin_platform_trend(self, days: int = 14) -> list:
        """按天统计「新增用户 / 新增课程 / 新增文档」，返回最近 days 天（含今天）。

        只统计真实存在的 created_at，不做任何估算或补值外推。
        空缺的日期在这里补 0（而不是让前端自己猜），这样图上不会出现断点。
        """
        from datetime import date, timedelta

        # None 表示「用默认值」，显式传 0 / 负数则夹到 1 —— 不用 `days or 14`，
        # 否则 days=0 会被悄悄当成 14，与「夹到 [1,90]」的语义不符。
        days = 14 if days is None else max(1, min(int(days), 90))
        today = date.today()
        start = today - timedelta(days=days - 1)

        def _daily(sql: str) -> dict:
            return {
                r["d"]: r["c"]
                for r in self._query(sql, (start.isoformat(),))
                if r.get("d")
            }

        users = _daily("SELECT date(created_at) AS d, count(*) AS c FROM t_user "
                       "WHERE date(created_at) >= ? GROUP BY d")
        courses = _daily("SELECT date(created_at) AS d, count(*) AS c FROM t_course "
                         "WHERE date(created_at) >= ? GROUP BY d")
        documents = _daily("SELECT date(created_at) AS d, count(*) AS c FROM t_document "
                           "WHERE date(created_at) >= ? GROUP BY d")

        trend = []
        for i in range(days):
            d = (start + timedelta(days=i)).isoformat()
            trend.append({
                "date": d,
                "users": users.get(d, 0),
                "courses": courses.get(d, 0),
                "documents": documents.get(d, 0),
            })
        return trend

    def list_recent_audit_logs(self, limit: int = 10) -> list:
        """最近的管理员操作（工作台「最近活动」）"""
        return self._query(
            "SELECT log_id, operator_name, operator_role, action, target_type, target_id, "
            "result, detail, created_at FROM t_admin_audit_log "
            "ORDER BY log_id DESC LIMIT ?",
            (limit,),
        )

    def list_recent_users(self, limit: int = 10) -> list:
        """最近注册的用户（工作台「最近活动」）"""
        return self._query(
            "SELECT user_id, username, role, display_name, is_active, created_at "
            "FROM t_user ORDER BY user_id DESC LIMIT ?",
            (limit,),
        )

    def list_recent_courses(self, limit: int = 10) -> list:
        """最近创建的课程（工作台「最近活动」）"""
        return self._query(
            """
            SELECT c.course_id, c.course_name, c.created_at, c.status,
                   COALESCE(c.governance_status, 'normal') AS governance_status,
                   COALESCE(u.display_name, u.username, '') AS teacher_name
            FROM t_course c LEFT JOIN t_user u ON c.teacher_id = u.user_id
            ORDER BY c.course_id DESC LIMIT ?
            """,
            (limit,),
        )

    def list_recent_documents(self, limit: int = 10) -> list:
        """最近上传的文档（工作台「最近活动」）"""
        return self._query(
            """
            SELECT d.doc_id, d.file_name, d.course_id, d.file_type,
                   d.parse_status, d.extract_status, d.created_at,
                   COALESCE(c.course_name, '') AS course_name,
                   COALESCE(u.display_name, u.username, '') AS uploader_name
            FROM t_document d
            LEFT JOIN t_course c ON d.course_id = c.course_id
            LEFT JOIN t_user u ON d.uploader_id = u.user_id
            ORDER BY d.doc_id DESC LIMIT ?
            """,
            (limit,),
        )


# 全局关系型数据库实例
sql_db = SQLDatabase()
