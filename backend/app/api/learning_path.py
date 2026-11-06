"""
学习路径推荐 API（对齐规划文档 6.5，响应格式统一 {code, message, data, timestamp}）
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List

from ..core.response import success, error
from ..core.dependencies import get_current_user
from ..core.permissions import Permissions
from ..services.path_recommender import PathRecommender
from ..schemas.learning_path import TargetPathRequest

router = APIRouter(prefix="/api/v1/learning-path", tags=["学习路径"])


def _scope_and_guard(course_id, current_user: dict):
    """返回 (allowed_ids, 错误响应)。显式传课程时先做成员校验，未传时收敛到「我的课程」。"""
    allowed_ids = Permissions.allowed_course_ids(current_user)
    if course_id:
        try:
            cid = int(course_id)
        except (TypeError, ValueError):
            return None, error(1001, f"course_id 必须为整数，收到: {course_id}")
        perm = Permissions.require_course_content(cid, current_user)
        if not perm["ok"]:
            return None, error(perm["code"], perm["message"])
    return allowed_ids, None


class RecommendRequest(BaseModel):
    mastered: List[str] = []  # 已掌握的知识点名称列表
    course_id: str | None = None
    document_id: str | None = None


@router.post("/recommend")
async def recommend_next(request: RecommendRequest, current_user: dict = Depends(get_current_user)):
    """
    根据已掌握知识，推荐下一步学习内容（Phase 8B：按 course_id + document_id 隔离；
    课程中心改造：未传 course_id 时收敛为「我的课程」范围）
    """
    allowed_ids, denied = _scope_and_guard(request.course_id, current_user)
    if denied is not None:
        return denied
    recommendations = PathRecommender.recommend_next(
        mastered_knowledge=request.mastered,
        course_id=request.course_id,
        document_id=request.document_id,
        allowed_ids=allowed_ids,
    )
    return success({
        "mastered": request.mastered,
        "recommendations": recommendations,
    })


@router.post("/path-to-target")
async def path_to_target(request: TargetPathRequest, current_user: dict = Depends(get_current_user)):
    """
    生成到达目标知识点的学习路径；无先修路径时降级返回目标点 + 相关概念
    """
    allowed_ids, denied = _scope_and_guard(request.course_id, current_user)
    if denied is not None:
        return denied
    result = PathRecommender.get_learning_path(
        target_knowledge=request.target,
        course_id=request.course_id,
        document_id=request.document_id,
        allowed_ids=allowed_ids,
    )
    return success({
        "target": request.target,
        "paths": result["paths"],
        "path_count": len(result["paths"]),
        "fallback": result["fallback"],
        "target_node": result["target"],
        "related": result["related"],
        "reason": result["reason"],
    })


@router.get("/prerequisites/{knowledge_name}")
async def get_prerequisites(knowledge_name: str, course_id: str = None, document_id: str = None,
                            current_user: dict = Depends(get_current_user)):
    """
    获取某个知识点的所有前置知识
    """
    allowed_ids, denied = _scope_and_guard(course_id, current_user)
    if denied is not None:
        return denied
    prereqs = PathRecommender.get_prerequisites(knowledge_name, course_id, document_id,
                                                allowed_ids=allowed_ids)
    return success({
        "knowledge": knowledge_name,
        "prerequisites": prereqs,
        "count": len(prereqs),
    })
