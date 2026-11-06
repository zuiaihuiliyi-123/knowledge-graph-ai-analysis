"""管理员端 · 文档级知识融合 API

权限边界：**每一个**端点都显式挂 `Depends(require_admin)`（与 `api/admin.py` 同一纪律，
不在路由前缀上挂一次就完事 —— 漏挂会立刻表现为「没有 admin 变量可用」而不是静默变公开接口）。

归属校验与业务逻辑在 `services/fusion/fusion_admin.py`：文档存在 → `t_document.course_id`
必须等于请求 course_id → 候选/映射的双方 kp_id 属于该 (course_id, document_id)。
前端隐藏按钮不算数，跨文档伪造 document_id 一律拒绝。

审计：写操作接 Request，交 AdminService.write_audit；**Dry Run 不审计、不写库**。
"""
from fastapi import APIRouter, Depends, Request

from ..core.dependencies import require_admin
from ..core.response import success, error
from ..services.fusion.fusion_admin import FusionAdminService
from ..schemas.fusion import (
    ScanBody,
    ReviewBody,
    ApplyBody,
    RevokeBody,
)

router = APIRouter(prefix="/api/v1/admin/fusion", tags=["管理员端 · 知识融合"])


def _reply(result: dict, message: str = None) -> dict:
    if result["ok"]:
        return success(result["data"], message or result.get("message", "success"))
    return error(result["code"], result["message"])


# 请求体见 app/schemas/fusion.py。


# ---------- 概览 ----------


@router.get("/overview")
async def fusion_overview(course_id: int, document_id: int,
                          admin: dict = Depends(require_admin)):
    """统计卡。

    **同时给「原始口径」与「折叠后口径」的节点/边数**：管理端其它页面的课程图统计
    （admin_service 直查 Neo4j）不感知融合，两个数字对不上是预期的，页面据此标注，
    而不是静默改口径让数字"看起来一致"。
    """
    return _reply(FusionAdminService.overview(course_id, document_id))


@router.get("/graph")
async def fusion_graph(course_id: int, document_id: int, limit: int = 800,
                       folded: bool = True, admin: dict = Depends(require_admin)):
    """管理员专用的只读图谱。

    管理员不是课程成员，`/api/v1/graph/{course_id}` 会因 `require_course_content`
    判为 4003，所以这里单开一个入口，权限由 `require_admin` 兜住。
    `folded=false` 可查看**未折叠**的原始图，用于核对"融合前后到底差了什么"。
    """
    return _reply(FusionAdminService.graph(course_id, document_id, limit, folded))


# ---------- 候选 ----------


@router.get("/candidates")
async def list_candidates(course_id: int, document_id: int, decision: str = None,
                          status: str = None, keyword: str = None,
                          page: int = 1, page_size: int = 20,
                          admin: dict = Depends(require_admin)):
    return _reply(FusionAdminService.list_candidates(
        course_id, document_id, decision, status, keyword, page, page_size))


@router.get("/candidates/{candidate_id}")
async def get_candidate(candidate_id: int, admin: dict = Depends(require_admin)):
    """候选详情：双方上下文 + 评分明细 + 重建的文本命中上下文。

    端点不接 document_id —— 候选行自带 (course_id, document_id)，返回前校验归属即可。
    """
    return _reply(await FusionAdminService.get_candidate(candidate_id))


@router.post("/candidates/scan")
async def scan_candidates(body: ScanBody, request: Request,
                          admin: dict = Depends(require_admin)):
    """扫描候选。persist=false（默认）时零写入、不审计。"""
    return _reply(await FusionAdminService.scan(
        body.course_id, body.document_id, body.persist, body.use_llm, admin, request))


@router.post("/candidates/{candidate_id}/review")
async def review_candidate(candidate_id: int, body: ReviewBody, request: Request,
                           admin: dict = Depends(require_admin)):
    """人工审核：通过 / 驳回 / 暂缓。审核结论不会被后续扫描覆盖。"""
    return _reply(FusionAdminService.review(
        candidate_id, body.course_id, body.document_id, body.action,
        body.comment, admin, request))


# ---------- 应用 / 撤销 ----------


@router.post("/apply")
async def apply_fusion(body: ApplyBody, request: Request,
                       admin: dict = Depends(require_admin)):
    """应用融合。

    `dry_run=true`（默认）零写入、不审计；正式执行必须显式传 `confirm=true`，
    否则拒绝 —— 避免一次误点的请求直接改动图谱。
    """
    return _reply(FusionAdminService.apply(
        body.course_id, body.document_id, body.candidate_ids,
        body.dry_run, body.confirm, admin, request))


@router.get("/maps")
async def list_maps(course_id: int, document_id: int, status: str = None,
                    page: int = 1, page_size: int = 20,
                    admin: dict = Depends(require_admin)):
    return _reply(FusionAdminService.list_maps(
        course_id, document_id, status, page, page_size))


@router.post("/maps/{fusion_id}/revoke")
async def revoke_fusion(fusion_id: int, body: RevokeBody, request: Request,
                        admin: dict = Depends(require_admin)):
    return _reply(FusionAdminService.revoke(
        body.course_id, body.document_id, fusion_id, body.confirm, admin, request))


@router.post("/runs/{run_id}/undo")
async def undo_run(run_id: str, body: RevokeBody, request: Request,
                   admin: dict = Depends(require_admin)):
    return _reply(FusionAdminService.undo_run(
        body.course_id, body.document_id, run_id, body.confirm, admin, request))


# ---------- 批次 / 冲突 ----------


@router.get("/runs")
async def list_runs(course_id: int, document_id: int, page: int = 1, page_size: int = 20,
                    admin: dict = Depends(require_admin)):
    return _reply(FusionAdminService.list_runs(
        course_id, document_id, page, page_size))


@router.get("/conflicts")
async def list_conflicts(course_id: int, document_id: int, status: str = None,
                         page: int = 1, page_size: int = 20,
                         admin: dict = Depends(require_admin)):
    return _reply(FusionAdminService.list_conflicts(
        course_id, document_id, status, page, page_size))
