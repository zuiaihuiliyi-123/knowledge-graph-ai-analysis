"""学习路径请求模型。"""

from pydantic import BaseModel


class TargetPathRequest(BaseModel):
    target: str  # 目标知识点
    course_id: str | None = None
    document_id: str | None = None
