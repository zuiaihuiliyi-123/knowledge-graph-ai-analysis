"""课程成员服务（课程中心：加入 / 申请 / 审核 / 邀请 / 移除）

加入规则（安全关键，全部在此收口）：
- 加课码 + join_mode='auto'      -> 直接 approved
- 加课码 + join_mode='approval'  -> pending，等教师审核
- 加课码 + join_mode='closed'    -> 拒绝（4006）；但邀请链接仍然有效
- 邀请令牌                       -> 一律 approved（教师已明确同意，且不因 join_mode=closed 失效）
- 曾被移除（removed）后无论何种方式 -> 强制 pending + join_source='apply'
  （防止泄露出去的加课码把「已被移除的学生」重新自动放进来）
- 已被拒绝（rejected）后可重新申请 -> pending

统一返回 {"ok": bool, "code": int, "message": str, "data": dict}
"""
from datetime import datetime, timedelta

from ..core.codes import gen_invite_token, normalize_join_code
from ..core.database import db
from ..core.sql_database import course_is_visible, sql_db

TIME_FMT = "%Y-%m-%d %H:%M:%S"
INVITE_DEFAULT_DAYS = 7


def _now() -> str:
    return datetime.now().strftime(TIME_FMT)


def _display_name(user: dict) -> str:
    """展示名（资料在 t_user_profile）：昵称 > 真名 > display_name > 用户名"""
    if not user:
        return ""
    profile = sql_db.get_user_profile(user["user_id"]) or {}
    return (profile.get("nickname") or profile.get("real_name")
            or user.get("display_name") or user.get("username") or "")


