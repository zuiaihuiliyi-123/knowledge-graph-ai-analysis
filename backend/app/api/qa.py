"""
智能问答 API（对齐规划文档 6.4，响应格式统一 {code, message, data, timestamp}）
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..core.response import success, error
from ..core.dependencies import get_current_user
from ..core.permissions import Permissions
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

    课程中心改造：
    - 显式传了 course_id：先校验课程成员身份，非成员直接 4003（不进入检索）；
    - 未传 course_id（AI 悬浮窗在未选课程时的调用）：检索范围收敛为
      「当前用户可访问的课程」，不再退化成全库扫描。
    """
    allowed_ids = Permissions.allowed_course_ids(current_user)
    if request.course_id:
        try:
            cid = int(request.course_id)
        except (TypeError, ValueError):
            return error(1001, f"course_id 必须为整数，收到: {request.course_id}")
        perm = Permissions.require_course_content(cid, current_user)
        if not perm["ok"]:
            return error(perm["code"], perm["message"])

    # 一次调用同时拿到答案与引用来源：检索在 ask_with_sources 内部只跑一次。
    # 旧写法先 ask() 再单独 search_related_nodes()，同一次提问检索两遍
    # （外部 embedding 调用、Neo4j 查询、向量反序列化全部翻倍），
    # 且 sources 来自第二遍，可能与喂给 LLM 的上下文不一致。
    # 阶段 G（async 修复）：该方法的同步体已在 qa_service 内部走 asyncio.to_thread，
    # 故此处 await 它就等于把 embedding + SQLite + Neo4j + LLM 全部移出事件循环。
    result = await qa_service.ask_with_sources(request.question, request.course_id,
                                               request.document_id, allowed_ids=allowed_ids)

    return success({
        "question": request.question,
        "answer": result["answer"],
        # 结构化引用来源（kp_id/name/category/description），供前端"证据链"展示
        "sources": result["sources"],
    })
