"""
用户认证 API（对齐项目统一响应格式 {code, message, data, timestamp}）

密码哈希改用标准库 PBKDF2-SHA256（零外部依赖，实现在 core/security.py）。
原实现引入的 passlib/bcrypt 未写入 requirements.txt 且 passlib 已停止维护，
会导致后端 import 即崩溃（ModuleNotFoundError），故替换。
"""
import datetime

import jwt
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ..core.config import settings
from ..core.dependencies import get_current_user
from ..core.response import success, error
from ..core.security import hash_password, verify_password
from ..core.sql_database import sql_db
from ..services.admin_service import AdminService
from ..services.profile_service import ProfileService

router = APIRouter(prefix="/api/auth", tags=["用户认证"])


class UserRegister(BaseModel):
    username: str
    password: str
    role: str = "student"
    email: str | None = None
    display_name: str | None = None


class UserLogin(BaseModel):
    username: str
    password: str


class ChangePassword(BaseModel):
    old_password: str
    new_password: str


class DeactivateAccount(BaseModel):
    password: str


def _issue_token(user: dict) -> str:
    """签发 HS256 JWT，载荷含 user_id/username/role，有效期 24 小时"""
    payload = {
        "sub": str(user["user_id"]),
        "username": user["username"],
        "role": user["role"],
        "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=24),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


@router.post("/register")
def register(user: UserRegister):
    """用户注册（默认学生角色，可指定 teacher）"""
    username = (user.username or "").strip()
    if not username:
        return error(1001, "用户名不能为空")
    if not user.password:
        return error(1001, "密码不能为空")
    if user.role not in ("teacher", "student"):
        return error(1001, "角色不合法，仅支持 teacher/student")

    if sql_db.get_user_by_username(username):
        return error(2006, f"用户名已存在: {username}")

    user_id = sql_db.create_user(
        username=username,
        password_hash=hash_password(user.password),
        role=user.role,
        display_name=user.display_name,
        email=user.email,
    )
    return success({"user_id": user_id, "username": username})


@router.post("/login")
def login(user: UserLogin, request: Request):
    """用户登录，成功返回 JWT"""
    username = (user.username or "").strip()
    db_user = sql_db.get_user_by_username(username)
    if not db_user or not verify_password(user.password, db_user["password_hash"]):
        # 管理员账号的失败登录是审计关注点（暴力破解/异常来源），记一条失败记录。
        # 只对「用户名确实存在且是管理员」的情况记录：否则任何随机用户名都能往审计表里灌数据。
        # 注意不能记录密码，也不回显"该用户名存在"这类信息（响应与原来完全一致）。
        if db_user and db_user.get("role") == "admin":
            AdminService.write_audit(
                {"user_id": db_user["user_id"], "username": db_user["username"], "role": "admin"},
                "admin.login", "user", db_user["user_id"], result="failure",
                detail="管理员登录失败：用户名或密码错误", request=request,
            )
        return error(2002, "用户名或密码错误")
    # 已注销（软停用）账号禁止登录
    if not db_user.get("is_active", 1):
        return error(2004, "该账号已注销，无法登录")

    # 课程中心改造：登录响应追加 display_name / nickname / avatar_url，
    # 让侧边栏首屏就能显示昵称与头像，不必等 /api/v1/profile 返回。
    # 纯新增字段：JWT 载荷本身不变（老 token 继续可用），前端按 key 读取，
    # 新增键不会影响既有逻辑（kg_user 的消费方见 utils/userRole.js 等）。
    profile = sql_db.get_user_profile(db_user["user_id"]) or {}

    # 管理员登录写入审计日志（纯旁路：写失败不影响登录，见 AdminService.write_audit）
    if db_user.get("role") == "admin":
        AdminService.write_audit(
            {"user_id": db_user["user_id"], "username": db_user["username"], "role": "admin"},
            "admin.login", "user", db_user["user_id"],
            detail="管理员登录成功", request=request,
        )

    return success({
        "access_token": _issue_token(db_user),
        "token_type": "bearer",
        "user": {
            "user_id": db_user["user_id"],
            "username": db_user["username"],
            "role": db_user["role"],
            "display_name": db_user.get("display_name"),
            "nickname": profile.get("nickname"),
            "real_name": profile.get("real_name"),
            "avatar_url": ProfileService.avatar_url_for(
                db_user["user_id"], profile.get("avatar_url")),
            # 首次登录必须改密（引导账号的随机密码 / 管理员重置过的密码）：
            # 前端守卫据此把人挡在改密页，未改密前进不了任何业务页面。
            # 需要重新登录才生效的字段，故意不放进 JWT 载荷，避免 token 与库中状态不一致。
            "must_change_password": int(db_user.get("must_change_password") or 0),
        },
    })


@router.post("/change-password")
def change_password(body: ChangePassword, current_user: dict = Depends(get_current_user)):
    """修改当前登录用户密码（校验原密码；仅本人）"""
    old_password = body.old_password or ""
    new_password = body.new_password or ""
    if not old_password:
        return error(1001, "请输入原密码")
    if len(new_password) < 6:
        return error(1001, "新密码长度至少 6 位")

    db_user = sql_db.get_user_by_id(current_user["user_id"])
    if not db_user:
        return error(2001, "用户不存在")
    if not verify_password(old_password, db_user["password_hash"]):
        return error(2003, "原密码不正确")
    if verify_password(new_password, db_user["password_hash"]):
        return error(1001, "新密码不能与原密码相同")

    sql_db.update_password(current_user["user_id"], hash_password(new_password))
    # 用户自己完成了改密：清掉「首次登录必须改密」标记，此后可以正常进入各业务页面。
    # 只有此处（用户本人持旧密码主动修改）能清除；管理员重置密码时会重新置 1。
    sql_db.set_must_change_password(current_user["user_id"], False)
    return success({"user_id": current_user["user_id"]})


@router.post("/deactivate")
def deactivate_account(body: DeactivateAccount, current_user: dict = Depends(get_current_user)):
    """注销（停用）当前登录账号：校验密码后置 is_active=0，仅本人可操作"""
    password = body.password or ""
    if not password:
        return error(1001, "请输入密码")

    db_user = sql_db.get_user_by_id(current_user["user_id"])
    if not db_user:
        return error(2001, "用户不存在")
    if not verify_password(password, db_user["password_hash"]):
        return error(2002, "密码不正确")

    sql_db.deactivate_user(current_user["user_id"])
    return success({"user_id": current_user["user_id"]})
