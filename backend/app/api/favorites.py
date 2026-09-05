"""
学生收藏夹 API（收藏 = 学生个人知识点书签，独立于学习状态）

- GET    /api/v1/favorites?course_id={course_id}  查询当前用户的收藏（按课程过滤）
- POST   /api/v1/favorites                        新增收藏 {course_id, kp_id}
- DELETE /api/v1/favorites/{kp_id}?course_id={id} 取消收藏

收藏按 (user_id, course_id, kp_id) 唯一隔离，user_id 始终取自 JWT 登录用户，
不允许前端传入任意 user_id。
"""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..core.database import db
from ..core.dependencies import get_current_user
from ..core.response import success, error
from ..core.sql_database import sql_db

router = APIRouter(prefix="/api/v1/favorites", tags=["收藏夹"])


def _coerce_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class FavoriteCreate(BaseModel):
    course_id: str
    document_id: str = None
    kp_id: str


@router.get("")
async def list_favorites(
    course_id: str = Query(None, description="课程 ID（可选，缺省返回全部课程的收藏）"),
    document_id: str = Query(None, description="文档 ID（可选，Phase 8B 按文档隔离）"),
    current_user: dict = Depends(get_current_user),
):
    """查询当前登录用户的收藏知识点（按课程 + 文档过滤）"""
    user_id = current_user["user_id"]
    if course_id is not None and course_id != "" and document_id is not None and document_id != "":
        cid = _coerce_int(course_id)
        did = _coerce_int(document_id)
        if cid is None:
            return error(4001, "参数错误：course_id 必须为整数")
        if did is None:
            return error(4001, "参数错误：document_id 必须为整数")
        rows = sql_db.list_favorites_by_user_course(user_id, cid, did)
    elif course_id is not None and course_id != "":
        cid = _coerce_int(course_id)
        if cid is None:
            return error(4001, "参数错误：course_id 必须为整数")
        rows = sql_db.list_favorites_by_user_course(user_id, cid)
    else:
        rows = sql_db.list_favorites_by_user(user_id)
    return success({"items": rows, "count": len(rows)})


@router.post("")
async def add_favorite(request: FavoriteCreate, current_user: dict = Depends(get_current_user)):
    """新增收藏（幂等：重复收藏不产生重复记录）"""
    cid = _coerce_int(request.course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    did = _coerce_int(request.document_id)
    if did is None:
        return error(4001, "参数错误：document_id 必须为整数")
    kp_id = (request.kp_id or "").strip()
    if not kp_id:
        return error(4001, "参数错误：kp_id 不能为空")

    # 校验知识点存在（文档级，与学习记录一致，避免收藏到不存在的知识点）
    recs = db.query(
        "MATCH (n:KnowledgePoint {course_id: $cid, document_id: $did, kp_id: $kp_id}) "
        "RETURN n.kp_id AS kp_id",
        {"cid": cid, "did": did, "kp_id": kp_id},
    )
    if not recs:
        return error(4002, f"知识点不存在：course_id={cid}, document_id={did}, kp_id={kp_id}")

    created = sql_db.add_favorite(current_user["user_id"], cid, did, kp_id)
    return success({"course_id": cid, "document_id": did, "kp_id": kp_id, "created": created})


@router.delete("/{kp_id}")
async def remove_favorite(
    kp_id: str,
    course_id: str = Query(..., description="课程 ID（收藏按课程隔离，删除需指定课程）"),
    document_id: str = Query(..., description="文档 ID（Phase 8B 按文档隔离）"),
    current_user: dict = Depends(get_current_user),
):
    """取消收藏"""
    cid = _coerce_int(course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    did = _coerce_int(document_id)
    if did is None:
        return error(4001, "参数错误：document_id 必须为整数")
    kp_id = (kp_id or "").strip()
    deleted = sql_db.remove_favorite(current_user["user_id"], cid, did, kp_id)
    return success({"course_id": cid, "document_id": did, "kp_id": kp_id, "deleted": deleted})
