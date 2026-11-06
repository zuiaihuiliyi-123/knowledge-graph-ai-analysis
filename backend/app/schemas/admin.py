"""管理员端请求模型（用户资料 / 角色 / 治理 / 课程转移）。"""

from pydantic import BaseModel, Field


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
