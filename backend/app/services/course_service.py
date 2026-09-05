"""
课程管理服务（对齐规划文档 6.6.6~6.6.10）

统一返回 {"ok": bool, "code": int, "message": str, "data": dict}
"""
import os

from ..core.database import db
from ..core.sql_database import sql_db


class CourseService:
    """课程 CRUD 业务逻辑"""

    @staticmethod
    def create_course(course_name: str, teacher_id: int = None,
                      course_code: str = None, description: str = None) -> dict:
        # 参数校验
        if not course_name or not course_name.strip():
            return {"ok": False, "code": 1001, "message": "课程名不能为空"}
        course_name = course_name.strip()
        if len(course_name) > 50:
            return {"ok": False, "code": 1001, "message": "课程名过长（最大50字符）"}
        if description and len(description) > 500:
            return {"ok": False, "code": 1001, "message": "课程简介过长（最大500字符）"}

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

        course_id = sql_db.create_course(
            course_name=course_name, teacher_id=teacher_id,
            course_code=course_code, description=description,
        )
        return {"ok": True, "code": 0, "message": "success",
                "data": {"course_id": course_id, "course_name": course_name, "created": True}}

    @staticmethod
    def list_courses(page: int = 1, page_size: int = 10,
                     teacher_id: int = None, keyword: str = None) -> dict:
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        total, rows = sql_db.list_courses_page(page, page_size, teacher_id, keyword)

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

        items = []
        for r in rows:
            cid = r["course_id"]
            items.append({
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
                "created_at": r["created_at"],
                "updated_at": r.get("updated_at"),
            })

        return {"ok": True, "code": 0, "message": "success",
                "data": {"total": total, "page": page, "page_size": page_size, "items": items}}

    @staticmethod
    def get_course_detail(course_id: int) -> dict:
        course = sql_db.get_course(course_id)
        if course is None:
            return {"ok": False, "code": 2001, "message": f"课程不存在: course_id={course_id}"}

        teacher = sql_db.get_user_by_id(course["teacher_id"])
        teacher_name = ""
        if teacher:
            teacher_name = teacher.get("display_name") or teacher.get("username", "")

        doc_count = sql_db.count_documents_by_course(course_id)
        node_cnt = db.query(
            "MATCH (n:KnowledgePoint {course_id: $cid}) RETURN count(n) AS cnt",
            {"cid": course_id},
        )[0]["cnt"]
        edge_cnt = db.query(
            "MATCH (:KnowledgePoint {course_id: $cid})-[r]->(:KnowledgePoint {course_id: $cid}) "
            "RETURN count(r) AS cnt",
            {"cid": course_id},
        )[0]["cnt"]

        return {"ok": True, "code": 0, "message": "success", "data": {
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
        }}

    @staticmethod
    def update_course(course_id: int, course_name: str = None,
                      course_code: str = None, description: str = None) -> dict:
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

        sql_db.update_course(course_id, course_name=course_name,
                             course_code=course_code, description=description)
        updated = sql_db.get_course(course_id)
        return {"ok": True, "code": 0, "message": "success",
                "data": {"course_id": course_id, "updated": True, "course": updated}}

    @staticmethod
    def delete_course(course_id: int, confirm: bool = False) -> dict:
        course = sql_db.get_course(course_id)
        if course is None:
            return {"ok": False, "code": 2001, "message": f"课程不存在: course_id={course_id}"}
        if not confirm:
            return {"ok": False, "code": 2008, "message": "删除课程需二次确认（confirm=true）"}

        # 1. 删除文档文件（本地文件系统）
        docs = sql_db.list_documents_by_course(course_id)
        for d in docs:
            p = d.get("file_path")
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

        # 2. 删除 Neo4j 图谱节点与关系
        removed_nodes, removed_edges = db.delete_course_graph(course_id)

        # 3. 删除 SQLite 记录（文档 + 学习记录 + 收藏 + 向量 + 课程；Phase 9 补向量清理）
        removed_embeddings = sql_db.count_embeddings_by_course(course_id)
        removed_documents = sql_db.delete_course(course_id)

        return {"ok": True, "code": 0, "message": "success", "data": {
            "course_id": course_id,
            "deleted": True,
            "removed_documents": removed_documents,
            "removed_nodes": removed_nodes,
            "removed_edges": removed_edges,
            "removed_embeddings": removed_embeddings,
        }}
