"""融合流程编排：扫描候选（P1）→ 判定 → 应用 / 撤销（P2）。

编排层负责的是**顺序与事务边界**，具体算法都在各自模块里：
`entity_context`（重建上下文）→ `entity_candidates`（召回与打分）→
`entity_resolver`（规范节点选择 + 分档 + LLM 消歧）→ `graph_folder`（读图折叠）。

Dry Run 与正式执行走的是**同一条代码路径**，唯一差别是最后写不写库 ——
这样"预览里看到的"和"真正会发生的事"不会出现两套口径。
"""
import logging
import uuid

from ...core.sql_database import sql_db
from ..kg_manager import KnowledgeGraphManager
from . import entity_candidates, entity_context, entity_resolver, fusion_config
from .fusion_map import load_active_mapping
from .graph_folder import fold_graph

_logger = logging.getLogger(__name__)

# 读取文档图时的节点上限。与 API 层默认值一致，超过部分不参与候选生成。
GRAPH_LIMIT = 2000


def _ok(data=None, message="success"):
    return {"ok": True, "code": 0, "message": message, "data": data}


def _fail(code, message):
    return {"ok": False, "code": code, "message": message, "data": None}


def _check_scope(course_id, document_id):
    """归属校验：文档必须存在，且确实属于该课程。

    所有对外入口（扫描/应用/撤销）都先过这一关 —— 跨文档、跨课程的伪造请求
    在触达任何数据之前就被挡掉。
    """
    try:
        doc = sql_db.get_document(document_id)
    except Exception as e:  # noqa: BLE001
        return None, _fail(5003, f"读取文档失败: {e}")
    if not doc:
        return None, _fail(2001, f"文档不存在: document_id={document_id}")
    if int(doc.get("course_id") or -1) != int(course_id):
        return None, _fail(4003, "文档不属于该课程，拒绝跨课程操作")
    return doc, None


