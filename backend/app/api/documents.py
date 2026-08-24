"""
文档管理 API（对齐规划文档 6.2 文档管理接口）
"""
from fastapi import APIRouter, UploadFile, File, Form

from ..core.response import success, error
from ..services.document_service import DocumentService

router = APIRouter(prefix="/api/v1/documents", tags=["文档管理"])


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(..., description="上传的文档文件（PDF/TXT/DOCX/MD）"),
    course_name: str = Form(..., description="课程名称"),
    teacher_id: int = Form(None, description="教师ID（认证未实现前可省略）"),
):
    """教师上传课程文档，自动建课程并完成解析、抽取、入图"""
    content = await file.read()
    result = await DocumentService.process_upload(
        content=content,
        filename=file.filename or "未命名",
        course_name=course_name,
        teacher_id=teacher_id,
    )
    if not result["ok"]:
        return error(result["code"], result["message"])
    return success(result["data"])
