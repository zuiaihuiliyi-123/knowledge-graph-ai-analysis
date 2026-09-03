"""
教师教学监测 API（对齐「教师端数据总览」增强）

- GET /api/v1/teacher/students/progress?course_id={course_id}
  查看自己课程下的学生学习进度（仅教师角色；校验课程归属，防止越权查看他人课程）。
"""
from fastapi import APIRouter, Depends, Query

from ..core.dependencies import require_teacher
from ..core.response import success, error
from ..core.sql_database import sql_db
from ..services.teacher_service import TeacherService

router = APIRouter(prefix="/api/v1/teacher", tags=["教师教学监测"])


def _coerce_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@router.get("/students/progress")
async def get_students_progress(
    course_id: str = Query(..., description="课程 ID"),
    current_user: dict = Depends(require_teacher),
):
    """查看自己课程下的学生学习进度（仅教师，课程归属校验）"""
    cid = _coerce_int(course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")

    course = sql_db.get_course(cid)
    if course is None:
        return error(2001, f"课程不存在: course_id={cid}")
    if course["teacher_id"] != current_user["user_id"]:
        return error(4003, "无权限：仅该课程所属教师可查看班级学习情况")

    return success(TeacherService.get_students_progress(cid))