async def scan(course_id: int, document_id, persist: bool = False, use_llm: bool = False,
               disambiguator=None, top_k: int = None, max_pairs: int = None,
               run_id: str = None, operator: dict = None) -> dict:
    """扫描文档内候选。

    persist=False（默认）时**零写入**：连 run 行都不落，结果完全由内存算出。
    persist=True 时写一条 run 与若干候选行；**已审核过的候选状态不会被覆盖**。
    """
    doc, err = _check_scope(course_id, document_id)
    if err:
        return err

    try:
        graph = KnowledgeGraphManager.get_raw_graph_v1(course_id, document_id, limit=GRAPH_LIMIT)
    except Exception as e:  # noqa: BLE001
        # 图库不可用必须**明确报错**，不能返回 0 候选让管理员以为"文档很干净"
        return _fail(5002, f"读取知识图谱失败（Neo4j 不可用？）: {e}")

    nodes, edges = graph.get("nodes") or [], graph.get("edges") or []

    # ---- 重建上下文（拿不到就整体降级，绝不阻断） ----
    chunks, ctx_error = await entity_context.load_chunks(course_id, document_id)
    contexts, context_ok = {}, chunks is not None
    if context_ok:
        for n in nodes:
            props = n.get("properties") or {}
            contexts[n["id"]] = entity_context.build_for(
                n["id"], n.get("label") or "", n.get("description") or "", [],
                chunks)
    else:
        _logger.warning("上下文重建不可用 course_id=%s document_id=%s: %s",
                        course_id, document_id, ctx_error)

    profiles = entity_candidates.build_profiles(nodes, edges, contexts)

    # 已经是 ACTIVE 映射源的节点不再参与：它们已经折掉了，重复提议只会制造噪声
    active = load_active_mapping(course_id, document_id, strict=True)
    already = set(active.keys())
    candidates_profiles = [p for p in profiles if p["kp_id"] not in already]

    pairs = entity_candidates.generate_candidates(
        candidates_profiles, edges, top_k=top_k, max_pairs=max_pairs)

    rows, counts = [], {"SAME": 0, "DIFFERENT": 0, "UNCERTAIN": 0}
    for c in pairs:
        a, b = c["a"], c["b"]
        source, target, canonical_reason = entity_resolver.choose_canonical(a, b)
        detail = c["detail"]
        decision, band = entity_resolver.decide_by_rules(
            detail["score"], allow_review=use_llm and disambiguator is not None)

        decision_source = "RULE"
        reason = "; ".join(detail.get("notes") or []) or None
        if decision is None and band == entity_resolver.BAND_REVIEW:
            verdict = disambiguator.judge({
                "document_id": document_id,
                "a": _pair_side(a), "b": _pair_side(b),
            })
            decision = verdict.get("decision") or "UNCERTAIN"
            decision_source = "LLM"
            reason = verdict.get("reason") or reason
        if decision is None:
            decision = "UNCERTAIN"

        counts[decision] = counts.get(decision, 0) + 1
        rows.append({
            "source_kp_id": source["kp_id"], "target_kp_id": target["kp_id"],
            "source_name": source["name"], "target_name": target["name"],
            "source_category": source.get("category"), "target_category": target.get("category"),
            "score": round(detail["score"], 4),
            "score_detail": _json(detail),
            "decision": decision, "decision_source": decision_source,
            "reason": reason,
            # 下面几项不入库，仅供 API 直接返回（避免前端再查一次图）
            "canonical_reason": canonical_reason,
            "band": band,
        })

    result = {
        "course_id": course_id, "document_id": document_id,
        "raw_node_count": len(nodes), "raw_edge_count": len(edges),
        "scanned_node_count": len(candidates_profiles),
        "skipped_already_fused": len(already),
        "candidate_count": len(rows),
        "same_count": counts.get("SAME", 0),
        "different_count": counts.get("DIFFERENT", 0),
        "uncertain_count": counts.get("UNCERTAIN", 0),
        "context_ok": context_ok, "context_error": ctx_error,
        "llm_used": bool(use_llm and disambiguator is not None),
        "candidates": rows,
        "run_id": None,
        "persisted": False,
    }

    if persist:
        rid = run_id or uuid.uuid4().hex[:16]
        try:
            # 单事务：run 行 → 候选 → 收尾，任一步异常整体回滚，不留半完成状态
            with sql_db._connect() as conn:
                sql_db.insert_fusion_run(
                    rid, course_id, document_id, "APPLY", config_json=_json(fusion_config.snapshot()),
                    operator_id=(operator or {}).get("user_id"),
                    operator_name=(operator or {}).get("username"), conn=conn)
                sql_db.upsert_fusion_candidates(
                    course_id, document_id,
                    [{k: v for k, v in r.items() if k not in ("canonical_reason", "band")}
                     for r in rows], run_id=rid, conn=conn)
                sql_db.finish_fusion_run(
                    rid, "COMPLETED", total_candidates=len(rows),
                    n_same=counts.get("SAME", 0), n_different=counts.get("DIFFERENT", 0),
                    n_uncertain=counts.get("UNCERTAIN", 0), conn=conn)
                conn.commit()
            result["run_id"] = rid
            result["persisted"] = True
        except Exception as e:  # noqa: BLE001
            _logger.exception("写入候选失败 course_id=%s document_id=%s", course_id, document_id)
            return _fail(5003, f"写入候选失败: {e}")

    return _ok(result)


def _load_profiles(course_id, document_id):
    """读原始图并整理成 profile（不需要重建上下文：规范节点选择只用图谱自有属性）"""
    graph = KnowledgeGraphManager.get_raw_graph_v1(course_id, document_id, limit=GRAPH_LIMIT)
    nodes, edges = graph.get("nodes") or [], graph.get("edges") or []
    return entity_candidates.build_profiles(nodes, edges, {}), nodes, edges


def _would_cycle(source_id: str, target_id: str, current: dict) -> bool:
    """把 source→target 加进 current 是否会成环（current 已保证出度 ≤ 1）"""
    if source_id == target_id:
        return True
    seen, cur = set(), target_id
    while cur in current:
        if cur == source_id or cur in seen:
            return True
        seen.add(cur)
        cur = current[cur]
    return cur == source_id


def _edge_delta(nodes, edges, mapping_before, mapping_after):
    """用 `fold_graph` 前后各跑一次，得出这次融合对边的具体影响。

    复用折叠本身来算"会发生什么"，而不是另写一套等价逻辑 ——
    这样预览里看到的与实际落地后的结果不可能不一致。
    """
    _, before = fold_graph(nodes, edges, mapping_before)
    _, after = fold_graph(nodes, edges, mapping_after)
    keys_before = {(e["source"], e["target"], e["type"]) for e in before}
    keys_after = {(e["source"], e["target"], e["type"]) for e in after}
    added = keys_after - keys_before
    removed = keys_before - keys_after
    return {
        "edges_before": len(before),
        "edges_after": len(after),
        "rewritten": sorted(f"{s}->{t}({ty})" for s, t, ty in added),
        "dropped": sorted(f"{s}->{t}({ty})" for s, t, ty in removed),
        "dropped_count": len(removed),
    }


