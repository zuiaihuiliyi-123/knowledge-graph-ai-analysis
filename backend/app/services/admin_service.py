"""管理员端服务（平台治理 / 用户管理 / 课程治理 / 资源管理 / 系统监控 / 审计日志）

定位：管理员**不是超级教师**。教师负责教学（备课、传文档、管图谱、批改），
学生负责学习，管理员负责整个平台的治理：账号、课程存废、资源完整性、系统健康、操作留痕。
故本模块刻意**不**提供「替教师改知识点」「替学生改学习记录」这类越权教学动作——
那会绕过课程权限模型，让管理员变成没有边界的角色。

统一返回契约与其它 service 一致：{"ok": bool, "code": int, "message": str, "data": dict}
错误码沿用项目既有分段：2001 不存在 / 2002 冲突 / 1001 参数非法 / 4003 无权限 / 4007 字段非法。

复用优先：
- 平台统计直接复用 DashboardService.get_stats(None)（course_ids=None 即全平台），
  不重写一套 Neo4j 聚合；
- 文档删除复用 DocumentService.delete_document（它已保证 Neo4j + SQLite + 本地文件
  三处一致），不另写一套删除逻辑；
- 资料字段白名单与长度校验复用 ProfileService。
"""
import logging
import secrets
import string

from ..core.config import settings
from ..core.database import db
from ..core.metrics import snapshot as metrics_snapshot
from ..core.security import hash_password, verify_password
from ..core.sql_database import (
    ADMIN_ACTIONS, COURSE_GOVERNANCE_STATUS, COURSE_VISIBLE_SQL, USER_ROLES, sql_db,
)
from .dashboard_service import DashboardService
from .document_service import DocumentService
from .member_service import MemberService
from .profile_service import ProfileService

_logger = logging.getLogger(__name__)

# 归档课程时写入的备注前缀（保持可读，且带时间便于追溯）
ARCHIVE_REASON_MAX = 200


