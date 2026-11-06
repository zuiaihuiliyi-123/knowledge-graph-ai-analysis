"""管理员端知识融合请求模型（扫描 / 审核 / 应用 / 撤销）。"""

from pydantic import BaseModel


class ScanBody(BaseModel):
    course_id: int
    document_id: int
    persist: bool = False
    use_llm: bool = False


class ReviewBody(BaseModel):
    course_id: int
    document_id: int
    action: str          # accept / reject / defer
    comment: str | None = None


class ApplyBody(BaseModel):
    course_id: int
    document_id: int
    candidate_ids: list[int] | None = None
    dry_run: bool = True
    confirm: bool = False


class RevokeBody(BaseModel):
    course_id: int
    document_id: int
    confirm: bool = False