def _plan_item(candidate: dict, profiles: dict, edges: list, nodes: list,
               active_map: dict, staged: dict) -> tuple:
    """给一条候选算出「真正要做什么」，返回 `(plan, conflict)`。

    plan 为 None 表示这条候选这次不处理（冲突已说明原因）。
    """
    src_id = candidate["source_kp_id"]
    tgt_id = candidate["target_kp_id"]
    a, b = profiles.get(src_id), profiles.get(tgt_id)

    if a is None or b is None:
        # 端点已不在图中：kp_id 在文档重新抽取后会整体更换，旧候选必然失效。
        # 这是**失败安全**的方向 —— 静默失效 + 记账，绝不乱折。
        return None, ("ENDPOINT_MISSING",
                      f"候选端点已不在图谱中（源存在={a is not None}，目标存在={b is not None}）")
    if src_id in active_map:
        return None, None   # 已经融合过，直接跳过（幂等，不算冲突）
    if src_id in staged:
        return None, ("MULTI_TARGET", f"同一源在同批中被指向多个目标，{src_id} 只保留分数最高的一条")

    # 规范节点按**当前**属性重算（扫描后节点可能被教师改过）
    source, target, canonical_reason = entity_resolver.choose_canonical(a, b)
    s_id, t_id = source["kp_id"], target["kp_id"]

    if _would_cycle(s_id, t_id, {**active_map, **staged}):
        return None, ("CYCLE", f"{s_id} → {t_id} 会与已有映射构成环，已拒绝")

    mapping_after = {**active_map, **staged, s_id: t_id}
    delta = _edge_delta(nodes, edges, {**active_map, **staged}, mapping_after)

    touching = [e for e in edges if s_id in (e.get("source"), e.get("target"))]
    return {
        "candidate_id": candidate.get("candidate_id"),
        "source": _pair_side(source), "target": _pair_side(target),
        "source_kp_id": s_id, "target_kp_id": t_id,
        "score": candidate.get("score"),
        "decision_source": candidate.get("decision_source") or "MANUAL",
        "canonical_reason": canonical_reason,
        "affected_relations": [
            {"id": e.get("id"), "type": e.get("type"),
             "direction": "out" if e.get("source") == s_id else "in",
             "other_endpoint": e.get("target") if e.get("source") == s_id else e.get("source")}
            for e in touching
        ],
        "impact": {
            "nodes_folded": 1,
            "edges_before": delta["edges_before"],
            "edges_after": delta["edges_after"],
            "edges_dropped": delta["dropped_count"],
            "dropped_detail": delta["dropped"][:20],
        },
    }, None


def _select_candidates(course_id, document_id, candidate_ids):
    """挑出本次要处理的候选：显式指定，或「已人工通过」+「规则判 SAME」"""
    if candidate_ids:
        out = []
        for cid in candidate_ids:
            row = sql_db.get_fusion_candidate(int(cid))
            if not row:
                return None, _fail(2001, f"候选不存在: candidate_id={cid}")
            if int(row["course_id"]) != int(course_id) or \
                    int(row["document_id"]) != int(document_id):
                return None, _fail(4003, f"候选 {cid} 不属于该文档，拒绝跨文档操作")
            out.append(row)
        return out, None

    total, rows = sql_db.list_fusion_candidates(course_id, document_id, page=1, page_size=1000)
    return [r for r in rows
            if r["status"] == "ACCEPTED"
            or (r["decision"] == "SAME" and r["status"] == "PENDING")], None


