"""用户、账号态与个人资料。"""

import sqlite3
from ..schema import ARCHIVED_USERNAME_PREFIX
from ..connection import _now
from ..bootstrap import (DEFAULT_ADMIN_USERNAME, DEFAULT_TEACHER_USERNAME)

class UsersMixin:
    """用户、账号态与个人资料。"""


    # ---------- 用户 ----------

    def create_user(self, username: str, password_hash: str, role: str = "student",
                    display_name: str = None, email: str = None) -> int:
        return self._execute(
            "INSERT INTO t_user (username, password_hash, role, display_name, email) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, password_hash, role, display_name, email),
        )


    def create_user_releasing_inactive(self, username: str, password_hash: str,
                                       role: str = "student", display_name: str = None,
                                       email: str = None) -> int | None:
        """注册新账号；同名旧账号若已注销（is_active=0），先归档其用户名再插入。

        背景：注销是软停用（保留 user_id 与全部历史数据），但 `username` 有 UNIQUE
        约束。若只把 is_active 置 0，同名用户永远无法重新注册，表现就是「已注销，
        却仍提示用户名已存在」。

        处理方式：把旧行的 username 改为 `ARCHIVED_USERNAME_PREFIX<id>__<原名>`，
        再插入新用户。旧行仍保留、外键与历史数据不受影响；新账号获得干净的 user_id。

        返回新用户 user_id；同名账号仍启用、同名旧账号是管理员/引导账号，或并发注册
        中被别人抢先插入时返回 None（调用方统一按「用户名已存在」处理）。
        查询、归档、插入放在同一个 `BEGIN IMMEDIATE` 事务里，避免两个请求同时归档
        同一行或同时插入造成 UNIQUE 冲突。
        """
        with self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT user_id, is_active, role FROM t_user WHERE username = ?",
                    (username,),
                ).fetchone()
                if row is not None:
                    # 启用中的账号占用用户名；管理员与引导账号（admin / sysadmin）的
                    # 用户名不允许被复用，避免停用后名字被普通账号占用、掩盖真实身份，
                    # 也让 ensure_default_* 在重启时仍能认出引导账号。
                    protected = row["role"] == "admin" or username in (
                        DEFAULT_TEACHER_USERNAME, DEFAULT_ADMIN_USERNAME)
                    if int(row["is_active"] or 0) or protected:
                        conn.rollback()
                        return None
                    archived = f"{ARCHIVED_USERNAME_PREFIX}{row['user_id']}__{username}"
                    conn.execute(
                        "UPDATE t_user SET username = ?, updated_at = ? "
                        "WHERE user_id = ? AND is_active = 0",
                        (archived, _now(), row["user_id"]),
                    )
                cur = conn.execute(
                    "INSERT INTO t_user (username, password_hash, role, display_name, email) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (username, password_hash, role, display_name, email),
                )
                conn.commit()
                return cur.lastrowid
            except sqlite3.IntegrityError:
                # 并发插入同名账号（BEGIN IMMEDIATE 已挡住正常路径，这里兜底外部写入）
                conn.rollback()
                return None


    def get_user_by_username(self, username: str) -> dict:
        return self._query_one("SELECT * FROM t_user WHERE username = ?", (username,))


    def get_user_by_id(self, user_id: int) -> dict:
        return self._query_one("SELECT * FROM t_user WHERE user_id = ?", (user_id,))


    def update_password(self, user_id: int, password_hash: str) -> int:
        """更新指定用户密码哈希，并递增 token_version（旧 token 立即失效）。"""
        return self._execute(
            "UPDATE t_user SET password_hash = ?, "
            "token_version = token_version + 1, updated_at = ? WHERE user_id = ?",
            (password_hash, _now(), user_id),
        )


    def deactivate_user(self, user_id: int) -> int:
        """注销（软停用）指定用户：置 is_active=0 并吊销现有 token，保留历史数据"""
        return self._execute(
            "UPDATE t_user SET is_active = 0, "
            "token_version = token_version + 1, updated_at = ? WHERE user_id = ?",
            (_now(), user_id),
        )


    def bump_token_version(self, user_id: int) -> int:
        """吊销该用户已签发的全部 token（登出 / 强制下线）。"""
        return self._execute(
            "UPDATE t_user SET token_version = token_version + 1, "
            "updated_at = ? WHERE user_id = ?",
            (_now(), user_id),
        )


    def list_users(self) -> list:
        return self._query("SELECT * FROM t_user ORDER BY user_id")


    def set_must_change_password(self, user_id: int, required: bool) -> int:
        """设置/清除「首次登录必须修改密码」标记"""
        return self._execute(
            "UPDATE t_user SET must_change_password = ? WHERE user_id = ?",
            (1 if required else 0, user_id),
        )


    # ---------- 用户资料（个人中心） ----------

    _PROFILE_FIELDS = ("avatar_url", "real_name", "nickname", "gender", "school",
                       "college", "bio", "student_no", "major", "grade",
                       "class_name", "teacher_no", "title", "research_area")


    def get_user_profile(self, user_id: int) -> dict:
        """取用户身份信息 + 资料（资料行不存在时右侧字段为 NULL，不算错误）"""
        return self._query_one(
            """
            SELECT u.user_id, u.username, u.role, u.display_name, u.email,
                   u.is_active, u.created_at AS user_created_at,
                   p.avatar_url, p.real_name, p.nickname, p.gender, p.school,
                   p.college, p.bio, p.student_no, p.major, p.grade, p.class_name,
                   p.teacher_no, p.title, p.research_area,
                   p.updated_at AS profile_updated_at
            FROM t_user u
            LEFT JOIN t_user_profile p ON p.user_id = u.user_id
            WHERE u.user_id = ?
            """,
            (user_id,),
        )


    def upsert_user_profile(self, user_id: int, **fields) -> None:
        """写入/更新资料（仅白名单字段；None 表示清空该字段，与「不修改」由服务层区分）"""
        cols = [k for k in fields if k in self._PROFILE_FIELDS]
        if not cols:
            return
        now = _now()
        placeholders = ", ".join("?" * len(cols))
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols)
        self._execute(
            f"INSERT INTO t_user_profile (user_id, {', '.join(cols)}, updated_at) "
            f"VALUES (?, {placeholders}, ?) "
            f"ON CONFLICT(user_id) DO UPDATE SET {updates}, updated_at = excluded.updated_at",
            tuple([user_id] + [fields[c] for c in cols] + [now]),
        )


    def list_user_profiles(self, user_ids: list) -> dict:
        """批量取资料，返回 {user_id: {nickname, real_name, avatar_url, school, ...}}（成员列表用）"""
        if not user_ids:
            return {}
        rows = self._query(
            f"""
            SELECT u.user_id, u.username, u.display_name,
                   p.avatar_url, p.real_name, p.nickname, p.school, p.college,
                   p.major, p.grade, p.class_name, p.student_no, p.teacher_no, p.title
            FROM t_user u
            LEFT JOIN t_user_profile p ON p.user_id = u.user_id
            WHERE u.user_id IN ({','.join('?' * len(user_ids))})
            """,
            tuple(user_ids),
        )
        return {r["user_id"]: dict(r) for r in rows}


    def count_user_profiles(self) -> int:
        return self._query_one("SELECT count(*) AS cnt FROM t_user_profile")["cnt"]
