"""
文档管理 API（对齐规划文档 6.2 文档管理接口）

Phase 5 改造：
- 上传不再自动建课：POST /upload 必须传 course_id（上传到已有课程）
- 新增文档列表 / 详情 / 删除三个端点，均做「文档 → 课程 → 教师归属」权限校验

在线阅读改造（新增，不改动上述任何已有端点）：
- GET /{doc_id}/content 返回文档原始字节流，供前端 PDF/TXT/DOCX 阅读器使用
"""
from typing import List

from fastapi import APIRouter, UploadFile, File, Form, Depends, Query
from fastapi.responses import FileResponse, JSONResponse

from ..core.config import settings
from ..core.response import success, error
from ..core.dependencies import get_current_user, require_teacher
from ..services.document_service import DocumentService

router = APIRouter(prefix="/api/v1/documents", tags=["文档管理"])


def _coerce_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _too_large(file: UploadFile) -> bool:
    """按 UploadFile.size 提前拦截超限文件，避免先读进内存再发现超限。"""
    size = getattr(file, "size", None)
    return size is not None and size > settings.MAX_UPLOAD_SIZE


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(..., description="上传的文档文件（PDF/TXT/DOCX/MD）"),
    course_id: str = Form(..., description="目标课程 ID（上传到已有课程，不再自动建课）"),
    background: bool = Query(
        False, description="true=落库后立即返回、后台处理（大文件建议）；false=同步跑完再返回"),
    current_user: dict = Depends(require_teacher),
):
    """教师向已有课程上传文档，完成解析、抽取、入图（teacher_id 取自登录用户）。

    `background=true` 时返回 `status=processing`，前端轮询文档列表获取进度；
    默认（false）保持既有同步语义：返回时图谱已构建完成。
    """
    cid = _coerce_int(course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    if _too_large(file):
        return error(
            1002,
            f"文件大小超限，最大 {settings.MAX_UPLOAD_SIZE // 1024 // 1024}MB",
        )
    content = await file.read()
    result = await DocumentService.process_upload(
        content=content,
        filename=file.filename or "未命名",
        course_id=cid,
        teacher_id=current_user["user_id"],
        background=background,
    )
    if not result["ok"]:
        return error(result["code"], result["message"])
    return success(result["data"])


@router.post("/batch-upload")
async def batch_upload_documents(
    files: List[UploadFile] = File(..., description="批量上传的文档（PDF/TXT/DOCX/MD）"),
    course_id: str = Form(..., description="目标课程 ID"),
    current_user: dict = Depends(require_teacher),
):
    """教师批量上传文档到已有课程。

    请求只做校验 / 落盘 / 去重，随后**立即返回**每个文件自己的 document_id；
    解析、知识抽取与图谱入库在后台按并发上限逐文档执行，前端轮询文档列表获取真实进度。
    单个文件失败不会影响同批其他文件。
    """
    cid = _coerce_int(course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    if len(files) > settings.MAX_BATCH_FILES:
        return error(1001, f"单次最多上传 {settings.MAX_BATCH_FILES} 个文件")
    for f in files:
        if _too_large(f):
            return error(
                1002,
                f"文件「{f.filename or '未命名'}」大小超限，单文件最大 "
                f"{settings.MAX_UPLOAD_SIZE // 1024 // 1024}MB",
            )
    result = await DocumentService.process_batch(files, cid, current_user["user_id"])
    if not result["ok"]:
        return error(result["code"], result["message"])
    return success(result["data"])


@router.get("")
async def list_documents(
    course_id: str = Query(..., description="课程 ID"),
    current_user: dict = Depends(get_current_user),
):
    """某课程的文档列表（课程成员可见：课程创建者 / 协作教师 / 已加入的学生）"""
    cid = _coerce_int(course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    result = DocumentService.list_documents(cid, current_user["user_id"], current_user.get("role", "teacher"))
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.get("/{doc_id}")
async def get_document(doc_id: int, current_user: dict = Depends(get_current_user)):
    """文档详情（课程成员可见）。

    课程中心改造：原先限教师（require_teacher），现改为按课程成员关系判定——
    学生成员也需要读文档元数据（阅读器在缺少 course_id 时会走这条路径），
    非成员仍由服务层返回 4003。
    """
    result = DocumentService.get_document_detail(
        doc_id, current_user["user_id"], current_user.get("role", "student"))
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


# 业务错误码 -> HTTP 状态码（在线阅读是二进制流接口，客户端需要靠状态码区分
# 「没权限」「文件没了」和「内容本身损坏」，不能一律 200）
_CONTENT_ERROR_STATUS = {2001: 404, 2002: 404, 2003: 404, 4003: 403}


@router.get("/{doc_id}/content", response_class=FileResponse)
async def get_document_content(doc_id: int, current_user: dict = Depends(get_current_user)):
    """在线阅读：返回文档原始字节流（PDF/TXT/MD/DOCX）。

    - 权限与文档列表口径一致（课程成员可见；非成员由服务层返回 4003 → HTTP 403）
    - Content-Type 由文档类型决定；Content-Disposition 为 inline，仅暴露文件名，不含服务器路径
    - 支持 Range 请求（由 FileResponse 处理），PDF 阅读器可分段加载
    - 只读接口，不影响上传 / 解析 / 抽取 / 删除等既有行为
    """
    result = DocumentService.get_document_content(
        doc_id, current_user["user_id"], current_user.get("role", "student"),
    )
    if not result["ok"]:
        return JSONResponse(
            status_code=_CONTENT_ERROR_STATUS.get(result["code"], 400),
            content=error(result["code"], result["message"]),
        )
    info = result["data"]
    # filename + content_disposition_type="inline" 由 Starlette 生成
    # `inline; filename*=utf-8''...`（仅文件名，不含服务器路径）
    return FileResponse(
        path=info["path"],
        media_type=info["media_type"],
        filename=info["file_name"],
        content_disposition_type="inline",
    )


@router.post("/{doc_id}/retry")
async def retry_document(doc_id: int, current_user: dict = Depends(require_teacher)):
    """重试单篇失败/中断的文档（复用已保存文件，不重复上传）。

    已成功完成的文档会被拒绝，不会重复处理；正在处理中的文档同样拒绝，
    避免同一 document_id 被并发写入两张图。
    """
    result = await DocumentService.retry_document(doc_id, current_user["user_id"])
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.delete("/{doc_id}")
async def delete_document(doc_id: int, current_user: dict = Depends(require_teacher)):
    """删除文档（仅该文档所属课程的教师；Phase 5 仅删记录+文件，图谱级清理留待 Phase 8/9）"""
    result = DocumentService.delete_document(doc_id, current_user["user_id"])
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])