def apply(course_id: int, document_id, candidate_ids=None, dry_run: bool = True,
          operator: dict = None, run_id: str = None) -> dict:
    """应用融合。

    dry_run=True（**默认**）时零写入、不落 run、不写审计，返回逐条影响明细。
    正式执行在**一个 SQLite 事务**内完成：run → 候选状态 → 映射 → epoch → 失效向量，
    任一步异常整体回滚，**不留半完成状态**。全程不碰 Neo4j。
    """
    doc, err = _check_scope(course_id, document_id)
    if err:
        return err

    try:
        # strict=True：管理操作在真实状态未知时必须失败关闭，
        # 不能把「读不出映射」当成「没有映射」继续写
        active_map = load_active_mapping(course_id, document_id, strict=True)
        profiles_list, nodes, edges = _load_profiles(course_id, document_id)
    except Exception as e:  # noqa: BLE001
        return _fail(5002, f"读取融合状态或图谱失败: {e}")

    profiles = {p["kp_id"]: p for p in profiles_list}

    selected, err = _select_candidates(course_id, document_id, candidate_ids)
    if err:
        return err

    # 同一源出现多条 SAME 候选 → 只允许一条成为映射（目标唯一），其余记冲突
    by_source = {}
    for c in selected:
        by_source.setdefault(c["source_kp_id"], []).append(c)

    plans, conflicts, staged = [], [], {}
    for src_id, group in by_source.items():
        if len(group) > 1:
            entries = [{"target": profiles.get(g["target_kp_id"]) or {"kp_id": g["target_kp_id"]},
                        "score": g.get("score") or 0.0, "candidate": g} for g in group]
            chosen, superseded = entity_resolver.resolve_multi_target(entries)
            for s in superseded:
                conflicts.append((s["candidate"], "MULTI_TARGET",
                                  f"同一源 {src_id} 命中多个 SAME 目标，只保留分数最高的一条"))
            group = [chosen["candidate"]]

        for c in group:
            plan, conflict = _plan_item(c, profiles, edges, nodes, active_map, staged)
            if plan:
                staged[plan["source_kp_id"]] = plan["target_kp_id"]
                plans.append(plan)
            elif conflict:
                conflicts.append((c, conflict[0], conflict[1]))

    doc_payload = {
        "course_id": course_id, "document_id": document_id,
        "dry_run": bool(dry_run),
        "selected_count": len(selected),
        "planned_merges": plans,
        "planned_count": len(plans),
        "conflicts": [{"candidate_id": c.get("candidate_id"), "type": t, "detail": d}
                      for c, t, d in conflicts],
        "skipped_already_fused": sum(1 for c in selected
                                     if c["source_kp_id"] in active_map),
        "run_id": None,
    }

    if dry_run or not plans:
        return _ok(doc_payload)

    rid = run_id or uuid.uuid4().hex[:16]
    try:
        with sql_db._connect() as conn:
            sql_db.insert_fusion_run(
                rid, course_id, document_id, "APPLY",
                config_json=_json(fusion_config.snapshot()),
                operator_id=(operator or {}).get("user_id"),
                operator_name=(operator or {}).get("username"), conn=conn)

            applied = 0
            for plan in plans:
                fid = sql_db.insert_fusion_map(
                    course_id, document_id, plan["source_kp_id"], plan["target_kp_id"],
                    source_name=plan["source"]["name"], target_name=plan["target"]["name"],
                    source_category=plan["source"].get("category"),
                    target_category=plan["target"].get("category"),
                    source_description=plan["source"].get("description"),
                    score=plan.get("score"),
                    decision_source=plan.get("decision_source") or "MANUAL",
                    reason=plan.get("canonical_reason"), run_id=rid,
                    candidate_id=plan.get("candidate_id"),
                    operator_id=(operator or {}).get("user_id"),
                    operator_name=(operator or {}).get("username"), conn=conn)
                if fid:
                    applied += 1
                    if plan.get("candidate_id"):
                        sql_db.set_fusion_candidate_status(
                            plan["candidate_id"], "ACCEPTED",
                            reviewed_by=(operator or {}).get("user_id"),
                            comment="已应用融合", conn=conn)

            for cand, ctype, detail in conflicts:
                sql_db.insert_fusion_conflict(
                    course_id, document_id, ctype,
                    source_kp_id=cand.get("source_kp_id"),
                    target_kp_id=cand.get("target_kp_id"), detail=detail,
                    run_id=rid, conn=conn)

            # 融合状态变了 → epoch +1，并删掉该文档的向量，让下一次问答恰好重建一次
            sql_db.fusion_scope_bump_epoch(course_id, document_id, conn=conn)
            sql_db.delete_embeddings_by_document(course_id, document_id, conn=conn)

            sql_db.finish_fusion_run(rid, "COMPLETED", total_candidates=len(selected),
                                     n_applied=applied, conn=conn)
            conn.commit()
    except Exception as e:  # noqa: BLE001
        _logger.exception("应用融合失败 course_id=%s document_id=%s", course_id, document_id)
        return _fail(5003, f"应用融合失败（已整体回滚）: {e}")

    _invalidate_vector_cache(course_id)
    doc_payload["run_id"] = rid
    doc_payload["applied_count"] = len(plans)
    return _ok(doc_payload)


