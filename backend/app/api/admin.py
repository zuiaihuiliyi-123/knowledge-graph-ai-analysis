"""管理员端 API（平台治理 / 用户管理 / 课程治理 / 资源管理 / 系统监控 / 审计日志）

权限边界：本模块**每一个**端点都挂 Depends(require_admin)。
不是在路由前缀上挂一次就完事——每个处理函数都显式声明依赖，这样新增端点时
漏挂权限会立刻表现为「没有 admin 变量可用」而不是静默变成公开接口。

设计取舍：
- 管理员**不做**教学工作：这里没有「改知识点」「改学习记录」「替学生完成任务」的接口。
  管理员看得到全平台数据，但课程内容的变更一律留给课程教师（见 admin_service 的模块注释）；
- 需要 IP / User-Agent 的写操作统一接收 Request，交给 AdminService.write_audit 落审计表。
"""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..core.dependencies import require_admin
from ..core.response import success, error
from ..core.sql_database import sql_db
from ..services.admin_service import AdminService

router = APIRouter(prefix="/api/v1/admin", tags=["管理员端"])


def _reply(result: dict, message: str = None) -> dict:
    """service 契约 -> 统一响应（失败时保留 service 给的错误码）"""
    if result["ok"]:
        return success(result["data"], message or result.get("message", "success"))
    return error(result["code"], result["message"])


# ---------- 请求体 ----------


class UserUpdate(BaseModel):
    """编辑用户资料。

    刻意**不含** role / is_active / password：这三个是高危操作，各自有独立端点与独立审计动作，
    混进一个"万能更新"接口会让审计日志无法区分「改了昵称」和「改了角色」。
    """
    display_name: str | None = None
    email: str | None = None
    profile: dict | None = Field(default=None, description="资料字段（按被编辑者角色白名单过滤）")


class RoleUpdate(BaseModel):
    role: str


class ResetPassword(BaseModel):
    # 不传则由服务端生成随机初始密码（只在响应里返回一次）
    new_password: str | None = None


class GovernanceAction(BaseModel):
    # hide / restore / archive / unarchive / close / reopen
    action: str
    note: str | None = None


class TransferCourse(BaseModel):
    teacher_id: int


# ---------- 工作台 ----------


@router.get("/dashboard")
async def admin_dashboard(days: int = 14, admin: dict = Depends(require_admin)):
    """管理员工作台总览：平台计数、角色分布、治理概览、系统状态摘要、最近活动"""
    return _reply(AdminService.dashboard(days=days))


@router.get("/options")
async def admin_options(admin: dict = Depends(require_admin)):
    """筛选下拉的可选项（教师列表 + 全平台课程）。

    单独一个接口的原因：用户管理 / 课程管理 / 资源管理三个页面的筛选器都要它，
    各自去拉一次全量列表既慢又会让筛选器依赖分页结果。
    """
    _, teachers = sql_db.list_users_admin(role="teacher", page=1, page_size=100,
                                          sort_by="user_id", sort_order="asc")
    _, courses = sql_db.list_courses_admin(page=1, page_size=500,
                                          sort_by="course_id", sort_order="asc")
    return success({
        "teachers": [
            {"user_id": t["user_id"], "username": t["username"],
             "name": t.get("display_name") or t.get("real_name") or t["username"],
             "is_active": t.get("is_active", 1)}
            for t in teachers
        ],
        "courses": [
            {"course_id": c["course_id"], "course_name": c["course_name"],
             "teacher_name": c.get("teacher_name") or ""}
            for c in courses
        ],
    })


# ---------- 用户管理 ----------


@router.get("/users")
async def list_users(keyword: str = None, role: str = None, status: str = None,
                     page: int = 1, page_size: int = 20,
                     sort_by: str = None, sort_order: str = None,
                     admin: dict = Depends(require_admin)):
    """用户列表（搜索 / 角色筛选 / 状态筛选 / 排序 / 分页）"""
    return _reply(AdminService.list_users(keyword, role, status, page, page_size,
                                         sort_by, sort_order))


@router.get("/users/{user_id}")
async def get_user(user_id: int, admin: dict = Depends(require_admin)):
    """用户详情（资料 + 统计 + 所在课程 + 相关操作记录）。响应中不含密码哈希。"""
    return _reply(AdminService.get_user(user_id))


@router.put("/users/{user_id}")
async def update_user(user_id: int, body: UserUpdate, request: Request,
                      admin: dict = Depends(require_admin)):
    """编辑用户资料（不含角色与状态）"""
    return _reply(AdminService.update_user(
        admin, user_id, body.model_dump(exclude_unset=True), request))


@router.post("/users/{user_id}/enable")
async def enable_user(user_id: int, request: Request, admin: dict = Depends(require_admin)):
    """启用账号"""
    return _reply(AdminService.set_user_active(admin, user_id, True, request))


@router.post("/users/{user_id}/disable")
async def disable_user(user_id: int, request: Request, admin: dict = Depends(require_admin)):
    """禁用账号（不删除其历史学习数据，仅拒绝登录）"""
    return _reply(AdminService.set_user_active(admin, user_id, False, request))


@router.put("/users/{user_id}/role")
async def change_role(user_id: int, body: RoleUpdate, request: Request,
                      admin: dict = Depends(require_admin)):
    """修改用户角色（student / teacher / admin）"""
    return _reply(AdminService.set_user_role(admin, user_id, body.role, request))


