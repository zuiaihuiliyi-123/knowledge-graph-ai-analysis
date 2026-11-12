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


class InviteCodeCreate(BaseModel):
    """签发教师注册邀请码。

    valid_days：0（或缺省）表示永久有效；上限 3650 天（10 年）由 service 再校验一次。
    码本身由服务端生成，**不接受客户端指定**——否则管理员可以把码设成 "123456"，
    一次撞库就能批量注册教师。
    """
    note: str | None = Field(default=None, max_length=100, description="备注，例如「计算机学院 2026 春」")
    valid_days: int = Field(default=0, ge=0, le=3650, description="有效期天数，0 表示永久")
