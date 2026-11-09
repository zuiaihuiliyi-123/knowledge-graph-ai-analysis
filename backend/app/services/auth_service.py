"""用户认证服务（注册 / 登录 / 改密 / 注销）。

Phase 2 从 api/auth.py 下沉：路由只做参数解析与响应包装，账号查询、口令校验
与管理员登录审计统一收在这里。返回契约与其它 service 一致：
{"ok": bool, "code": int, "message": str, "data": dict}
"""
import datetime
import secrets

import jwt

from ..core.config import settings
from ..core.security import hash_password, verify_password
from ..core.sql_database import ARCHIVED_USERNAME_PREFIX, sql_db
from ..core.user_state import invalidate_user_state
from .admin_service import AdminService
from .profile_service import ProfileService


class AuthService:
    """登录、注册与本人账号操作。"""

    @staticmethod
    def _ok(data=None, message: str = "success") -> dict:
        return {"ok": True, "code": 0, "message": message, "data": data}

    @staticmethod
    def _fail(code: int, message: str) -> dict:
        return {"ok": False, "code": code, "message": message, "data": None}

    @staticmethod
    def issue_token(user: dict) -> str:
        """签发 HS256 JWT，载荷含 user_id/username/role/ver，有效期 24 小时。

        ver 用于服务端吊销：改密 / 禁用 / 改角色 / 登出时 t_user.token_version +1，
        旧 token 的 ver 与之不一致，get_current_user 会直接拒绝。
        """
        payload = {
            "sub": str(user["user_id"]),
            "username": user["username"],
            "role": user["role"],
            "ver": int(user.get("token_version") or 0),
            "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=24),
        }
        return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")

    @staticmethod
    def register(username: str, password: str, role: str = "student",
                 email: str = None, display_name: str = None,
                 teacher_invite_code: str = None) -> dict:
        """注册；教师自助注册必须携带正确的 TEACHER_INVITE_CODE。"""
        username = (username or "").strip()
        if not username:
            return AuthService._fail(1001, "用户名不能为空")
        if not (3 <= len(username) <= 20):
            return AuthService._fail(1001, "用户名长度需在 3-20 个字符之间")
        if username.startswith(ARCHIVED_USERNAME_PREFIX):
            return AuthService._fail(1001, "用户名不合法，请更换")
        if not password:
            return AuthService._fail(1001, "密码不能为空")
        if len(password) < settings.MIN_PASSWORD_LENGTH:
            return AuthService._fail(
                1001, f"密码长度至少 {settings.MIN_PASSWORD_LENGTH} 位")

        role = (role or "student").strip().lower()
        if role not in ("teacher", "student"):
            return AuthService._fail(1001, "角色不合法，仅支持 teacher/student")

        if role == "teacher":
            expected = (settings.TEACHER_INVITE_CODE or "").strip()
            if not expected:
                # 未配置邀请码 = 未开放教师自助注册。与「邀请码错误」用同一业务码，
                # 避免把「系统是否配置了邀请码」当成探测信息暴露出去。
                return AuthService._fail(4003, "教师注册未开放：请联系管理员开通账号")
            provided = (teacher_invite_code or "").strip()
            if not provided or not secrets.compare_digest(provided, expected):
                return AuthService._fail(4003, "教师邀请码不正确")

        user_id = sql_db.create_user_releasing_inactive(
            username=username,
            password_hash=hash_password(password),
            role=role,
            display_name=display_name,
            email=email,
        )
        if user_id is None:
            return AuthService._fail(2006, f"用户名已存在: {username}")
        return AuthService._ok({"user_id": user_id, "username": username, "role": role})

    @staticmethod
    def login(username: str, password: str, request=None) -> dict:
        """口令登录；管理员成功/失败登录各写一条审计（失败写审计不泄露账号是否存在）。"""
        username = (username or "").strip()
        db_user = sql_db.get_user_by_username(username)
        if not db_user or not verify_password(password, db_user["password_hash"]):
            # 只对「用户名确实存在且是管理员」记录失败登录：否则任何随机用户名都能灌审计表。
            if db_user and db_user.get("role") == "admin":
                AdminService.write_audit(
                    {"user_id": db_user["user_id"], "username": db_user["username"],
                     "role": "admin"},
                    "admin.login", "user", db_user["user_id"], result="failure",
                    detail="管理员登录失败：用户名或密码错误", request=request,
                )
            return AuthService._fail(2002, "用户名或密码错误")
        if not db_user.get("is_active", 1):
            return AuthService._fail(2004, "该账号已注销，无法登录")

        profile = sql_db.get_user_profile(db_user["user_id"]) or {}

        if db_user.get("role") == "admin":
            AdminService.write_audit(
                {"user_id": db_user["user_id"], "username": db_user["username"],
                 "role": "admin"},
                "admin.login", "user", db_user["user_id"],
                detail="管理员登录成功", request=request,
            )

        return AuthService._ok({
            "access_token": AuthService.issue_token(db_user),
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
                # 首次登录必须改密；需要重新登录才生效的字段，故意不放进 JWT 载荷。
                "must_change_password": int(db_user.get("must_change_password") or 0),
            },
        })

    @staticmethod
    def change_password(user_id: int, old_password: str, new_password: str) -> dict:
        """本人改密：校验原密码，成功后清除 must_change_password。"""
        old_password = old_password or ""
        new_password = new_password or ""
        if not old_password:
            return AuthService._fail(1001, "请输入原密码")
        if len(new_password) < settings.MIN_PASSWORD_LENGTH:
            return AuthService._fail(
                1001, f"新密码长度至少 {settings.MIN_PASSWORD_LENGTH} 位")

        db_user = sql_db.get_user_by_id(user_id)
        if not db_user:
            return AuthService._fail(2001, "用户不存在")
        if not verify_password(old_password, db_user["password_hash"]):
            return AuthService._fail(2003, "原密码不正确")
        if verify_password(new_password, db_user["password_hash"]):
            return AuthService._fail(1001, "新密码不能与原密码相同")

        sql_db.update_password(user_id, hash_password(new_password))
        # 只有本人持旧密码主动修改才能清除该标记；管理员重置密码时会重新置 1。
        sql_db.set_must_change_password(user_id, False)
        # 账号态缓存里还留着改密前的 must_change_password=1，必须立即失效，
        # 否则用户改完密码后仍会被后端挡 30 秒。
        invalidate_user_state(user_id)
        # update_password 已递增 token_version，旧 token 立即失效。
        # 这里按新版本号再签一个，避免用户「改完密码就被踢回登录页」。
        updated = sql_db.get_user_by_id(user_id)
        return AuthService._ok({
            "user_id": user_id,
            "access_token": AuthService.issue_token(updated),
            "token_type": "bearer",
        })

    @staticmethod
    def deactivate(user_id: int, password: str) -> dict:
        """本人注销（软停用）：校验密码后置 is_active=0，并立即失效账号态缓存。"""
        password = password or ""
        if not password:
            return AuthService._fail(1001, "请输入密码")

        db_user = sql_db.get_user_by_id(user_id)
        if not db_user:
            return AuthService._fail(2001, "用户不存在")
        if not verify_password(password, db_user["password_hash"]):
            return AuthService._fail(2002, "密码不正确")

        # 最后管理员保护：注销后若没有任何启用的 admin，平台将再无人能恢复治理入口
        # （管理员端的所有操作都需要 admin 登录）。管理员接口侧的
        # `_guard_self_and_last_admin` 不覆盖本人自助注销，必须在这里单独拦。
        if db_user.get("role") == "admin" \
                and sql_db.count_active_admins(exclude_user_id=user_id) == 0:
            return AuthService._fail(
                4003, "不能注销最后一个管理员账号：请先创建或启用另一位管理员")

        sql_db.deactivate_user(user_id)
        invalidate_user_state(user_id)
        return AuthService._ok({"user_id": user_id})

    @staticmethod
    def logout(user_id: int) -> dict:
        """登出：递增 token_version，使该用户已签发的全部 token 立即失效。"""
        sql_db.bump_token_version(user_id)
        invalidate_user_state(user_id)
        return AuthService._ok({"user_id": user_id})
