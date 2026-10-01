"""管理员端 · 文档级知识融合 API

权限边界：**每一个**端点都显式挂 `Depends(require_admin)`（与 `api/admin.py` 同一纪律，
不在路由前缀上挂一次就完事 —— 漏挂会立刻表现为「没有 admin 变量可用」而不是静默变公开接口）。

归属校验：所有端点在后端依次校验
  require_admin → 文档存在 → `t_document.course_id == course_id`
  → 候选/映射的双方 kp_id 属于该 (course_id, document_id) → 状态允许该操作。
前端隐藏按钮不算数，跨文档伪造 document_id 一律拒绝。

审计：写操作接 Request，交 `AdminService.write_audit`；**Dry Run 不审计、不写库**。
"""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ..core.dependencies import require_admin
from ..core.response import success, error
from ..core.sql_database import sql_db
from ..services.admin_service import AdminService
from ..services.fusion import fusion_pipeline
from ..services.kg_manager import KnowledgeGraphManager

router = APIRouter(prefix="/api/v1/admin/fusion", tags=["管理员端 · 知识融合"])


def _reply(result: dict, message: str = None) -> dict:
    if result["ok"]:
        return success(result["data"], message or result.get("message", "success"))
    return error(result["code"], result["message"])


# ---------- 请求体 ----------


class ScanBody(BaseModel):
    course_id: int
    document_id: int
    persist: bool = False
    use_llm: bool = False


class ReviewBody(BaseModel):
    course_id: int
    document_id: int
    action: str          # accept / reject / defer
    comment: str | None = None


class ApplyBody(BaseModel):
    course_id: int
    document_id: int
    candidate_ids: list[int] | None = None
    dry_run: bool = True
    confirm: bool = False


class RevokeBody(BaseModel):
    course_id: int
    document_id: int
    confirm: bool = False


_REVIEW_STATUS = {"accept": "ACCEPTED", "reject": "REJECTED", "defer": "DEFERRED"}


# ---------- 概览 ----------


@router.get("/overview")
async def fusion_overview(course_id: int, document_id: int,
                          admin: dict = Depends(require_admin)):
    """统计卡。

    **同时给「原始口径」与「折叠后口径」的节点/边数**：管理端其它页面的课程图统计
    （admin_service 直查 Neo4j）不感知融合，两个数字对不上是预期的，页面据此标注，
    而不是静默改口径让数字"看起来一致"。
    """
    doc = sql_db.get_document(document_id)
    if not doc:
        return error(2001, f"文档不存在: {document_id}")
    if int(doc["course_id"]) != int(course_id):
        return error(4003, "文档不属于该课程，拒绝跨课程操作")

    try:
        raw = KnowledgeGraphManager.get_raw_graph_v1(course_id, document_id, limit=2000)
        folded = KnowledgeGraphManager.get_graph_v1(course_id, document_id, limit=2000)
        graph_ok, graph_error = True, None
    except Exception as e:  # noqa: BLE001
        raw = folded = {"nodes": [], "edges": []}
        graph_ok, graph_error = False, f"图库不可用: {e}"

    cand = sql_db.count_fusion_candidates(course_id, document_id)
    _, conflicts = sql_db.list_fusion_conflicts(course_id, document_id, status="OPEN",
                                                page=1, page_size=1)
    run_total, runs = sql_db.list_fusion_runs(course_id, document_id, page=1, page_size=1)

    embed_ok = False
    try:
        from ..core.config import settings
        embed_ok = bool(settings.EMBEDDING_API_KEY)
    except Exception:  # noqa: BLE001
        pass

    return success({
        "course_id": course_id,
        "document_id": document_id,
        "file_name": doc["file_name"],
        "course_name": (sql_db.get_course(int(course_id)) or {}).get("course_name"),
        "graph_available": graph_ok,
        "graph_error": graph_error,
        "embedding_available": embed_ok,
        "graph": {
            "raw_node_count": len(raw["nodes"]), "raw_edge_count": len(raw["edges"]),
            "folded_node_count": len(folded["nodes"]), "folded_edge_count": len(folded["edges"]),
        },
        "candidates": cand,
        "fused_active": sql_db.count_fusion_map(course_id, document_id, status="ACTIVE"),
        "fused_revoked": sql_db.count_fusion_map(course_id, document_id, status="REVOKED"),
        "open_conflicts": _count_conflicts(course_id, document_id),
        "run_count": run_total,
        "last_run": runs[0] if runs else None,
    })


def _count_conflicts(course_id, document_id) -> int:
    total, _ = sql_db.list_fusion_conflicts(course_id, document_id, status="OPEN",
                                            page=1, page_size=1)
    return total


