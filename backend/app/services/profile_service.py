"""用户资料服务（个人中心）

设计要点：
- 资料与认证完全分离：t_user 的 username / password_hash / role 本模块只读不写，
  所有可编辑字段落在 t_user_profile；
- 所有资料字段可选、可为空；
- 按角色做字段白名单——学生提交 teacher_no 会被静默丢弃而不是报错，
  这样前端复用同一个表单对象也不会误写越权字段。

统一返回 {"ok": bool, "code": int, "message": str, "data": dict}
"""
import os

from ..core.config import settings
from ..core.sql_database import GENDERS, sql_db

# 字段长度上限（超出返回 4007，避免被塞入超长文本）
MAX_LEN = {
    "real_name": 50, "nickname": 50, "school": 100, "college": 100,
    "bio": 200, "student_no": 50, "major": 50, "grade": 20,
    "class_name": 50, "teacher_no": 50, "title": 50, "research_area": 100,
}

# 三组可编辑字段，按角色取并集
SHARED_FIELDS = {"avatar_url", "real_name", "nickname", "gender", "school", "bio"}
STUDENT_FIELDS = {"student_no", "college", "major", "grade", "class_name"}
TEACHER_FIELDS = {"teacher_no", "college", "title", "research_area"}

# 输出给前端的资料字段（保证前端拿到的键集合恒定，缺省为 None）
OUTPUT_FIELDS = ("avatar_url", "real_name", "nickname", "gender", "school",
                 "college", "bio", "student_no", "major", "grade", "class_name",
                 "teacher_no", "title", "research_area")


