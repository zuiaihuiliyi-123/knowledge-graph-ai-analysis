"""课程权限判定（课程中心改造的核心）

为什么单独一个模块而不写进 dependencies.py：
dependencies.py 是「无状态身份层」（JWT -> {user_id, username, role}，零数据库访问），
而权限判定每次都要读 SQLite 的课程与成员关系。混在一起会让 FastAPI 依赖层变得有状态。

返回契约与 services 完全一致：{"ok": bool, "code": int, "message": str, "data": dict}
因此路由里的写法与既有的 `if not r["ok"]: return error(r["code"], r["message"])` 一模一样：

    perm = Permissions.require_course_content(cid, current_user)
    if not perm["ok"]:
        return error(perm["code"], perm["message"])

统一拒绝码 4003：
documents.py 的 _CONTENT_ERROR_STATUS 已把 4003 映射为 HTTP 403，因此文档内容接口
与前端阅读器无需任何改动即可正确显示「无权限」。

三档权限速查（详见 Permissions.resolve 的 data 字段）：
    关系                              读元数据  读内容  管理
    创建者 / 协作教师                 ✅        ✅      ✅
    已通过学生成员                    ✅        ✅      ❌
    待审核 / 被拒绝 / 已被移除        ✅(仅看自己的申请状态) ❌ ❌
    公开课程（仅学生）                ✅        ❌      ❌
    公开课程（教师）                  ❌        ❌      ❌
    无关系                            ❌        ❌      ❌
"""
from .sql_database import course_is_visible, sql_db

# 用户与课程的关系（由 resolve 推导，供路由与前端展示）
REL_OWNER = "OWNER"                    # 课程创建者（teacher_id 本人）
REL_TEACHER_MEMBER = "TEACHER_MEMBER"  # 被邀请为教师角色的已通过成员
REL_MEMBER = "MEMBER"                  # 已通过审核的学生成员
REL_PENDING = "PENDING"                # 已申请/待审核
REL_REJECTED = "REJECTED"              # 已被拒绝
REL_REMOVED = "REMOVED"                # 已被移除
REL_PUBLIC = "PUBLIC"                  # 非成员，但课程公开可发现
REL_NONE = "NONE"                      # 无任何关系


