"""
文档管理 API（对齐规划文档 6.2 文档管理接口）
"""
from fastapi import APIRouter, UploadFile, File, Form, Depends

from ..core.response import success, error
from ..core.dependencies import require_teacher
from ..services.document_service import DocumentService

router = APIRouter(prefix="/api/v1/documents", tags=["文档管理"])


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(..., description="上传的文档文件（PDF/TXT/DOCX/MD）"),
    course_name: str = Form(..., description="课程名称"),
    current_user: dict = Depends(require_teacher),
):
    """教师上传课程文档，自动建课程并完成解析、抽取、入图（teacher_id 取自登录用户）"""
    content = await file.read()
    result = await DocumentService.process_upload(
        content=content,
        filename=file.filename or "未命名",
        course_name=course_name,
        teacher_id=current_user["user_id"],
    )
    if not result["ok"]:
        return error(result["code"], result["message"])
    return success(result["data"])
