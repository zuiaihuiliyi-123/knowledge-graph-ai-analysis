"""课程成员与邀请。"""

from ..connection import _now

class MembersMixin:
    """课程成员与邀请。"""


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


    def downgrade_teacher_memberships(self, user_id: int) -> int:
        """把该用户在全部课程上的教师成员行降为学生，返回影响行数。

        账号角色与课程成员角色是两套数据：管理员把教师降级后，t_course_member
        里 role='teacher' 的历史行不会自动失效。Permissons 已按账号角色兜底拒绝，
        这里再把快照修正掉，避免「账号已是学生、成员列表仍显示协作教师」的脏数据。
        只改 role，不动 status —— 是否仍留在课程里由教师后续审核决定。
        """
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE t_course_member SET role = 'student', updated_at = ? "
                "WHERE user_id = ? AND role = 'teacher'",
                (_now(), user_id),
            )
            conn.commit()
            return cur.rowcount


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


    def delete_membership(self, course_id: int, user_id: int) -> int:
        """撤回申请：删除待审核的成员行（返回影响行数）。

        只允许删除 pending 记录（条件写进 SQL，避免调用方先读后写产生竞态）。
        删除而非置 removed：撤回后该课程应重新出现在「发现课程」里，
        而 list_discover 会排除一切仍有成员行的课程。
        """
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM t_course_member "
                "WHERE course_id = ? AND user_id = ? AND status = 'pending'",
                (course_id, user_id),
            )
            conn.commit()
            return cur.rowcount

    # 说明：按 kp 分组统计与「未挂知识点」计数统一走 count_questions_grouped_by_kp（关联表口径），
    # 不再另设 count_questions_grouped_by_kps / count_unlinked_questions 两个近似方法——
    # 一题多 kp 的明细由 get_question_kps_map() 提供，避免同一语义有多个入口。


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


    def get_invite(self, invite_id: int, course_id: int) -> dict:
        """按 id 取邀请（限定课程），用于判断邀请角色是否属于治理级操作。"""
        return self._query_one(
            "SELECT * FROM t_course_invite WHERE invite_id = ? AND course_id = ?",
            (invite_id, course_id),
        )


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
