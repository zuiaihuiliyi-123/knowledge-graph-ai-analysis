"""个人资料请求模型。"""

from pydantic import BaseModel


class ProfileUpdate(BaseModel):
    # 全部可选；未传的字段保持不变，传空串表示清空该字段
    avatar_url: str | None = None
    real_name: str | None = None
    nickname: str | None = None
    gender: str | None = None
    school: str | None = None
    college: str | None = None
    bio: str | None = None
    student_no: str | None = None
    major: str | None = None
    grade: str | None = None
    class_name: str | None = None
    teacher_no: str | None = None
    title: str | None = None
    research_area: str | None = None
