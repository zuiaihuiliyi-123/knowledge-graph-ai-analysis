"""
鉴权依赖（JWT Bearer Token）

认证端点（api/auth.py）登录后签发 HS256 JWT，载荷为 {sub=user_id, username, role, exp}。
受保护接口通过 Depends(get_current_user) 注入当前用户；教师专属接口用 Depends(require_teacher)，
管理员专属接口（/api/v1/admin/*）用 Depends(require_admin)。

关于 require_admin 的一次数据库访问：本模块其余部分刻意保持「零数据库访问」，
但管理员权限是本系统权限最高的一档，只信 JWT 里的 role 会留下两个真实缺口——
管理员被降级或禁用后，其旧 token 在 24 小时有效期内仍能通行全平台。
故 require_admin 额外回查一次 t_user，确认「用户仍存在 + 仍启用 + 角色仍是 admin」。

阶段 1A 起，**所有**鉴权都回查账号态（`core/user_state.py`，短 TTL 缓存）：
只信 JWT 会让「停用账号」「教师降级」的旧 token 在 24 小时内继续通行，
而 `require_teacher` 恰是题库/上传/管课这类写操作的唯一闸门。
"""
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from .config import settings
from .sql_database import sql_db
from .user_state import load_user_state

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

    # JWT 只证明「签发时身份有效」：账号被禁用、被降级后，旧 token 必须立刻失去相应权限。
    # 回查账号态走 user_state 的短 TTL 缓存（默认 30s），避免每个受保护请求都读一次 SQLite；
    # 管理员改角色/禁用时会主动 invalidate，故高危操作没有延迟窗口。
    # 账号不存在或被停用一律 401：让前端拦截器清 token 回登录页，而不是反复拿到 403。
    state = load_user_state(user_id)
    if state is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="登录凭证对应的账号不存在，请重新登录")
    if not state["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="该账号已被停用，请重新登录")

    return {
        "user_id": user_id,
        "username": state["username"] or payload.get("username", ""),
        # 角色取库内值而非 JWT 值：降级（teacher -> student）随缓存 TTL 收敛，
        # 避免「旧 token 仍是教师」继续通过 require_teacher。
        "role": state["role"] or payload.get("role", ""),
    }


def require_teacher(current_user: dict = Depends(get_current_user)) -> dict:
    """要求教师角色；学生访问返回 403"""
    if current_user["role"] != "teacher":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权限：仅教师可执行此操作")
    return current_user


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """要求管理员角色（三档校验：JWT 有效 -> 用户仍存在且启用 -> 角色仍为 admin）。

    返回的 dict 额外带上 username：审计日志需要操作人姓名，避免每个端点再查一次。
    任一环节不满足均抛 403（而非 401）——身份是有效的，只是权限不足；
    401 由 get_current_user 负责，前端拦截器对 401 会清 token 跳登录，
    若这里复用 401，被降级的管理员会被误判为「登录过期」而反复跳登录页。
    """
    user_id = current_user["user_id"]
    db_user = sql_db.get_user_by_id(user_id)
    if not db_user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权限：账号不存在")
    if not db_user.get("is_active", 1):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权限：账号已被停用")
    if db_user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权限：仅管理员可执行此操作")
    return {
        "user_id": user_id,
        "username": db_user.get("username") or current_user.get("username", ""),
        "role": "admin",
    }
