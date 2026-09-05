"""
学习路径推荐 API（对齐规划文档 6.5，响应格式统一 {code, message, data, timestamp}）
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List

from ..core.response import success
from ..core.dependencies import get_current_user
from ..services.path_recommender import PathRecommender

router = APIRouter(prefix="/api/v1/learning-path", tags=["学习路径"])


class RecommendRequest(BaseModel):
    mastered: List[str] = []  # 已掌握的知识点名称列表
    course_id: str | None = None
    document_id: str | None = None


class TargetPathRequest(BaseModel):
    target: str  # 目标知识点
    course_id: str | None = None
    document_id: str | None = None


@router.post("/recommend")
async def recommend_next(request: RecommendRequest, current_user: dict = Depends(get_current_user)):
    """
    根据已掌握知识，推荐下一步学习内容（Phase 8B：按 course_id + document_id 隔离）
    """
    recommendations = PathRecommender.recommend_next(
        mastered_knowledge=request.mastered,
        course_id=request.course_id,
        document_id=request.document_id,
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
    result = PathRecommender.get_learning_path(
        target_knowledge=request.target,
        course_id=request.course_id,
        document_id=request.document_id,
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
    prereqs = PathRecommender.get_prerequisites(knowledge_name, course_id, document_id)
    return success({
        "knowledge": knowledge_name,
        "prerequisites": prereqs,
        "count": len(prereqs),
    })
