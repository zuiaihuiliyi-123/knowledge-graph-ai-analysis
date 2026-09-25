"""课程管理服务（对齐规划文档 6.6.6~6.6.10）

课程中心改造新增：加课码、加入方式、课程分类/院系/封面、成员统计、发现课程。

统一返回 {"ok": bool, "code": int, "message": str, "data": dict}
"""
import os

from ..core.codes import gen_join_code
from ..core.database import db
from ..core.sql_database import JOIN_MODES, course_is_visible, sql_db
from ..core.storage import resolve_document_path
from .profile_service import ProfileService


def _member_summary(counts: dict) -> dict:
    """把 {status: count} 折算成卡片需要的成员数 / 待审核数"""
    counts = counts or {}
    return {
        "member_count": counts.get("approved", 0),
        "pending_count": counts.get("pending", 0),
    }


class CourseService:
    """课程 CRUD 业务逻辑"""

    # ---------- 创建 ----------

    @staticmethod
    def _unique_join_code() -> str:
        """生成不与现有课程冲突的加课码（唯一索引兜底，这里做一次快速重试）"""
        for _ in range(20):
            code = gen_join_code()
            if sql_db.get_course_by_join_code(code) is None:
                return code
        return gen_join_code(8)  # 极端情况交给唯一索引报错，不做无限循环

    @staticmethod
    def create_course(course_name: str, teacher_id: int = None,
                      course_code: str = None, description: str = None,
                      join_mode: str = "approval", organization: str = None,
                      category: str = None, cover: str = None,
                      is_public: int = 1) -> dict:
        # 参数校验
        if not course_name or not course_name.strip():
            return {"ok": False, "code": 1001, "message": "课程名不能为空"}
        course_name = course_name.strip()
        if len(course_name) > 50:
            return {"ok": False, "code": 1001, "message": "课程名过长（最大50字符）"}
        if description and len(description) > 500:
            return {"ok": False, "code": 1001, "message": "课程简介过长（最大500字符）"}
        if join_mode not in JOIN_MODES:
            return {"ok": False, "code": 1001,
                    "message": f"加入方式非法（可选：{'/'.join(JOIN_MODES)}）"}
        if is_public not in (0, 1):
            return {"ok": False, "code": 1001, "message": "是否公开取值非法（0/1）"}
        if category and len(category) > 30:
            return {"ok": False, "code": 1001, "message": "课程分类过长（最大30字符）"}
        if organization and len(organization) > 100:
            return {"ok": False, "code": 1001, "message": "开课院系过长（最大100字符）"}

        # 教师：由 JWT 注入，缺省即报错；显式传入需校验角色
        if teacher_id is None:
            return {"ok": False, "code": 1004, "message": "缺少教师信息"}
        user = sql_db.get_user_by_id(teacher_id)
        if user is None:
            return {"ok": False, "code": 1004, "message": f"教师账号不存在: user_id={teacher_id}"}
        if user["role"] != "teacher":
            return {"ok": False, "code": 1004, "message": "无权限：仅教师角色可创建课程"}

        # 重名校验
        if sql_db.get_course_by_name(course_name):
            return {"ok": False, "code": 2003, "message": f"课程名已存在: {course_name}"}
        if course_code and sql_db.get_course_by_code(course_code):
            return {"ok": False, "code": 2003, "message": f"课程编号已存在: {course_code}"}

        join_code = CourseService._unique_join_code()
        course_id = sql_db.create_course(
            course_name=course_name, teacher_id=teacher_id,
            course_code=course_code, description=description,
            join_code=join_code, join_mode=join_mode, organization=organization,
            category=category, cover=cover, is_public=is_public,
        )
        # 教师自动成为该课程的 approved 教师成员（与「课程成员」模型一致）
        sql_db.upsert_membership(course_id, teacher_id, "teacher", "approved", "create")
        return {"ok": True, "code": 0, "message": "success",
                "data": {"course_id": course_id, "course_name": course_name,
                         "join_code": join_code, "join_mode": join_mode,
                         "is_public": is_public, "created": True}}

    # ---------- 列表 ----------

    @staticmethod
    def list_courses(page: int = 1, page_size: int = 10,
                     teacher_id: int = None, keyword: str = None,
                     course_ids: list = None, category: str = None,
                     is_public: int = None, exclude_course_ids: list = None,
                     viewer: dict = None) -> dict:
        """课程列表。

        viewer：当前登录用户。传入时，仅对「该用户可管理的课程」附带 join_code——
        加课码等于课程的加入凭证，学生（含已加入的成员）不应在列表里看到，
        否则可以把码转发给课程外的人。不传 viewer 时一律不返回 join_code。
        """
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        total, rows = sql_db.list_courses_page(
            page, page_size, teacher_id, keyword,
            category=category, is_public=is_public,
            course_ids=course_ids, exclude_course_ids=exclude_course_ids,
        )

        # 可管理该课程 => 可以看加课码；同时记录当前用户成员关系，供前端区分主讲/协作教师
        manageable = set()
        membership_map = {}
        if viewer:
            for c in rows:
                if c["teacher_id"] == viewer["user_id"]:
                    manageable.add(c["course_id"])
            for m in sql_db.list_memberships_by_user(viewer["user_id"]):
                membership_map[m["course_id"]] = m
                if m["role"] == "teacher" and m["status"] == "approved":
                    manageable.add(m["course_id"])

        # 文档数（SQLite 按课程聚合）与节点数/关系数（Neo4j 按课程聚合；Neo4j 不可用时置 0）
        doc_counts = sql_db.count_documents_grouped()
        node_counts, edge_counts = {}, {}
        try:
            for r in db.query("MATCH (n:KnowledgePoint) RETURN n.course_id AS cid, count(n) AS cnt"):
                node_counts[r["cid"]] = r["cnt"]
            for r in db.query(
                "MATCH (a:KnowledgePoint)-[rel]->(b:KnowledgePoint) "
                "WHERE a.course_id = b.course_id "
                "RETURN a.course_id AS cid, count(rel) AS cnt"
            ):
                edge_counts[r["cid"]] = r["cnt"]
        except Exception:
            pass

        member_counts = sql_db.member_counts_by_course([r["course_id"] for r in rows])

        items = []
        for r in rows:
            cid = r["course_id"]
            item = {
                "course_id": cid,
                "course_name": r["course_name"],
                "course_code": r.get("course_code"),
                "description": r.get("description"),
                "teacher_id": r["teacher_id"],
                "teacher_name": r.get("teacher_name", ""),
                "document_count": doc_counts.get(cid, 0),
                "node_count": node_counts.get(cid, 0),
                "edge_count": edge_counts.get(cid, 0),
                "status": r["status"],
                # 治理状态必须带出来：发现课程等功能用 course_is_visible 组合判定
                # （status 与 governance_status 同时满足），少了这个键会让过滤条件
                # 因为读到 None 而静默失效——下架的课程照样出现在发现页。
                "governance_status": r.get("governance_status") or "normal",
                "created_at": r["created_at"],
                "updated_at": r.get("updated_at"),
                "category": r.get("category"),
                "organization": r.get("organization"),
                "cover": r.get("cover"),
                "join_mode": r.get("join_mode"),
                "is_public": r.get("is_public"),
            }
            if cid in manageable:
                item["join_code"] = r.get("join_code")
            item.update(_member_summary(member_counts.get(cid)))
            if viewer:
                # 与 list_my_courses 同一套约定：创建者即使没有成员行也算教师
                is_owner = r["teacher_id"] == viewer["user_id"]
                m = membership_map.get(cid) or {}
                item["is_owner"] = is_owner
                item["my_role"] = m.get("role") or ("teacher" if is_owner else None)
                item["my_status"] = m.get("status") or ("approved" if is_owner else None)
            items.append(item)

        return {"ok": True, "code": 0, "message": "success",
                "data": {"total": total, "page": page, "page_size": page_size, "items": items}}

    @staticmethod
    def list_my_courses(user: dict, status: str = "approved", page: int = 1,
                        page_size: int = 12, keyword: str = None) -> dict:
        """我的课程：教师=自己创建/协作的；学生=已通过审核的。

        status='pending' 时返回「申请中 / 被拒绝」的课程（学生查看审核进度与意见）。
        """
        uid, role = user["user_id"], user.get("role", "")
        memberships = {m["course_id"]: m for m in sql_db.list_memberships_by_user(uid)}
        if status == "approved":
            ids = sql_db.list_accessible_course_ids(uid, role)
        else:
            ids = [cid for cid, m in memberships.items()
                   if m["status"] in ("pending", "rejected")]
        if not ids:
            return {"ok": True, "code": 0, "message": "success",
                    "data": {"total": 0, "page": 1, "page_size": page_size, "items": []}}

        r = CourseService.list_courses(page, page_size, None, keyword,
                                       course_ids=ids, viewer=user)
        for item in r["data"]["items"]:
            m = memberships.get(item["course_id"]) or {}
            # 课程创建者即使没有成员行也算教师
            is_owner = item["teacher_id"] == uid
            item["my_role"] = m.get("role") or ("teacher" if is_owner else "student")
            item["my_status"] = m.get("status") or ("approved" if is_owner else None)
            item["is_owner"] = is_owner
            item["review_comment"] = m.get("review_comment")
            item["applied_reason"] = m.get("applied_reason")
            item["joined_at"] = m.get("joined_at")
        return r

    @staticmethod
    def list_discover(user: dict, keyword: str = None, category: str = None,
                      page: int = 1, page_size: int = 12) -> dict:
        """发现课程：公开且启用的课程，排除「我已是成员 / 我已申请」的"""
        uid = user["user_id"]
        mine = [m["course_id"] for m in sql_db.list_memberships_by_user(uid)]
        r = CourseService.list_courses(page, page_size, None, keyword,
                                       category=category, is_public=1,
                                       exclude_course_ids=mine)
        # 发现课程页不带加课码：这里的课程调用者都不是教师（自己创建的课已被排除）
        # 只保留「对学生可见」的课程：业务状态开放 **且** 未被平台下架 / 归档。
        # 判定口径统一在 course_is_visible，避免各处自行组合 status 与 governance_status。
        data = r["data"]
        data["items"] = [i for i in data["items"] if course_is_visible(i)]
        memberships = {m["course_id"]: m for m in sql_db.list_memberships_by_user(uid)}
        for item in data["items"]:
            item["my_relation"] = "NONE" if item["course_id"] not in memberships else "KNOWN"
        return r

    # ---------- 详情 ----------

    @staticmethod
    def get_course_detail(course_id: int, viewer: dict = None) -> dict:
        course = sql_db.get_course(course_id)
        if course is None:
            return {"ok": False, "code": 2001, "message": f"课程不存在: course_id={course_id}"}

        teacher = sql_db.get_user_by_id(course["teacher_id"])
        teacher_name = ""
        if teacher:
            profile = sql_db.get_user_profile(teacher["user_id"]) or {}
            teacher_name = ProfileService.display_name_of(
                {**profile, **{"username": teacher.get("username"),
                               "display_name": teacher.get("display_name")}})

        doc_count = sql_db.count_documents_by_course(course_id)
        # Neo4j 不可用时统计退化为 0，而不是让课程详情整页 500
        try:
            node_cnt = db.query(
                "MATCH (n:KnowledgePoint {course_id: $cid}) RETURN count(n) AS cnt",
                {"cid": course_id},
            )[0]["cnt"]
            edge_cnt = db.query(
                "MATCH (:KnowledgePoint {course_id: $cid})-[r]->(:KnowledgePoint {course_id: $cid}) "
                "RETURN count(r) AS cnt",
                {"cid": course_id},
            )[0]["cnt"]
        except Exception:
            node_cnt, edge_cnt = 0, 0

        counts = sql_db.count_members_by_status(course_id)
        data = {
            "course_id": course_id,
            "course_name": course["course_name"],
            "course_code": course.get("course_code"),
            "description": course.get("description"),
            "teacher_id": course["teacher_id"],
            "teacher_name": teacher_name,
            "document_count": doc_count,
            "node_count": node_cnt,
            "edge_count": edge_cnt,
            "status": course["status"],
            "created_at": course["created_at"],
            "updated_at": course["updated_at"],
            "category": course.get("category"),
            "organization": course.get("organization"),
            "cover": course.get("cover"),
            "join_mode": course.get("join_mode"),
            "is_public": course.get("is_public"),
            "member_count": counts.get("approved", 0),
            "pending_count": counts.get("pending", 0),
        }
        # 加课码只对课程教师可见（学生拿到码也只会重复加入，没有必要暴露）
        if viewer and (viewer["user_id"] == course["teacher_id"]
                       or (viewer.get("role") == "teacher"
                           and sql_db.get_membership(course_id, viewer["user_id"]))):
            data["join_code"] = course.get("join_code")
        return {"ok": True, "code": 0, "message": "success", "data": data}

    # ---------- 更新 ----------

    @staticmethod
    def update_course(course_id: int, course_name: str = None,
                      course_code: str = None, description: str = None,
                      category: str = None, organization: str = None,
                      cover: str = None, join_mode: str = None,
                      is_public: int = None) -> dict:
        course = sql_db.get_course(course_id)
        if course is None:
            return {"ok": False, "code": 2001, "message": f"课程不存在: course_id={course_id}"}

        # 逐字段校验
        if course_name is not None:
            course_name = course_name.strip()
            if not course_name:
                return {"ok": False, "code": 1001, "message": "课程名不能为空"}
            if len(course_name) > 50:
                return {"ok": False, "code": 1001, "message": "课程名过长（最大50字符）"}
            if course_name != course["course_name"] and sql_db.get_course_by_name(course_name):
                return {"ok": False, "code": 2003, "message": f"课程名已存在: {course_name}"}
        if description is not None and len(description) > 500:
            return {"ok": False, "code": 1001, "message": "课程简介过长（最大500字符）"}
        if (course_code is not None and course_code != course.get("course_code")
                and sql_db.get_course_by_code(course_code)):
            return {"ok": False, "code": 2003, "message": f"课程编号已存在: {course_code}"}
        if join_mode is not None and join_mode not in JOIN_MODES:
            return {"ok": False, "code": 1001,
                    "message": f"加入方式非法（可选：{'/'.join(JOIN_MODES)}）"}
        if is_public is not None and is_public not in (0, 1):
            return {"ok": False, "code": 1001, "message": "是否公开取值非法（0/1）"}
        if category is not None and len(category) > 30:
            return {"ok": False, "code": 1001, "message": "课程分类过长（最大30字符）"}

        # 注意：join_code 不在此接口更新，只能通过「刷新加课码」重生成，
        # 避免教师手填一个与别课冲突 / 有规律的码。
        sql_db.update_course(course_id, course_name=course_name,
                             course_code=course_code, description=description,
                             category=category, organization=organization,
                             cover=cover, join_mode=join_mode, is_public=is_public)
        updated = sql_db.get_course(course_id)
        return {"ok": True, "code": 0, "message": "success",
                "data": {"course_id": course_id, "updated": True, "course": updated}}

    @staticmethod
    def refresh_join_code(course_id: int) -> dict:
        """刷新加课码：单条 UPDATE 覆盖，旧码立即失效"""
        course = sql_db.get_course(course_id)
        if course is None:
            return {"ok": False, "code": 2001, "message": f"课程不存在: course_id={course_id}"}
        code = CourseService._unique_join_code()
        sql_db.update_course_join_code(course_id, code)
        return {"ok": True, "code": 0, "message": "success",
                "data": {"course_id": course_id, "join_code": code}}

    @staticmethod
    def get_join_code(course_id: int) -> dict:
        course = sql_db.get_course(course_id)
        if course is None:
            return {"ok": False, "code": 2001, "message": f"课程不存在: course_id={course_id}"}
        return {"ok": True, "code": 0, "message": "success", "data": {
            "course_id": course_id,
            "join_code": course.get("join_code"),
            "join_mode": course.get("join_mode"),
            "is_public": course.get("is_public"),
        }}

    # ---------- 删除 ----------

    @staticmethod
    def delete_course(course_id: int, confirm: bool = False) -> dict:
        course = sql_db.get_course(course_id)
        if course is None:
            return {"ok": False, "code": 2001, "message": f"课程不存在: course_id={course_id}"}
        if not confirm:
            return {"ok": False, "code": 2008, "message": "删除课程需二次确认（confirm=true）"}

        # 1. 删除文档文件（本地文件系统）
        # 路径解析见 core/storage：历史行的 file_path 可能是其他机器的路径，需回退定位
        docs = sql_db.list_documents_by_course(course_id)
        for d in docs:
            p = resolve_document_path(d)
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

        # 2. 删除 Neo4j 图谱节点与关系
        removed_nodes, removed_edges = db.delete_course_graph(course_id)

        # 3. 删除 SQLite 记录（成员 + 邀请 + 文档 + 学习记录 + 收藏 + 向量 + 题库 + 课程）
        # 题库（题目/答题记录/题目收藏）在 SQLDatabase.delete_course 内按子表顺序一并清理，
        # 这里先取题目数用于返回报告（删除后无法再统计）。
        removed_embeddings = sql_db.count_embeddings_by_course(course_id)
        removed_questions = sql_db.count_questions_by_course(course_id)
        removed_answers = sql_db.count_answers_by_course(course_id)
        removed_documents = sql_db.delete_course(course_id)

        return {"ok": True, "code": 0, "message": "success", "data": {
            "course_id": course_id,
            "deleted": True,
            "removed_documents": removed_documents,
            "removed_nodes": removed_nodes,
            "removed_edges": removed_edges,
            "removed_embeddings": removed_embeddings,
            "removed_questions": removed_questions,
            "removed_answers": removed_answers,
        }}
