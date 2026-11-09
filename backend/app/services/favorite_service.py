"""学生知识点收藏服务。

Phase 2 从 api/favorites.py 下沉：收藏按 (user_id, course_id, document_id, kp_id)
隔离，user_id 始终取自 JWT；新增收藏前校验课程成员身份与知识点存在性。
"""
import logging

from ..core.database import db
from ..core.permissions import Permissions
from ..core.sql_database import sql_db

logger = logging.getLogger(__name__)


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
            FavoriteService._backfill_names(user_id, cid, did, rows)
        elif course_id is not None and course_id != "":
            cid = _coerce_int(course_id)
            if cid is None:
                return FavoriteService._fail(4001, "参数错误：course_id 必须为整数")
            rows = sql_db.list_favorites_by_user_course(user_id, cid)
            FavoriteService._backfill_names(user_id, cid, None, rows)
        else:
            rows = sql_db.list_favorites_by_user(user_id)
        return FavoriteService._ok({"items": rows, "count": len(rows)})

    @staticmethod
    def _backfill_names(user_id: int, course_id: int, document_id, rows: list) -> None:
        """为缺少名字快照的旧收藏补名字。

        早期收藏只存 kp_id；一旦文档重新抽取（kp_id 整体更换），这些收藏就永远无法
        映射回任何知识点。这里趁节点还在，按 kp_id 从 Neo4j 取名字写回 SQLite，
        以后再抽取就能按同名回挂。Neo4j 不可用时保持原样（读收藏不应因此失败）。
        """
        missing = [r for r in rows if not r.get("kp_name")]
        if not missing:
            return
        kp_ids = [r["kp_id"] for r in missing if r.get("kp_id")]
        if not kp_ids:
            return
        try:
            recs = db.query(
                "MATCH (n:KnowledgePoint {course_id: $cid}) WHERE n.kp_id IN $kp_ids "
                "RETURN n.kp_id AS kp_id, n.name AS kp_name",
                {"cid": course_id, "kp_ids": kp_ids},
            )
        except Exception as e:  # noqa: BLE001 - 补名字是尽力而为，失败不能影响收藏列表
            logger.warning("收藏补名字失败 course_id=%s: %s", course_id, e)
            return
        name_by_id = {r["kp_id"]: r.get("kp_name") for r in recs if r.get("kp_name")}
        for r in missing:
            kp_name = name_by_id.get(r.get("kp_id"))
            if not kp_name:
                continue
            r["kp_name"] = kp_name
            sql_db.update_favorite_kp_name(
                user_id, course_id, document_id if document_id is not None else r.get("document_id"),
                r["kp_id"], kp_name,
            )

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
            "RETURN n.kp_id AS kp_id, n.name AS kp_name",
            {"cid": cid, "did": did, "kp_id": kp_id},
        )
        if not recs:
            return FavoriteService._fail(
                4002, f"知识点不存在：course_id={cid}, document_id={did}, kp_id={kp_id}")

        kp_name = recs[0].get("kp_name")
        created = sql_db.add_favorite(current_user["user_id"], cid, did, kp_id, kp_name)
        return FavoriteService._ok({"course_id": cid, "document_id": did,
                                    "kp_id": kp_id, "kp_name": kp_name, "created": created})

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
