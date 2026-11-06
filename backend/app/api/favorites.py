"""
学生收藏夹 API（收藏 = 学生个人知识点书签，独立于学习状态）

- GET    /api/v1/favorites?course_id={course_id}  查询当前用户的收藏（按课程过滤）
- POST   /api/v1/favorites                        新增收藏 {course_id, kp_id}
- DELETE /api/v1/favorites/{kp_id}?course_id={id} 取消收藏

收藏按 (user_id, course_id, kp_id) 唯一隔离，user_id 始终取自 JWT 登录用户，
不允许前端传入任意 user_id。
"""
from fastapi import APIRouter, Depends, Query

from ..core.dependencies import get_current_user
from ..core.response import success, error
from ..services.favorite_service import FavoriteService
from ..schemas.favorites import FavoriteCreate

router = APIRouter(prefix="/api/v1/favorites", tags=["收藏夹"])


def _reply(result: dict) -> dict:
    """把 service 的统一结果信封转换为接口响应。"""
    if result["ok"]:
        return success(result["data"], result.get("message", "success"))
    return error(result["code"], result["message"])


@router.get("")
async def list_favorites(
    course_id: str = Query(None, description="课程 ID（可选，缺省返回全部课程的收藏）"),
    document_id: str = Query(None, description="文档 ID（可选，Phase 8B 按文档隔离）"),
    current_user: dict = Depends(get_current_user),
):
    """查询当前登录用户的收藏知识点（按课程 + 文档过滤）。

    课程中心改造：指定了 course_id 时校验课程成员身份；不指定 course_id 的
    「全部课程」分支保持原样——读的是自己的收藏，且退出课程后仍需能查看历史收藏。
    """
    return _reply(FavoriteService.list(current_user, course_id, document_id))


@router.post("")
async def add_favorite(request: FavoriteCreate, current_user: dict = Depends(get_current_user)):
    """新增收藏（幂等：重复收藏不产生重复记录；需为课程成员）"""
    return _reply(FavoriteService.add(
        current_user, request.course_id, request.document_id, request.kp_id))


@router.delete("/{kp_id}")
async def remove_favorite(
    kp_id: str,
    course_id: str = Query(..., description="课程 ID（收藏按课程隔离，删除需指定课程）"),
    document_id: str = Query(..., description="文档 ID（Phase 8B 按文档隔离）"),
    current_user: dict = Depends(get_current_user),
):
    """取消收藏。

    与 DELETE /learning/mark 保持同一套口径：

    - 不校验课程成员身份（退出/被移除课程后仍须能清理自己的收藏）；
    - 幂等：收藏不存在时返回 `deleted=0`，不报错；
    - 参数校验与 POST /favorites 一致：`kp_id` 为空/空白（例如路径里是 `%20`）直接 4001。
      以前空白 kp_id 会被带进 SQL 删 0 行并返回 success，前端无从判断是否真的取消成功。
    """
    return _reply(FavoriteService.remove(
        current_user["user_id"], course_id, document_id, kp_id))
