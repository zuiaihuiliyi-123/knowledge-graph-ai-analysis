"""学习记录请求模型。"""

from pydantic import BaseModel


class MarkRequest(BaseModel):
    course_id: str
    # str | None：Pydantic v2 下 `str = None` 不接受显式 null（前端未选文档时会传 null）
    document_id: str | None = None
    kp_id: str
    status: str = "MASTERED"     # MASTERED / LEARNING / RECOMMENDED
    mastery_level: int = 100     # 0-100
