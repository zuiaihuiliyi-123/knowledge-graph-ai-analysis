"""账号态缓存：把「JWT 快照」与「库内真实状态」按短 TTL 收敛（阶段 1A 安全收口）。

为什么需要：JWT 里的 role / 是否停用是**签发那一刻**的快照。禁用账号或把教师降级后，
旧 token 仍能在 24 小时有效期内通行——这是「角色变更后权限不收敛」的根因。

设计取舍：
- 每个受保护请求都回查 `t_user` 会把「每请求一次 SQLite 读」放大到全站接口，
  而问答链路本就在意毫秒级开销；故加短 TTL 缓存，把查询压到「每账号每 30 秒最多一次」。
- TTL 窗口内存在最长 30 秒的生效延迟，这是安全性与开销之间刻意接受的折中；
  管理员改角色 / 禁用时调 `invalidate_user_state()` 立即失效，高危路径没有延迟。
- 缓存值允许是 `None`（账号已不存在），避免对已删账号反复查库。

与 `require_admin` 的分工：后者刻意**不走缓存**（管理员是最高权限档，宁可多一次查询），
本模块只服务普通鉴权路径。
"""
import threading
import time

from .sql_database import sql_db

# 账号态缓存有效期（秒）。调大省查询但拉长「停用/降级」生效延迟；0 表示不缓存。
USER_STATE_TTL = 30.0

_lock = threading.Lock()
_cache: dict[int, tuple[float, dict | None]] = {}


def invalidate_user_state(user_id: int | None = None) -> None:
    """失效单个账号（给定 user_id）或全部（None）的缓存。

    调用点：管理员启用/禁用账号、修改角色、删除账号之后——这些操作必须立刻生效。
    """
    with _lock:
        if user_id is None:
            _cache.clear()
        else:
            _cache.pop(int(user_id), None)


def load_user_state(user_id: int) -> dict | None:
    """读取账号态 `{username, role, is_active}`；账号不存在返回 `None`（同样走缓存）。"""
    uid = int(user_id)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(uid)
        if hit is not None and now - hit[0] < USER_STATE_TTL:
            return hit[1]

    row = sql_db.get_user_by_id(uid)
    state = None if not row else {
        "username": row.get("username") or "",
        "role": (row.get("role") or "").strip(),
        "is_active": int(row.get("is_active", 1) or 0),
    }
    with _lock:
        _cache[uid] = (now, state)
    return state
