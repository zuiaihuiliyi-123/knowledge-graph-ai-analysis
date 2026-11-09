"""
用户认证 API（对齐项目统一响应格式 {code, message, data, timestamp}）

密码哈希改用标准库 PBKDF2-SHA256（零外部依赖，实现在 core/security.py）。
原实现引入的 passlib/bcrypt 未写入 requirements.txt 且 passlib 已停止维护，
会导致后端 import 即崩溃（ModuleNotFoundError），故替换。
"""
import math
import threading
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

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

# 登录失败限流（进程内实现，单 worker 足够；多 worker 部署可换成 Redis）。
# 目的不是完美的防暴力破解，而是把「在线撞库」的成本从「无限次」提高到「10 分钟 8 次」。
_LOGIN_WINDOW_SECONDS = 600
_LOGIN_MAX_FAILURES = 8
_MAX_LOGIN_BUCKETS = 4096  # 防止海量随机用户名把限流表撑爆
_login_failures: dict[str, list[float]] = {}
_login_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    """取直连对端 IP；此处刻意不信任 X-Forwarded-For（避免伪造头绕过限流）。"""
    return request.client.host if request and request.client else "unknown"


def _login_key(username: str, request: Request) -> str:
    return f"{(username or '').strip().lower()}|{_client_ip(request)}"


def _prune_failures(key: str, now: float) -> list[float]:
    items = _login_failures.get(key, [])
    items = [t for t in items if now - t < _LOGIN_WINDOW_SECONDS]
    if items:
        _login_failures[key] = items
    else:
        _login_failures.pop(key, None)
    return items


def _failure_count(key: str) -> int:
    """窗口内的连续失败次数（顺带清理过期记录）。"""
    now = time.monotonic()
    with _login_lock:
        return len(_prune_failures(key, now))


def _lock_remaining_seconds(key: str) -> int:
    """仍处于锁定时返回还需等待的秒数，未锁定返回 0。

    解锁时刻按窗口内**最早**一次失败推算：这样「已锁」和「还剩多久」来自同一份数据，
    不会出现提示说锁了、实际却没锁（或反之）的矛盾。
    """
    now = time.monotonic()
    with _login_lock:
        items = _prune_failures(key, now)
        if len(items) < _LOGIN_MAX_FAILURES:
            return 0
        return max(1, int(_LOGIN_WINDOW_SECONDS - (now - min(items))))


def _record_login_failure(key: str) -> None:
    now = time.monotonic()
    with _login_lock:
        if key not in _login_failures and len(_login_failures) >= _MAX_LOGIN_BUCKETS:
            # 丢弃最早插入的桶；限流是尽力而为，不能成为新的内存泄漏点
            _login_failures.pop(next(iter(_login_failures)), None)
        items = _prune_failures(key, now)
        items.append(now)
        _login_failures[key] = items


def _clear_login_failures(key: str) -> None:
    with _login_lock:
        _login_failures.pop(key, None)


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
    """用户登录，成功返回 JWT。连续失败 8 次锁定 10 分钟（按用户名 + 直连 IP）。"""
    login_key = _login_key(user.username, request)
    waiting = _lock_remaining_seconds(login_key)
    if waiting:
        minutes = max(1, math.ceil(waiting / 60))
        return JSONResponse(
            status_code=429,
            content=error(4290, f"登录失败次数过多，请 {minutes} 分钟后再试"),
        )
    result = AuthService.login(user.username, user.password, request)
    if result.get("ok"):
        _clear_login_failures(login_key)
    elif result.get("code") == 2002:
        # 只把「用户名或密码错误」计入失败次数：停用账号、参数错误等不应
        # 继续累加，否则正常用户可能因一次误操作被锁 10 分钟。
        _record_login_failure(login_key)
        # 明确告诉用户「还能试几次」：现场演示/答辩最怕不知道剩余次数就撞上锁定。
        left = _LOGIN_MAX_FAILURES - _failure_count(login_key)
        if left > 0:
            result["message"] = f"{result.get('message') or '用户名或密码错误'}，还可尝试 {left} 次"
        else:
            result["message"] = (
                f"用户名或密码错误，已连续失败 {_LOGIN_MAX_FAILURES} 次，账号锁定 10 分钟"
            )
    return _reply(result)


@router.post("/change-password")
def change_password(body: ChangePassword, current_user: dict = Depends(get_current_user)):
    """修改当前登录用户密码（校验原密码；仅本人）"""
    return _reply(AuthService.change_password(
        current_user["user_id"], body.old_password, body.new_password))


@router.post("/logout")
def logout(current_user: dict = Depends(get_current_user)):
    """登出：递增 token_version，使该用户已签发的全部 token 立即失效。"""
    return _reply(AuthService.logout(current_user["user_id"]))


@router.post("/deactivate")
def deactivate_account(body: DeactivateAccount, current_user: dict = Depends(get_current_user)):
    """注销（停用）当前登录账号：校验密码后置 is_active=0，仅本人可操作"""
    return _reply(AuthService.deactivate(current_user["user_id"], body.password))
