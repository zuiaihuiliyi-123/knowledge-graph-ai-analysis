"""智能问答请求模型。"""

from pydantic import BaseModel


class QuestionRequest(BaseModel):
    question: str
    # 前端未选择课程/文档时会显式传 null，须用 Optional 接受 null；
    # 旧写法 str = None 在 Pydantic v2 下会把 null 判为「不是合法字符串」→ 422。
    #
    # 同时接受 int：Pydantic v2 不做 int→str 隐式转换，只声明 str 时
    # {"course_id": 63} 会直接 422（前端 DocumentKnowledgePanel 的 props 允许 Number）。
    course_id: str | int | None = None
    document_id: str | int | None = None
