"""
智能问答 API（对齐规划文档 6.4，响应格式统一 {code, message, data, timestamp}）
"""
from fastapi import APIRouter
from pydantic import BaseModel

from ..core.response import success
from ..services.qa_service import QAService

router = APIRouter(prefix="/api/v1/qa", tags=["智能问答"])

qa_service = QAService()


class QuestionRequest(BaseModel):
    question: str
    course_id: str = None


@router.post("/ask")
async def ask_question(request: QuestionRequest):
    """
    学生向AI提问（RAG模式）
    """
    answer = await qa_service.ask(request.question, request.course_id)

    # 获取引用来源
    sources = qa_service.search_related_knowledge(request.question, request.course_id)

    return success({
        "question": request.question,
        "answer": answer,
        "sources": sources,
    })
