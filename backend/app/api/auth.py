"""
用户认证 API（对齐项目统一响应格式 {code, message, data, timestamp}）

密码哈希改用标准库 PBKDF2-SHA256（零外部依赖，实现在 core/security.py）。
原实现引入的 passlib/bcrypt 未写入 requirements.txt 且 passlib 已停止维护，
会导致后端 import 即崩溃（ModuleNotFoundError），故替换。
"""
from fastapi import APIRouter, Depends, Request

from ..core.dependencies import get_current_user
from ..core.response import success, error
from ..services.auth_service import AuthService
from ..schemas.auth import (
    UserRegister,
    UserLogin,
    ChangePassword,
    DeactivateAccount,
)

router = APIRouter(prefix="/api/auth", tags=["用户认证"])


def _reply(result: dict) -> dict:
    """把 service 的统一结果信封转换为接口响应。"""
    if result["ok"]:
        return success(result["data"], result.get("message", "success"))
    return error(result["code"], result["message"])


@router.post("/register")
def register(user: UserRegister):
    """用户注册。

    - 学生：默认角色，直接注册；
    - 教师：必须携带 `teacher_invite_code` 且与 `TEACHER_INVITE_CODE` 一致。
      未配置该环境变量时教师自助注册**关闭**（教师账号由管理员创建/提升）。

    为什么收紧：原先 `role` 完全由请求体决定，任何人 POST 一下就能拿到教师身份，
    进而建课、上传文档、管理别的学生 —— 这是最容易被利用的一条越权入口。
    邀请码走 `secrets.compare_digest` 定长比较，避免逐字符比较带来的时序差异。
    """
    return _reply(AuthService.register(
        username=user.username, password=user.password, role=user.role,
        email=user.email, display_name=user.display_name,
        teacher_invite_code=user.teacher_invite_code,
    ))


@router.post("/login")
def login(user: UserLogin, request: Request):
    """用户登录，成功返回 JWT"""
    return _reply(AuthService.login(user.username, user.password, request))


@router.post("/change-password")
def change_password(body: ChangePassword, current_user: dict = Depends(get_current_user)):
    """修改当前登录用户密码（校验原密码；仅本人）"""
    return _reply(AuthService.change_password(
        current_user["user_id"], body.old_password, body.new_password))


@router.post("/deactivate")
def deactivate_account(body: DeactivateAccount, current_user: dict = Depends(get_current_user)):
    """注销（停用）当前登录账号：校验密码后置 is_active=0，仅本人可操作"""
    return _reply(AuthService.deactivate(current_user["user_id"], body.password))
