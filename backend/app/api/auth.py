"""
用户认证 API（对齐项目统一响应格式 {code, message, data, timestamp}）

密码哈希改用标准库 PBKDF2-SHA256（零外部依赖，实现在 core/security.py）。
原实现引入的 passlib/bcrypt 未写入 requirements.txt 且 passlib 已停止维护，
会导致后端 import 即崩溃（ModuleNotFoundError），故替换。
"""
import datetime

import jwt
from fastapi import APIRouter
from pydantic import BaseModel

from ..core.config import settings
from ..core.response import success, error
from ..core.security import hash_password, verify_password
from ..core.sql_database import sql_db

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
def login(user: UserLogin):
    """用户登录，成功返回 JWT"""
    username = (user.username or "").strip()
    db_user = sql_db.get_user_by_username(username)
    if not db_user or not verify_password(user.password, db_user["password_hash"]):
        return error(2002, "用户名或密码错误")

    return success({
        "access_token": _issue_token(db_user),
        "token_type": "bearer",
        "user": {
            "user_id": db_user["user_id"],
            "username": db_user["username"],
            "role": db_user["role"],
        },
    })
