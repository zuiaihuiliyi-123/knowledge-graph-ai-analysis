"""
用户认证 API（预留）
"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/auth", tags=["用户认证"])


@router.post("/login")
async def login(username: str, password: str):
    """用户登录（预留）"""
    # TODO: 实现 JWT 认证
    return {"message": "登录功能开发中", "username": username}


@router.post("/register")
async def register(username: str, password: str, role: str = "student"):
    """用户注册（预留）"""
    # TODO: 实现用户注册
    return {"message": "注册功能开发中", "username": username, "role": role}
