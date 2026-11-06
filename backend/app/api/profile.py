"""
个人中心 API（用户资料读写 / 头像）

安全约定：
- 只能读写自己的资料：user_id 一律取自 JWT，接口不接受任何 user_id 参数；
- 字段按角色白名单（学生提交 teacher_no 会被静默丢弃，见 ProfileService）；
- 头像 GET 不鉴权：<img src> 无法携带 Authorization 头，且头像本身是要展示在
  成员列表里的公开信息（服务端生成文件名、白名单扩展名、限 2MB、替换即删旧文件）。
"""
from fastapi import APIRouter, Depends, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse

from ..core.config import settings
from ..core.dependencies import get_current_user
from ..core.response import success, error
from ..services.profile_service import ProfileService
from ..schemas.profile import ProfileUpdate

router = APIRouter(prefix="/api/v1/profile", tags=["个人中心"])


@router.get("")
async def get_profile(current_user: dict = Depends(get_current_user)):
    """获取当前登录用户的资料（身份 + 个人资料，未填写的字段为 null）"""
    result = ProfileService.get_profile(current_user["user_id"])
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.put("")
async def update_profile(body: ProfileUpdate, current_user: dict = Depends(get_current_user)):
    """更新当前登录用户的资料（仅本人；按角色白名单过滤字段）。

    用 exclude_unset=True 区分「未提交该字段」与「显式传 null/空串」：
    前者保持不变，后者清空。若用 model_dump()，未提交的字段会全部变成 None 并被清空，
    导致「只改一个字段」把其余资料抹掉。
    """
    result = ProfileService.update_profile(current_user["user_id"], current_user.get("role", ""),
                                           body.model_dump(exclude_unset=True))
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.post("/avatar")
async def upload_avatar(file: UploadFile = File(..., description="头像图片（jpg/jpeg/png/webp，≤2MB）"),
                        current_user: dict = Depends(get_current_user)):
    """上传头像（替换旧头像；文件名由服务端生成）"""
    if getattr(file, "size", None) is not None and file.size > settings.MAX_AVATAR_SIZE:
        return error(1002, f"头像大小超限，最大 {settings.MAX_AVATAR_SIZE // 1024 // 1024}MB")
    content = await file.read()
    result = ProfileService.save_avatar(current_user["user_id"], content, file.filename or "")
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.delete("/avatar")
async def delete_avatar(current_user: dict = Depends(get_current_user)):
    """删除当前用户的头像（清空库中指针并移除磁盘文件）"""
    result = ProfileService.delete_avatar(current_user["user_id"])
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.get("/avatar/{user_id}")
async def get_avatar(user_id: int):
    """读取头像图片（公开；无头像返回 404，前端回退为姓名首字母色块）"""
    path = ProfileService.avatar_path(user_id)
    if not path:
        return JSONResponse(status_code=404, content={"detail": "该用户未设置头像"})
    return FileResponse(path)