@router.post("/users/{user_id}/reset-password")
async def reset_password(user_id: int, request: Request,
                         body: ResetPassword = ResetPassword(),
                         admin: dict = Depends(require_admin)):
    """重置用户密码。

    安全约定：不读取也不展示任何原有密码；未指定新密码时服务端生成随机初始密码，
    **只在本次响应中返回一次**；审计日志只记录"重置了谁的密码"，绝不记录密码内容。
    """
    return _reply(AdminService.reset_password(admin, user_id, body.new_password, request))


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, request: Request, admin: dict = Depends(require_admin)):
    """删除用户（仅限无任何关联业务数据的账号；其余情况引导使用禁用）"""
    return _reply(AdminService.delete_user(admin, user_id, request))


# ---------- 课程管理 ----------


@router.get("/courses")
async def list_courses(keyword: str = None, teacher_id: int = None, category: str = None,
                       is_public: int = None, status: int = None,
                       governance_status: str = None, only_without_teacher: bool = False,
                       inactive_days: int = None, page: int = 1, page_size: int = 20,
                       sort_by: str = None, sort_order: str = None,
                       admin: dict = Depends(require_admin)):
    """全平台课程列表（对比教师端的「我的课程」）"""
    return _reply(AdminService.list_courses(
        keyword, teacher_id, category, is_public, status, governance_status,
        only_without_teacher, inactive_days, page, page_size, sort_by, sort_order))


@router.get("/courses/{course_id}")
async def get_course(course_id: int, admin: dict = Depends(require_admin)):
    """课程详情（只读）：概览 + 成员 + 文档 + 图谱统计"""
    return _reply(AdminService.get_course(course_id))


@router.post("/courses/{course_id}/governance")
async def course_governance(course_id: int, body: GovernanceAction, request: Request,
                            admin: dict = Depends(require_admin)):
    """课程治理：下架 / 恢复 / 归档 / 取消归档 / 关闭 / 重新开放"""
    return _reply(AdminService.set_course_governance(
        admin, course_id, body.action, body.note, request))


@router.post("/courses/{course_id}/transfer")
async def transfer_course(course_id: int, body: TransferCourse, request: Request,
                          admin: dict = Depends(require_admin)):
    """转移课程负责人（原负责人保留为协作教师，不会连带失去课程访问权）"""
    return _reply(AdminService.transfer_course(admin, course_id, body.teacher_id, request))


@router.delete("/courses/{course_id}")
async def delete_course(course_id: int, request: Request, confirm: bool = False,
                        admin: dict = Depends(require_admin)):
    """删除课程（级联清理图谱 / 文档 / 学习记录 / 收藏 / 题库 / 本地文件）。

    必须显式 confirm=true：删除课程的连带影响最大，不能因为「管理员点了删除」就跳过确认。
    """
    return _reply(AdminService.delete_course(admin, course_id, confirm, request))


# ---------- 课程治理工作台 ----------


@router.get("/governance")
async def governance_overview(admin: dict = Depends(require_admin)):
    """治理工作台：自动挑出需要管理员介入的课程（已下架/已归档/无负责人/负责人被禁用/长期无活动/空课程）"""
    return _reply(AdminService.governance_overview())


# ---------- 资源管理 ----------


@router.get("/resources/documents")
async def list_documents(keyword: str = None, course_id: int = None, file_type: str = None,
                         parse_status: str = None, extract_status: str = None,
                         uploader_id: int = None, page: int = 1, page_size: int = 20,
                         sort_by: str = None, sort_order: str = None,
                         admin: dict = Depends(require_admin)):
    """全平台文档资源列表（课程 → 文档 → 抽取状态）"""
    return _reply(AdminService.list_documents(
        keyword, course_id, file_type, parse_status, extract_status, uploader_id,
        page, page_size, sort_by, sort_order))


@router.delete("/resources/documents/{doc_id}")
async def delete_document(doc_id: int, request: Request,
                          admin: dict = Depends(require_admin)):
    """删除文档资源（与教师端同一套完整性清理：图谱 → 向量 → 学习记录 → 收藏 → 题库 → 文件 → 记录）"""
    return _reply(AdminService.delete_document(admin, doc_id, request))


@router.get("/resources/extraction-tasks")
async def list_extraction_tasks(course_id: int = None, status: str = None,
                                keyword: str = None, page: int = 1, page_size: int = 20,
                                admin: dict = Depends(require_admin)):
    """知识抽取任务监控（以文档的解析/抽取状态派生，不新建异步任务表）"""
    return _reply(AdminService.list_extraction_tasks(course_id, status, keyword,
                                                    page, page_size))


# ---------- 系统监控 ----------


@router.get("/system")
async def system_status(admin: dict = Depends(require_admin)):
    """系统状态：各组件真实探测结果 + 运行指标 + 库表行数。

    探测不到的组件返回 available=false 并说明原因，不伪造「正常」。
    """
    return _reply(AdminService.system_status())


# ---------- 审计日志 ----------


@router.get("/audit-logs")
async def list_audit_logs(operator_id: int = None, action: str = None,
                          target_type: str = None, result: str = None,
                          keyword: str = None, start_time: str = None,
                          end_time: str = None, page: int = 1, page_size: int = 20,
                          admin: dict = Depends(require_admin)):
    """审计日志查询（时间 / 操作人 / 操作类型 / 目标类型 / 结果 / 关键词 + 分页）"""
    return _reply(AdminService.list_audit_logs(
        operator_id, action, target_type, result, keyword, start_time, end_time,
        page, page_size))
