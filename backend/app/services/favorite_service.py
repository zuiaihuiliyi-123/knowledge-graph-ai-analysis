"""学生知识点收藏服务。

Phase 2 从 api/favorites.py 下沉：收藏按 (user_id, course_id, document_id, kp_id)
隔离，user_id 始终取自 JWT；新增收藏前校验课程成员身份与知识点存在性。
"""
from ..core.database import db
from ..core.permissions import Permissions
from ..core.sql_database import sql_db


def _coerce_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class FavoriteService:
    """知识点收藏的查询、新增与取消。"""

    @staticmethod
    def _ok(data=None) -> dict:
        return {"ok": True, "code": 0, "message": "success", "data": data}

    @staticmethod
    def _fail(code: int, message: str) -> dict:
        return {"ok": False, "code": code, "message": message, "data": None}

    @staticmethod
    def list(current_user: dict, course_id=None, document_id=None) -> dict:
        """查询当前用户的收藏；指定课程时校验成员身份，未指定时返回全部历史收藏。"""
        user_id = current_user["user_id"]
        if course_id is not None and course_id != "":
            cid_check = _coerce_int(course_id)
            if cid_check is None:
                return FavoriteService._fail(4001, "参数错误：course_id 必须为整数")
            perm = Permissions.require_course_content(cid_check, current_user)
            if not perm["ok"]:
                return FavoriteService._fail(perm["code"], perm["message"])

        if course_id is not None and course_id != "" \
                and document_id is not None and document_id != "":
            cid = _coerce_int(course_id)
            did = _coerce_int(document_id)
            if cid is None:
                return FavoriteService._fail(4001, "参数错误：course_id 必须为整数")
            if did is None:
                return FavoriteService._fail(4001, "参数错误：document_id 必须为整数")
            rows = sql_db.list_favorites_by_user_course(user_id, cid, did)
        elif course_id is not None and course_id != "":
            cid = _coerce_int(course_id)
            if cid is None:
                return FavoriteService._fail(4001, "参数错误：course_id 必须为整数")
            rows = sql_db.list_favorites_by_user_course(user_id, cid)
        else:
            rows = sql_db.list_favorites_by_user(user_id)
        return FavoriteService._ok({"items": rows, "count": len(rows)})

    @staticmethod
    def add(current_user: dict, course_id, document_id, kp_id: str) -> dict:
        """新增收藏（幂等；需为课程成员且知识点存在于该文档图谱）。"""
        cid = _coerce_int(course_id)
        if cid is None:
            return FavoriteService._fail(4001, "参数错误：course_id 必须为整数")
        perm = Permissions.require_course_content(cid, current_user)
        if not perm["ok"]:
            return FavoriteService._fail(perm["code"], perm["message"])

        did = _coerce_int(document_id)
        if did is None:
            return FavoriteService._fail(4001, "参数错误：document_id 必须为整数")
        kp_id = (kp_id or "").strip()
        if not kp_id:
            return FavoriteService._fail(4001, "参数错误：kp_id 不能为空")

        recs = db.query(
            "MATCH (n:KnowledgePoint {course_id: $cid, document_id: $did, kp_id: $kp_id}) "
            "RETURN n.kp_id AS kp_id",
            {"cid": cid, "did": did, "kp_id": kp_id},
        )
        if not recs:
            return FavoriteService._fail(
                4002, f"知识点不存在：course_id={cid}, document_id={did}, kp_id={kp_id}")

        created = sql_db.add_favorite(current_user["user_id"], cid, did, kp_id)
        return FavoriteService._ok({"course_id": cid, "document_id": did,
                                    "kp_id": kp_id, "created": created})

    @staticmethod
    def remove(user_id: int, course_id, document_id, kp_id: str) -> dict:
        """取消收藏；不校验成员身份（退出课程后仍可清理），幂等返回 deleted。"""
        cid = _coerce_int(course_id)
        if cid is None:
            return FavoriteService._fail(4001, "参数错误：course_id 必须为整数")
        did = _coerce_int(document_id)
        if did is None:
            return FavoriteService._fail(4001, "参数错误：document_id 必须为整数")
        kp_id = (kp_id or "").strip()
        if not kp_id:
            return FavoriteService._fail(4001, "参数错误：kp_id 不能为空")

        deleted = sql_db.remove_favorite(user_id, cid, did, kp_id)
        return FavoriteService._ok({"course_id": cid, "document_id": did,
                                    "kp_id": kp_id, "deleted": deleted})
