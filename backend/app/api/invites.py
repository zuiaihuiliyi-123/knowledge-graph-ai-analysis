"""
课程邀请 API（邀请落地页 / 接受邀请）

单独用 /api/v1/invites 前缀，与 /api/v1/courses/{course_id} 完全隔离，
从根本上避免 "invites" 被当成 course_id 解析（否则会 422）。
"""
from fastapi import APIRouter, Depends

from ..core.dependencies import get_current_user
from ..core.permissions import Permissions
from ..core.response import success, error
from ..services.member_service import MemberService
from ..schemas.invites import AcceptRequest

router = APIRouter(prefix="/api/v1/invites", tags=["课程邀请"])


@router.get("/{token}")
async def preview_invite(token: str, current_user: dict = Depends(get_current_user)):
    """邀请落地页预览：未接受前先展示课程 / 邀请人 / 有效期，并带上我的当前关系"""
    result = MemberService.preview_invite(token)
    if not result["ok"]:
        return error(result["code"], result["message"])
    data = result["data"]
    perm = Permissions.resolve(data["course_id"], current_user)
    data["my_relation"] = perm["data"]["relation"] if perm["ok"] else "NONE"
    return success(data)


@router.post("/accept")
async def accept_invite(body: AcceptRequest, current_user: dict = Depends(get_current_user)):
    """接受邀请加入课程（幂等：已是成员时返回 already_member=true，不会消耗邀请令牌）"""
    result = MemberService.accept_invite(current_user["user_id"], body.token)
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])
