"""
密码哈希与校验（标准库 PBKDF2-SHA256，零外部依赖）

原先哈希逻辑写在 api/auth.py 内；抽出到此处供 auth（登录/注册）与
sql_database（默认教师密码）复用，避免 sql_database 反向 import api/auth 造成循环依赖。
"""
import hashlib
import hmac
import secrets

# PBKDF2 迭代次数（安全与性能折中，普通电脑可接受）
PBKDF2_ITERATIONS = 100_000


def hash_password(password: str) -> str:
    """PBKDF2-SHA256 哈希密码，返回 "salt$hash_hex"（盐为随机 32 位十六进制）"""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    )
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验密码；stored 为空/格式非法（如旧版占位值 "<not-implemented>"）时返回 False"""
    try:
        salt, hash_hex = stored.split("$", 1)
    except (ValueError, AttributeError):
        return False
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    )
    return hmac.compare_digest(dk.hex(), hash_hex)
