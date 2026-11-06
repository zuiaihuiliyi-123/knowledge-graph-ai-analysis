"""图谱编辑请求模型（节点 / 关系）。"""

from pydantic import BaseModel


class NodeCreate(BaseModel):
    name: str
    category: str = "概念"
    description: str = ""


class NodeUpdate(BaseModel):
    # str | None：Pydantic v2 下 `str = None` 不接受显式 null（语义均为「不修改该字段」）
    name: str | None = None
    category: str | None = None
    description: str | None = None


class EdgeCreate(BaseModel):
    source: str  # 源知识点 kp_id
    target: str  # 目标知识点 kp_id
    type: str = "RELATED_TO"
