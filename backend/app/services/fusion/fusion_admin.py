"""管理员端 · 文档级知识融合服务。

Phase 2 从 api/admin_fusion.py 下沉：概览、候选、审核、应用/撤销、映射、批次与冲突
的数据库与图库访问都收在这里；路由只保留请求体解析与响应包装。

权限边界仍由路由的 Depends(require_admin) 兜住；本模块的每个写操作都继续调用
AdminService.write_audit（Dry Run 不审计，与原实现一致）。
"""
import json

from ...core.config import settings
from ...core.sql_database import sql_db
from ..admin_service import AdminService
from ..kg_manager import KnowledgeGraphManager
from . import entity_context, fusion_pipeline
from .entity_resolver import LlmDisambiguator

REVIEW_STATUS = {"accept": "ACCEPTED", "reject": "REJECTED", "defer": "DEFERRED"}


class FusionAdminService:
    """管理员端知识融合的查询与写操作。"""

    @staticmethod
    def _ok(data=None, message: str = "success") -> dict:
        return {"ok": True, "code": 0, "message": message, "data": data}

    @staticmethod
    def _fail(code: int, message: str) -> dict:
        return {"ok": False, "code": code, "message": message, "data": None}

    @staticmethod
    def _document_scope(course_id, document_id):
        """校验文档存在且属于该课程；返回 (doc, None) 或 (None, 错误信封)。"""
        doc = sql_db.get_document(document_id)
        if not doc:
            return None, FusionAdminService._fail(2001, f"文档不存在: {document_id}")
        if int(doc["course_id"]) != int(course_id):
            return None, FusionAdminService._fail(4003, "文档不属于该课程，拒绝跨课程操作")
        return doc, None

    @staticmethod
    def overview(course_id: int, document_id: int) -> dict:
        """统计卡：原始口径 + 折叠后口径的节点/边数，候选/映射/冲突/批次摘要。"""
        doc, err = FusionAdminService._document_scope(course_id, document_id)
        if err:
            return err

        try:
            raw = KnowledgeGraphManager.get_raw_graph_v1(course_id, document_id, limit=2000)
            folded = KnowledgeGraphManager.get_graph_v1(course_id, document_id, limit=2000)
            graph_ok, graph_error = True, None
        except Exception as e:  # noqa: BLE001
            raw = folded = {"nodes": [], "edges": []}
            graph_ok, graph_error = False, f"图库不可用: {e}"

        cand = sql_db.count_fusion_candidates(course_id, document_id)
        open_conflicts = sql_db.list_fusion_conflicts(
            course_id, document_id, status="OPEN", page=1, page_size=1)[0]
        run_total, runs = sql_db.list_fusion_runs(
            course_id, document_id, page=1, page_size=1)

        return FusionAdminService._ok({
            "course_id": course_id,
            "document_id": document_id,
            "file_name": doc["file_name"],
            "course_name": (sql_db.get_course(int(course_id)) or {}).get("course_name"),
            "graph_available": graph_ok,
            "graph_error": graph_error,
            "embedding_available": bool(settings.EMBEDDING_API_KEY),
            "graph": {
                "raw_node_count": len(raw["nodes"]), "raw_edge_count": len(raw["edges"]),
                "folded_node_count": len(folded["nodes"]),
                "folded_edge_count": len(folded["edges"]),
            },
            "candidates": cand,
            "fused_active": sql_db.count_fusion_map(course_id, document_id, status="ACTIVE"),
            "fused_revoked": sql_db.count_fusion_map(course_id, document_id, status="REVOKED"),
            "open_conflicts": open_conflicts,
            "run_count": run_total,
            "last_run": runs[0] if runs else None,
        })

    @staticmethod
    def graph(course_id: int, document_id: int, limit: int = 800,
              folded: bool = True) -> dict:
        """管理员专用只读图谱（管理员不是课程成员，不能走 /api/v1/graph）。"""
        _, err = FusionAdminService._document_scope(course_id, document_id)
        if err:
            return err
        try:
            graph = (KnowledgeGraphManager.get_graph_v1(course_id, document_id, limit=limit)
                     if folded else
                     KnowledgeGraphManager.get_raw_graph_v1(course_id, document_id, limit=limit))
        except Exception as e:  # noqa: BLE001
            return FusionAdminService._fail(5002, f"读取知识图谱失败（Neo4j 不可用？）: {e}")
        return FusionAdminService._ok(graph)

    @staticmethod
    def list_candidates(course_id: int, document_id: int, decision: str = None,
                        status: str = None, keyword: str = None,
                        page: int = 1, page_size: int = 20) -> dict:
        total, rows = sql_db.list_fusion_candidates(
            course_id, document_id, decision=decision, status=status, keyword=keyword,
            page=page, page_size=page_size)
        return FusionAdminService._ok({
            "list": rows, "total": total, "page": page, "page_size": page_size})

    @staticmethod
    async def get_candidate(candidate_id: int) -> dict:
        """候选详情：双方上下文 + 评分明细 + 重建的文本命中上下文。"""
        row = sql_db.get_fusion_candidate(candidate_id)
        if not row:
            return FusionAdminService._fail(2001, f"候选不存在: candidate_id={candidate_id}")

        course_id, document_id = row["course_id"], row["document_id"]
        try:
            graph = KnowledgeGraphManager.get_raw_graph_v1(
                course_id, document_id, limit=2000)
        except Exception as e:  # noqa: BLE001
            return FusionAdminService._fail(5002, f"读取知识图谱失败（Neo4j 不可用？）: {e}")

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

        ctx_ok, ctx_error, contexts = False, None, {}
        try:
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

        try:
            detail = json.loads(row.get("score_detail") or "{}")
        except (TypeError, ValueError):
            detail = {}

        return FusionAdminService._ok({
            "candidate": row,
            "score_detail": detail,
            "source": sides["source"],
            "target": sides["target"],
            "context_available": ctx_ok,
            "context_error": ctx_error,
            "note": "「重建的文本命中上下文」由重新分块后按名称命中重建，**不是原始抽取证据**"
                    "（抽取阶段未保留 chunk 溯源）。",
        })

    @staticmethod
    async def scan(course_id: int, document_id: int, persist: bool = False,
                   use_llm: bool = False, admin: dict = None, request=None) -> dict:
        """扫描候选；persist=false（默认）时零写入、不审计。"""
        disambiguator = None
        if use_llm:
            disambiguator = LlmDisambiguator()
            if not disambiguator.available:
                disambiguator = None

        result = await fusion_pipeline.scan(
            course_id, document_id, persist=persist,
            use_llm=bool(disambiguator), disambiguator=disambiguator, operator=admin)
        if result["ok"] and persist and admin is not None:
            AdminService.write_audit(
                admin, "fusion.scan", target_type="document", target_id=document_id,
                detail=f"扫描候选 {result['data']['candidate_count']} 条",
                request=request)
        return result

    @staticmethod
    def review(candidate_id: int, course_id: int, document_id: int, action: str,
               comment: str = None, admin: dict = None, request=None) -> dict:
        """人工审核：通过 / 驳回 / 暂缓；审核结论不会被后续扫描覆盖。"""
        if action not in REVIEW_STATUS:
            return FusionAdminService._fail(
                1001, f"非法动作: {action}（可选 {sorted(REVIEW_STATUS)}）")

        row = sql_db.get_fusion_candidate(candidate_id)
        if not row:
            return FusionAdminService._fail(2001, f"候选不存在: candidate_id={candidate_id}")
        if int(row["course_id"]) != int(course_id) \
                or int(row["document_id"]) != int(document_id):
            return FusionAdminService._fail(4003, "候选不属于该文档，拒绝跨文档操作")

        n = sql_db.set_fusion_candidate_status(
            candidate_id, REVIEW_STATUS[action],
            reviewed_by=(admin or {}).get("user_id"), comment=comment)
        if admin is not None:
            AdminService.write_audit(
                admin, "fusion.review", target_type="fusion_candidate",
                target_id=candidate_id,
                detail=f"{action}（{row['source_name']} → {row['target_name']}）",
                request=request)
        return FusionAdminService._ok({
            "candidate_id": candidate_id,
            "status": REVIEW_STATUS[action],
            "updated": n,
        })

    @staticmethod
    def apply(course_id: int, document_id: int, candidate_ids=None, dry_run: bool = True,
              confirm: bool = False, admin: dict = None, request=None) -> dict:
        """应用融合；dry_run=true（默认）零写入、不审计，正式执行必须 confirm=true。"""
        if not dry_run and not confirm:
            return FusionAdminService._fail(1001, "正式应用融合需要显式确认（confirm=true）")

        result = fusion_pipeline.apply(
            course_id, document_id, candidate_ids=candidate_ids,
            dry_run=dry_run, operator=admin)
        if result["ok"] and not dry_run and admin is not None:
            AdminService.write_audit(
                admin, "fusion.apply", target_type="document", target_id=document_id,
                detail=f"应用融合 {result['data'].get('applied_count', 0)} 条"
                       f"（run_id={result['data'].get('run_id')}）",
                request=request)
        return result

    @staticmethod
    def revoke(course_id: int, document_id: int, fusion_id: int, confirm: bool = False,
               admin: dict = None, request=None) -> dict:
        """撤销单条融合；必须显式确认。"""
        if not confirm:
            return FusionAdminService._fail(1001, "撤销融合需要显式确认（confirm=true）")
        result = fusion_pipeline.revoke(
            course_id, document_id, fusion_id, operator=admin)
        if result["ok"] and result["data"].get("revoked") and admin is not None:
            AdminService.write_audit(
                admin, "fusion.revoke", target_type="fusion_map", target_id=fusion_id,
                detail=f"撤销融合记录 {fusion_id}", request=request)
        return result

    @staticmethod
    def undo_run(course_id: int, document_id: int, run_id: str, confirm: bool = False,
                 admin: dict = None, request=None) -> dict:
        """整批撤销；必须显式确认。"""
        if not confirm:
            return FusionAdminService._fail(1001, "整批撤销需要显式确认（confirm=true）")
        result = fusion_pipeline.undo_run(
            course_id, document_id, run_id, operator=admin)
        if result["ok"] and admin is not None:
            AdminService.write_audit(
                admin, "fusion.undo_run", target_type="fusion_run", target_id=run_id,
                detail=f"整批撤销 {result['data'].get('revoked_count', 0)} 条",
                request=request)
        return result

    @staticmethod
    def list_maps(course_id: int, document_id: int, status: str = None,
                  page: int = 1, page_size: int = 20) -> dict:
        rows = sql_db.list_fusion_map(course_id, document_id, status=status)
        start = max(0, (page - 1) * page_size)
        return FusionAdminService._ok({
            "list": rows[start:start + page_size], "total": len(rows),
            "page": page, "page_size": page_size})

    @staticmethod
    def list_runs(course_id: int, document_id: int, page: int = 1,
                  page_size: int = 20) -> dict:
        total, rows = sql_db.list_fusion_runs(
            course_id, document_id, page=page, page_size=page_size)
        return FusionAdminService._ok({
            "list": rows, "total": total, "page": page, "page_size": page_size})

    @staticmethod
    def list_conflicts(course_id: int, document_id: int, status: str = None,
                       page: int = 1, page_size: int = 20) -> dict:
        total, rows = sql_db.list_fusion_conflicts(
            course_id, document_id, status=status, page=page, page_size=page_size)
        return FusionAdminService._ok({
            "list": rows, "total": total, "page": page, "page_size": page_size})
