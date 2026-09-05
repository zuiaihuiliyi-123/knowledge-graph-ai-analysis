"""
智能问答 API（对齐规划文档 6.4，响应格式统一 {code, message, data, timestamp}）
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..core.response import success
from ..core.dependencies import get_current_user
from ..services.qa_service import QAService

router = APIRouter(prefix="/api/v1/qa", tags=["智能问答"])

qa_service = QAService()


class QuestionRequest(BaseModel):
    question: str
    # 前端未选择课程/文档时会显式传 null，须用 Optional 接受 null；
    # 旧写法 str = None 在 Pydantic v2 下会把 null 判为「不是合法字符串」→ 422。
    course_id: str | None = None
    document_id: str | None = None


@router.post("/ask")
async def ask_question(request: QuestionRequest, current_user: dict = Depends(get_current_user)):
    """
    学生向AI提问（RAG模式；Phase 8C：按 course_id + document_id 限定候选知识点范围）
    """
    answer = await qa_service.ask(request.question, request.course_id, request.document_id)

    # 获取引用来源（结构化：kp_id/name/category/description，供前端"证据链"展示）
    sources = qa_service.search_related_nodes(request.question, request.course_id, request.document_id)

    return success({
        "question": request.question,
        "answer": answer,
        "sources": sources,
    })
