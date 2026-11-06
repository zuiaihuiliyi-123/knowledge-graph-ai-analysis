"""
学习记录 API（对齐规划文档 6.6 学习记录持久化）

mark：标记知识点掌握状态（学生点击"掌握"按钮 / 图谱节点勾选）
progress：查询学生在某课程下的学习进度（已掌握知识点 + 总进度百分比）
"""
from fastapi import APIRouter, Depends

from ..core.dependencies import get_current_user
from ..core.response import success, error
from ..services.learning_service import LearningService
from ..schemas.learning import MarkRequest

router = APIRouter(prefix="/api/v1/learning", tags=["学习记录"])


def _reply(result: dict) -> dict:
    """把 service 的统一结果信封转换为接口响应。"""
    if result["ok"]:
        return success(result["data"], result.get("message", "success"))
    return error(result["code"], result["message"])


@router.post("/mark")
async def mark_knowledge(request: MarkRequest, current_user: dict = Depends(get_current_user)):
    """标记/更新知识点掌握状态（默认 MASTERED 表示已掌握；需为课程成员）"""
    return _reply(LearningService.mark(
        current_user, request.course_id, request.document_id, request.kp_id,
        request.status, request.mastery_level))


@router.delete("/mark")
async def unmark_knowledge(course_id: str, document_id: str, kp_id: str,
                           current_user: dict = Depends(get_current_user)):
    """取消知识点掌握标记（删除对应学习记录）。

    两条刻意保持的口径（与 POST /mark 对齐，也与 DELETE /favorites 对齐）：

    - **不校验课程成员身份**：退出/被移除课程后，学生仍须能清理自己的记录；
    - **幂等**：记录本来就不存在时返回 `deleted=0` 而不是报错——重复点击「取消掌握」
      不该弹红条（POST 是 upsert，二者合起来才是「设置/取消」的完整语义）。

    但参数校验必须和 POST 一致：`kp_id` 为空串/空白时直接 4001。
    以前这里会把空串一路带进 SQL（`WHERE kp_id = ''`），删掉 0 行却返回 success，
    调用方看不出「什么都没发生」。
    """
    return _reply(LearningService.unmark(
        current_user["user_id"], course_id, document_id, kp_id))


@router.get("/progress")
async def get_progress(course_id: str = None, document_id: str = None,
                       current_user: dict = Depends(get_current_user)):
    """查询学生学习进度；指定 course_id + document_id 返回该文档进度，否则返回全部学习记录（按课程分组）。

    课程中心改造：指定课程时校验成员身份；「全部学习记录」分支保持原样——
    那是学生自己的历史数据，退出课程后学习总览仍要能展示。
    """
    return _reply(LearningService.progress(current_user, course_id, document_id))