class AdminService:
    """管理员端业务逻辑（全部为平台级操作，不做任何课程内容级写入）"""

    # ---------- 通用 ----------

    @staticmethod
    def _fail(code: int, message: str) -> dict:
        return {"ok": False, "code": code, "message": message, "data": None}

    @staticmethod
    def _ok(data=None, message: str = "success") -> dict:
        return {"ok": True, "code": 0, "message": message, "data": data}

    @staticmethod
    def _client_info(request) -> tuple:
        """从 Request 取 (ip, user_agent)，用于审计日志。取不到时返回 (None, None)。

        X-Forwarded-For 优先：部署在反向代理后面时 request.client.host 是代理地址，
        记下来对审计毫无意义。多级代理时取第一段（最靠近客户端的那个）。
        """
        if request is None:
            return None, None
        ip = None
        try:
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                ip = forwarded.split(",")[0].strip()
            elif request.client:
                ip = request.client.host
            user_agent = request.headers.get("user-agent")
        except Exception:                      # request 不是标准对象时不阻断业务
            return None, None
        return ip, (user_agent or "")[:300]

    @staticmethod
    def write_audit(operator: dict, action: str, target_type: str = None,
                    target_id=None, result: str = "success", detail: str = None,
                    request=None) -> int:
        """写一条审计日志。

        审计写入失败**不应该**让业务操作回滚或报错（日志是旁路，不是事务的一部分），
        故这里吞掉异常并记 error 级日志——否则日志表出问题会连带管理端全盘不可用。
        """
        if action not in ADMIN_ACTIONS:
            # 不阻断，但留下痕迹：动作名漏登记说明代码与枚举不同步
            _logger.warning("未登记的审计动作名: %s", action)
        ip, user_agent = AdminService._client_info(request)
        try:
            return sql_db.add_audit_log(
                operator_id=operator.get("user_id"),
                operator_name=operator.get("username") or "",
                operator_role=operator.get("role") or "admin",
                action=action, target_type=target_type, target_id=target_id,
                result=result, detail=detail, ip=ip, user_agent=user_agent,
            )
        except Exception as exc:               # pragma: no cover - 防御性
            _logger.error("审计日志写入失败: action=%s err=%s", action, exc)
            return 0

    # ---------- 工作台 ----------

    @staticmethod
    def dashboard(days: int = 14) -> dict:
        """管理员工作台总览：平台计数 + 角色分布 + 治理概览 + 系统状态 + 最近活动。

        平台图数据（知识点 / 关系）复用 DashboardService.get_stats(None)——
        course_ids=None 表示不收敛课程，即全平台口径，避免重复实现 Neo4j 聚合。
        Neo4j 不可用时 get_stats 会静默退化为 0（既有行为），故另用 graph_available
        标明真实可用性，前端据此显示「未检测」而不是把 0 当成真实值。
        """
        counts = sql_db.admin_platform_counts()
        graph_stats = DashboardService.get_stats(None)
        graph_available = AdminService._neo4j_available()

        return AdminService._ok({
            "counts": {
                **counts,
                "node_count": graph_stats.get("node_count", 0),
                "edge_count": graph_stats.get("edge_count", 0),
                "concept_node_count": graph_stats.get("concept_node_count", 0),
            },
            "role_distribution": [
                {"role": "student", "label": "学生", "count": counts["student_count"]},
                {"role": "teacher", "label": "教师", "count": counts["teacher_count"]},
                {"role": "admin", "label": "管理员", "count": counts["admin_count"]},
            ],
            "governance": sql_db.count_courses_by_governance(),
            # 业务状态（status）与治理状态（governance_status）是两个维度，分开报数：
            # - enabled / disabled：课程自身的开放 / 关闭（教师或管理员设置）
            # - visible：两者组合后的真实结果，即学生当前能不能发现并加入该课程
            "course_status": {
                "enabled": sql_db._query_one(
                    "SELECT count(*) AS c FROM t_course WHERE status = 1")["c"],
                "disabled": sql_db._query_one(
                    "SELECT count(*) AS c FROM t_course WHERE status = 0")["c"],
                "visible": sql_db._query_one(
                    f"SELECT count(*) AS c FROM t_course WHERE {COURSE_VISIBLE_SQL}")["c"],
                "public": counts["public_course_count"],
            },
            "extraction": sql_db.count_extraction_tasks(),
            "document_status": sql_db.count_documents_by_status(),
            "graph_available": graph_available,
            "category_distribution": graph_stats.get("category_distribution", {}),
            "relation_distribution": graph_stats.get("relation_distribution", {}),
            # 平台趋势：按天真实聚合各表 created_at（有几天数据就显示几天，不做外推）
            "trend": sql_db.admin_platform_trend(days),
            "recent_users": sql_db.list_recent_users(8),
            "recent_courses": sql_db.list_recent_courses(8),
            "recent_documents": sql_db.list_recent_documents(8),
            "recent_audits": sql_db.list_recent_audit_logs(8),
            "audit_log_count": sql_db.count_audit_logs(),
        })

    @staticmethod
    def _neo4j_available() -> bool:
        """真实探测 Neo4j 是否可用（不做任何写入，只取 1 个节点）"""
        try:
            db.query("MATCH (n:KnowledgePoint) RETURN n LIMIT 1")
            return True
        except Exception:
            return False

    # ---------- 用户管理 ----------

    @staticmethod
    def list_users(keyword=None, role=None, status=None, page=1, page_size=20,
                   sort_by=None, sort_order=None) -> dict:
        if role and role not in USER_ROLES:
            return AdminService._fail(1001, f"角色筛选值非法：{role}")
        if status and status not in ("active", "disabled"):
            return AdminService._fail(1001, f"状态筛选值非法：{status}")
        page = max(int(page or 1), 1)
        page_size = min(max(int(page_size or 20), 1), 100)

        total, rows = sql_db.list_users_admin(keyword, role, status, page, page_size,
                                             sort_by, sort_order)
        items = [AdminService._user_row(r) for r in rows]
        return AdminService._ok({
            "total": total, "page": page, "page_size": page_size, "items": items,
        })

    @staticmethod
    def _user_row(row: dict) -> dict:
        """列表行的展示字段（不含邮箱等敏感资料时也保持键恒定，前端无需判空分支）"""
        return {
            "user_id": row["user_id"],
            "username": row["username"],
            "role": row["role"],
            "display_name": row.get("display_name"),
            "real_name": row.get("real_name"),
            "nickname": row.get("nickname"),
            "school": row.get("school"),
            "college": row.get("college"),
            "student_no": row.get("student_no"),
            "teacher_no": row.get("teacher_no"),
            "is_active": row.get("is_active", 1),
            "created_at": row.get("created_at"),
            "last_active": row.get("last_active"),
        }

    @staticmethod
    def get_user(user_id: int) -> dict:
        """用户详情：基础资料 + 学生/教师资料 + 统计 + 所在课程。

        刻意不返回 password_hash（SQL 层就已排除），管理员在任何界面都看不到密码。
        """
        row = sql_db.get_user_admin_detail(user_id)
        if row is None:
            return AdminService._fail(2001, f"用户不存在: user_id={user_id}")
        row["avatar_url"] = ProfileService.avatar_url_for(user_id, row.get("avatar_url"))
        # 相关操作记录走定向查询（target_type + target_id），而不是取最近 N 条再过滤 ——
        # 后者只能看到「最近 20 条全平台日志里恰好命中该用户」的那几条，会漏。
        _, audit_rows = sql_db.list_audit_logs(target_type="user", target_id=user_id,
                                              page=1, page_size=50)
        return AdminService._ok({
            "user": row,
            "stats": sql_db.get_user_admin_stats(user_id),
            "courses": sql_db.list_user_courses_admin(user_id),
            "audit_logs": audit_rows,
        })

    @staticmethod
    def update_user(operator: dict, user_id: int, fields: dict, request=None) -> dict:
        """编辑用户资料（显示名 / 邮箱 + 资料表字段）。

        不包含角色与状态：这两个是高危操作，各自有独立端点与各自的审计动作。
        """
        target = sql_db.get_user_by_id(user_id)
        if target is None:
            return AdminService._fail(2001, f"用户不存在: user_id={user_id}")

        changed = []
        display_name = fields.get("display_name")
        email = fields.get("email")
        if display_name is not None and display_name != target.get("display_name"):
            sql_db._execute(
                "UPDATE t_user SET display_name = ?, updated_at = "
                "datetime('now', 'localtime') WHERE user_id = ?",
                ((str(display_name).strip() or None), user_id),
            )
            changed.append("display_name")
        if email is not None and email != target.get("email"):
            sql_db._execute(
                "UPDATE t_user SET email = ?, updated_at = datetime('now', 'localtime') "
                "WHERE user_id = ?",
                ((str(email).strip() or None), user_id),
            )
            changed.append("email")

        # 资料字段：复用个人中心的白名单与长度校验（按被编辑者的角色取字段集）
        profile_payload = {
            k: v for k, v in (fields.get("profile") or {}).items()
        }
        if profile_payload:
            result = ProfileService.update_profile(user_id, target["role"], profile_payload)
            if not result["ok"]:
                return result
            changed.extend(list(profile_payload.keys()))

        if not changed:
            return AdminService._ok({"user_id": user_id, "changed": []},
                                    "没有需要修改的字段")

        AdminService.write_audit(
            operator, "user.update", "user", user_id,
            detail=f"编辑用户 {target['username']} 的资料：{', '.join(changed)}",
            request=request,
        )
        return AdminService._ok({"user_id": user_id, "changed": changed})

    @staticmethod
    def _guard_self_and_last_admin(operator: dict, target: dict, action_label: str) -> dict | None:
        """高危操作的共同护栏：不能对自己动手、不能清掉最后一个可用管理员。

        返回 None 表示放行，否则返回失败响应。
        """
        if target["user_id"] == operator["user_id"]:
            return AdminService._fail(2002, f"不能{action_label}自己的账号")
        if target.get("role") == "admin" and target.get("is_active", 1):
            others = sql_db._query_one(
                "SELECT count(*) AS c FROM t_user WHERE role = 'admin' "
                "AND is_active = 1 AND user_id != ?",
                (target["user_id"],),
            )["c"]
            if others == 0:
                return AdminService._fail(2002, f"系统至少需要保留一个启用状态的管理员，无法{action_label}最后一个管理员")
        return None

    @staticmethod
    def set_user_active(operator: dict, user_id: int, active: bool, request=None) -> dict:
        """启用 / 禁用账号。禁用不影响其历史学习数据，仅拒绝登录。"""
        target = sql_db.get_user_by_id(user_id)
        if target is None:
            return AdminService._fail(2001, f"用户不存在: user_id={user_id}")
        if not active:
            guard = AdminService._guard_self_and_last_admin(operator, target, "禁用")
            if guard:
                return guard
        if bool(target.get("is_active", 1)) == bool(active):
            return AdminService._ok({"user_id": user_id, "is_active": 1 if active else 0},
                                    "账号状态未变化")

        sql_db.set_user_active(user_id, active)
        action = "user.enable" if active else "user.disable"
        AdminService.write_audit(
            operator, action, "user", user_id,
            detail=f"{'启用' if active else '禁用'}账号 {target['username']}",
            request=request,
        )
        return AdminService._ok({"user_id": user_id, "is_active": 1 if active else 0})

    @staticmethod
    def set_user_role(operator: dict, user_id: int, role: str, request=None) -> dict:
        """修改用户角色（student / teacher / admin）。"""
        if role not in USER_ROLES:
            return AdminService._fail(1001, f"角色非法：{role}（仅支持 {'/'.join(USER_ROLES)}）")
        target = sql_db.get_user_by_id(user_id)
        if target is None:
            return AdminService._fail(2001, f"用户不存在: user_id={user_id}")
        if target["role"] == role:
            return AdminService._ok({"user_id": user_id, "role": role}, "角色未变化")

        if target["role"] == "admin":
            guard = AdminService._guard_self_and_last_admin(operator, target, "降级")
            if guard:
                return guard
        else:
            guard = AdminService._guard_self_and_last_admin(operator, target, "修改角色")
            if guard:
                return guard

        sql_db.set_user_role(user_id, role)
        AdminService.write_audit(
            operator, "user.role_change", "user", user_id,
            detail=f"把 {target['username']} 的角色从 {target['role']} 改为 {role}",
            request=request,
        )
        return AdminService._ok({"user_id": user_id, "role": role})

    # 重置密码时自动生成的密码长度（仅用易读字符，避免管理员抄错）
    _RESET_ALPHABET = string.ascii_letters.replace("l", "").replace("I", "").replace("O", "") + "23456789"

    @staticmethod
    def reset_password(operator: dict, user_id: int, new_password: str = None,
                       request=None) -> dict:
        """重置用户密码。

        安全约定：
        - 不读取、不返回、不记录原密码（原密码是哈希，本来也读不出明文）；
        - 未提供新密码时由服务端生成一个随机初始密码，**只在本次响应里返回一次**，
          管理员需当场转告用户，之后无法再查；
        - 审计日志只记「重置了某人的密码」，绝不记录密码内容。
        """
        target = sql_db.get_user_by_id(user_id)
        if target is None:
            return AdminService._fail(2001, f"用户不存在: user_id={user_id}")

        generated = False
        if new_password:
            password = str(new_password)
            if len(password) < 6:
                return AdminService._fail(1001, "新密码长度至少 6 位")
            if verify_password(password, target["password_hash"]):
                return AdminService._fail(1001, "新密码不能与原密码相同")
        else:
            password = "".join(secrets.choice(AdminService._RESET_ALPHABET) for _ in range(10))
            generated = True

        sql_db.update_password(user_id, hash_password(password))
        # 管理员重置过的密码必然经过第三方（管理员）之手，且系统生成的那个还打印在
        # 管理员屏幕上 —— 一律要求用户首次登录后自行改掉，避免「重置密码」变成
        # 长期共享凭据。用户改完密码后该标记由 /api/auth/change-password 清除。
        sql_db.set_must_change_password(user_id, True)
        AdminService.write_audit(
            operator, "user.reset_password", "user", user_id,
            detail=f"重置了 {target['username']} 的密码"
                   + ("（系统生成初始密码）" if generated else "（指定新密码）"),
            request=request,
        )
        return AdminService._ok({
            "user_id": user_id,
            "username": target["username"],
            # 仅当密码是系统生成时才回传，便于管理员转告；管理员自己设定的密码不回传
            "initial_password": password if generated else None,
            "generated": generated,
        }, "密码已重置")

    @staticmethod
    def delete_user(operator: dict, user_id: int, request=None) -> dict:
        """删除用户。

        只有「从未产生任何业务数据」的账号才允许物理删除（否则会留下孤儿课程 / 文档 ——
        外键会直接拒绝，这里提前给出可读的拒绝理由）。其余情况引导使用「禁用」：
        禁用可随时恢复，删除不可逆。
        """
        target = sql_db.get_user_by_id(user_id)
        if target is None:
            return AdminService._fail(2001, f"用户不存在: user_id={user_id}")
        guard = AdminService._guard_self_and_last_admin(operator, target, "删除")
        if guard:
            return guard

        refs = sql_db.count_user_references(user_id)
        blocking = {k: v for k, v in refs.items() if v}
        if blocking:
            labels = {
                "owned_courses": "门创建的课程", "documents": "份上传的文档",
                "learning_records": "条学习记录", "favorites": "条收藏",
                "memberships": "条课程成员关系", "answers": "条答题记录",
                "questions": "道创建的题目",
            }
            detail = "、".join(f"{v}{labels.get(k, k)}" for k, v in blocking.items())
            return AdminService._fail(
                2002,
                f"该用户仍有业务数据（{detail}），为保护课程与学习记录不可删除。"
                f"如确需停用，请改用「禁用账号」。",
            )

        sql_db.delete_user(user_id)
        AdminService.write_audit(
            operator, "user.delete", "user", user_id,
            detail=f"删除用户 {target['username']}（{target['role']}，无关联业务数据）",
            request=request,
        )
        return AdminService._ok({"user_id": user_id, "username": target["username"]})

    # ---------- 课程管理 ----------

    @staticmethod
    def list_courses(keyword=None, teacher_id=None, category=None, is_public=None,
                     status=None, governance_status=None, only_without_teacher=False,
                     inactive_days=None, page=1, page_size=20,
                     sort_by=None, sort_order=None) -> dict:
        """全平台课程列表（对比教师端的「我的课程」）"""
        if governance_status and governance_status not in COURSE_GOVERNANCE_STATUS:
            return AdminService._fail(1001, f"治理状态非法：{governance_status}")
        if teacher_id is not None:
            try:
                teacher_id = int(teacher_id)
            except (TypeError, ValueError):
                return AdminService._fail(1001, "teacher_id 非法")
        page = max(int(page or 1), 1)
        page_size = min(max(int(page_size or 20), 1), 100)

        total, rows = sql_db.list_courses_admin(
            keyword=keyword, teacher_id=teacher_id, category=category,
            is_public=is_public, status=status, governance_status=governance_status,
            only_without_teacher=only_without_teacher, inactive_days=inactive_days,
            page=page, page_size=page_size, sort_by=sort_by, sort_order=sort_order,
        )
        items = [AdminService._course_row(r) for r in rows]
        return AdminService._ok({
            "total": total, "page": page, "page_size": page_size, "items": items,
        })

    @staticmethod
    def _course_row(row: dict) -> dict:
        return {
            "course_id": row["course_id"],
            "course_name": row["course_name"],
            "course_code": row.get("course_code"),
            "description": row.get("description"),
            "teacher_id": row.get("teacher_id"),
            "teacher_name": row.get("teacher_name") or "",
            "teacher_active": row.get("teacher_active"),
            "member_count": row.get("member_count", 0),
            "pending_member_count": row.get("pending_member_count", 0),
            "document_count": row.get("document_count", 0),
            "entity_count": row.get("entity_count", 0),
            "relation_count": row.get("relation_count", 0),
            "status": row.get("status", 1),
            "is_public": row.get("is_public", 1),
            "join_mode": row.get("join_mode"),
            "category": row.get("category"),
            "organization": row.get("organization"),
            "governance_status": row.get("governance_status") or "normal",
            "governance_note": row.get("governance_note"),
            "archived_at": row.get("archived_at"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    @staticmethod
    def get_course(course_id: int) -> dict:
        """课程详情（管理员视角）：概览 + 成员 + 文档 + 图谱统计。

        成员/文档/图谱全部**只读复用**既有数据源：
        - 成员：sql_db.list_members（与教师端成员管理同一份查询）
        - 文档：sql_db.list_documents_by_course
        - 图谱：Neo4j 按 course_id 计数（失败则标记 graph_available=False）
        """
        course = sql_db.get_course_admin_detail(course_id)
        if course is None:
            return AdminService._fail(2001, f"课程不存在: course_id={course_id}")

        members = MemberService.list_members(course_id, status=None, role=None,
                                            keyword=None, page=1, page_size=100)
        member_items = members["data"]["items"]
        documents = sql_db.list_documents_by_course(course_id)

        graph = {"node_count": None, "edge_count": None, "available": True}
        try:
            nodes = db.query(
                "MATCH (n:KnowledgePoint {course_id: $cid}) RETURN count(n) AS c",
                {"cid": int(course_id)},
            )
            edges = db.query(
                "MATCH (a:KnowledgePoint {course_id: $cid})-[r]->(b) "
                "WHERE a.course_id = b.course_id RETURN count(r) AS c",
                {"cid": int(course_id)},
            )
            graph["node_count"] = nodes[0]["c"] if nodes else 0
            graph["edge_count"] = edges[0]["c"] if edges else 0
        except Exception as exc:
            _logger.warning("课程 %s 图谱统计失败（Neo4j 不可用？）: %s", course_id, exc)
            graph["available"] = False

        return AdminService._ok({
            "course": AdminService._course_row(course),
            "members": member_items,
            "documents": [
                {
                    "doc_id": d["doc_id"], "file_name": d["file_name"],
                    "file_type": d["file_type"], "file_size": d["file_size"],
                    "parse_status": d["parse_status"], "extract_status": d["extract_status"],
                    "entity_count": d.get("entity_count", 0),
                    "relation_count": d.get("relation_count", 0),
                    "created_at": d.get("created_at"),
                }
                for d in documents
            ],
            "graph": graph,
        })

    @staticmethod
    def set_course_governance(operator: dict, course_id: int, action: str,
                              note: str = None, request=None) -> dict:
        """课程治理动作，分两类，各自只写自己那一维，互不覆盖：

        治理维度（只写 governance_status / archived_at / governance_note）
          - 下架 hide        -> hidden       学生端不再能发现 / 申请 / 加课码加入 / 邀请加入
          - 恢复 restore     -> normal       撤销下架，**课程的业务状态原样保留**
          - 归档 archive     -> archived     冻结留档（同上，可见性一并收回）
          - 取消归档 unarchive -> normal     撤销归档，业务状态同样原样保留

        业务维度（只写 status，不动治理状态）
          - 关闭 close       -> status=0     管理员代教师关闭课程
          - 重新开放 reopen  -> status=1     撤销上一步

        为什么恢复不再把 status 置 1（早期版本如此，是一个真实缺陷）：
        下架若连动 status=0、恢复再连动 status=1，就会把「教师自己关闭的课程」一并打开——
        治理动作覆盖了业务事实，事后也分不清 status=0 是治理造成的还是教师造成的。
        现在 status 这一列**只有** close/reopen 与教师侧会写，治理动作一行都不碰，
        覆盖问题在结构上消失，无需额外字段记录「治理前的状态」。
        下架的可见性效果改由 COURSE_VISIBLE_SQL（status 与 governance_status 的组合）
        在发现页 / 申请 / 加课码 / 邀请 / 公开课元数据五处统一判定。
        """
        course = sql_db.get_course(course_id)
        if course is None:
            return AdminService._fail(2001, f"课程不存在: course_id={course_id}")

        note = (note or "").strip()[:ARCHIVE_REASON_MAX] or None

        if action == "hide":
            # 已归档的课程不允许直接下架：先取消归档，否则 archived_at 会与 hidden 状态并存，
            # 治理工作台里会同时出现在「已下架」和「已归档」两个桶里。
            if (course.get("governance_status") or "normal") == "archived":
                return AdminService._fail(2002, "该课程已归档，请先「取消归档」再下架")
            sql_db.set_course_governance(course_id, "hidden", note=note)
            audit_action, label = "course.hide", "下架"
        elif action == "restore":
            if (course.get("governance_status") or "normal") != "hidden":
                return AdminService._fail(2002, "该课程当前不是「已下架」状态，无需恢复")
            sql_db.set_course_governance(course_id, "normal", note="", archived_at="")
            audit_action, label = "course.restore", "恢复"
        elif action == "archive":
            sql_db.set_course_governance(course_id, "archived", note=note,
                                         archived_at=_now_str())
            audit_action, label = "course.archive", "归档"
        elif action == "unarchive":
            if (course.get("governance_status") or "normal") != "archived":
                return AdminService._fail(2002, "该课程当前不是「已归档」状态，无需取消归档")
            sql_db.set_course_governance(course_id, "normal", note="", archived_at="")
            audit_action, label = "course.unarchive", "取消归档"
        elif action == "close":
            if course.get("status") == 0:
                return AdminService._fail(2002, "该课程的业务状态已经是「已关闭」")
            if (course.get("governance_status") or "normal") != "normal":
                return AdminService._fail(2002, "该课程已被平台下架或归档，无需再关闭")
            sql_db.update_course(course_id, status=0)
            audit_action, label = "course.close", "关闭"
            note = None                      # 业务动作不写治理备注
        elif action == "reopen":
            if course.get("status") == 1:
                return AdminService._fail(2002, "该课程的业务状态已经是「开放」")
            sql_db.update_course(course_id, status=1)
            audit_action, label = "course.reopen", "重新开放"
            note = None
        else:
            return AdminService._fail(1001, f"未知的治理动作：{action}")

        updated = sql_db.get_course(course_id) or {}
        AdminService.write_audit(
            operator, audit_action, "course", course_id,
            detail=f"{label}课程「{course['course_name']}」"
                   + (f"，备注：{note}" if note else ""),
            request=request,
        )
        return AdminService._ok({
            "course_id": course_id,
            "governance_status": updated.get("governance_status") or "normal",
            "status": updated.get("status"),
        }, f"课程已{label}")

    @staticmethod
    def transfer_course(operator: dict, course_id: int, new_teacher_id: int,
                        request=None) -> dict:
        """转移课程负责人（新负责人必须是 role=teacher 的账号）"""
        course = sql_db.get_course(course_id)
        if course is None:
            return AdminService._fail(2001, f"课程不存在: course_id={course_id}")
        try:
            new_teacher_id = int(new_teacher_id)
        except (TypeError, ValueError):
            return AdminService._fail(1001, "新负责人 user_id 非法")

        new_teacher = sql_db.get_user_by_id(new_teacher_id)
        if new_teacher is None:
            return AdminService._fail(2001, f"新负责人不存在: user_id={new_teacher_id}")
        if new_teacher["role"] != "teacher":
            return AdminService._fail(
                1001, f"课程负责人必须是教师账号，{new_teacher['username']} 当前角色为 {new_teacher['role']}")
        if not new_teacher.get("is_active", 1):
            return AdminService._fail(1001, f"{new_teacher['username']} 已被禁用，不能作为课程负责人")
        if course["teacher_id"] == new_teacher_id:
            return AdminService._ok({"course_id": course_id, "teacher_id": new_teacher_id},
                                    "该教师已是课程负责人")

        old_teacher = sql_db.get_user_by_id(course["teacher_id"]) or {}
        sql_db.transfer_course_owner(course_id, course["teacher_id"], new_teacher_id)
        AdminService.write_audit(
            operator, "course.transfer", "course", course_id,
            detail=f"课程「{course['course_name']}」负责人由 "
                   f"{old_teacher.get('username') or course['teacher_id']} 转移给 "
                   f"{new_teacher['username']}（原负责人保留为协作教师）",
            request=request,
        )
        return AdminService._ok({
            "course_id": course_id, "teacher_id": new_teacher_id,
            "teacher_name": new_teacher.get("display_name") or new_teacher["username"],
        })

    @staticmethod
    def delete_course(operator: dict, course_id: int, confirm: bool = False,
                      request=None) -> dict:
        """删除课程（复用 CourseService 的级联清理：Neo4j + SQLite + 本地文件）。

        管理员删除课程不绕过二次确认：confirm 必须显式为 true。
        """
        from .course_service import CourseService   # 局部导入，避免模块级循环依赖

        course = sql_db.get_course(course_id)
        if course is None:
            return AdminService._fail(2001, f"课程不存在: course_id={course_id}")
        if not confirm:
            return AdminService._fail(2008, "删除课程需要二次确认（confirm=true）")

        result = CourseService.delete_course(course_id, confirm=True)
        if not result["ok"]:
            return result

        AdminService.write_audit(
            operator, "course.delete", "course", course_id,
            detail=f"删除课程「{course['course_name']}」"
                   f"（文档 {result['data'].get('removed_documents', 0)} 份、"
                   f"图谱节点 {result['data'].get('removed_nodes', 0)} 个）",
            request=request,
        )
        return result

    # ---------- 课程治理视图 ----------

    @staticmethod
    def governance_overview() -> dict:
        """治理工作台：把「需要管理员介入」的课程自动挑出来。

        全部基于既有字段的真实判定（不臆造评分）：
        - 已下架 / 已归档：管理员处置过的
        - 无教师：teacher_id 为空或指向已不存在的用户
        - 教师已禁用：课程还在但负责人登录不了
        - 待审核积压：pending 成员数 > 0
        - 长期无活动：updated_at 早于 90 天
        - 空课程：没有文档且没有成员（除负责人）
        """
        inactive_days = 90
        buckets = {}

        def _collect(name, label, desc, **kwargs):
            total, rows = sql_db.list_courses_admin(page=1, page_size=50, **kwargs)
            buckets[name] = {
                "key": name, "label": label, "description": desc,
                "total": total, "items": [AdminService._course_row(r) for r in rows],
            }

        _collect("hidden", "已下架课程", "已从学生端隐藏，学生无法发现或加入",
                 governance_status="hidden")
        _collect("archived", "已归档课程", "已冻结归档，保留数据供追溯",
                 governance_status="archived")
        _collect("without_teacher", "无负责人课程", "课程没有有效的创建教师，需指派负责人",
                 only_without_teacher=True)
        _collect("inactive", "长期无活动课程", f"超过 {inactive_days} 天没有任何更新",
                 inactive_days=inactive_days)

        # 教师已禁用：需要在 Python 侧过滤（teacher_active 由联表带出）
        _, rows = sql_db.list_courses_admin(page=1, page_size=200)
        disabled_teacher = [AdminService._course_row(r) for r in rows
                            if r.get("teacher_active") == 0]
        buckets["teacher_disabled"] = {
            "key": "teacher_disabled", "label": "负责人被禁用的课程",
            "description": "课程负责人的账号已被禁用，课程无法被正常管理",
            "total": len(disabled_teacher), "items": disabled_teacher,
        }

        pending_total, pending_rows = sql_db.list_courses_admin(page=1, page_size=50,
                                                               sort_by="course_id")
        with_pending = [AdminService._course_row(r) for r in pending_rows
                        if r.get("pending_member_count")]
        buckets["pending_review"] = {
            "key": "pending_review", "label": "有待审核申请的课程",
            "description": "课程存在待教师审核的加入申请",
            "total": len(with_pending), "items": with_pending,
        }

        # 空课程：无文档、无学生成员
        empty = [AdminService._course_row(r) for r in rows
                 if not r.get("document_count")
                 and (r.get("member_count") or 0) <= 1]
        buckets["empty"] = {
            "key": "empty", "label": "空课程", "description": "没有文档、也没有学生的课程",
            "total": len(empty), "items": empty,
        }

        return AdminService._ok({
            "governance": sql_db.count_courses_by_governance(),
            "buckets": list(buckets.values()),
        })

    # ---------- 资源（文档）管理 ----------

    @staticmethod
    def list_documents(keyword=None, course_id=None, file_type=None,
                       parse_status=None, extract_status=None, uploader_id=None,
                       page=1, page_size=20, sort_by=None, sort_order=None) -> dict:
        if course_id is not None:
            try:
                course_id = int(course_id)
            except (TypeError, ValueError):
                return AdminService._fail(1001, "course_id 非法")
        page = max(int(page or 1), 1)
        page_size = min(max(int(page_size or 20), 1), 100)

        total, rows = sql_db.list_documents_admin(
            keyword=keyword, course_id=course_id, file_type=file_type,
            parse_status=parse_status, extract_status=extract_status,
            uploader_id=uploader_id, page=page, page_size=page_size,
            sort_by=sort_by, sort_order=sort_order,
        )
        return AdminService._ok({
            "total": total, "page": page, "page_size": page_size,
            "items": [
                {
                    "doc_id": r["doc_id"],
                    "course_id": r["course_id"],
                    "course_name": r.get("course_name") or "",
                    "uploader_id": r.get("uploader_id"),
                    "uploader_name": r.get("uploader_name") or "",
                    "file_name": r["file_name"],
                    "file_type": r["file_type"],
                    "file_size": r.get("file_size", 0),
                    "parse_status": r.get("parse_status"),
                    "extract_status": r.get("extract_status"),
                    "error_message": r.get("error_message"),
                    "chunk_count": r.get("chunk_count", 0),
                    "entity_count": r.get("entity_count", 0),
                    "relation_count": r.get("relation_count", 0),
                    "created_at": r.get("created_at"),
                    "updated_at": r.get("updated_at"),
                }
                for r in rows
            ],
        })

    @staticmethod
    def delete_document(operator: dict, doc_id: int, request=None) -> dict:
        """删除文档资源。

        整体复用 DocumentService.delete_document：它已按顺序清理
        Neo4j 图谱 → 向量 → 学习记录 → 收藏 → 题库 → 本地文件 → SQLite 记录，
        任何一步失败都会返回 5002 而不是假装成功。
        管理员删除**不改变**这套完整性规则，只是把「仅课程教师」的入口换成
        「仅管理员」——不会为了管理员方便而留下孤立文件或图库残留。
        """
        doc = sql_db.get_document(int(doc_id)) if str(doc_id).lstrip("-").isdigit() else None
        if doc is None:
            return AdminService._fail(2002, f"文档不存在: doc_id={doc_id}")

        course = sql_db.get_course(doc["course_id"]) or {}
        # 走管理员专用入口 delete_document_by_admin：它要求传入已认证的管理员主体
        # 并自行再断言一次角色，而不是接受一个 as_admin=True 之类的布尔旁路。
        # 级联清理与教师端共用同一实现，完整性约束完全一致。
        result = DocumentService.delete_document_by_admin(doc_id, operator)
        if not result["ok"]:
            AdminService.write_audit(
                operator, "resource.delete", "document", doc_id, result="failure",
                detail=f"删除文档「{doc['file_name']}」失败：{result['message']}",
                request=request,
            )
            return result

        AdminService.write_audit(
            operator, "resource.delete", "document", doc_id,
            detail=f"删除文档「{doc['file_name']}」（课程「{course.get('course_name', doc['course_id'])}」），"
                   f"已清理图谱节点 {result['data'].get('removed_nodes', 0)} 个",
            request=request,
        )
        return result

    @staticmethod
    def list_extraction_tasks(course_id=None, status=None, keyword=None,
                              page=1, page_size=20) -> dict:
        """知识抽取任务监控（以 t_document 为任务事实来源，详见 sql_db.list_extraction_tasks）"""
        if status and status not in ("pending", "processing", "success", "failed"):
            return AdminService._fail(1001, f"任务状态非法：{status}")
        if course_id is not None:
            try:
                course_id = int(course_id)
            except (TypeError, ValueError):
                return AdminService._fail(1001, "course_id 非法")
        page = max(int(page or 1), 1)
        page_size = min(max(int(page_size or 20), 1), 100)

        total, rows = sql_db.list_extraction_tasks(course_id, status, keyword,
                                                  page, page_size)
        items = []
        for r in rows:
            duration = _duration_seconds(r.get("started_at"), r.get("finished_at"))
            items.append({
                "task_id": r["task_id"],
                "course_id": r["course_id"],
                "course_name": r.get("course_name") or "",
                "file_name": r["file_name"],
                "status": r.get("task_status"),
                "parse_status": r.get("parse_status"),
                "extract_status": r.get("extract_status"),
                "entity_count": r.get("entity_count", 0),
                "relation_count": r.get("relation_count", 0),
                "chunk_count": r.get("chunk_count", 0),
                "started_at": r.get("started_at"),
                "finished_at": r.get("finished_at"),
                "duration_seconds": duration,
                "error_message": r.get("error_message"),
            })
        return AdminService._ok({
            "total": total, "page": page, "page_size": page_size,
            "summary": sql_db.count_extraction_tasks(),
            "items": items,
        })

    # ---------- 系统监控 ----------

    @staticmethod
    def system_status() -> dict:
        """各组件真实状态 + 运行指标。

        「真实」的含义：每项都实际探测一次，探测不到就返回 available=False 并带上原因，
        绝不因为「理论上应该正常」而硬编码 ok。文件存储探测同时给出目录路径与可写性，
        因为「能读不能写」是上传失败最常见的原因，而只看 exists 是看不出来的。
        """
        components = []

        # FastAPI：能执行到这里就说明进程活着（这是唯一可以肯定的结论）
        components.append({
            "key": "api", "label": "API 服务 (FastAPI)", "available": True,
            "detail": f"服务运行中，版本 {settings.VERSION}",
        })

        # SQLite
        try:
            sql_db._query_one("SELECT count(*) AS c FROM t_user")
            components.append({
                "key": "sqlite", "label": "关系库 (SQLite)", "available": True,
                "detail": f"{settings.SQLITE_DB_PATH}",
            })
        except Exception as exc:
            components.append({
                "key": "sqlite", "label": "关系库 (SQLite)", "available": False,
                "detail": f"连接失败：{exc}",
            })

        # Neo4j
        neo4j_ok = AdminService._neo4j_available()
        components.append({
            "key": "neo4j", "label": "图数据库 (Neo4j)", "available": neo4j_ok,
            "detail": settings.NEO4J_URI if neo4j_ok else
                      f"无法连接 {settings.NEO4J_URI}（图谱与部分统计会退化）",
        })

        # LLM：只校验配置是否齐备，不做真实调用（真实调用要花钱且会拖慢监控页）
        llm_configured = bool(settings.LLM_API_KEY) and \
            settings.LLM_API_KEY != "your-api-key-here"
        components.append({
            "key": "llm", "label": "大模型 (LLM)", "available": llm_configured,
            # 措辞刻意保守：配置齐备 ≠ 上游可用，这里只能确认「已配置」
            "detail": (f"已配置 {settings.LLM_MODEL}（@{settings.LLM_API_BASE}），"
                       f"未发起真实调用") if llm_configured else "未配置 LLM_API_KEY",
        })

        # Embedding（RAG 向量检索依赖）
        embedding_configured = bool(settings.EMBEDDING_API_KEY)
        components.append({
            "key": "embedding", "label": "向量化 (Embedding)", "available": embedding_configured,
            "detail": (f"已配置 {settings.EMBEDDING_MODEL}") if embedding_configured
                      else "未配置 EMBEDDING_API_KEY：RAG 将退回关键词检索",
        })

        # 文件存储
        components.append(AdminService._storage_status())

        return AdminService._ok({
            "components": components,
            "graph_available": neo4j_ok,
            "metrics": metrics_snapshot(),
            "counts": sql_db.admin_platform_counts(),
            "extraction": sql_db.count_extraction_tasks(),
            "document_status": sql_db.count_documents_by_status(),
            "table_rows": AdminService._table_rows(),
        })

    @staticmethod
    def _storage_status() -> dict:
        """上传目录状态：存在性 + 是否可写（可写性用真实的临时文件探针验证）"""
        import os
        import tempfile

        upload_dir = settings.UPLOAD_DIR
        if not os.path.isdir(upload_dir):
            return {
                "key": "storage", "label": "文件存储", "available": False,
                "detail": f"目录不存在：{upload_dir}",
            }
        probe_path = None
        try:
            fd, probe_path = tempfile.mkstemp(prefix=".kg_write_probe_", dir=upload_dir)
            os.close(fd)
            os.remove(probe_path)
            return {
                "key": "storage", "label": "文件存储", "available": True,
                "detail": f"{upload_dir}（可写）",
            }
        except Exception as exc:
            return {
                "key": "storage", "label": "文件存储", "available": False,
                "detail": f"目录存在但不可写：{exc}",
            }
        finally:
            if probe_path and os.path.exists(probe_path):
                try:
                    os.remove(probe_path)
                except OSError:
                    pass

    @staticmethod
    def _table_rows() -> list:
        """各表行数（运维视角的库体积概览）"""
        tables = (
            "t_user", "t_user_profile", "t_course", "t_course_member", "t_course_invite",
            "t_document", "t_learning_record", "t_student_favorite", "t_kp_embedding",
            "t_question", "t_answer_record", "t_admin_audit_log",
        )
        rows = []
        for t in tables:
            try:
                rows.append({"table": t,
                             "count": sql_db._query_one(f"SELECT count(*) AS c FROM {t}")["c"]})
            except Exception:
                rows.append({"table": t, "count": None})
        return rows

    # ---------- 审计日志 ----------

    @staticmethod
    def list_audit_logs(operator_id=None, action=None, target_type=None,
                        result=None, keyword=None, start_time=None, end_time=None,
                        page=1, page_size=20) -> dict:
        page = max(int(page or 1), 1)
        page_size = min(max(int(page_size or 20), 1), 200)
        if operator_id is not None:
            try:
                operator_id = int(operator_id)
            except (TypeError, ValueError):
                return AdminService._fail(1001, "operator_id 非法")

        total, rows = sql_db.list_audit_logs(
            operator_id=operator_id, action=action, target_type=target_type,
            result=result, keyword=keyword, start_time=start_time, end_time=end_time,
            page=page, page_size=page_size,
        )
        return AdminService._ok({
            "total": total, "page": page, "page_size": page_size, "items": rows,
            # 可选筛选项由后端下发，避免前端硬编码动作名与后端不同步
            "actions": list(ADMIN_ACTIONS),
            "target_types": ["user", "course", "document", "settings"],
            "action_labels": ACTION_LABELS,
        })


def _now_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _duration_seconds(started_at, finished_at):
    """两次本地时间字符串之间的秒数；任一缺失返回 None（前端显示「—」而不是 0）"""
    if not started_at or not finished_at:
        return None
    from datetime import datetime
    try:
        start = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
        end = datetime.strptime(finished_at, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None
    return max(int((end - start).total_seconds()), 0)


# 审计动作的中文标签（前端筛选下拉与列表展示都取这里，避免两处各写一份）
ACTION_LABELS = {
    "admin.login": "管理员登录",
    "user.update": "编辑用户资料",
    "user.enable": "启用账号",
    "user.disable": "禁用账号",
    "user.role_change": "修改角色",
    "user.reset_password": "重置密码",
    "user.delete": "删除用户",
    "course.hide": "下架/关闭课程",
    "course.restore": "恢复/重新开放课程",
    "course.archive": "归档课程",
    "course.unarchive": "取消归档",
    "course.transfer": "转移课程负责人",
    "course.delete": "删除课程",
    "resource.delete": "删除文档资源",
    "settings.update": "修改平台设置",
}
