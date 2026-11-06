"""智能问答请求模型。"""

from pydantic import BaseModel


class QuestionRequest(BaseModel):
    question: str
    # 前端未选择课程/文档时会显式传 null，须用 Optional 接受 null；
    # 旧写法 str = None 在 Pydantic v2 下会把 null 判为「不是合法字符串」→ 422。
    course_id: str | None = None
    document_id: str | None = None
