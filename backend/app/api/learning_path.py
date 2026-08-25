"""
学习路径推荐 API（对齐规划文档 6.5，响应格式统一 {code, message, data, timestamp}）
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import List

from ..core.response import success
from ..services.path_recommender import PathRecommender

router = APIRouter(prefix="/api/v1/learning-path", tags=["学习路径"])


class RecommendRequest(BaseModel):
    mastered: List[str] = []  # 已掌握的知识点名称列表
    course_id: str = None


class TargetPathRequest(BaseModel):
    target: str  # 目标知识点
    course_id: str = None


@router.post("/recommend")
async def recommend_next(request: RecommendRequest):
    """
    根据已掌握知识，推荐下一步学习内容
    """
    recommendations = PathRecommender.recommend_next(
        mastered_knowledge=request.mastered,
        course_id=request.course_id
    )
    return success({
        "mastered": request.mastered,
        "recommendations": recommendations,
    })


@router.post("/path-to-target")
async def path_to_target(request: TargetPathRequest):
    """
    生成到达目标知识点的学习路径
    """
    paths = PathRecommender.get_learning_path(
        target_knowledge=request.target,
        course_id=request.course_id
    )
    return success({
        "target": request.target,
        "paths": paths,
        "path_count": len(paths),
    })


@router.get("/prerequisites/{knowledge_name}")
async def get_prerequisites(knowledge_name: str, course_id: str = None):
    """
    获取某个知识点的所有前置知识
    """
    prereqs = PathRecommender.get_prerequisites(knowledge_name, course_id)
    return success({
        "knowledge": knowledge_name,
        "prerequisites": prereqs,
        "count": len(prereqs),
    })
