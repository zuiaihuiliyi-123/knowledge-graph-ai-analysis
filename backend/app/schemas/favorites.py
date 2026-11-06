"""知识点收藏请求模型。"""

from pydantic import BaseModel


class FavoriteCreate(BaseModel):
    course_id: str
    # 必须写 str | None：Pydantic v2 下 `str = None` 不接受显式 null，
    # 而前端在未选文档时会传 null，会直接 422。
    document_id: str | None = None
    kp_id: str
