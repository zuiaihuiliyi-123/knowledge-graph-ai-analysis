"""
统一 API 响应格式（对齐规划文档 6.1.2）
"""
from datetime import datetime


def _now_iso() -> str:
    """返回 ISO8601 时间字符串（含本地时区偏移，如 +08:00）"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def success(data=None, message: str = "success") -> dict:
    """成功响应：{code: 0, message, data, timestamp}"""
    return {"code": 0, "message": message, "data": data, "timestamp": _now_iso()}


def error(code: int, message: str) -> dict:
    """失败响应：{code, message, data: null, timestamp}"""
    return {"code": code, "message": message, "data": None, "timestamp": _now_iso()}
