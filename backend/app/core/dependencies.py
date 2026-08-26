"""
鉴权依赖（JWT Bearer Token）

认证端点（api/auth.py）登录后签发 HS256 JWT，载荷为 {sub=user_id, username, role, exp}。
受保护接口通过 Depends(get_current_user) 注入当前用户；教师专属接口用 Depends(require_teacher)。
"""
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from .config import settings

# auto_error=False：无 Authorization 头时由 get_current_user 统一返回 401，而非 FastAPI 默认的 403
security = HTTPBearer(auto_error=False)


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """校验 JWT 并返回当前用户 {user_id:int, username:str, role:str}；失败抛 401"""
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录或缺少凭证")

    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的登录凭证")

    # sub 在签发时存的是 user_id 字符串
    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的登录凭证")

    return {
        "user_id": user_id,
        "username": payload.get("username", ""),
        "role": payload.get("role", ""),
    }


def require_teacher(current_user: dict = Depends(get_current_user)) -> dict:
    """要求教师角色；学生访问返回 403"""
    if current_user["role"] != "teacher":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权限：仅教师可执行此操作")
    return current_user