class ProfileService:
    """个人资料读写与展示名解析"""

    @staticmethod
    def editable_fields(role: str) -> set:
        """该角色可编辑的字段集合（白名单即权限边界）"""
        fields = set(SHARED_FIELDS)
        if role == "student":
            fields |= STUDENT_FIELDS
        elif role == "teacher":
            fields |= TEACHER_FIELDS
        # admin 只保留共享字段：管理员没有学籍，也不该有「职称 / 研究方向」这类教师属性。
        # 这里刻意写成显式分支而不是 else，避免将来新增角色时被默默当成教师。
        return fields

    @staticmethod
    def _shape(row: dict) -> dict:
        """把 LEFT JOIN 结果整理成固定键集合的响应体"""
        data = {
            "user_id": row["user_id"],
            "username": row["username"],
            "role": row["role"],
            "email": row.get("email"),
            "is_active": row.get("is_active"),
            "created_at": row.get("user_created_at"),
            "profile_updated_at": row.get("profile_updated_at"),
        }
        for f in OUTPUT_FIELDS:
            data[f] = row.get(f)
        return data

    @staticmethod
    def get_profile(user_id: int) -> dict:
        row = sql_db.get_user_profile(user_id)
        if row is None:
            return {"ok": False, "code": 2001, "message": f"用户不存在: user_id={user_id}"}
        data = ProfileService._shape(row)
        # 库里指针为空时回退到磁盘上的头像文件，避免「图还在但显示不出来」
        data["avatar_url"] = ProfileService.avatar_url_for(user_id, data.get("avatar_url"))
        return {"ok": True, "code": 0, "message": "success", "data": data}

    @staticmethod
    def update_profile(user_id: int, role: str, fields: dict) -> dict:
        """更新资料：只写「白名单 ∩ 请求字段」，其余静默忽略。

        明确传入的空串按「清空该字段」处理（写 NULL），未传入的字段保持不变。
        """
        allowed = ProfileService.editable_fields(role)
        payload = {}
        for key, value in (fields or {}).items():
            if key not in allowed:
                continue                      # 角色不匹配的字段：丢弃而非报错
            if value is None:
                payload[key] = None
                continue
            text = str(value).strip()
            if text == "":
                payload[key] = None           # 空串 = 清空
                continue
            limit = MAX_LEN.get(key, 100)
            if len(text) > limit:
                return {"ok": False, "code": 4007,
                        "message": f"字段 {key} 过长（最大 {limit} 字符）"}
            if key == "gender" and text not in GENDERS:
                return {"ok": False, "code": 4007,
                        "message": f"性别取值非法：{text}"}
            payload[key] = text

        sql_db.upsert_user_profile(user_id, **payload)
        return ProfileService.get_profile(user_id)

    # ---------- 头像 ----------

    @staticmethod
    def save_avatar(user_id: int, content: bytes, filename: str) -> dict:
        """保存头像：服务端生成文件名（绝不用上传的原始文件名），替换时删除旧文件"""
        ext = os.path.splitext(filename or "")[1].lower()
        if ext not in settings.ALLOWED_AVATAR_EXTENSIONS:
            return {"ok": False, "code": 1001,
                    "message": "头像格式不支持（仅 jpg / jpeg / png / webp）"}
        if not content:
            return {"ok": False, "code": 1003, "message": "上传文件为空"}
        if len(content) > settings.MAX_AVATAR_SIZE:
            return {"ok": False, "code": 1002,
                    "message": f"头像过大（最大 {settings.MAX_AVATAR_SIZE // 1024 // 1024}MB）"}

        os.makedirs(settings.AVATAR_DIR, exist_ok=True)
        old = ProfileService._find_avatar(user_id)
        new_name = f"{user_id}_{_timestamp()}{ext}"
        new_path = os.path.join(settings.AVATAR_DIR, new_name)
        try:
            with open(new_path, "wb") as f:
                f.write(content)
        except OSError as e:
            return {"ok": False, "code": 5001, "message": f"头像保存失败: {e}"}

        if old and os.path.basename(old) != new_name:
            try:
                os.remove(old)
            except OSError:
                pass

        # 带版本参数，确保浏览器不会命中旧头像缓存
        url = ProfileService._url_of_avatar_file(new_path)
        sql_db.upsert_user_profile(user_id, avatar_url=url)
        return {"ok": True, "code": 0, "message": "success",
                "data": {"avatar_url": url}}

    @staticmethod
    def _find_avatar(user_id: int) -> str:
        """在头像目录中找该用户当前的头像文件（按文件名前缀匹配，取最新一个）"""
        if not os.path.isdir(settings.AVATAR_DIR):
            return None
        prefix = f"{user_id}_"
        candidates = [f for f in os.listdir(settings.AVATAR_DIR)
                      if f.startswith(prefix) and not f.endswith(".tmp")]
        if not candidates:
            return None
        candidates.sort()
        return os.path.join(settings.AVATAR_DIR, candidates[-1])

    @staticmethod
    def avatar_path(user_id: int) -> str:
        """供 FileResponse 使用；无头像返回 None"""
        return ProfileService._find_avatar(user_id)

    @staticmethod
    def delete_avatar(user_id: int) -> dict:
        """删除头像：移除磁盘上的头像文件并把库中指针置空。"""
        if os.path.isdir(settings.AVATAR_DIR):
            prefix = f"{user_id}_"
            for name in os.listdir(settings.AVATAR_DIR):
                if name.startswith(prefix) and not name.endswith(".tmp"):
                    try:
                        os.remove(os.path.join(settings.AVATAR_DIR, name))
                    except OSError:
                        pass
        sql_db.upsert_user_profile(user_id, avatar_url=None)
        return {"ok": True, "code": 0, "message": "success", "data": {"avatar_url": None}}

    @staticmethod
    def _url_of_avatar_file(path: str) -> str:
        """由头像文件路径拼出直链。

        文件名格式为 {user_id}_{时间戳}{扩展名}，时间戳（含微秒）兼作版本参数，
        确保换头像后浏览器不会继续命中旧图缓存。
        """
        stem = os.path.splitext(os.path.basename(path))[0]  # 2_20260913133738159050
        user_id, _, version = stem.rpartition("_")
        return f"/api/v1/profile/avatar/{user_id}?v={version}"

    @staticmethod
    def avatar_url_for(user_id: int, stored: str = None) -> str:
        """取用户头像直链：库里有值就用库里的，为空则回退到扫描头像目录。

        回退为什么必要：avatar_url 只是「指向哪个文件」的指针，头像文件本身按
        {user_id}_{时间戳}{扩展名} 独立存在磁盘上。app.db 曾是 git 跟踪的二进制文件
        （现已移出跟踪，见 .gitignore），在跟踪期间发生过被合并脚本整体覆盖、
        指针丢失而图片还在的事故（见 backup/dbmerge_20260917/），此时扫目录仍能取到
        最新头像，用户不必重新上传。注意 _find_avatar 取的是字典序最后一个，
        而文件名前缀时间戳保证字典序 = 时间序，故结果即最新一张。
        """
        if stored:
            return stored
        path = ProfileService._find_avatar(user_id)
        return ProfileService._url_of_avatar_file(path) if path else None

    # ---------- 展示名 ----------

    @staticmethod
    def display_name_of(row: dict) -> str:
        """展示名优先级：昵称 > 真实姓名 > 用户表 display_name > 用户名"""
        if not row:
            return ""
        return (row.get("nickname") or row.get("real_name")
                or row.get("display_name") or row.get("username") or "")

    @staticmethod
    def batch_display_names(user_ids: list) -> dict:
        """批量取展示名，返回 {user_id: name}（成员列表 / 教学监测用）"""
        profiles = sql_db.list_user_profiles(user_ids)
        return {uid: ProfileService.display_name_of(row) for uid, row in profiles.items()}


def _timestamp() -> str:
    """文件名时间戳（含微秒）。

    必须带微秒：只用「秒」的话，同一秒内连续两次上传会生成同名文件，
    头像 URL 不变 → 浏览器继续显示缓存里的旧头像。同时保留字典序 = 时间序，
    这样 _find_avatar 取「最后一个」就是最新头像。
    """
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d%H%M%S%f")
