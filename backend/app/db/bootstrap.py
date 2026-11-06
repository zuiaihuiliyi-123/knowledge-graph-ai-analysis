"""默认引导账号与口令策略。"""

import os
import logging
from ..core.security import hash_password, verify_password
import secrets
import string

_logger = logging.getLogger(__name__)

# 引导账号（首次启动自动创建）的凭据策略。
#
# **代码里不再内置任何默认密码。** 历史版本曾把 admin123 写成 DEFAULT_ADMIN_PASSWORD 的兜底值，
# 那等于给每份部署装了一个公开口令：任何人 clone 仓库就知道管理员密码。
# 现在的规则（ensure_default_admin 执行）：
#   1. 环境变量 DEFAULT_ADMIN_PASSWORD 有值  -> 用它（部署方自己负责保密），不强制改密；
#   2. 未配置                              -> 现场生成随机强密码，**只在启动日志里打印一次**，
#      并给该账号打上 must_change_password=1（首次登录必须改密，日志泄漏也只有一次性价值）。
#
# 刻意**不**复用已有的 "admin" 用户名：那个账号是 role=teacher 的演示教师，且是课程 5 的
# 创建者，把它改成管理员会让「教师登录」与权限回归测试全线受影响。
DEFAULT_ADMIN_USERNAME = os.getenv("DEFAULT_ADMIN_USERNAME", "sysadmin")

# 演示教师账号的初始密码。同样不内置默认值，未配置时随机生成并打印一次。
# 注意：仅在**该账号尚不存在**时生效；已存在的账号密码不会被覆盖。
DEFAULT_TEACHER_USERNAME = "admin"

# 历史版本内置过的引导密码。启动时若发现引导账号仍能用其中任何一个登录，
# 打一条 WARNING 提醒轮换——这是「已知弱口令清单」，不是可用的默认值。
LEGACY_WEAK_PASSWORDS = ("admin123",)

def generate_bootstrap_password(length: int = 16) -> str:
    """生成随机引导密码（去易混字符，便于从日志里准确抄录）"""
    alphabet = string.ascii_letters.replace("l", "").replace("I", "").replace("O", "") + "23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))

class BootstrapMixin:
    """默认教师/管理员引导账号与口令策略。"""


    def ensure_default_teacher(self) -> int:
        """确保存在演示教师账号 admin，返回其 user_id。

        幂等：已存在则原样返回，**不覆盖密码**。
        新库首次创建时：优先取环境变量 DEFAULT_TEACHER_PASSWORD；
        未配置则随机生成并只在启动日志打印一次（不再内置 admin123 这类公开口令）。
        """
        existing = self.get_user_by_username(DEFAULT_TEACHER_USERNAME)
        if existing:
            # 迁移：旧版占位密码 "<not-implemented>" 无法通过校验，替换为真实哈希
            if existing["password_hash"] == "<not-implemented>":
                password, _ = self._bootstrap_password("DEFAULT_TEACHER_PASSWORD")
                self._execute(
                    "UPDATE t_user SET password_hash = ? WHERE user_id = ?",
                    (hash_password(password), existing["user_id"]),
                )
                _logger.warning(
                    "演示教师 %s 的历史占位密码已替换为随机密码，请用重置密码流程重新获取",
                    DEFAULT_TEACHER_USERNAME)
                existing = self.get_user_by_username(DEFAULT_TEACHER_USERNAME)
            self._warn_if_legacy_weak_password(existing)
            return existing["user_id"]

        password, generated = self._bootstrap_password("DEFAULT_TEACHER_PASSWORD")
        user_id = self.create_user(
            username=DEFAULT_TEACHER_USERNAME, password_hash=hash_password(password),
            role="teacher", display_name="默认教师",
        )
        if generated:
            self._print_bootstrap_credentials(
                DEFAULT_TEACHER_USERNAME, password, "演示教师",
                "DEFAULT_TEACHER_PASSWORD（演示教师的用户名固定为 admin，不可配）")
        return user_id


    def ensure_default_admin(self) -> int:
        """确保存在管理员引导账号（管理员端首次可用），返回其 user_id。

        幂等：已存在则原样返回，**不覆盖密码、不改角色**——管理员上线后自行改过密码，
        重启服务不应该把它重置回默认值。用户名见 DEFAULT_ADMIN_USERNAME。

        密码来源（见文件头 DEFAULT_ADMIN_* 注释）：
        - 环境变量 DEFAULT_ADMIN_PASSWORD 有值 -> 用它，不强制改密；
        - 未配置 -> 现场生成随机密码，只在启动日志打印一次，并置 must_change_password=1。
        """
        existing = self.get_user_by_username(DEFAULT_ADMIN_USERNAME)
        if existing:
            self._warn_if_legacy_weak_password(existing)
            return existing["user_id"]

        password, generated = self._bootstrap_password("DEFAULT_ADMIN_PASSWORD")
        user_id = self.create_user(
            username=DEFAULT_ADMIN_USERNAME,
            password_hash=hash_password(password),
            role="admin", display_name="系统管理员",
        )
        if generated:
            # 随机密码是打印在日志里的，日志可能被留存/转发，故首次登录强制改密。
            self.set_must_change_password(user_id, True)
            self._print_bootstrap_credentials(
                DEFAULT_ADMIN_USERNAME, password, "管理员",
                "DEFAULT_ADMIN_USERNAME / DEFAULT_ADMIN_PASSWORD")
        return user_id


    @staticmethod
    def _bootstrap_password(env_key: str) -> tuple:
        """引导密码来源：(password, generated)。

        环境变量优先；未配置则随机生成。生成时必须由调用方安排「只打印一次」，
        generated=True 也用于决定是否强制首次改密。
        """
        configured = (os.getenv(env_key) or "").strip()
        if configured:
            return configured, False
        return generate_bootstrap_password(), True


    @staticmethod
    def _print_bootstrap_credentials(username: str, password: str, label: str,
                                     env_hint: str) -> None:
        """把随机生成的引导凭据打印到启动日志（仅此一次，之后无法再查）。

        env_hint 必须与调用方**真正读取**的环境变量一致：演示教师的用户名是写死的
        "admin"（不读环境变量），只有口令可配——若一律提示 DEFAULT_ADMIN_*，
        使用者会给一个不生效的变量白忙一场。
        """
        banner = "=" * 68
        print(f"\n{banner}\n"
              f"  已创建{label}引导账号（随机密码，仅本次显示，请立即记录）\n"
              f"    用户名：{username}\n"
              f"    密  码：{password}\n"
              f"  该口令只在本次启动日志里出现，首次登录后系统会要求修改密码。\n"
              f"  如需自行指定，请在启动前设置环境变量：\n"
              f"    {env_hint}\n"
              f"{banner}\n", flush=True)


    @staticmethod
    def _warn_if_legacy_weak_password(user: dict) -> None:
        """引导账号仍在使用历史内置弱口令时，每次启动都提醒轮换。

        只提示、不改动：把密码改掉可能让正在使用该账号的人被锁在外面。
        """
        try:
            if any(verify_password(p, user["password_hash"]) for p in LEGACY_WEAK_PASSWORDS):
                _logger.warning(
                    "安全提醒：引导账号 %s 仍在使用内置的历史弱口令 %s，请尽快通过"
                    "「修改密码」或管理员端「重置密码」轮换。",
                    user["username"], "/".join(LEGACY_WEAK_PASSWORDS),
                )
        except Exception:                       # 哈希格式异常不应阻断启动
            pass