def revoke(course_id: int, document_id, fusion_id: int, operator: dict = None) -> dict:
    """撤销单条融合：只把映射置 REVOKED（**不删行**），历史与审计全部保留。"""
    doc, err = _check_scope(course_id, document_id)
    if err:
        return err

    rows = sql_db.list_fusion_map(course_id, document_id)
    row = next((r for r in rows if int(r["fusion_id"]) == int(fusion_id)), None)
    if not row:
        return _fail(2001, f"融合记录不存在或不属于该文档: fusion_id={fusion_id}")
    if row["status"] != "ACTIVE":
        return _ok({"fusion_id": fusion_id, "revoked": False, "already_revoked": True})

    try:
        with sql_db._connect() as conn:
            n = sql_db.revoke_fusion_map(course_id, document_id, fusion_id,
                                         revoked_by=(operator or {}).get("user_id"), conn=conn)
            if not n:
                conn.rollback()
                return _ok({"fusion_id": fusion_id, "revoked": False, "already_revoked": True})
            sql_db.fusion_scope_bump_epoch(course_id, document_id, conn=conn)
            sql_db.delete_embeddings_by_document(course_id, document_id, conn=conn)
            conn.commit()
    except Exception as e:  # noqa: BLE001
        return _fail(5003, f"撤销融合失败（已整体回滚）: {e}")

    _invalidate_vector_cache(course_id)
    return _ok({"fusion_id": fusion_id, "revoked": True, "already_revoked": False})


def undo_run(course_id: int, document_id, run_id: str, operator: dict = None) -> dict:
    """整批撤销某个 run 下的全部融合。"""
    doc, err = _check_scope(course_id, document_id)
    if err:
        return err

    run = sql_db.get_fusion_run(run_id)
    if not run or int(run["course_id"]) != int(course_id) \
            or int(run["document_id"]) != int(document_id):
        return _fail(2001, f"批次不存在或不属于该文档: run_id={run_id}")

    try:
        with sql_db._connect() as conn:
            n = sql_db.revoke_fusion_run(course_id, document_id, run_id,
                                         revoked_by=(operator or {}).get("user_id"), conn=conn)
            sql_db.fusion_scope_bump_epoch(course_id, document_id, conn=conn)
            sql_db.delete_embeddings_by_document(course_id, document_id, conn=conn)
            conn.commit()
    except Exception as e:  # noqa: BLE001
        return _fail(5003, f"整批撤销失败（已整体回滚）: {e}")

    _invalidate_vector_cache(course_id)
    return _ok({"run_id": run_id, "revoked_count": n})


def _invalidate_vector_cache(course_id):
    """融合状态变更后立刻失效进程内向量缓存（同进程立即生效；跨进程由 TTL 收敛）"""
    try:
        from ..vector_index import vector_index
        vector_index.invalidate(kind="kp", course_id=course_id)
    except Exception as e:  # noqa: BLE001 - 失效失败不影响融合结果本身
        _logger.warning("失效向量缓存失败 course_id=%s: %s", course_id, e)


def _pair_side(p: dict) -> dict:
    """给 LLM / 前端看的实体上下文（不含内部字段）"""
    ctx = p.get("context") or {}
    return {
        "kp_id": p["kp_id"], "name": p["name"], "category": p.get("category"),
        "description": p.get("description") or "", "is_manual": p.get("is_manual"),
        "aliases": p.get("aliases") or [],
        # 明确标注这是**重建**的文本命中上下文，不是原始抽取证据
        "reconstructed_context": {
            "reconstruction_ok": bool(ctx.get("reconstruction_ok")),
            "hit_terms": ctx.get("hit_terms") or [],
            "chunk_indices": ctx.get("chunk_indices") or [],
            "snippets": ctx.get("snippets") or [],
            "reason": ctx.get("reason"),
        },
    }


def _json(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)
