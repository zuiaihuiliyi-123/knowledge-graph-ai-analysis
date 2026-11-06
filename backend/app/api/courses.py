"""
课程管理 API（对齐规划文档 6.6.6~6.6.10；课程中心改造）

路由顺序很重要：/discover、/my、/join-by-code 这些静态路径必须声明在
GET /{course_id} 之前，否则 FastAPI 会拿 course_id: int 去解析 "discover"
并返回 422。test_course_list_scope.py 里有一条针对该顺序的回归断言。

权限统一走 core/permissions.Permissions：
- 列表/详情：require_course_read（成员可读，含待审核者查看自己的申请状态）
- 修改/删除：require_course_manage / require_course_owner
"""
from fastapi import APIRouter, Query, Depends

from ..core.response import success, error
from ..core.dependencies import get_current_user, require_teacher
from ..core.permissions import Permissions
from ..services.course_service import CourseService
from ..services.member_service import MemberService
from ..schemas.courses import (
    CourseCreate,
    CourseUpdate,
    JoinByCodeRequest,
    ApplyRequest,
    JoinModeUpdate,
)

router = APIRouter(prefix="/api/v1/courses", tags=["课程管理"])


# 请求体见 app/schemas/courses.py（可选字段的 Pydantic v2 写法说明也移到了那里）。


# ---------------- 创建 / 列表 ----------------

@router.post("")
async def create_course(body: CourseCreate, current_user: dict = Depends(require_teacher)):
    """创建课程（仅教师角色；teacher_id 取自登录用户）

    创建成功后会生成唯一加课码，并把创建者登记为 approved 教师成员。
    """
    result = CourseService.create_course(
        course_name=body.course_name, teacher_id=current_user["user_id"],
        course_code=body.course_code, description=body.description,
        join_mode=body.join_mode, organization=body.organization,
        category=body.category, cover=body.cover, is_public=body.is_public,
    )
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.get("/discover")
async def discover_courses(
    keyword: str = Query(None, description="课程名/简介关键词"),
    category: str = Query(None, description="课程分类"),
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """发现课程：公开且启用中的课程，自动排除「我已是成员 / 我已申请」的"""
    result = CourseService.list_discover(current_user, keyword, category, page, page_size)
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.get("/my")
async def my_courses(
    status: str = Query("approved", description="approved=我的课程；pending=申请中/被拒绝"),
    keyword: str = Query(None, description="课程名关键词"),
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """我的课程（教师=自己创建/协作的；学生=已通过审核的）"""
    result = CourseService.list_my_courses(current_user, status, page, page_size, keyword)
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.post("/join-by-code")
async def join_by_code(body: JoinByCodeRequest,
                       current_user: dict = Depends(get_current_user)):
    """用加课码加入课程（是否需要审核由课程的加入方式决定）"""
    result = MemberService.join_by_code(current_user["user_id"], body.join_code, body.reason)
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.get("")
async def list_courses(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(10, ge=1, le=100, description="每页数量"),
    teacher_id: int = Query(None, description="按教师筛选（仅当等于自己的 user_id 时生效）"),
    keyword: str = Query(None, description="课程名关键词搜索"),
    category: str = Query(None, description="课程分类"),
    current_user: dict = Depends(get_current_user),
):
    """课程列表（分页 + 关键词 + 分类）。

    课程中心改造：结果收敛到「当前用户可访问的课程」。
    客户端传入的 teacher_id 不能再扩大结果集——只有等于调用者自己的 user_id 时才被采纳。
    """
    scoped_teacher_id = (current_user["user_id"]
                         if teacher_id == current_user["user_id"] else None)
    course_ids = Permissions.allowed_course_ids(current_user)
    result = CourseService.list_courses(page, page_size, scoped_teacher_id, keyword,
                                        course_ids=course_ids, category=category,
                                        viewer=current_user)
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.get("/{course_id}")
async def get_course(course_id: int, current_user: dict = Depends(get_current_user)):
    """课程详情（含文档/节点/关系统计、成员数、我的成员关系）"""
    perm = Permissions.require_course_read(course_id, current_user)
    if not perm["ok"]:
        return error(perm["code"], perm["message"])
    result = CourseService.get_course_detail(course_id, viewer=current_user)
    if not result["ok"]:
        return error(result["code"], result["message"])
    data = result["data"]
    data["my_relation"] = perm["data"]["relation"]
    data["my_member_status"] = (
        perm["data"]["member"]["status"] if perm["data"]["member"] else
        ("approved" if perm["data"]["is_owner"] else None))
    data["can_manage"] = perm["data"]["can_manage"]
    return success(data)


@router.put("/{course_id}")
async def update_course(course_id: int, body: CourseUpdate,
                        current_user: dict = Depends(require_teacher)):
    """更新课程信息（仅该课程教师，仅传入的字段生效）"""
    perm = Permissions.require_course_manage(course_id, current_user)
    if not perm["ok"]:
        return error(perm["code"], perm["message"])
    result = CourseService.update_course(
        course_id, body.course_name, body.course_code, body.description,
        category=body.category, organization=body.organization, cover=body.cover,
        join_mode=body.join_mode, is_public=body.is_public,
    )
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.delete("/{course_id}")
async def delete_course(course_id: int, confirm: bool = Query(False, description="删除二次确认，须为 true"),
                        current_user: dict = Depends(require_teacher)):
    """删除课程及其全部关联数据（仅课程创建者；文档/图谱/学习记录/成员/邀请）"""
    perm = Permissions.require_course_owner(course_id, current_user)
    if not perm["ok"]:
        return error(perm["code"], perm["message"])
    result = CourseService.delete_course(course_id, confirm)
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


# ---------------- 加课码 / 加入方式 ----------------

@router.get("/{course_id}/join-code")
async def get_join_code(course_id: int, current_user: dict = Depends(require_teacher)):
    """查看课程加课码（仅该课程教师）"""
    perm = Permissions.require_course_manage(course_id, current_user)
    if not perm["ok"]:
        return error(perm["code"], perm["message"])
    result = CourseService.get_join_code(course_id)
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.post("/{course_id}/join-code/refresh")
async def refresh_join_code(course_id: int, current_user: dict = Depends(require_teacher)):
    """刷新加课码（仅该课程教师；旧码立即失效）"""
    perm = Permissions.require_course_manage(course_id, current_user)
    if not perm["ok"]:
        return error(perm["code"], perm["message"])
    result = CourseService.refresh_join_code(course_id)
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.put("/{course_id}/join-mode")
async def update_join_mode(course_id: int, body: JoinModeUpdate,
                           current_user: dict = Depends(require_teacher)):
    """设置加入方式与是否公开（仅该课程教师）"""
    perm = Permissions.require_course_manage(course_id, current_user)
    if not perm["ok"]:
        return error(perm["code"], perm["message"])
    result = CourseService.update_course(course_id, join_mode=body.join_mode,
                                         is_public=body.is_public)
    if not result["ok"]:
        return error(result["code"], result["message"])
    return success(CourseService.get_join_code(course_id)["data"])


@router.post("/{course_id}/apply")
async def apply_to_course(course_id: int, body: ApplyRequest | None = None,
                          current_user: dict = Depends(get_current_user)):
    """从「发现课程」申请加入（公开课程；可填申请理由，始终进入待审核）"""
    reason = body.reason if body else None
    result = MemberService.apply_to_course(current_user["user_id"], course_id, reason)
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])
