"""
数据总览 API
"""
from fastapi import APIRouter, Depends

from ..core.dependencies import get_current_user
from ..core.response import success
from ..services.dashboard_service import DashboardService

router = APIRouter(prefix="/api/v1/dashboard", tags=["数据总览"])


@router.get("/stats")
async def get_stats(current_user: dict = Depends(get_current_user)):
    """获取全局数据总览统计（课程/用户/文档/知识点/关系，缺数据返回 0）"""
    return success(DashboardService.get_stats())