@router.get("/graph")
async def fusion_graph(course_id: int, document_id: int, limit: int = 800,
                       folded: bool = True, admin: dict = Depends(require_admin)):
    """管理员专用的只读图谱。

    管理员不是课程成员，`/api/v1/graph/{course_id}` 会因 `require_course_content`
    判为 4003，所以这里单开一个入口，权限由 `require_admin` 兜住。
    `folded=false` 可查看**未折叠**的原始图，用于核对"融合前后到底差了什么"。
    """
    doc = sql_db.get_document(document_id)
    if not doc:
        return error(2001, f"文档不存在: {document_id}")
    if int(doc["course_id"]) != int(course_id):
        return error(4003, "文档不属于该课程，拒绝跨课程操作")
    try:
        graph = (KnowledgeGraphManager.get_graph_v1(course_id, document_id, limit=limit)
                 if folded else
                 KnowledgeGraphManager.get_raw_graph_v1(course_id, document_id, limit=limit))
    except Exception as e:  # noqa: BLE001
        return error(5002, f"读取知识图谱失败（Neo4j 不可用？）: {e}")
    return success(graph)


# ---------- 候选 ----------


@router.get("/candidates")
async def list_candidates(course_id: int, document_id: int, decision: str = None,
                          status: str = None, keyword: str = None,
                          page: int = 1, page_size: int = 20,
                          admin: dict = Depends(require_admin)):
    total, rows = sql_db.list_fusion_candidates(
        course_id, document_id, decision=decision, status=status, keyword=keyword,
        page=page, page_size=page_size)
    return success({"list": rows, "total": total, "page": page, "page_size": page_size})


@router.get("/candidates/{candidate_id}")
async def get_candidate(candidate_id: int, admin: dict = Depends(require_admin)):
    """候选详情：双方上下文 + 评分明细 + 重建的文本命中上下文。

    端点不接 document_id —— 候选行自带 (course_id, document_id)，返回前校验归属即可。
    """
    row = sql_db.get_fusion_candidate(candidate_id)
    if not row:
        return error(2001, f"候选不存在: candidate_id={candidate_id}")

    course_id, document_id = row["course_id"], row["document_id"]
    try:
        graph = KnowledgeGraphManager.get_raw_graph_v1(course_id, document_id, limit=2000)
    except Exception as e:  # noqa: BLE001
        return error(5002, f"读取知识图谱失败（Neo4j 不可用？）: {e}")

    nodes = {n["id"]: n for n in graph["nodes"]}
    sides = {}
    for key in ("source", "target"):
        kp_id = row[f"{key}_kp_id"]
        n = nodes.get(kp_id)
        sides[key] = {
            "kp_id": kp_id,
            "name": row.get(f"{key}_name") or (n or {}).get("label"),
            "category": row.get(f"{key}_category"),
            "description": (n or {}).get("description") or "",
            "in_graph": n is not None,
        }

    # 重建上下文（只读；失败就给不可用标记，不阻断详情展示）
    ctx_ok, ctx_error, contexts = False, None, {}
    try:
        import asyncio
        from ..services.fusion import entity_context
        chunks, ctx_error = await entity_context.load_chunks(course_id, document_id)
        ctx_ok = chunks is not None
        if ctx_ok:
            for key, s in sides.items():
                contexts[key] = entity_context.build_for(
                    s["kp_id"], s["name"] or "", s["description"], [], chunks)
    except Exception as e:  # noqa: BLE001
        ctx_error = str(e)

    for key, s in sides.items():
        s["reconstructed_context"] = contexts.get(key) or {
            "reconstruction_ok": False, "discriminative": False, "hit_terms": [],
            "chunk_indices": [], "snippets": [], "reason": ctx_error or "未重建",
            "method": "rechunk_exact_name_hit",
        }

    import json
    try:
        detail = json.loads(row.get("score_detail") or "{}")
    except (TypeError, ValueError):
        detail = {}

    return success({
        "candidate": row,
        "score_detail": detail,
        "source": sides["source"],
        "target": sides["target"],
        "context_available": ctx_ok,
        "context_error": ctx_error,
        "note": "「重建的文本命中上下文」由重新分块后按名称命中重建，**不是原始抽取证据**"
                "（抽取阶段未保留 chunk 溯源）。",
    })


@router.post("/candidates/scan")
async def scan_candidates(body: ScanBody, request: Request,
                          admin: dict = Depends(require_admin)):
    """扫描候选。persist=false（默认）时零写入、不审计。"""
    disambiguator = None
    if body.use_llm:
        from ..services.fusion.entity_resolver import LlmDisambiguator
        disambiguator = LlmDisambiguator()
        if not disambiguator.available:
            disambiguator = None

    result = await fusion_pipeline.scan(
        body.course_id, body.document_id, persist=body.persist,
        use_llm=bool(disambiguator), disambiguator=disambiguator, operator=admin)
    if result["ok"] and body.persist:
        AdminService.write_audit(
            admin, "fusion.scan", target_type="document", target_id=body.document_id,
            detail=f"扫描候选 {result['data']['candidate_count']} 条",
            request=request)
    return _reply(result)


