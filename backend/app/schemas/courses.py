"""课程中心请求模型（创建 / 更新 / 加课码 / 申请 / 加入方式）。"""

from pydantic import BaseModel


# 注意：可选字段必须写成 `str | None = None`。
# Pydantic v2 下 `field: str = None` 只表示「默认 None，类型仍是 str」，
# 客户端显式传 null 会被判为 422（Input should be a valid string）——
# 前端「课程简介留空」正好会传 null，历史实现因此存在一个隐藏的创建失败路径。
# 同类写法问题在 app/schemas/qa.py 的 QuestionRequest 上已有注释记录。
class CourseCreate(BaseModel):
    course_name: str
    course_code: str | None = None
    description: str | None = None
    category: str | None = None
    organization: str | None = None
    cover: str | None = None
    join_mode: str = "approval"      # auto / approval / closed
    is_public: int = 1


class CourseUpdate(BaseModel):
    course_name: str | None = None
    course_code: str | None = None
    description: str | None = None
    category: str | None = None
    organization: str | None = None
    cover: str | None = None
    join_mode: str | None = None
    is_public: int | None = None


class JoinByCodeRequest(BaseModel):
    join_code: str
    reason: str | None = None


class ApplyRequest(BaseModel):
    reason: str | None = None


class JoinModeUpdate(BaseModel):
    join_mode: str
    is_public: int | None = None
