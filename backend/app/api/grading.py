"""
主观题批改 API（教师端）

- GET  /api/v1/grading/pending            待批改列表（含题面/参考答案/学生解答，先交先批）
- POST /api/v1/grading/{record_id}        单题批改（就地更新分数/评语）
- POST /api/v1/grading/batch              批量批改（同一分数与评语）
- GET  /api/v1/grading/summary            批改进度汇总（角标）

权限：接口层 require_teacher；service 层再做「课程归属校验」（越权 → 4003）。
⚠ 路由顺序：/pending 与 /summary 是静态路径，但本模块没有 /{x} 形式的 GET，
  故不存在被路径参数吞掉的问题；POST /batch 与 POST /{record_id} 并存时，
  FastAPI 会优先匹配更具体的 /batch 前缀（与题库模块同一约定）。
"""
from fastapi import APIRouter, Depends, Query

from ..core.dependencies import require_teacher
from ..core.response import success, error
from ..services.grading_service import GradingService
from ..schemas.grading import (
    GradeRequest,
    BatchGradeRequest,
)

router = APIRouter(prefix="/api/v1/grading", tags=["主观题批改"])


def _coerce_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _wrap(result: dict):
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.get("/pending")
async def pending_list(
    course_id: str = Query(..., description="课程 ID"),
    document_id: str = Query(None, description="文档 ID（可选，含课程通用题）"),
    kp_id: str = Query(None, description="知识点 ID（可选）"),
    student_id: str = Query(None, description="学生 user_id（可选）"),
    status: str = Query("PENDING", description="筛选：PENDING=待批改（默认）/ GRADED=已批改（仅主观题，供复查改判）/ ALL=全部"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(require_teacher),
):
    """批改台列表：默认只返回待批改的主观题作答（按提交先后排序）

    status=GRADED 时返回已批改的主观题（按批改时间倒序），教师可复查并再次提交改判
    （就地更新语义：允许覆盖旧分与旧评语）。
    """
    cid = _coerce_int(course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    return _wrap(GradingService.pending_list(
        current_user["user_id"], cid, document_id=document_id, kp_id=kp_id,
        student_id=student_id, page=page, page_size=page_size, status=status,
    ))


@router.get("/summary")
async def grading_summary(course_id: str = Query(..., description="课程 ID"),
                          current_user: dict = Depends(require_teacher)):
    """批改进度汇总：待批改 / 已批改 / 平均分（教师端角标）"""
    cid = _coerce_int(course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    return _wrap(GradingService.summary(current_user["user_id"], cid))


@router.post("/batch")
async def grade_batch(body: BatchGradeRequest,
                      current_user: dict = Depends(require_teacher)):
    """批量批改（同一分数与评语）；跳过无权限/客观题/不存在的记录并逐条报告原因"""
    return _wrap(GradingService.grade_batch(
        current_user["user_id"], body.record_ids, body.score, body.comment,
    ))


@router.post("/{record_id}")
async def grade_one(record_id: int, body: GradeRequest,
                    current_user: dict = Depends(require_teacher)):
    """单题批改：0~100 分（>=60 记为答对），可附评语；允许重批（覆盖旧分）"""
    return _wrap(GradingService.grade_one(
        current_user["user_id"], record_id, body.score, body.comment,
    ))
