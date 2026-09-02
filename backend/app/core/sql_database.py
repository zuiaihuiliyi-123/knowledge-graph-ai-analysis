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
import sqlite3
from datetime import datetime

from .config import settings
from .security import hash_password

# 枚举取值（与规划文档表格 8/9/10/11 的 ENUM 定义一致，供应用层校验）
USER_ROLES = ("teacher", "student")
DOC_FILE_TYPES = ("PDF", "TXT", "DOCX", "MD")
DOC_PARSE_STATUS = ("UPLOADED", "PARSING", "PARSED", "FAILED")
DOC_EXTRACT_STATUS = ("PENDING", "EXTRACTING", "COMPLETED", "FAILED")
RECORD_STATUS = ("MASTERED", "LEARNING", "RECOMMENDED")
RECORD_SOURCE = ("MANUAL", "SYSTEM")

# 默认教师账号初始密码（仅用于演示/初始开发；生产环境应删除默认账号或改为环境变量注入）
DEFAULT_TEACHER_PASSWORD = "admin123"


def _now() -> str:
    """返回当前本地时间字符串（MySQL DATETIME 兼容格式）"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 建表 DDL（含索引、CHECK 约束、外键）。顺序敏感：先建被引用的父表。
_SCHEMA_SQL = [
    # 4.2.1 用户表
    """
    CREATE TABLE IF NOT EXISTS t_user (
        user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
        username      TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role          TEXT NOT NULL DEFAULT 'student' CHECK (role IN ('teacher', 'student')),
        display_name  TEXT,
        email         TEXT,
        is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
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
        course_id  INTEGER NOT NULL,
        kp_id      TEXT NOT NULL,
        embedding  TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        PRIMARY KEY (course_id, kp_id)
    )
    """,

    # 4.2.6 学生收藏表（收藏 = 学生个人知识点书签，独立于学习状态；kp_id 为逻辑外键指向 Neo4j）
    """
    CREATE TABLE IF NOT EXISTS t_student_favorite (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        course_id  INTEGER NOT NULL,
        kp_id      TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        FOREIGN KEY (user_id) REFERENCES t_user(user_id),
        FOREIGN KEY (course_id) REFERENCES t_course(course_id),
        UNIQUE (user_id, course_id, kp_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_fav_user_course ON t_student_favorite(user_id, course_id);",
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

    def list_users(self) -> list:
        return self._query("SELECT * FROM t_user ORDER BY user_id")

    def ensure_default_teacher(self) -> int:
        """确保存在默认教师账号 admin（初始密码 admin123，仅用于演示），返回其 user_id"""
        existing = self.get_user_by_username("admin")
        if existing:
            # 迁移：旧版占位密码 "<not-implemented>" 无法通过校验，替换为真实哈希
            if existing["password_hash"] == "<not-implemented>":
                self._execute(
                    "UPDATE t_user SET password_hash = ? WHERE user_id = ?",
                    (hash_password(DEFAULT_TEACHER_PASSWORD), existing["user_id"]),
                )
                existing = self.get_user_by_username("admin")
            return existing["user_id"]
        return self.create_user(
            username="admin", password_hash=hash_password(DEFAULT_TEACHER_PASSWORD),
            role="teacher", display_name="默认教师",
        )

    # ---------- 课程 ----------

    def create_course(self, course_name: str, teacher_id: int,
                      course_code: str = None, description: str = None) -> int:
        return self._execute(
            "INSERT INTO t_course (course_name, teacher_id, course_code, description) "
            "VALUES (?, ?, ?, ?)",
            (course_name, teacher_id, course_code, description),
        )

    def get_course(self, course_id: int) -> dict:
        return self._query_one("SELECT * FROM t_course WHERE course_id = ?", (course_id,))

    def list_courses(self) -> list:
        return self._query("SELECT * FROM t_course ORDER BY course_id")

    def get_course_by_name(self, course_name: str) -> dict:
        return self._query_one("SELECT * FROM t_course WHERE course_name = ?", (course_name,))

    def get_course_by_code(self, course_code: str) -> dict:
        return self._query_one("SELECT * FROM t_course WHERE course_code = ?", (course_code,))

    def list_courses_page(self, page: int = 1, page_size: int = 10,
                          teacher_id: int = None, keyword: str = None):
        """分页查询课程（LEFT JOIN 教师表取教师名），返回 (total, rows)"""
        where, params = [], []
        if teacher_id is not None:
            where.append("c.teacher_id = ?")
            params.append(teacher_id)
        if keyword:
            where.append("c.course_name LIKE ?")
            params.append(f"%{keyword}%")
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
        """更新课程字段（白名单，None 跳过表示不修改），自动刷新 updated_at"""
        allowed = {"course_name", "course_code", "description", "status"}
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
        """删除课程及其文档、学习记录、收藏（按子表->父表顺序满足外键），返回删除的文档数"""
        doc_count = self.count_documents_by_course(course_id)
        with self._connect() as conn:
            conn.execute("DELETE FROM t_student_favorite WHERE course_id = ?", (course_id,))
            conn.execute("DELETE FROM t_learning_record WHERE course_id = ?", (course_id,))
            conn.execute("DELETE FROM t_document WHERE course_id = ?", (course_id,))
            conn.execute("DELETE FROM t_course WHERE course_id = ?", (course_id,))
            conn.commit()
        return doc_count

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

    def count_documents_by_course(self, course_id: int) -> int:
        return self._query_one(
            "SELECT count(*) AS cnt FROM t_document WHERE course_id = ?", (course_id,),
        )["cnt"]

    def count_documents_grouped(self) -> dict:
        """按课程统计文档数，返回 {course_id: count}"""
        rows = self._query("SELECT course_id, count(*) AS cnt FROM t_document GROUP BY course_id")
        return {r["course_id"]: r["cnt"] for r in rows}

    # ---------- 学习记录 ----------

    def upsert_learning_record(self, user_id: int, course_id: int, kp_id: str,
                               status: str = "MASTERED", mastery_level: int = 100,
                               source: str = "MANUAL", last_learned_at: str = None) -> int:
        """
        写入学习记录；依赖 UNIQUE(user_id, course_id, kp_id) 冲突时更新，
        保证同一学生、同一课程、同一知识点仅一条记录。
        注：ON CONFLICT DO UPDATE 为 SQLite 语法，迁 MySQL 时改为 ON DUPLICATE KEY UPDATE。
        """
        now = _now()
        sql = """
        INSERT INTO t_learning_record
            (user_id, course_id, kp_id, status, mastery_level, source, last_learned_at,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, course_id, kp_id) DO UPDATE SET
            status = excluded.status,
            mastery_level = excluded.mastery_level,
            source = excluded.source,
            last_learned_at = excluded.last_learned_at,
            updated_at = excluded.updated_at
        """
        return self._execute(sql, (user_id, course_id, kp_id, status, mastery_level,
                                   source, last_learned_at, now, now))

    def list_records_by_user_course(self, user_id: int, course_id: int) -> list:
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

    def delete_learning_record(self, user_id: int, course_id: int, kp_id: str) -> int:
        """删除某用户某课程某知识点的学习记录（用于取消掌握标记）"""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM t_learning_record WHERE user_id = ? AND course_id = ? AND kp_id = ?",
                (user_id, course_id, kp_id),
            )
            conn.commit()
            return cur.rowcount

    # ---------- 学生收藏（收藏 = 个人知识点书签，独立于学习状态） ----------

    def add_favorite(self, user_id: int, course_id: int, kp_id: str) -> bool:
        """新增收藏（INSERT OR IGNORE 幂等）；返回是否新插入（True=新增，False=已存在未重复）"""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO t_student_favorite (user_id, course_id, kp_id) "
                "VALUES (?, ?, ?)",
                (user_id, course_id, kp_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def remove_favorite(self, user_id: int, course_id: int, kp_id: str) -> int:
        """取消收藏，返回删除条数"""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM t_student_favorite "
                "WHERE user_id = ? AND course_id = ? AND kp_id = ?",
                (user_id, course_id, kp_id),
            )
            conn.commit()
            return cur.rowcount

    def list_favorites_by_user_course(self, user_id: int, course_id: int) -> list:
        """查询某用户某课程的收藏（按收藏时间倒序）"""
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

    # ---------- 知识点向量（RAG 向量检索） ----------

    def upsert_kp_embedding(self, course_id: int, kp_id: str, embedding: list) -> int:
        """写入/更新知识点向量（embedding 序列化为 JSON 文本）"""
        return self._execute(
            "INSERT INTO t_kp_embedding (course_id, kp_id, embedding, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(course_id, kp_id) DO UPDATE SET "
            "embedding = excluded.embedding, updated_at = excluded.updated_at",
            (course_id, kp_id, json.dumps(embedding), _now()),
        )

    def get_embeddings_by_course(self, course_id: int) -> list:
        """返回课程全部知识点向量，[{kp_id, embedding(list[float])}]"""
        rows = self._query(
            "SELECT kp_id, embedding FROM t_kp_embedding WHERE course_id = ?",
            (course_id,),
        )
        result = []
        for r in rows:
            try:
                vec = json.loads(r["embedding"])
            except (ValueError, TypeError):
                continue
            result.append({"kp_id": r["kp_id"], "embedding": vec})
        return result

    def delete_embeddings_by_course(self, course_id: int) -> int:
        """删除课程全部知识点向量，返回删除条数"""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM t_kp_embedding WHERE course_id = ?", (course_id,))
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


# 全局关系型数据库实例
sql_db = SQLDatabase()
