"""学习记录服务（标记掌握 / 取消掌握 / 学习进度）。

Phase 2 从 api/learning.py 下沉：权限校验、Neo4j 知识点存在性校验与 SQLite
读写统一放在服务层，路由只做参数解析与响应包装。
"""
from datetime import datetime

from ..core.database import db
from ..core.permissions import Permissions
from ..core.sql_database import sql_db

# 状态取值（对齐 sql_database 的 RECORD_STATUS）
VALID_STATUS = {"MASTERED", "LEARNING", "RECOMMENDED"}


def _coerce_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class LearningService:
    """学生知识点掌握状态与学习进度。"""

    @staticmethod
    def _ok(data=None) -> dict:
        return {"ok": True, "code": 0, "message": "success", "data": data}

    @staticmethod
    def _fail(code: int, message: str) -> dict:
        return {"ok": False, "code": code, "message": message, "data": None}

    @staticmethod
    def mark(current_user: dict, course_id, document_id, kp_id: str,
             status: str = "MASTERED", mastery_level: int = 100) -> dict:
        """标记/更新知识点掌握状态（需为课程成员，且知识点必须存在于该文档图谱）。"""
        cid = _coerce_int(course_id)
        if cid is None:
            return LearningService._fail(4001, "参数错误：course_id 必须为整数")
        perm = Permissions.require_course_content(cid, current_user)
        if not perm["ok"]:
            return LearningService._fail(perm["code"], perm["message"])

        did = _coerce_int(document_id)
        if did is None:
            return LearningService._fail(4001, "参数错误：document_id 必须为整数")
        kp_id = (kp_id or "").strip()
        if not kp_id:
            return LearningService._fail(4001, "参数错误：kp_id 不能为空")
        if status not in VALID_STATUS:
            return LearningService._fail(
                4001, f"参数错误：status 取值应为 {sorted(VALID_STATUS)}")
        if not (0 <= mastery_level <= 100):
            return LearningService._fail(4001, "参数错误：mastery_level 应在 0-100 之间")

        # 校验知识点存在（文档级），避免写入孤立学习记录。
        recs = db.query(
            "MATCH (n:KnowledgePoint {course_id: $cid, document_id: $did, kp_id: $kp_id}) "
            "RETURN n.kp_id AS kp_id",
            {"cid": cid, "did": did, "kp_id": kp_id},
        )
        if not recs:
            return LearningService._fail(
                4002, f"知识点不存在：course_id={cid}, document_id={did}, kp_id={kp_id}")

        record_id = sql_db.upsert_learning_record(
            user_id=current_user["user_id"], course_id=cid, document_id=did,
            kp_id=kp_id, status=status, mastery_level=mastery_level,
            source="MANUAL", last_learned_at=_now(),
        )
        return LearningService._ok({
            "record_id": record_id,
            "course_id": cid,
            "document_id": did,
            "kp_id": kp_id,
            "status": status,
            "mastery_level": mastery_level,
        })

    @staticmethod
    def unmark(user_id: int, course_id, document_id, kp_id: str) -> dict:
        """取消掌握标记。

        刻意不校验课程成员身份：退出/被移除课程后仍须能清理自己的记录。
        幂等：记录不存在时返回 deleted=0，不报错。
        """
        cid = _coerce_int(course_id)
        if cid is None:
            return LearningService._fail(4001, "参数错误：course_id 必须为整数")
        did = _coerce_int(document_id)
        if did is None:
            return LearningService._fail(4001, "参数错误：document_id 必须为整数")
        kp_id = (kp_id or "").strip()
        if not kp_id:
            return LearningService._fail(4001, "参数错误：kp_id 不能为空")

        deleted = sql_db.delete_learning_record(user_id, cid, did, kp_id)
        return LearningService._ok({"course_id": cid, "document_id": did,
                                    "kp_id": kp_id, "deleted": deleted})

    @staticmethod
    def progress(current_user: dict, course_id=None, document_id=None) -> dict:
        """学习进度；指定 course_id + document_id 返回该文档进度，否则返回全部记录。"""
        user_id = current_user["user_id"]

        if course_id is not None and course_id != "":
            cid_check = _coerce_int(course_id)
            if cid_check is None:
                return LearningService._fail(4001, "参数错误：course_id 必须为整数")
            perm = Permissions.require_course_content(cid_check, current_user)
            if not perm["ok"]:
                return LearningService._fail(perm["code"], perm["message"])

        if course_id is not None and course_id != "" \
                and document_id is not None and document_id != "":
            cid = _coerce_int(course_id)
            if cid is None:
                return LearningService._fail(4001, "参数错误：course_id 必须为整数")
            did = _coerce_int(document_id)
            if did is None:
                return LearningService._fail(4001, "参数错误：document_id 必须为整数")
            records = sql_db.list_records_by_user_course(user_id, cid, did)
            mastered = [r for r in records if r["status"] == "MASTERED"]
            total = db.query(
                "MATCH (n:KnowledgePoint {course_id: $cid, document_id: $did}) "
                "RETURN count(n) AS cnt",
                {"cid": cid, "did": did},
            )
            total_nodes = total[0]["cnt"] if total else 0
            return LearningService._ok({
                "course_id": cid,
                "document_id": did,
                "total_nodes": total_nodes,
                "mastered_count": len(mastered),
                "mastered_kp_ids": [r["kp_id"] for r in mastered],
                "progress": round(len(mastered) / total_nodes * 100, 1) if total_nodes else 0.0,
                "records": records,
            })

        # 只指定课程、未指定文档：返回**课程级**进度。
        # 调用方（学生端概览 / 学习路径）只拿到 course_id 时，契约里应当有
        # mastered_kp_ids；旧实现落到最下面的「全部课程」分支，返回的是
        # {courses: [...]}，调用方读 progress.mastered_kp_ids 得到 undefined，
        # 已掌握标记被当成空集合。这里补齐课程级口径（与文档级同名字段）。
        if course_id is not None and course_id != "":
            cid = _coerce_int(course_id)
            if cid is None:
                return LearningService._fail(4001, "参数错误：course_id 必须为整数")
            records = sql_db.list_records_by_user_course(user_id, cid)
            mastered = [r for r in records if r["status"] == "MASTERED"]
            total = db.query(
                "MATCH (n:KnowledgePoint {course_id: $cid}) RETURN count(n) AS cnt",
                {"cid": cid},
            )
            total_nodes = total[0]["cnt"] if total else 0
            return LearningService._ok({
                "course_id": cid,
                "document_id": None,
                "total_nodes": total_nodes,
                "mastered_count": len(mastered),
                "mastered_kp_ids": [r["kp_id"] for r in mastered],
                "progress": round(len(mastered) / total_nodes * 100, 1) if total_nodes else 0.0,
                "records": records,
            })

        # 不指定课程：返回该用户全部学习记录（按课程分组）。
        all_records = sql_db.list_records_by_user(user_id)
        by_course = {}
        for r in all_records:
            by_course.setdefault(r["course_id"], []).append(r)
        return LearningService._ok({
            "courses": [
                {
                    "course_id": cid,
                    "mastered_kp_ids": [r["kp_id"] for r in rs if r["status"] == "MASTERED"],
                    "record_count": len(rs),
                }
                for cid, rs in by_course.items()
            ],
        })
