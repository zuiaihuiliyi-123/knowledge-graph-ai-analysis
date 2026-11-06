"""管理员端统计、治理、审计与平台趋势。"""

from ..schema import (COURSE_GOVERNANCE_STATUS, COURSE_VISIBLE_SQL)
from ..connection import _now

class AdminMixin:
    """管理员端统计、治理、审计与平台趋势。"""


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
        """启用 / 禁用账号（管理员操作）；同时递增 token_version 让旧会话立即失效。"""
        return self._execute(
            "UPDATE t_user SET is_active = ?, token_version = token_version + 1, "
            "updated_at = ? WHERE user_id = ?",
            (1 if is_active else 0, _now(), user_id),
        )


    def set_user_role(self, user_id: int, role: str) -> int:
        """修改用户角色（管理员操作）；同时递增 token_version，旧权限立即失效。"""
        return self._execute(
            "UPDATE t_user SET role = ?, token_version = token_version + 1, "
            "updated_at = ? WHERE user_id = ?",
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

        时间口径：started_at / finished_at 取 t_document 的 extract_started_at /
        extract_finished_at，即**纯抽取**的起止时刻（含入图前的 LLM 调用），
        不含存文件与解析。两列为 NULL 表示「未记录」（历史数据，或尚未跑到该阶段），
        调用方应显示「—」而不是按 0 处理。
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
                   -- 抽取本身的起止时刻（写入收口在 update_document）。
                   -- 不能用 created_at / updated_at：前者是建档时刻，后者是「行最后修改
                   -- 时间」，回填统计、重建图谱、改元数据都会刷新它——实测把 08-28 抽完的
                   -- 文档算成 48889 分（34 天）。旧库两列为 NULL（真实时刻从未记录过），
                   -- 前端据此显示「—」而不是一个假数字。
                   d.extract_started_at  AS started_at,
                   d.extract_finished_at AS finished_at,
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
