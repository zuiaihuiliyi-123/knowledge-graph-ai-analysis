"""文档级知识融合（Fusion）P2：应用、幂等、整体回滚与撤销

运行方式（在 backend 目录下执行）：
    python test_fusion_idempotency_undo.py

隔离手段（见 fusion_testkit）：**app.db 副本** + **合成图替换 Neo4j 读取**，
全程不写线上库、不写 Neo4j。
"""
import json
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.sql_database import sql_db
from app.services.fusion import fusion_pipeline as FP
from fusion_testkit import Harness, default_scenario, make_edge, make_node

failures = []


def check(label, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def edge_keys(graph):
    return sorted((e["source"], e["target"], e["type"]) for e in graph["edges"])


def main():
    print("=" * 72)
    print("文档级知识融合 P2：应用 / 幂等 / 回滚 / 撤销")
    print("=" * 72)

    with Harness() as h:
        cid, did = h.course_id, h.document_id
        nodes, edges = default_scenario()
        h.set_graph(nodes, edges)

        baseline = json.dumps(h.folded_graph, sort_keys=True, ensure_ascii=False)

        # ---------- 1. 扫描并落库 ----------
        print("\nStep 1: 扫描候选并落库")
        import asyncio
        r = asyncio.run(FP.scan(cid, did, persist=True, use_llm=False))
        check("扫描成功", r["ok"], r.get("message"))
        check("产出了候选", r["data"]["candidate_count"] > 0,
              f"{r['data']['candidate_count']} 条")
        total, rows = sql_db.list_fusion_candidates(cid, did)
        check("候选已落库", total == r["data"]["candidate_count"], f"{total} 条")

        # ---------- 2. 未审核的候选不会被 apply 选中 ----------
        print("\nStep 2: 只有「已人工通过」或「规则判 SAME」的候选会被应用")
        r = FP.apply(cid, did, dry_run=True)
        check("dry_run 可调用", r["ok"], r.get("message"))
        check("（本题候选均为 UNCERTAIN，未审核）不产生合并计划",
              r["data"]["planned_count"] == 0,
              f"selected={r['data']['selected_count']} planned={r['data']['planned_count']}")

        # 驳回一条，确认它不会再被选中
        cand = next(x for x in rows if x["source_kp_id"] == "kp_a" and x["target_kp_id"] == "kp_b")
        sql_db.set_fusion_candidate_status(cand["candidate_id"], "REJECTED",
                                           reviewed_by=1, comment="先驳回看看")
        r = FP.apply(cid, did, dry_run=True)
        check("被驳回的候选不会被应用", r["data"]["planned_count"] == 0)

        # 再改判为通过
        sql_db.set_fusion_candidate_status(cand["candidate_id"], "ACCEPTED",
                                           reviewed_by=1, comment="确认同义")

        # ---------- 3. Dry Run 零写入 ----------
        print("\nStep 3: Dry Run 必须零业务数据写入")
        before = {
            "maps": sql_db.count_fusion_map(cid, did),
            "revoked": sql_db.count_fusion_map(cid, did, status="REVOKED"),
            "runs": sql_db.list_fusion_runs(cid, did)[0],
            "scope": sql_db.fusion_scope_get(cid, did),
        }
        r = FP.apply(cid, did, dry_run=True)
        check("dry_run 返回了合并计划", r["data"]["planned_count"] == 1,
              f"planned={r['data']['planned_count']}")
        plan = r["data"]["planned_merges"][0]
        check("计划里含源/目标与影响范围",
              plan["source"]["kp_id"] == "kp_a" and plan["target"]["kp_id"] == "kp_b"
              and plan["impact"]["edges_after"] is not None, str(plan["impact"]))
        check("计划里说明为何选它当规范节点", bool(plan["canonical_reason"]),
              plan["canonical_reason"])
        check("计划里列了受影响的边", len(plan["affected_relations"]) == 2,
              str(plan["affected_relations"]))
        after = {
            "maps": sql_db.count_fusion_map(cid, did),
            "revoked": sql_db.count_fusion_map(cid, did, status="REVOKED"),
            "runs": sql_db.list_fusion_runs(cid, did)[0],
            "scope": sql_db.fusion_scope_get(cid, did),
        }
        check("dry_run 未写入任何映射", after["maps"] == before["maps"], str(after["maps"]))
        check("dry_run 未新建批次", after["runs"] == before["runs"])
        check("dry_run 未改动融合 epoch", after["scope"] == before["scope"], str(after["scope"]))
        check("dry_run 后图谱逐字节不变",
              json.dumps(h.folded_graph, sort_keys=True, ensure_ascii=False) == baseline)

        # ---------- 4. 正式应用 ----------
        print("\nStep 4: 正式应用")
        r = FP.apply(cid, did, dry_run=False)
        check("应用成功", r["ok"], r.get("message"))
        check("写入了 1 条映射", sql_db.count_fusion_map(cid, did, status="ACTIVE") == 1)
        check("生成了 run_id", bool(r["data"]["run_id"]), str(r["data"].get("run_id")))

        g = h.folded_graph
        check("折叠后节点数 4 → 3", len(g["nodes"]) == 3, f"{len(g['nodes'])}")
        check("折叠后边数 4 → 2（去重一条、自环丢一条）",
              len(g["edges"]) == 2, str(edge_keys(g)))
        check("边被改写到规范节点", ("kp_b", "kp_c", "APPLIES_TO") in edge_keys(g), str(edge_keys(g)))
        check("重复边保留了 confidence 更高的那条",
              max(e["properties"]["confidence"] for e in g["edges"] if e["target"] == "kp_c") == 0.95,
              str([(e["target"], e["properties"]["confidence"]) for e in g["edges"]]))
        host = next(n for n in g["nodes"] if n["id"] == "kp_b")
        check("宿主节点保留了被折掉的来源信息",
              [f["kp_id"] for f in host["properties"].get("fused_from", [])] == ["kp_a"],
              str(host["properties"].get("fused_from")))
        check("被折掉的原名称没有丢失",
              host["properties"]["fused_from"][0]["name"] == "BFS")
        check("候选被标记为已通过",
              sql_db.get_fusion_candidate(cand["candidate_id"])["status"] == "ACCEPTED")

        epoch_after_apply = sql_db.fusion_scope_get(cid, did)["epoch"]

        # ---------- 5. 重复应用幂等 ----------
        print("\nStep 5: 重复应用必须幂等")
        r2 = FP.apply(cid, did, dry_run=False)
        check("重复应用不新增映射", sql_db.count_fusion_map(cid, did) == 1,
              f"{sql_db.count_fusion_map(cid, did)} 条")
        check("重复应用时该源已被融合，计划为空", r2["data"]["planned_count"] == 0,
              f"planned={r2['data']['planned_count']}")
        check("重复应用不新建 epoch",
              sql_db.fusion_scope_get(cid, did)["epoch"] == epoch_after_apply,
              str(sql_db.fusion_scope_get(cid, did)["epoch"]))
        check("重复应用后图谱仍与首次一致",
              json.dumps(h.folded_graph, sort_keys=True, ensure_ascii=False) ==
              json.dumps(g, sort_keys=True, ensure_ascii=False))

        # ---------- 6. 撤销：逐字节恢复 ----------
        print("\nStep 6: 撤销后读图必须逐字节恢复，且历史一律保留")
        scope_before_revoke = sql_db.fusion_scope_get(cid, did)["epoch"]
        _, run_rows = sql_db.list_fusion_runs(cid, did)
        rid = run_rows[0]["run_id"]

        maps = sql_db.list_fusion_map(cid, did, status="ACTIVE")
        rv = FP.revoke(cid, did, maps[0]["fusion_id"])
        check("撤销成功", rv["ok"] and rv["data"]["revoked"], str(rv))
        check("撤销后图谱与融合前**逐字节相同**",
              json.dumps(h.folded_graph, sort_keys=True, ensure_ascii=False) == baseline)
        check("撤销后 ACTIVE 映射为 0", sql_db.count_fusion_map(cid, did, status="ACTIVE") == 0)
        check("撤销**不删行**（映射历史仍在）", sql_db.count_fusion_map(cid, did) == 1,
              f"{sql_db.count_fusion_map(cid, did)} 行")
        check("映射行标记为 REVOKED",
              sql_db.list_fusion_map(cid, did)[0]["status"] == "REVOKED")
        check("撤销记录了操作人与时间",
              sql_db.list_fusion_map(cid, did)[0]["revoked_at"] is not None)
        check("候选的审核结论不因撤销而丢失",
              sql_db.get_fusion_candidate(cand["candidate_id"])["status"] == "ACCEPTED")
        check("批次记录仍在", sql_db.get_fusion_run(rid) is not None)
        check("撤销会推进 epoch（触发索引重建）",
              sql_db.fusion_scope_get(cid, did)["epoch"] > scope_before_revoke,
              f"{scope_before_revoke} -> {sql_db.fusion_scope_get(cid, did)['epoch']}")

        check("重复撤销幂等（返回已撤销）",
              FP.revoke(cid, did, maps[0]["fusion_id"])["data"].get("already_revoked") is True)

        # 撤销后**重新应用**必须真正生效。
        # 这里曾踩到真实缺陷：ON CONFLICT DO NOTHING 会撞上那行 REVOKED 的旧记录，
        # rowcount=0、映射没复活，接口却返回成功 —— 表现为「点了应用但图谱没变」。
        sql_db.set_fusion_candidate_status(cand["candidate_id"], "ACCEPTED", reviewed_by=1)
        r_re = FP.apply(cid, did, dry_run=False)
        check("撤销后重新应用成功", r_re["ok"] and r_re["data"]["applied_count"] == 1,
              f"applied={r_re['data'].get('applied_count')}")
        check("重新应用后映射重新变为 ACTIVE",
              sql_db.count_fusion_map(cid, did, status="ACTIVE") == 1,
              str(sql_db.count_fusion_map(cid, did, status="ACTIVE")))
        check("重新应用后旧行被复活（不新增行）", sql_db.count_fusion_map(cid, did) == 1,
              str(sql_db.count_fusion_map(cid, did)))
        check("重新应用后 revoked_at 被清空",
              sql_db.list_fusion_map(cid, did)[0]["revoked_at"] is None)
        check("重新应用后图谱与首次融合一致",
              json.dumps(h.folded_graph, sort_keys=True, ensure_ascii=False) ==
              json.dumps(g, sort_keys=True, ensure_ascii=False))

        # ---------- 7. 整体回滚 ----------
        print("\nStep 7: 事务中途失败必须整体回滚，不留半完成状态")
        # 先撤销上一步的融合，让这次 apply 真的有活可干 ——
        # 否则该源已 ACTIVE，apply 会在事务之前就提前返回，
        # 测试会"通过"却根本没覆盖回滚路径（前置断言就是防这个）。
        for m in sql_db.list_fusion_map(cid, did, status="ACTIVE"):
            FP.revoke(cid, did, m["fusion_id"])
        sql_db.set_fusion_candidate_status(cand["candidate_id"], "ACCEPTED", reviewed_by=1)
        pre = FP.apply(cid, did, dry_run=True)
        check("（前置）确有可执行的合并计划，回滚路径才被真正覆盖",
              pre["data"]["planned_count"] == 1, f"planned={pre['data']['planned_count']}")

        snap_maps = sql_db.count_fusion_map(cid, did)
        snap_scope = sql_db.fusion_scope_get(cid, did)
        snap_emb = sql_db._query_one(
            "SELECT count(*) AS c FROM t_kp_embedding WHERE course_id = ? AND document_id = ?",
            (cid, did))["c"]

        orig_bump = sql_db.fusion_scope_bump_epoch

        def boom(*a, **kw):
            raise RuntimeError("模拟写库失败")

        sql_db.fusion_scope_bump_epoch = boom
        try:
            r3 = FP.apply(cid, did, dry_run=False)
        finally:
            sql_db.fusion_scope_bump_epoch = orig_bump

        check("应用失败时返回明确错误", not r3["ok"], str(r3.get("message")))
        check("回滚后映射数未变", sql_db.count_fusion_map(cid, did) == snap_maps,
              f"{sql_db.count_fusion_map(cid, did)} vs {snap_maps}")
        check("回滚后 epoch 未变", sql_db.fusion_scope_get(cid, did) == snap_scope)
        check("回滚后向量未被误删",
              sql_db._query_one("SELECT count(*) AS c FROM t_kp_embedding "
                                "WHERE course_id = ? AND document_id = ?", (cid, did))["c"] == snap_emb)

        # ---------- 8. 跨文档隔离 ----------
        print("\nStep 8: 跨文档 / 跨课程的伪造请求必须被拒")
        r4 = FP.apply(cid + 1, did, dry_run=False)
        check("课程不匹配 → 拒绝", not r4["ok"] and r4["code"] == 4003, str(r4.get("message")))
        r5 = FP.apply(cid, did + 1, dry_run=False)
        check("文档不存在 → 拒绝", not r5["ok"] and r5["code"] == 2001, str(r5.get("message")))
        r6 = FP.revoke(cid, did, 999999)
        check("撤销不存在的融合记录 → 拒绝", not r6["ok"] and r6["code"] == 2001,
              str(r6.get("message")))

        # ---------- 9. 环与多目标冲突 ----------
        print("\nStep 9: 环检测与多目标冲突")
        h.set_graph(
            nodes=[make_node("kp_x", "甲"), make_node("kp_y", "乙"), make_node("kp_z", "丙")],
            edges=[make_edge("e1", "kp_x", "kp_y")])
        sql_db.delete_fusion_by_document(cid, did)
        sql_db.insert_fusion_map(cid, did, "kp_x", "kp_y", "甲", "乙")
        import asyncio as _a
        _a.run(FP.scan(cid, did, persist=True))
        # 手工造一条反向候选并置为 ACCEPTED
        sql_db.upsert_fusion_candidates(cid, did, [{
            "source_kp_id": "kp_y", "target_kp_id": "kp_x", "source_name": "乙",
            "target_name": "甲", "source_category": "概念", "target_category": "概念",
            "score": 0.99, "decision": "SAME", "decision_source": "MANUAL"}])
        _, cands = sql_db.list_fusion_candidates(cid, did)
        rev = next(c for c in cands if c["source_kp_id"] == "kp_y")
        sql_db.set_fusion_candidate_status(rev["candidate_id"], "ACCEPTED", reviewed_by=1)

        r7 = FP.apply(cid, did, dry_run=True)
        cycle = [c for c in r7["data"]["conflicts"] if c["type"] == "CYCLE"]
        check("反向映射被判为环并拒绝", len(cycle) == 1, str(r7["data"]["conflicts"]))
        check("成环的候选没有进入合并计划",
              all(p["source_kp_id"] != "kp_y" for p in r7["data"]["planned_merges"]))

        # 多目标：同一个源指向两个不同目标
        sql_db.delete_fusion_by_document(cid, did)
        sql_db.upsert_fusion_candidates(cid, did, [
            {"source_kp_id": "kp_x", "target_kp_id": "kp_y", "source_name": "甲",
             "target_name": "乙", "source_category": "概念", "target_category": "概念",
             "score": 0.90, "decision": "SAME", "decision_source": "RULE"},
            {"source_kp_id": "kp_x", "target_kp_id": "kp_z", "source_name": "甲",
             "target_name": "丙", "source_category": "概念", "target_category": "概念",
             "score": 0.95, "decision": "SAME", "decision_source": "RULE"},
        ])
        r8 = FP.apply(cid, did, dry_run=True)
        # 注意方向：候选行只标识「这一对」，**规范节点方向在 apply 时按当前节点属性重算**
        # （扫描之后节点可能被教师改过），所以这里断言的是「合并了哪一对」，
        # 而不是候选行里写的那个方向。
        plan0 = r8["data"]["planned_merges"][0]
        pair = {plan0["source_kp_id"], plan0["target_kp_id"]}
        check("同一源命中两个目标时只保留高分那条（合并 kp_x/kp_z 这一对）",
              r8["data"]["planned_count"] == 1 and pair == {"kp_x", "kp_z"},
              f"planned={[(p['source_kp_id'], p['target_kp_id']) for p in r8['data']['planned_merges']]}")
        check("方向由 apply 时的规则梯子重算，且 source != target",
              plan0["source_kp_id"] != plan0["target_kp_id"]
              and bool(plan0["canonical_reason"]),
              f"{plan0['source_kp_id']} -> {plan0['target_kp_id']}（{plan0['canonical_reason']}）")
        multi = [c for c in r8["data"]["conflicts"] if c["type"] == "MULTI_TARGET"]
        check("被顶掉的记入 MULTI_TARGET 冲突", len(multi) == 1, str(r8["data"]["conflicts"]))

        # ---------- 10. 端点失效 ----------
        print("\nStep 10: 端点已不在图中时，候选静默失效并记账")
        h.set_graph(nodes=[make_node("kp_y", "乙"), make_node("kp_z", "丙")], edges=[])
        sql_db.delete_fusion_by_document(cid, did)
        sql_db.upsert_fusion_candidates(cid, did, [{
            "source_kp_id": "kp_gone", "target_kp_id": "kp_y", "source_name": "已消失",
            "target_name": "乙", "source_category": "概念", "target_category": "概念",
            "score": 0.9, "decision": "SAME", "decision_source": "RULE"}])
        r9 = FP.apply(cid, did, dry_run=True)
        missing = [c for c in r9["data"]["conflicts"] if c["type"] == "ENDPOINT_MISSING"]
        check("端点缺失被记为冲突而非崩溃", len(missing) == 1, str(r9["data"]["conflicts"]))
        check("缺失端点的候选不进入计划", r9["data"]["planned_count"] == 0)

    print("\n" + "=" * 72)
    if failures:
        print(f"失败 {len(failures)} 项：")
        for f in failures:
            print("  -", f)
    else:
        print("全部通过")
    print("=" * 72)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