class MemberService:
    """课程成员与邀请的业务逻辑"""

    @staticmethod
    def _fail(code: int, message: str) -> dict:
        return {"ok": False, "code": code, "message": message}

    # ---------- 成员查询 ----------

    @staticmethod
    def list_members(course_id: int, status: str = None, role: str = None,
                     keyword: str = None, page: int = 1, page_size: int = 20) -> dict:
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        total, rows = sql_db.list_members(course_id, status, role, keyword, page, page_size)
        items = [MemberService._shape_member(r) for r in rows]
        MemberService._enrich_progress(course_id, items)
        return {"ok": True, "code": 0, "message": "success", "data": {
            "total": total, "page": page, "page_size": page_size, "items": items}}

    @staticmethod
    def _shape_member(row: dict) -> dict:
        return {
            "user_id": row["user_id"],
            "username": row.get("username"),
            "display_name": row.get("display_name"),
            "real_name": row.get("real_name"),
            "nickname": row.get("nickname"),
            "avatar_url": row.get("avatar_url"),
            "gender": row.get("gender"),
            "school": row.get("school"),
            "college": row.get("college"),
            "major": row.get("major"),
            "grade": row.get("grade"),
            "class_name": row.get("class_name"),
            "student_no": row.get("student_no"),
            "teacher_no": row.get("teacher_no"),
            "title": row.get("title"),
            "research_area": row.get("research_area"),
            "role": row.get("role"),
            "status": row.get("status"),
            "join_source": row.get("join_source"),
            "applied_reason": row.get("applied_reason"),
            "review_comment": row.get("review_comment"),
            "joined_at": row.get("joined_at"),
            "created_at": row.get("created_at"),
            # 展示名统一走「昵称 > 真名 > display_name > 用户名」
            "name": (row.get("nickname") or row.get("real_name")
                     or row.get("display_name") or row.get("username") or ""),
        }

    @staticmethod
    def _enrich_progress(course_id: int, items: list):
        """为成员列表补上学习进度（掌握数 / 进度百分比 / 收藏数）。

        Neo4j 不可用时知识点总数退化为 0（进度显示 0 而不是抛异常）——
        成员管理是 SQLite 主导的功能，不应因为图库没起来就整页报错。
        """
        try:
            total_knowledge = db.query(
                "MATCH (n:KnowledgePoint {course_id: $cid}) RETURN count(n) AS cnt",
                {"cid": course_id},
            )[0]["cnt"]
        except Exception:
            total_knowledge = 0

        fav_counts = sql_db.count_favorites_by_course(course_id)
        mastered = {}
        for r in sql_db.list_records_by_course(course_id):
            if r["status"] == "MASTERED":
                mastered[r["user_id"]] = mastered.get(r["user_id"], 0) + 1

        for item in items:
            uid = item["user_id"]
            count = mastered.get(uid, 0)
            item["total_knowledge"] = total_knowledge
            item["mastered_count"] = count
            item["favorite_count"] = fav_counts.get(uid, 0)
            item["progress"] = (round(min(1.0, count / total_knowledge) * 100, 1)
                                if total_knowledge else 0.0)

    @staticmethod
    def member_stats(course_id: int) -> dict:
        counts = sql_db.count_members_by_status(course_id)
        approved_ids = sql_db.list_member_user_ids(course_id, "approved")
        items = [{"user_id": uid} for uid in approved_ids]
        MemberService._enrich_progress(course_id, items)
        avg = (round(sum(i["progress"] for i in items) / len(items), 1) if items else 0.0)
        return {"ok": True, "code": 0, "message": "success", "data": {
            "course_id": course_id,
            "total": sum(counts.values()),
            "approved": counts.get("approved", 0),
            "pending": counts.get("pending", 0),
            "rejected": counts.get("rejected", 0),
            "removed": counts.get("removed", 0),
            "avg_progress": avg,
        }}

    # ---------- 审核 / 移除 ----------

    @staticmethod
    def approve(course_id: int, user_id: int, operator_id: int) -> dict:
        member = sql_db.get_membership(course_id, user_id)
        if member is None:
            return MemberService._fail(2004, f"成员记录不存在: user_id={user_id}")
        if member["status"] == "approved":
            return MemberService._fail(4004, "该学生已是课程成员")
        sql_db.set_membership_status(course_id, user_id, "approved", reviewed_by=operator_id)
        return {"ok": True, "code": 0, "message": "success", "data": {
            "course_id": course_id, "user_id": user_id, "status": "approved"}}

    @staticmethod
    def reject(course_id: int, user_id: int, operator_id: int, comment: str = None) -> dict:
        course = sql_db.get_course(course_id)
        if course is None:
            return MemberService._fail(2001, f"课程不存在: course_id={course_id}")
        if course["teacher_id"] == user_id:
            return MemberService._fail(4009, "不能拒绝课程创建者")

        member = sql_db.get_membership(course_id, user_id)
        if member is None:
            return MemberService._fail(2004, f"成员记录不存在: user_id={user_id}")
        if comment and len(comment) > 200:
            return MemberService._fail(1001, "审核意见过长（最大 200 字符）")
        sql_db.set_membership_status(course_id, user_id, "rejected",
                                     reviewed_by=operator_id, comment=comment)
        return {"ok": True, "code": 0, "message": "success", "data": {
            "course_id": course_id, "user_id": user_id, "status": "rejected"}}

    @staticmethod
    def remove(course_id: int, user_id: int, operator_id: int) -> dict:
        """移除成员：只翻状态，保留其学习记录与收藏（那是学生自己的数据）。

        只有「课程创建者」受保护（4009）。协作教师是课程创建者主动邀请进来的，
        因此创建者也必须能把协作教师移出去——否则一次误邀请就无法撤销。
        """
        course = sql_db.get_course(course_id)
        if course is None:
            return MemberService._fail(2001, f"课程不存在: course_id={course_id}")
        if course["teacher_id"] == user_id:
            return MemberService._fail(4009, "不能移除课程创建者")

        member = sql_db.get_membership(course_id, user_id)
        if member is None:
            return MemberService._fail(2004, f"成员记录不存在: user_id={user_id}")

        sql_db.set_membership_status(course_id, user_id, "removed", reviewed_by=operator_id)
        return {"ok": True, "code": 0, "message": "success", "data": {
            "course_id": course_id, "user_id": user_id, "status": "removed",
            "removed_learning": False}}

    # ---------- 加入（加课码 / 申请 / 邀请） ----------

    @staticmethod
    def _guard_membership(member: dict) -> dict:
        """返回 (允许继续?, 直接拒绝的响应)。已是成员 / 审核中直接拒绝，避免重复申请。"""
        if member is None:
            return None
        if member["status"] == "approved":
            return MemberService._fail(4004, "您已是该课程成员")
        if member["status"] == "pending":
            return MemberService._fail(4004, "您的申请正在审核中，请耐心等待")
        return None

    @staticmethod
    def join_by_code(user_id: int, join_code: str, reason: str = None) -> dict:
        code = normalize_join_code(join_code)
        if not code:
            return MemberService._fail(1001, "请输入加课码")
        if reason and len(reason) > 200:
            return MemberService._fail(1001, "申请理由过长（最大 200 字符）")

        course = sql_db.get_course_by_join_code(code)
        if course is None:
            return MemberService._fail(4005, "加课码无效，请确认后重试")
        # 已停用（教师/管理员关闭）或被平台下架/归档的课程都不再接受新成员。
        # 判定走 course_is_visible：只加治理维度，不改变原有 status 语义。
        if not course_is_visible(course):
            return MemberService._fail(4003, "该课程已停用或已下架，无法加入")

        course_id = course["course_id"]
        if course["teacher_id"] == user_id:
            return MemberService._fail(4004, "您是该课程的创建教师")

        member = sql_db.get_membership(course_id, user_id)
        blocked = MemberService._guard_membership(member)
        if blocked:
            return blocked

        # 加入角色随账号角色：教师加入即协作教师（需审核后共管课程），学生即学生成员
        account = sql_db.get_user_by_id(user_id) or {}
        member_role = "teacher" if account.get("role") == "teacher" else "student"

        join_mode = course.get("join_mode") or "approval"
        # 曾被移除的学生：无论课程是否自动加入，都必须重新走审核，
        # 否则一个外泄的加课码就能把「已被移除」的决定撤销掉。
        if member and member["status"] == "removed":
            sql_db.upsert_membership(course_id, user_id, member_role, "pending", "apply", reason)
            return {"ok": True, "code": 0, "message": "success", "data": {
                "course_id": course_id, "course_name": course["course_name"],
                "status": "pending", "join_mode": join_mode}}

        if join_mode == "closed":
            return MemberService._fail(4006, "该课程已关闭加入，请联系教师获取邀请链接")

        # 教师经加课码加入一律需审核（协作教师有课程管理权，防加课码外泄直接得权）
        if member_role == "teacher":
            status = "pending"
        else:
            status = "approved" if join_mode == "auto" else "pending"
        sql_db.upsert_membership(course_id, user_id, member_role, status, "code", reason)
        return {"ok": True, "code": 0, "message": "success", "data": {
            "course_id": course_id, "course_name": course["course_name"],
            "status": status, "role": member_role, "join_mode": join_mode}}

    @staticmethod
    def apply_to_course(user_id: int, course_id: int, reason: str = None) -> dict:
        """从「发现课程」申请加入公开课：始终进入待审核"""
        if reason and len(reason) > 200:
            return MemberService._fail(1001, "申请理由过长（最大 200 字符）")
        course = sql_db.get_course(course_id)
        if course is None:
            return MemberService._fail(2001, f"课程不存在: course_id={course_id}")
        if course.get("is_public") != 1 or not course_is_visible(course):
            return MemberService._fail(4003, "该课程未开放申请")
        if course["teacher_id"] == user_id:
            return MemberService._fail(4004, "您是该课程的创建教师")

        member = sql_db.get_membership(course_id, user_id)
        blocked = MemberService._guard_membership(member)
        if blocked:
            return blocked

        # 申请角色随账号角色：教师申请即协作教师（审核通过后共管课程），学生即学生成员
        account = sql_db.get_user_by_id(user_id) or {}
        member_role = "teacher" if account.get("role") == "teacher" else "student"

        sql_db.upsert_membership(course_id, user_id, member_role, "pending", "apply", reason)
        return {"ok": True, "code": 0, "message": "success", "data": {
            "course_id": course_id, "course_name": course["course_name"], "role": member_role,
            "status": "pending", "join_mode": course.get("join_mode") or "approval"}}

    @staticmethod
    def leave_course(user_id: int, course_id: int) -> dict:
        """学生主动退出课程：置为 removed，保留其学习记录与收藏"""
        course = sql_db.get_course(course_id)
        if course is None:
            return MemberService._fail(2001, f"课程不存在: course_id={course_id}")
        if course["teacher_id"] == user_id:
            return MemberService._fail(4003, "课程创建者不能退出自己的课程")
        member = sql_db.get_membership(course_id, user_id)
        if member is None or member["status"] != "approved":
            return MemberService._fail(2004, "您当前不是该课程成员")
        sql_db.set_membership_status(course_id, user_id, "removed", reviewed_by=user_id)
        return {"ok": True, "code": 0, "message": "success", "data": {
            "course_id": course_id, "user_id": user_id, "status": "removed"}}

    # ---------- 邀请 ----------

    @staticmethod
    def create_invite(course_id: int, operator_id: int, role: str = "student",
                      expires_in_days: int = INVITE_DEFAULT_DAYS) -> dict:
        if role not in ("student", "teacher"):
            return MemberService._fail(1001, f"邀请角色非法: {role}")
        try:
            days = int(expires_in_days)
        except (TypeError, ValueError):
            return MemberService._fail(1001, "有效期必须为整数天")
        days = min(max(days, 1), 365)

        expires_at = (datetime.now() + timedelta(days=days)).strftime(TIME_FMT)
        token = gen_invite_token()
        invite_id = sql_db.create_invite(course_id, token, operator_id, role, expires_at)
        return {"ok": True, "code": 0, "message": "success", "data": {
            "invite_id": invite_id,
            "token": token,
            "url": f"/invite/{token}",
            "role": role,
            "expires_at": expires_at,
        }}

    @staticmethod
    def _effective_status(invite: dict) -> str:
        """把 expires_at 折算进状态，前端只认这一个字段即可"""
        if invite["status"] == "active" and invite.get("expires_at") \
                and invite["expires_at"] < _now():
            return "expired"
        return invite["status"]

    @staticmethod
    def list_invites(course_id: int) -> dict:
        items = []
        for row in sql_db.list_invites_by_course(course_id):
            items.append({
                "invite_id": row["invite_id"],
                "token": row["token"],
                "role": row["role"],
                "status": row["status"],
                "effective_status": MemberService._effective_status(row),
                "inviter_name": row.get("inviter_name"),
                "used_by_name": row.get("used_by_name"),
                "used_at": row.get("used_at"),
                "expires_at": row.get("expires_at"),
                "created_at": row.get("created_at"),
            })
        return {"ok": True, "code": 0, "message": "success", "data": {"items": items}}

    @staticmethod
    def revoke_invite(course_id: int, invite_id: int) -> dict:
        if sql_db.revoke_invite(invite_id, course_id) == 0:
            return MemberService._fail(2004, "邀请不存在或已失效")
        return {"ok": True, "code": 0, "message": "success",
                "data": {"invite_id": invite_id, "revoked": True}}

    @staticmethod
    def preview_invite(token: str) -> dict:
        invite = sql_db.get_invite_by_token(token)
        if invite is None:
            return MemberService._fail(4010, "邀请链接无效")
        course = sql_db.get_course(invite["course_id"])
        if course is None:
            return MemberService._fail(4010, "邀请对应的课程已不存在")

        status = MemberService._effective_status(invite)
        teacher = sql_db.get_user_by_id(course["teacher_id"])
        inviter = sql_db.get_user_by_id(invite["invited_by"])
        counts = sql_db.count_members_by_status(course["course_id"])
        return {"ok": True, "code": 0, "message": "success", "data": {
            "course_id": course["course_id"],
            "course_name": course["course_name"],
            "description": course.get("description"),
            "category": course.get("category"),
            "organization": course.get("organization"),
            "teacher_name": _display_name(teacher),
            "inviter_name": _display_name(inviter),
            "role": invite["role"],
            "effective_status": status,
            "expires_at": invite.get("expires_at"),
            "member_count": counts.get("approved", 0),
        }}

    @staticmethod
    def accept_invite(user_id: int, token: str) -> dict:
        invite = sql_db.get_invite_by_token(token)
        if invite is None:
            return MemberService._fail(4010, "邀请链接无效")

        course_id = invite["course_id"]
        course = sql_db.get_course(course_id)
        if course is None:
            return MemberService._fail(4010, "邀请对应的课程已不存在")

        # 平台治理优先于教师的课程设置：已下架 / 已归档的课程不接受任何方式加入，
        # 邀请链接也不例外（它是教师的决定，但下架是平台的结论）。
        # 刻意只查 governance_status 而不查 status —— status=0 的课程仍允许邀请加入，
        # 这是 join_mode='closed' 的既有设计（关闭加课码与申请，保留邀请）。
        if (course.get("governance_status") or "normal") != "normal":
            return MemberService._fail(4003, "该课程已被平台下架或归档，无法加入")

        # 已是成员：幂等返回，不再消费邀请令牌
        member = sql_db.get_membership(course_id, user_id)
        if member and member["status"] == "approved":
            return {"ok": True, "code": 0, "message": "success", "data": {
                "course_id": course_id, "course_name": course["course_name"],
                "status": "approved", "already_member": True}}

        status = MemberService._effective_status(invite)
        if status == "used":
            return MemberService._fail(4010, "该邀请链接已被使用")
        if status == "revoked":
            return MemberService._fail(4010, "该邀请链接已被撤销")
        if status == "expired":
            return MemberService._fail(4010, "该邀请链接已过期")

        # 原子消费：rowcount==1 才说明本次真正用掉了这个邀请，避免并发/重复点击重复加课
        if sql_db.mark_invite_used(invite["invite_id"], user_id) == 0:
            return MemberService._fail(4010, "该邀请链接已被使用")

        sql_db.upsert_membership(course_id, user_id, invite["role"], "approved", "invite")
        return {"ok": True, "code": 0, "message": "success", "data": {
            "course_id": course_id, "course_name": course["course_name"],
            "status": "approved", "already_member": False}}
