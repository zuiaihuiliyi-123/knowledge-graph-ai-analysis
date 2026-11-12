"""
课程成员管理 API（课程中心：学生管理 / 审核申请 / 邀请）

单独成文件但复用 /api/v1/courses 前缀：这些动作在语义上都属于「对某门课程的成员做操作」，
拆到 /api/v1/course-members 反而会让前端多维护一套路径。

权限：全部为课程管理级（Permissions.require_course_manage）——课程创建者或被邀请的协作教师。
学生退出课程走 DELETE /{course_id}/members/{自己}，由 MemberService.leave_course 单独处理。
"""
from fastapi import APIRouter, Depends, Query

from ..core.dependencies import get_current_user
from ..core.permissions import Permissions
from ..core.response import success, error
from ..core.sql_database import sql_db
from ..services.member_service import MemberService
from ..schemas.course_members import (
    RejectRequest,
    InviteCreateRequest,
)

router = APIRouter(prefix="/api/v1/courses", tags=["课程成员"])


def _coerce_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _guard_member_action(course_id: int, current_user: dict, target_user_id: int):
    """成员审核/移除的权限分级。

    学生成员属于日常教学管理，创建者与协作教师都可操作；
    但对「教师成员」的批准、拒绝、移除会改变课程的管理团队，属于治理性操作，
    沿用最小权限原则，仅课程创建者可以执行。

    目标不是自己时才升级为 owner 校验：任何教师（含协作教师）都可以退出课程，
    该路径由 remove_member 里的 leave_course 分支处理。
    """
    member = sql_db.get_membership(course_id, target_user_id)
    is_teacher_target = bool(member and member.get("role") == "teacher")
    if is_teacher_target and target_user_id != current_user["user_id"]:
        return Permissions.require_course_owner(course_id, current_user)
    return Permissions.require_course_manage(course_id, current_user)


@router.get("/{course_id}/members")
async def list_members(
    course_id: int,
    status: str = Query(None, description="pending/approved/rejected/removed，缺省全部"),
    role: str = Query(None, description="teacher/student"),
    keyword: str = Query(None, description="用户名/姓名/昵称/学号搜索"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """课程成员列表（含学习进度与掌握情况；仅该课程教师）"""
    perm = Permissions.require_course_manage(course_id, current_user)
    if not perm["ok"]:
        return error(perm["code"], perm["message"])
    result = MemberService.list_members(course_id, status, role, keyword, page, page_size)
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.get("/{course_id}/members/stats")
async def member_stats(course_id: int, current_user: dict = Depends(get_current_user)):
    """成员统计（总人数/已通过/待审核/已移除/平均进度）——课程卡片的「待审核 N」角标也用它"""
    perm = Permissions.require_course_manage(course_id, current_user)
    if not perm["ok"]:
        return error(perm["code"], perm["message"])
    result = MemberService.member_stats(course_id)
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.post("/{course_id}/members/{user_id}/approve")
async def approve_member(course_id: int, user_id: int,
                         current_user: dict = Depends(get_current_user)):
    """同意学生加入申请（批准教师加入属于治理操作，仅创建者）"""
    perm = _guard_member_action(course_id, current_user, user_id)
    if not perm["ok"]:
        return error(perm["code"], perm["message"])
    result = MemberService.approve(course_id, user_id, current_user["user_id"])
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.post("/{course_id}/members/{user_id}/reject")
async def reject_member(course_id: int, user_id: int, body: RejectRequest | None = None,
                        current_user: dict = Depends(get_current_user)):
    """拒绝学生加入申请（拒绝教师加入同样仅创建者）"""
    perm = _guard_member_action(course_id, current_user, user_id)
    if not perm["ok"]:
        return error(perm["code"], perm["message"])
    comment = body.comment if body else None
    result = MemberService.reject(course_id, user_id, current_user["user_id"], comment)
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.delete("/{course_id}/members/{user_id}")
async def remove_member(course_id: int, user_id: int,
                        confirm: bool = Query(False, description="移除二次确认，须为 true"),
                        current_user: dict = Depends(get_current_user)):
    """移除成员 / 学生自己退出课程。

    - 教师移除学生：软移除（状态置 removed），保留学生的学习记录与收藏；
    - 学生传自己的 user_id：等同于「退出课程」，无需教师权限。
    """
    if user_id == current_user["user_id"]:
        result = MemberService.leave_course(current_user["user_id"], course_id)
        return success(result["data"]) if result["ok"] else error(result["code"], result["message"])

    # 移除教师成员（撤销协作资格）属于治理操作，仅创建者可执行；
    # 移除学生仍是日常教学管理，创建者与协作教师均可。
    perm = _guard_member_action(course_id, current_user, user_id)
    if not perm["ok"]:
        return error(perm["code"], perm["message"])
    if not confirm:
        return error(2008, "移除成员需二次确认（confirm=true）")
    result = MemberService.remove(course_id, user_id, current_user["user_id"])
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


# ---------------- 邀请 ----------------

@router.post("/{course_id}/invites")
async def create_invite(course_id: int, body: InviteCreateRequest | None = None,
                        current_user: dict = Depends(get_current_user)):
    """生成邀请链接（随机 token，可设角色与有效期）。

    邀请学生 = 日常教学管理，创建者与协作教师均可；
    邀请协作教师 = 组建管理团队，属于治理操作，仅创建者可执行。
    """
    role = body.role if body else "student"
    perm = (Permissions.require_course_owner(course_id, current_user)
            if role == "teacher"
            else Permissions.require_course_manage(course_id, current_user))
    if not perm["ok"]:
        return error(perm["code"], perm["message"])
    days = body.expires_in_days if body else 7
    result = MemberService.create_invite(course_id, current_user["user_id"], role, days)
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.get("/{course_id}/invites")
async def list_invites(course_id: int, current_user: dict = Depends(get_current_user)):
    """邀请列表（含折算过期后的生效状态）"""
    perm = Permissions.require_course_manage(course_id, current_user)
    if not perm["ok"]:
        return error(perm["code"], perm["message"])
    result = MemberService.list_invites(course_id)
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.delete("/{course_id}/invites/{invite_id}")
async def revoke_invite(course_id: int, invite_id: int,
                        current_user: dict = Depends(get_current_user)):
    """撤销邀请链接（撤销后立即失效）。

    协作教师可以撤销学生邀请；但撤销「教师邀请」会改变管理团队，
    与创建教师邀请一致，仅课程创建者可执行。
    """
    invite = sql_db.get_invite(invite_id, course_id)
    perm = (Permissions.require_course_owner(course_id, current_user)
            if invite and invite.get("role") == "teacher"
            else Permissions.require_course_manage(course_id, current_user))
    if not perm["ok"]:
        return error(perm["code"], perm["message"])
    result = MemberService.revoke_invite(course_id, invite_id)
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])
