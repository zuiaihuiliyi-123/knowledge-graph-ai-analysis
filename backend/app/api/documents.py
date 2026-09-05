"""
文档管理 API（对齐规划文档 6.2 文档管理接口）

Phase 5 改造：
- 上传不再自动建课：POST /upload 必须传 course_id（上传到已有课程）
- 新增文档列表 / 详情 / 删除三个端点，均做「文档 → 课程 → 教师归属」权限校验
"""
from fastapi import APIRouter, UploadFile, File, Form, Depends, Query

from ..core.response import success, error
from ..core.dependencies import get_current_user, require_teacher
from ..services.document_service import DocumentService

router = APIRouter(prefix="/api/v1/documents", tags=["文档管理"])


def _coerce_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(..., description="上传的文档文件（PDF/TXT/DOCX/MD）"),
    course_id: str = Form(..., description="目标课程 ID（上传到已有课程，不再自动建课）"),
    current_user: dict = Depends(require_teacher),
):
    """教师向已有课程上传文档，完成解析、抽取、入图（teacher_id 取自登录用户）"""
    cid = _coerce_int(course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    content = await file.read()
    result = await DocumentService.process_upload(
        content=content,
        filename=file.filename or "未命名",
        course_id=cid,
        teacher_id=current_user["user_id"],
    )
    if not result["ok"]:
        return error(result["code"], result["message"])
    return success(result["data"])


@router.get("")
async def list_documents(
    course_id: str = Query(..., description="课程 ID"),
    current_user: dict = Depends(get_current_user),
):
    """某课程的文档列表（教师仅见本人课程；学生可见全部课程文档，沿用当前无选课体系的课程可见性规则）"""
    cid = _coerce_int(course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    result = DocumentService.list_documents(cid, current_user["user_id"], current_user.get("role", "teacher"))
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.get("/{doc_id}")
async def get_document(doc_id: int, current_user: dict = Depends(require_teacher)):
    """文档详情（仅该文档所属课程的教师）"""
    result = DocumentService.get_document_detail(doc_id, current_user["user_id"])
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.delete("/{doc_id}")
async def delete_document(doc_id: int, current_user: dict = Depends(require_teacher)):
    """删除文档（仅该文档所属课程的教师；Phase 5 仅删记录+文件，图谱级清理留待 Phase 8/9）"""
    result = DocumentService.delete_document(doc_id, current_user["user_id"])
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])
