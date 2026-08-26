"""
课程管理 API（对齐规划文档 6.6.6~6.6.10）
"""
from fastapi import APIRouter, Query, Depends
from pydantic import BaseModel

from ..core.response import success, error
from ..core.dependencies import get_current_user, require_teacher
from ..services.course_service import CourseService

router = APIRouter(prefix="/api/v1/courses", tags=["课程管理"])


class CourseCreate(BaseModel):
    course_name: str
    course_code: str = None
    description: str = None


class CourseUpdate(BaseModel):
    course_name: str = None
    course_code: str = None
    description: str = None


@router.post("")
async def create_course(body: CourseCreate, current_user: dict = Depends(require_teacher)):
    """创建课程（仅教师角色；teacher_id 取自登录用户）"""
    result = CourseService.create_course(
        course_name=body.course_name, teacher_id=current_user["user_id"],
        course_code=body.course_code, description=body.description,
    )
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.get("")
async def list_courses(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(10, ge=1, le=100, description="每页数量"),
    teacher_id: int = Query(None, description="按教师筛选"),
    keyword: str = Query(None, description="课程名关键词搜索"),
    current_user: dict = Depends(get_current_user),
):
    """课程列表（分页 + 教师筛选 + 关键词搜索）"""
    result = CourseService.list_courses(page, page_size, teacher_id, keyword)
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.get("/{course_id}")
async def get_course(course_id: int, current_user: dict = Depends(get_current_user)):
    """课程详情（含文档/节点/关系统计）"""
    result = CourseService.get_course_detail(course_id)
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.put("/{course_id}")
async def update_course(course_id: int, body: CourseUpdate, current_user: dict = Depends(require_teacher)):
    """更新课程信息（仅教师角色，仅传入的字段生效）"""
    result = CourseService.update_course(
        course_id, body.course_name, body.course_code, body.description,
    )
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.delete("/{course_id}")
async def delete_course(course_id: int, confirm: bool = Query(False, description="删除二次确认，须为 true"),
                        current_user: dict = Depends(require_teacher)):
    """删除课程及其全部关联数据（仅教师角色，文档/图谱/学习记录）"""
    result = CourseService.delete_course(course_id, confirm)
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])
