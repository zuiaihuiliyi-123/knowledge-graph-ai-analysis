"""SQLite 建表 SQL、枚举常量与课程可见性判定。

从 core/sql_database.py 原样迁出；顺序与取值保持不变，勿在此处重排。
"""

# 枚举取值（与规划文档表格 8/9/10/11 的 ENUM 定义一致，供应用层校验）
# admin 为管理员端新增角色（平台治理，不属于任何课程的教学角色）。
# 注意：注册接口（api/auth.py）刻意不接受 "admin"，管理员只能由既有管理员在后台指派。
USER_ROLES = ("admin", "teacher", "student")

# 已注销账号被同名重新注册时，旧账号的用户名归档为此前缀：
#   __deleted_user_<user_id>__<原用户名>
# 只改 username，不动 user_id / is_active / 业务数据；外键与历史记录仍然指向旧账号。
# 注册接口拒绝该前缀，避免真实用户注册到会被归档逻辑占用的名字。
ARCHIVED_USERNAME_PREFIX = "__deleted_user_"
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
    # ---- 文档级知识融合（管理员端；Dry Run 不落审计，只有真正写库的操作才记） ----
    "fusion.scan",             # 扫描候选并落库（persist=true 时才记）
    "fusion.review",           # 审核候选（通过/驳回/暂缓）
    "fusion.apply",            # 应用融合（dry_run=true 时不记）
    "fusion.revoke",           # 撤销单条融合
    "fusion.undo_run",         # 撤销整批融合
)

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
        -- JWT 吊销版本号：改密 / 禁用 / 改角色 / 登出时 +1，旧 token 立即失效
        token_version INTEGER NOT NULL DEFAULT 0,
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
        -- 图谱构建阶段（批量上传需要把「抽取完成」与「图谱已入库」区分开）：
        -- PENDING -> BUILDING -> COMPLETED / FAILED。
        -- 旧库由 _migrate_document_graph_status 补列并按 extract_status 回填，
        -- 故 CHECK 只在新建库生效（SQLite 的 ADD COLUMN 不支持 CHECK）。
        graph_status      TEXT NOT NULL DEFAULT 'PENDING'
                          CHECK (graph_status IN ('PENDING', 'BUILDING', 'COMPLETED', 'FAILED')),
        error_message     TEXT,
        chunk_count       INTEGER NOT NULL DEFAULT 0,
        entity_count      INTEGER NOT NULL DEFAULT 0,
        relation_count    INTEGER NOT NULL DEFAULT 0,
        vector_collection TEXT,
        -- 抽取任务本身的起止时刻。刻意与 updated_at 分开：updated_at 是「行最后修改
        -- 时间」，回填统计 / 重建图谱 / 改元数据都会刷新它，曾经被拿去算耗时，结果把
        -- 一个月前抽完的文档显示成 48889 分。旧库由 _migrate_document_extract_timing
        -- 补列，历史行为 NULL（真实时刻从未记录过，无法还原）。
        extract_started_at  TEXT,
        extract_finished_at TEXT,
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
        -- 知识点名快照：重新抽取会整体更换 kp_id，名字快照用于把旧掌握记录映射回同名新节点
        kp_name         TEXT,
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
        -- 知识点名快照：重新抽取会更换 kp_id，收藏需要按同名回挂到新节点
        kp_name     TEXT,
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

    # ---------- L2：一题多知识点（召回层与多标签诊断的数据基础） ----------
    # 4.2.16 题目-知识点关联表：一题可挂 N 个知识点，复合主键 (question_id, kp_id) 天然幂等。
    # is_primary 为「权威」定义：t_question.kp_id 是它的冗余投影（兼容期双写），
    # 「一题最多一个主知识点」由 uq_qkp_primary 部分唯一索引在 DB 层保证，而非应用层自觉。
    # 与 t_answer_record / t_question_favorite 同一先例：question_id 设物理外键。
    """
    CREATE TABLE IF NOT EXISTS t_question_kp (
        question_id INTEGER NOT NULL,
        kp_id       TEXT NOT NULL,
        score       REAL,                                              -- 标注置信度（kp_labeler 产物）
        source      TEXT NOT NULL DEFAULT 'MANUAL'
                    CHECK (source IN ('MANUAL', 'AI', 'IMPORT')),      -- 与 QUESTION_SOURCE 同口径
        is_primary  INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
        created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        PRIMARY KEY (question_id, kp_id),
        FOREIGN KEY (question_id) REFERENCES t_question(question_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_qkp_kp ON t_question_kp(kp_id);",
    # 部分唯一索引（SQLite 自 3.8.0 起支持）：只对 is_primary=1 的行做 (question_id) 唯一约束
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_qkp_primary "
    "ON t_question_kp(question_id) WHERE is_primary = 1;",

    # ---------- 文档级知识融合与实体消歧（Fusion） ----------
    # 融合是「抽取后的后处理层」：**Neo4j 全程只读**，融合状态只落在这里的 SQLite 表，
    # 读图时由 kg_manager.get_graph_v1 在 Python 层把源节点折进目标节点（无 ACTIVE 映射时为纯 no-op）。
    # 隔离边界是 (course_id, document_id)：五张表都带这两列，任何读写都必须带上，禁止跨文档/跨课程。
    # kp_id / document_id 刻意**不设物理外键**（与 t_kp_embedding / t_kp_text 同一先例）：
    # _connect() 默认开 PRAGMA foreign_keys=ON，给 t_document 设 FK 且无 CASCADE 会让
    # 删文档的手工级联直接报错；仓库全线用手工级联而非 CASCADE。
    #
    # 4.2.17 融合批次：一次扫描/应用任务。DRY_RUN 只用于留痕，真正落行的只有 APPLY。
    # CHECK 取值一次性写全 —— SQLite 改 CHECK 需要重建表，新表定义时不留后患。
    """
    CREATE TABLE IF NOT EXISTS t_kp_fusion_run (
        run_id           TEXT PRIMARY KEY,
        course_id        INTEGER NOT NULL,
        document_id      INTEGER NOT NULL,
        mode             TEXT NOT NULL CHECK (mode IN ('DRY_RUN', 'APPLY')),
        status           TEXT NOT NULL DEFAULT 'RUNNING'
                         CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED', 'REVOKED')),
        total_candidates INTEGER NOT NULL DEFAULT 0,
        n_same           INTEGER NOT NULL DEFAULT 0,
        n_different      INTEGER NOT NULL DEFAULT 0,
        n_uncertain      INTEGER NOT NULL DEFAULT 0,
        n_applied        INTEGER NOT NULL DEFAULT 0,
        config_json      TEXT,
        operator_id      INTEGER,
        operator_name    TEXT,
        error_message    TEXT,
        started_at       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        finished_at      TEXT,
        revoked_at       TEXT,
        revoked_by       INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_fusion_run_scope "
    "ON t_kp_fusion_run(course_id, document_id, started_at);",

    # 4.2.18 候选对。刻意**不按 run 分区**：UNIQUE 跨 run 复用，
    # 「上一轮被驳回的候选」才不会在下一轮扫描里无限重现（见 upsert_fusion_candidates）。
    """
    CREATE TABLE IF NOT EXISTS t_kp_fusion_candidate (
        candidate_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id       INTEGER NOT NULL,
        document_id     INTEGER NOT NULL,
        source_kp_id    TEXT NOT NULL,
        target_kp_id    TEXT NOT NULL,
        source_name     TEXT,
        target_name     TEXT,
        source_category TEXT,
        target_category TEXT,
        score           REAL NOT NULL DEFAULT 0,
        score_detail    TEXT,
        decision        TEXT NOT NULL DEFAULT 'UNCERTAIN'
                        CHECK (decision IN ('SAME', 'DIFFERENT', 'UNCERTAIN')),
        decision_source TEXT NOT NULL DEFAULT 'RULE'
                        CHECK (decision_source IN ('RULE', 'LLM', 'MANUAL')),
        reason          TEXT,
        status          TEXT NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN ('PENDING', 'ACCEPTED', 'REJECTED',
                                          'DEFERRED', 'SUPERSEDED')),
        last_run_id     TEXT,
        reviewed_by     INTEGER,
        reviewed_at     TEXT,
        review_comment  TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        UNIQUE (course_id, document_id, source_kp_id, target_kp_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_fusion_cand_scope "
    "ON t_kp_fusion_candidate(course_id, document_id, status, decision);",

    # 4.2.19 已应用的融合映射 —— 读图折叠的唯一数据源。
    # UNIQUE(course_id, document_id, source_kp_id) 保证「目标唯一」：一个源最多一条出边。
    # 因此映射必构成一片有向森林（出度 ≤ 1 + 写时防环），每个连通分量的 root 唯一。
    # 撤销 = 把 status 改成 REVOKED（**不删行**），历史映射、决策依据与操作人全部保留。
    # *_name / *_category / source_description 是**属性快照**：kp_id 在重新抽取后会整体更换，
    # 有快照时历史记录仍然可读。
    """
    CREATE TABLE IF NOT EXISTS t_kp_fusion_map (
        fusion_id          INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id          INTEGER NOT NULL,
        document_id        INTEGER NOT NULL,
        source_kp_id       TEXT NOT NULL,
        target_kp_id       TEXT NOT NULL,
        source_name        TEXT,
        target_name        TEXT,
        source_category    TEXT,
        target_category    TEXT,
        source_description TEXT,
        score              REAL,
        decision_source    TEXT,
        reason             TEXT,
        run_id             TEXT,
        candidate_id       INTEGER,
        status             TEXT NOT NULL DEFAULT 'ACTIVE'
                           CHECK (status IN ('ACTIVE', 'REVOKED')),
        operator_id        INTEGER,
        operator_name      TEXT,
        created_at         TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        revoked_at         TEXT,
        revoked_by         INTEGER,
        UNIQUE (course_id, document_id, source_kp_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_fusion_map_scope "
    "ON t_kp_fusion_map(course_id, document_id, status);",

    # 4.2.20 冲突/异常记录。只追加，**不因撤销而消失**。
    """
    CREATE TABLE IF NOT EXISTS t_kp_fusion_conflict (
        conflict_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id     INTEGER NOT NULL,
        document_id   INTEGER NOT NULL,
        conflict_type TEXT NOT NULL
                      CHECK (conflict_type IN ('CYCLE', 'MULTI_TARGET',
                                               'ENDPOINT_MISSING', 'CROSS_DOC')),
        source_kp_id  TEXT,
        target_kp_id  TEXT,
        detail        TEXT,
        run_id        TEXT,
        status        TEXT NOT NULL DEFAULT 'OPEN'
                      CHECK (status IN ('OPEN', 'RESOLVED')),
        created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        resolved_at   TEXT,
        resolved_by   INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_fusion_conflict_scope "
    "ON t_kp_fusion_conflict(course_id, document_id, status);",

    # 4.2.21 融合 epoch：索引新鲜度的权威信号。
    # 每次 apply / revoke / 整批撤销 / 文档删除都在**同一事务内** +1。
    # 为什么不能只靠「t_kp_embedding 的 kp_id 集合是否一致」：Neo4j 不可用时
    # embedding._load_kp_records 会回落 t_kp_text 缓存，两边集合可能同时为空而
    # 「看似一致」，从而掩盖「融合状态已经变过、索引需要重建」。epoch 顺序无关且
    # 不依赖图库可用性，正好补这个洞。
    """
    CREATE TABLE IF NOT EXISTS t_kp_fusion_scope (
        course_id     INTEGER NOT NULL,
        document_id   INTEGER NOT NULL,
        epoch         INTEGER NOT NULL DEFAULT 0,
        indexed_epoch INTEGER,
        updated_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        PRIMARY KEY (course_id, document_id)
    )
    """,

    # 4.2.22 文档处理任务（阶段 4：断点续跑的可观测部分）。
    # 一行 = 一篇文档的**当前**处理任务（不是历史流水）：stage/status/attempts 便于
    # 排查「卡住」「反复失败」，heartbeat_at 由每个阶段刷新，用来判断进程是否还活着。
    # 中间产物（parsed.txt / extracted.json）在文件系统 TASK_STATE_DIR 下，不放库里：
    # 抽取结果动辄几十 KB，塞进 SQLite 会让每次列表查询都带着走。
    """
    CREATE TABLE IF NOT EXISTS t_document_task (
        doc_id        INTEGER PRIMARY KEY,
        course_id     INTEGER NOT NULL,
        stage         TEXT NOT NULL DEFAULT 'PARSE'
                      CHECK (stage IN ('PARSE', 'EXTRACT', 'BUILD', 'DONE')),
        status        TEXT NOT NULL DEFAULT 'IDLE'
                      CHECK (status IN ('IDLE', 'RUNNING', 'DONE', 'FAILED')),
        attempts      INTEGER NOT NULL DEFAULT 0,
        artifacts     TEXT,
        heartbeat_at  TEXT,
        error_message TEXT,
        created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        updated_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        FOREIGN KEY (doc_id) REFERENCES t_document(doc_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_document_task_status ON t_document_task(status);",
]
