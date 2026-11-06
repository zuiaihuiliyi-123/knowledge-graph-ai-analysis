"""主观题批改请求模型（单题 / 批量）。"""

from pydantic import BaseModel


class GradeRequest(BaseModel):
    score: float
    comment: str | None = None


class BatchGradeRequest(BaseModel):
    record_ids: list
    score: float
    comment: str | None = None
