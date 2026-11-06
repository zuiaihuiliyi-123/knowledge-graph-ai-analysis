"""课程成员请求模型（拒绝申请 / 创建邀请）。"""

from pydantic import BaseModel


class RejectRequest(BaseModel):
    comment: str | None = None


class InviteCreateRequest(BaseModel):
    role: str = "student"
    expires_in_days: int = 7
