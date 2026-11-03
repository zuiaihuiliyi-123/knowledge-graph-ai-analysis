"""
智能问答 API（对齐规划文档 6.4，响应格式统一 {code, message, data, timestamp}）

两个端点：
- `POST /ask`：一次性返回完整答案（默认，前端既有调用不变）；
- `POST /ask/stream`：SSE 流式返回（先 sources，再逐块 delta，最后 done），
  用于「边生成边显示」，首字延迟从「等整段生成完」降到「等首个 token」。
"""
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
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


def _resolve_scope(request: QuestionRequest, current_user: dict):
    """课程作用域与权限判定（`/ask` 与 `/ask/stream` 共用，避免两套口径）。

    返回 `(allowed_ids, denied)`：`denied` 非 None 时直接把它返回给客户端。
    """
    allowed_ids = Permissions.allowed_course_ids(current_user)
    if request.course_id:
        try:
            cid = int(request.course_id)
        except (TypeError, ValueError):
            return allowed_ids, error(1001, f"course_id 必须为整数，收到: {request.course_id}")
        perm = Permissions.require_course_content(cid, current_user)
        if not perm["ok"]:
            return allowed_ids, error(perm["code"], perm["message"])
    return allowed_ids, None


def _sse(event: str, data: dict) -> str:
    """SSE 帧格式：`event: <名称>` + `data: <JSON>` + 空行分隔。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/ask")
async def ask_question(request: QuestionRequest, current_user: dict = Depends(get_current_user)):
    """
    学生向AI提问（RAG模式；Phase 8C：按 course_id + document_id 限定候选知识点范围）

    课程中心改造：
    - 显式传了 course_id：先校验课程成员身份，非成员直接 4003（不进入检索）；
    - 未传 course_id（AI 悬浮窗在未选课程时的调用）：检索范围收敛为
      「当前用户可访问的课程」，不再退化成全库扫描。
    """
    allowed_ids, denied = _resolve_scope(request, current_user)
    if denied is not None:
        return denied

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


@router.post("/ask/stream")
async def ask_question_stream(request: QuestionRequest,
                              current_user: dict = Depends(get_current_user)):
    """流式问答（SSE）。

    事件序列：
        （`: ping`）                                        ← 长时间无输出时的保活注释帧
        event: sources   data: {"sources": [...]}       ← 先给证据链，前端可立即渲染
        event: delta     data: {"text": "…"}            ← 可能多次
        event: done      data: {"answer": "全文", "sources": [...], "finish_reason": "stop"}
        event: error     data: {"message": "…", "partial": "…"}  ← 失败时替代 done

    与 `/ask` 共用同一套检索与权限判定；`sources` 就是本次真正喂给 LLM 的上下文。
    响应头带 `X-Accel-Buffering: no`：反向代理（Nginx 等）默认会缓冲响应，
    不关掉的话前端仍会「一次性出现」，流式等于没做。
    """
    allowed_ids, denied = _resolve_scope(request, current_user)
    if denied is not None:
        return denied

    async def event_stream():
        try:
            async for item in qa_service.stream_answer(
                    request.question, request.course_id, request.document_id,
                    allowed_ids=allowed_ids):
                if item["event"] == "ping":
                    # 注释帧：SSE 规范里的保活写法。解析器会忽略它，
                    # 但代理/浏览器据此知道连接还活着（长检索、模型思考期间不发会断流）。
                    yield ": ping\n\n"
                    continue
                yield _sse(item["event"], item["data"])
        except Exception as exc:  # noqa: BLE001 - 流已开始，只能以 error 事件告知
            yield _sse("error", {"message": f"问答流中断：{exc}"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Connection 是逐跳头，ASGI 服务不会转写；保留反而容易误导排查，故不设
            "X-Accel-Buffering": "no",
        },
    )
