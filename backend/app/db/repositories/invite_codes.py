"""教师注册邀请码（管理员签发、单次使用）。

与 `t_course_invite`（课程邀请链接）刻意分开：那个是「加入某门课程」的令牌，
这个是「获得教师身份」的凭证，后者是更高危的授权，审计与生命周期都单独管理。
"""

import sqlite3

from ...core.codes import gen_teacher_invite_code, normalize_teacher_invite_code
from ..connection import _now


class InviteCodesMixin:
    """教师注册邀请码。"""

    def _exec_rowcount(self, sql: str, params: tuple = ()) -> int:
        """执行写操作并返回**影响行数**（`_execute` 返回的是 lastrowid，UPDATE 时无意义）。"""
        with self._connect() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.rowcount

    def create_teacher_invite_code(self, created_by: int, note: str = None,
                                   expires_at: str = None, retries: int = 5) -> tuple:
        """生成并插入一条邀请码，返回 (invite_id, 归一化后的码)。

        唯一性靠 `code` 的唯一索引兜底：理论上 12 位随机码不会撞，但「靠索引报错 +
        重试」比「先 SELECT 再 INSERT」少一次竞态窗口，代码也更短。
        """
        for _ in range(max(int(retries), 1)):
            code = normalize_teacher_invite_code(gen_teacher_invite_code())
            try:
                invite_id = self._execute(
                    "INSERT INTO t_teacher_invite_code (code, note, created_by, expires_at) "
                    "VALUES (?, ?, ?, ?)",
                    (code, note, created_by, expires_at),
                )
                return invite_id, code
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("生成教师邀请码失败：连续多次碰撞")

    def list_teacher_invite_codes(self, limit: int = 200) -> list:
        """按签发时间倒序列出邀请码（附带签发人 / 使用人名字，省掉前端二次查询）。"""
        return self._query(
            """
            SELECT t.invite_id, t.code, t.note, t.status, t.created_by, t.used_by,
                   t.used_at, t.expires_at, t.created_at,
                   cu.username     AS created_by_username,
                   COALESCE(cu.display_name, cu.username) AS created_by_name,
                   uu.username     AS used_by_username,
                   COALESCE(uu.display_name, uu.username) AS used_by_name
            FROM t_teacher_invite_code t
            LEFT JOIN t_user cu ON cu.user_id = t.created_by
            LEFT JOIN t_user uu ON uu.user_id = t.used_by
            ORDER BY t.invite_id DESC
            LIMIT ?
            """,
            (max(int(limit), 1),),
        )

    def get_teacher_invite_code(self, code: str) -> dict:
        return self._query_one(
            "SELECT * FROM t_teacher_invite_code WHERE code = ?", (code,),
        )

    def get_teacher_invite_code_by_id(self, invite_id: int) -> dict:
        return self._query_one(
            """
            SELECT t.*, COALESCE(u.display_name, u.username) AS used_by_name,
                   COALESCE(c.display_name, c.username) AS created_by_name
            FROM t_teacher_invite_code t
            LEFT JOIN t_user u ON u.user_id = t.used_by
            LEFT JOIN t_user c ON c.user_id = t.created_by
            WHERE t.invite_id = ?
            """,
            (invite_id,),
        )

    def claim_teacher_invite_code(self, invite_id: int) -> bool:
        """原子占用一条邀请码（先占位、注册成功后再回填 used_by）。

        为什么分「占用」与「回填」两步：used_by 要填的是**即将创建**的 user_id，
        此刻还不存在。若改成「先建号再标记」，两个请求同时用同一个码时可能各建出一个
        教师账号、只有一个标记成功，另一个就成了无授权的孤儿账号。条件全部写进 UPDATE
        的 WHERE 而不是「先查后改」，这样并发下只有一条能命中。
        """
        with self._connect() as conn:
            now = _now()
            cur = conn.execute(
                "UPDATE t_teacher_invite_code "
                "SET status = 'used', used_at = ? "
                "WHERE invite_id = ? AND status = 'active' "
                "AND (expires_at IS NULL OR expires_at > ?)",
                (now, invite_id, now),
            )
            conn.commit()
            return cur.rowcount > 0

    def attach_teacher_invite_code(self, invite_id: int, user_id: int) -> int:
        """注册成功后回填使用者（用于「谁是谁邀请进来的」追溯）。"""
        return self._exec_rowcount(
            "UPDATE t_teacher_invite_code SET used_by = ? WHERE invite_id = ?",
            (user_id, invite_id),
        )

    def release_teacher_invite_code(self, invite_id: int) -> int:
        """把已占用但注册最终失败的码退回可用（用户名已被占用等少见路径）。"""
        return self._exec_rowcount(
            "UPDATE t_teacher_invite_code SET status = 'active', used_by = NULL, "
            "used_at = NULL WHERE invite_id = ? AND status = 'used'",
            (invite_id,),
        )

    def revoke_teacher_invite_code(self, invite_id: int) -> int:
        """作废（只能作废未使用的；已使用的记录要保留 used_by，便于追溯是谁注册的）。"""
        return self._exec_rowcount(
            "UPDATE t_teacher_invite_code SET status = 'revoked' "
            "WHERE invite_id = ? AND status = 'active'",
            (invite_id,),
        )
