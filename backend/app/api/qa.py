"""
智能问答 API
"""
from fastapi import APIRouter
from pydantic import BaseModel
from ..services.qa_service import QAService

router = APIRouter(prefix="/api/qa", tags=["智能问答"])

qa_service = QAService()


class QuestionRequest(BaseModel):
    question: str
    course_id: str = None


class QuestionResponse(BaseModel):
    question: str
    answer: str
    sources: list = []


@router.post("/ask", response_model=QuestionResponse)
async def ask_question(request: QuestionRequest):
    """
    学生向AI提问（RAG模式）
    """
    answer = await qa_service.ask(request.question, request.course_id)

    # 获取引用来源
    sources = qa_service.search_related_knowledge(request.question, request.course_id)

    return QuestionResponse(
        question=request.question,
        answer=answer,
        sources=sources
    )
