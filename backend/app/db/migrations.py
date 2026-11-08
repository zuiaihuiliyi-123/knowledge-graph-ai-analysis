"""SQLite 建表后的幂等迁移与回填。"""

import json
import sqlite3
from ..core.codes import gen_join_code
from .schema import (_SCHEMA_SQL, _T_QUESTION_DDL)
from .connection import _vec_to_blob

class MigrationMixin:
    """建表后的幂等迁移、回填与表重建。"""


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
        # 阶段 G：最后切 journal_mode —— 此时库文件已存在且非空（空文件无法设 WAL），
        # 且上面所有迁移写事务都已结束，最容易一次成功。
        self.journal_state = self._apply_journal_mode()


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
            # 知识点名快照：重新抽取知识点会整体更换 kp_id，只有 kp_id 的掌握记录
            # 在新图谱里全部变成孤儿（已掌握数瞬间归零）。补一列名字用于按同名回挂。
            lr_columns = {row["name"] for row in
                          conn.execute("PRAGMA table_info(t_learning_record)").fetchall()}
            if "kp_name" not in lr_columns:
                conn.execute("ALTER TABLE t_learning_record ADD COLUMN kp_name TEXT")
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
            # 文档级删除/统计统一按 (course_id, document_id) 过滤，但这三张表现有索引的
            # 前导列是 user_id（或根本没有 document_id），删除文档时只能全表扫描。
            # 与 idx_kp_emb_scope 同理：document_id 列由上面的循环 ALTER 补齐，
            # 索引必须建在 _migrate 而非 _SCHEMA_SQL，否则旧库会因列不存在而报错。
            for stmt in (
                "CREATE INDEX IF NOT EXISTS idx_lr_course_doc "
                "ON t_learning_record(course_id, document_id)",
                "CREATE INDEX IF NOT EXISTS idx_fav_course_doc "
                "ON t_student_favorite(course_id, document_id)",
                "CREATE INDEX IF NOT EXISTS idx_ans_course_doc "
                "ON t_answer_record(course_id, document_id)",
            ):
                conn.execute(stmt)
            self._migrate_question_bank(conn)
            self._migrate_question_kp(conn)
            self._migrate_embeddings_blob(conn)
            self._migrate_embeddings_model(conn)
            self._migrate_course_center(conn)
            self._migrate_admin(conn)
            self._migrate_admin_user_columns(conn)
            self._migrate_document_extract_timing(conn)
            self._migrate_document_graph_status(conn)
            conn.commit()


    def _migrate_document_graph_status(self, conn: sqlite3.Connection):
        """幂等迁移：t_document 补「图谱构建状态」列。

        为什么需要独立列：批量上传后「文档上传成功」不再等于「图谱构建成功」，
        原表只有 parse_status / extract_status，无法表达「抽取已完成、正在写 Neo4j」。
        新增 graph_status 区分 PENDING / BUILDING / COMPLETED / FAILED。

        SQLite 的 ADD COLUMN 带 NOT NULL 必须给默认值，这里默认 'PENDING' 可满足。
        历史行按已有的 extract_status 回填：
          - extract_status='COMPLETED' 的历史文档，图谱当年已同步构建 → COMPLETED
          - 其余（失败 / 进行中 / 未处理）保持 PENDING，由前端统一按失败/待处理展示
        只回填 COMPLETED，重复执行无副作用；绝不重置已经写入的新状态。
        """
        cols = {row["name"] for row in
                conn.execute("PRAGMA table_info(t_document)").fetchall()}
        if "graph_status" not in cols:
            conn.execute(
                "ALTER TABLE t_document ADD COLUMN graph_status TEXT NOT NULL DEFAULT 'PENDING'"
            )
            conn.execute(
                "UPDATE t_document SET graph_status = 'COMPLETED' "
                "WHERE extract_status = 'COMPLETED'"
            )


    def _migrate_document_extract_timing(self, conn: sqlite3.Connection):
        """幂等迁移：t_document 补「抽取起止时刻」两列。

        背景：管理端「资源管理 → 知识抽取任务」原先用 created_at / updated_at 算耗时，
        而 updated_at 会被任何后续写操作刷新（回填统计、重建图谱、改元数据），
        于是「耗时」实际变成了「上传至今」——实测有文档显示 48889 分（34 天）。

        t_document 从未记录过抽取完成时刻，历史数据的真实耗时**无法还原**，
        故本迁移只补列，不伪造历史值：历史行两列留 NULL，前端显示「—」。
        之后由 update_document 在写 extract_status 的同一句 UPDATE 里打戳。

        SQLite 的 ADD COLUMN 不允许带 CHECK / UNIQUE，两列均为可空 TEXT，由应用层负责写入。
        """
        cols = {row["name"] for row in
                conn.execute("PRAGMA table_info(t_document)").fetchall()}
        for name in ("extract_started_at", "extract_finished_at"):
            if name not in cols:
                conn.execute(f"ALTER TABLE t_document ADD COLUMN {name} TEXT")


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


    def _migrate_question_kp(self, conn: sqlite3.Connection) -> int:
        """幂等迁移：把 t_question.kp_id 回填为 t_question_kp 的「主知识点」行。返回本次回填条数。

        表结构由 _SCHEMA_SQL 的 CREATE TABLE IF NOT EXISTS 建出（含 uq_qkp_primary 部分唯一索引），
        本方法只负责补历史数据。

        **只回填「关联表中无任何行」的题**，这一点是必须的：
        若某题已有 is_primary=1 的关联行（教师后续编辑产生），再按 t_question.kp_id 插一行
        is_primary=1 会**同时触碰 uq_qkp_primary**，导致后端每次启动即崩。
        因此判据是 `NOT EXISTS (SELECT 1 FROM t_question_kp ...)` 而不是 `INSERT OR IGNORE` 单打独斗。

        `set_question_kps()` 是兼容期内唯一的关联表写入方，它负责同步 t_question.kp_id（冗余投影），
        因此稳态下两者不会漂移；漂移检测见 scripts/scan_consistency.py 的口径。
        """
        cur = conn.execute(
            "INSERT OR IGNORE INTO t_question_kp (question_id, kp_id, source, is_primary) "
            "SELECT q.question_id, q.kp_id, 'MANUAL', 1 FROM t_question q "
            "WHERE q.kp_id IS NOT NULL AND q.kp_id <> '' "
            "  AND NOT EXISTS (SELECT 1 FROM t_question_kp k WHERE k.question_id = q.question_id)"
        )
        return cur.rowcount


    # ---------- L2 阶段 E：向量改存 float32 BLOB ----------
    # 与「新增列只写 _migrate()、不写 _SCHEMA_SQL」的纪律一致，否则新建库与迁移库结构分叉。
    _EMB_NEW_COLUMNS = (("embedding_blob", "BLOB"),)

    # 回填时的定位键（必须是各表的唯一键；用键而非 rowid，保持 MySQL 可移植）
    _BLOB_KEY_COLUMNS = {
        "t_kp_embedding": ("course_id", "kp_id"),
        "t_question_embedding": ("question_id",),
    }

    # 单次启动最多回填多少行：把 JSON 解析成本摊到多次启动，避免启动时间绑在向量总量上
    # （实测 5 万条 JSON 全量解析约 18 秒）。重复启动继续推进，全程幂等。
    _EMB_BACKFILL_LIMIT = 5000


    def _migrate_embeddings_blob(self, conn: sqlite3.Connection) -> int:
        """幂等迁移：给两张向量表补 `embedding_blob` 并**限量**回填，返回本次回填行数。

        旧 `embedding` TEXT 列**保留**（兼容期双写）；读取优先 BLOB，缺失时回落解析 JSON，
        因此未回填完的历史行也能正常读。`backfill_embedding_blobs()` 可由脚本显式全量调用。
        """
        total = 0
        for table in self._BLOB_KEY_COLUMNS:
            cols = {row["name"] for row in
                    conn.execute(f"PRAGMA table_info({table})").fetchall()}
            for name, decl in self._EMB_NEW_COLUMNS:
                if name not in cols:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            total += self._backfill_blob_rows(conn, table, self._EMB_BACKFILL_LIMIT)
        return total


    def _migrate_embeddings_model(self, conn: sqlite3.Connection):
        """幂等迁移：两张向量表补 `model` 列（记录向量由哪个 embedding 模型生成）。

        为什么必须记录：换模型（bge-m3 → 其它）或服务端默认维度变化后，旧向量与
        新查询向量维度不同。numpy 矩阵乘法遇到维度不一致会直接抛异常（问答 500），
        而「维度碰巧相同但语义空间不同」的情况更隐蔽——检索不报错，只是结果无意义。
        记录模型后，`KnowledgeEmbedder.ensure_index` 能识别过期索引并重建。

        历史行保持 NULL：迁移无法知道旧向量出自哪个模型，NULL 会被判定为「过期」，
        在下次问答/标注时按当前模型懒重建。只加列，不猜值。
        """
        for table in self._BLOB_KEY_COLUMNS:
            cols = {row["name"] for row in
                    conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if "model" not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN model TEXT")


    def _backfill_blob_rows(self, conn: sqlite3.Connection, table: str, limit: int) -> int:
        """把 table 中 embedding_blob 为空的行由 embedding(JSON) 转成 float32 BLOB（限量）。"""
        keys = self._BLOB_KEY_COLUMNS[table]
        rows = conn.execute(
            f"SELECT {', '.join(keys)}, embedding FROM {table} "
            f"WHERE embedding_blob IS NULL AND embedding IS NOT NULL AND embedding <> '' "
            f"LIMIT ?", (int(limit),)
        ).fetchall()
        where = " AND ".join(f"{k} = ?" for k in keys)
        done = 0
        for r in rows:
            try:
                vec = json.loads(r["embedding"])
            except (ValueError, TypeError):
                continue                       # 脏行跳过（不阻塞其他行）
            if not isinstance(vec, list) or not vec:
                continue
            conn.execute(f"UPDATE {table} SET embedding_blob = ? WHERE {where}",
                         (_vec_to_blob(vec), *[r[k] for k in keys]))
            done += 1
        return done


    def backfill_embedding_blobs(self, limit=None) -> int:
        """显式回填（脚本/运维用；不限量则一次做全）。返回回填行数。"""
        total = 0
        with self._connect() as conn:
            for table in self._BLOB_KEY_COLUMNS:
                total += self._backfill_blob_rows(conn, table, limit or 10 ** 9)
            conn.commit()
        return total


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