class Permissions:
    """课程 / 文档访问权限判定。全部为只读判定，不修改任何数据。"""

    # ---------- 基础 ----------

    @staticmethod
    def _deny(code: int, message: str) -> dict:
        return {"ok": False, "code": code, "message": message, "data": None}

    @staticmethod
    def _relation(course: dict, member: dict) -> str:
        """由「课程行 + 成员行」推导关系。

        优先级：创建者 > 已通过教师成员 > 已通过学生成员 > 待审核 > 被拒绝 >
                被移除 > 公开课程 > 无关系。
        被移除（removed）刻意排在 PUBLIC 之前：课程公开也不代表被移除的学生
        可以自动复活，必须重新申请。
        """
        if member:
            status = member.get("status")
            if status == "approved":
                return REL_TEACHER_MEMBER if member.get("role") == "teacher" else REL_MEMBER
            if status == "pending":
                return REL_PENDING
            if status == "rejected":
                return REL_REJECTED
            if status == "removed":
                return REL_REMOVED
        # 公开课可见性 = is_public + 业务状态开放 + 治理状态正常（见 course_is_visible）。
        # 加上治理维度后，被平台下架/归档的课程不再对非成员学生暴露元数据，
        # 「下架」因此无需再去改写课程的业务状态 status。
        if course.get("is_public") == 1 and course_is_visible(course):
            return REL_PUBLIC
        return REL_NONE

    @staticmethod
    def resolve(course_id, user: dict) -> dict:
        """一次性取回课程、该用户的关系与三档权限位。

        data = {course, relation, member, is_owner, is_member,
                can_read_metadata, can_read_content, can_manage}
        """
        try:
            cid = int(course_id)
        except (TypeError, ValueError):
            return Permissions._deny(4001, f"课程 id 非法: {course_id}")

        row = sql_db.get_course_with_membership(cid, user["user_id"])
        if row is None:
            return Permissions._deny(2001, f"课程不存在: course_id={cid}")

        member = None
        if row.get("member_status"):
            member = {
                "role": row.get("member_role"),
                "status": row.get("member_status"),
                "join_source": row.get("member_join_source"),
                "joined_at": row.get("member_joined_at"),
            }

        is_owner = row["teacher_id"] == user["user_id"]
        relation = REL_OWNER if is_owner else Permissions._relation(row, member)

        # PUBLIC 只对【学生】开放元数据读取：学生需要看到公开课详情才能「申请加入」；
        # 而教师对自己的课程之外不应有可见性（对应验收要求「教师访问别人课程必须拒绝」）。
        public_ok = relation == REL_PUBLIC and user.get("role") == "student"

        data = {
            "course": row,
            "relation": relation,
            "member": member,
            "is_owner": is_owner,
            "is_member": relation in (REL_OWNER, REL_TEACHER_MEMBER, REL_MEMBER),
            "can_read_metadata": relation in (
                REL_OWNER, REL_TEACHER_MEMBER, REL_MEMBER,
                REL_PENDING, REL_REJECTED, REL_REMOVED) or public_ok,
            "can_read_content": relation in (REL_OWNER, REL_TEACHER_MEMBER, REL_MEMBER),
            "can_manage": relation in (REL_OWNER, REL_TEACHER_MEMBER),
        }
        return {"ok": True, "code": 0, "message": "success", "data": data}

    @staticmethod
    def allowed_course_ids(user: dict) -> list:
        """该用户可读内容的课程 id 列表。

        用于「未指定课程」的读取接口收敛范围（问答 / 学习路径 / 数据总览），
        避免 course_id 缺省时退化成全库扫描。
        """
        return sql_db.list_accessible_course_ids(user["user_id"], user.get("role", ""))

    # ---------- 三档校验 ----------

    @staticmethod
    def require_course_read(course_id, user: dict) -> dict:
        """元数据级：课程详情、发现页详情、申请状态查看（待审核/被拒也能看到自己的状态）"""
        r = Permissions.resolve(course_id, user)
        if not r["ok"]:
            return r
        if not r["data"]["can_read_metadata"]:
            return Permissions._deny(4003, "无权限：您不是该课程成员，无法查看课程信息")
        return r

    @staticmethod
    def require_course_content(course_id, user: dict) -> dict:
        """内容级：文档列表/内容、知识图谱、问答、学习路径、收藏、学习记录"""
        r = Permissions.resolve(course_id, user)
        if not r["ok"]:
            return r
        if not r["data"]["can_read_content"]:
            return Permissions._deny(4003, "无权限：您不是该课程成员，无法访问课程内容")
        return r

    @staticmethod
    def require_course_manage(course_id, user: dict) -> dict:
        """管理级：改课程、传/删文档、改图谱、审核成员、发邀请、看加课码"""
        r = Permissions.resolve(course_id, user)
        if not r["ok"]:
            return r
        if not r["data"]["can_manage"]:
            return Permissions._deny(4003, "无权限：仅该课程教师可执行此操作")
        return r

    @staticmethod
    def require_course_owner(course_id, user: dict) -> dict:
        """仅课程创建者可执行（删除课程，级联影响最大）"""
        r = Permissions.resolve(course_id, user)
        if not r["ok"]:
            return r
        if not r["data"]["is_owner"]:
            return Permissions._deny(4003, "无权限：仅课程创建者可删除该课程")
        return r

    # ---------- 文档级（文档 -> 课程 -> 成员关系） ----------

    @staticmethod
    def require_document_content(doc_id, user: dict) -> dict:
        """文档内容/元数据读取：先由 doc_id 找到所属课程，再按内容级判定"""
        doc = sql_db.get_document(int(doc_id)) if str(doc_id).isdigit() else None
        if doc is None:
            return Permissions._deny(2002, f"文档不存在: doc_id={doc_id}")
        r = Permissions.require_course_content(doc["course_id"], user)
        if not r["ok"]:
            return r
        r["data"]["document"] = doc
        return r

    @staticmethod
    def require_document_manage(doc_id, user: dict) -> dict:
        """文档管理（删除等）：仅课程教师"""
        doc = sql_db.get_document(int(doc_id)) if str(doc_id).isdigit() else None
        if doc is None:
            return Permissions._deny(2002, f"文档不存在: doc_id={doc_id}")
        r = Permissions.require_course_manage(doc["course_id"], user)
        if not r["ok"]:
            return r
        r["data"]["document"] = doc
        return r