@router.post("/candidates/{candidate_id}/review")
async def review_candidate(candidate_id: int, body: ReviewBody, request: Request,
                           admin: dict = Depends(require_admin)):
    """人工审核：通过 / 驳回 / 暂缓。审核结论不会被后续扫描覆盖。"""
    if body.action not in _REVIEW_STATUS:
        return error(1001, f"非法动作: {body.action}（可选 {sorted(_REVIEW_STATUS)}）")

    row = sql_db.get_fusion_candidate(candidate_id)
    if not row:
        return error(2001, f"候选不存在: candidate_id={candidate_id}")
    if int(row["course_id"]) != int(body.course_id) \
            or int(row["document_id"]) != int(body.document_id):
        return error(4003, "候选不属于该文档，拒绝跨文档操作")

    n = sql_db.set_fusion_candidate_status(
        candidate_id, _REVIEW_STATUS[body.action],
        reviewed_by=admin.get("user_id"), comment=body.comment)
    AdminService.write_audit(
        admin, "fusion.review", target_type="fusion_candidate", target_id=candidate_id,
        detail=f"{body.action}（{row['source_name']} → {row['target_name']}）",
        request=request)
    return success({"candidate_id": candidate_id, "status": _REVIEW_STATUS[body.action],
                    "updated": n})


# ---------- 应用 / 撤销 ----------


@router.post("/apply")
async def apply_fusion(body: ApplyBody, request: Request,
                       admin: dict = Depends(require_admin)):
    """应用融合。

    `dry_run=true`（默认）零写入、不审计；正式执行必须显式传 `confirm=true`，
    否则拒绝 —— 避免一次误点的请求直接改动图谱。
    """
    if not body.dry_run and not body.confirm:
        return error(1001, "正式应用融合需要显式确认（confirm=true）")

    result = fusion_pipeline.apply(
        body.course_id, body.document_id, candidate_ids=body.candidate_ids,
        dry_run=body.dry_run, operator=admin)
    if result["ok"] and not body.dry_run:
        AdminService.write_audit(
            admin, "fusion.apply", target_type="document", target_id=body.document_id,
            detail=f"应用融合 {result['data'].get('applied_count', 0)} 条"
                   f"（run_id={result['data'].get('run_id')}）",
            request=request)
    return _reply(result)


@router.get("/maps")
async def list_maps(course_id: int, document_id: int, status: str = None,
                    page: int = 1, page_size: int = 20,
                    admin: dict = Depends(require_admin)):
    rows = sql_db.list_fusion_map(course_id, document_id, status=status)
    start = max(0, (page - 1) * page_size)
    return success({"list": rows[start:start + page_size], "total": len(rows),
                    "page": page, "page_size": page_size})


@router.post("/maps/{fusion_id}/revoke")
async def revoke_fusion(fusion_id: int, body: RevokeBody, request: Request,
                        admin: dict = Depends(require_admin)):
    if not body.confirm:
        return error(1001, "撤销融合需要显式确认（confirm=true）")
    result = fusion_pipeline.revoke(body.course_id, body.document_id, fusion_id,
                                    operator=admin)
    if result["ok"] and result["data"].get("revoked"):
        AdminService.write_audit(
            admin, "fusion.revoke", target_type="fusion_map", target_id=fusion_id,
            detail=f"撤销融合记录 {fusion_id}", request=request)
    return _reply(result)


@router.post("/runs/{run_id}/undo")
async def undo_run(run_id: str, body: RevokeBody, request: Request,
                   admin: dict = Depends(require_admin)):
    if not body.confirm:
        return error(1001, "整批撤销需要显式确认（confirm=true）")
    result = fusion_pipeline.undo_run(body.course_id, body.document_id, run_id,
                                      operator=admin)
    if result["ok"]:
        AdminService.write_audit(
            admin, "fusion.undo_run", target_type="fusion_run", target_id=run_id,
            detail=f"整批撤销 {result['data'].get('revoked_count', 0)} 条",
            request=request)
    return _reply(result)


# ---------- 批次 / 冲突 ----------


@router.get("/runs")
async def list_runs(course_id: int, document_id: int, page: int = 1, page_size: int = 20,
                    admin: dict = Depends(require_admin)):
    total, rows = sql_db.list_fusion_runs(course_id, document_id, page=page,
                                          page_size=page_size)
    return success({"list": rows, "total": total, "page": page, "page_size": page_size})


@router.get("/conflicts")
async def list_conflicts(course_id: int, document_id: int, status: str = None,
                         page: int = 1, page_size: int = 20,
                         admin: dict = Depends(require_admin)):
    total, rows = sql_db.list_fusion_conflicts(course_id, document_id, status=status,
                                               page=page, page_size=page_size)
    return success({"list": rows, "total": total, "page": page, "page_size": page_size})
