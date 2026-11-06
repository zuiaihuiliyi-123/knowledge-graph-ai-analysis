"""认证请求模型（注册 / 登录 / 改密 / 注销）。"""

from pydantic import BaseModel


class UserRegister(BaseModel):
    username: str
    password: str
    role: str = "student"
    email: str | None = None
    display_name: str | None = None
    # 教师自助注册所需邀请码（学生不需要；未配置 TEACHER_INVITE_CODE 时教师注册关闭）
    teacher_invite_code: str | None = None


class UserLogin(BaseModel):
    username: str
    password: str


class ChangePassword(BaseModel):
    old_password: str
    new_password: str


class DeactivateAccount(BaseModel):
    password: str
