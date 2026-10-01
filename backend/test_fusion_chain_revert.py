"""文档级知识融合（Fusion）P2：链式融合在不同撤销顺序下的结果

运行方式（在 backend 目录下执行）：
    python test_fusion_chain_revert.py

初始融合：`A→B`、`B→C`（映射**不做写时扁平化**，两条独立存在）。
本脚本断言：撤销任意子集后，读图结果都自洽且**确定** ——
这是"可撤销"能不能成立的关键，也决定了管理员能不能放心地逐条回退。

隔离手段同 fusion_testkit：app.db 副本 + 合成图。
"""
import json
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.sql_database import sql_db
from app.services.fusion import fusion_pipeline as FP
from fusion_testkit import Harness, make_edge, make_node

failures = []


def check(label, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


# confidence 刻意排成 0.5 < 0.8 < 0.9：
# 规范节点梯子的第 2 条就决定了方向，于是 A→B、B→C 这两条链式映射方向确定，
# 不会被第 4 条（度数）翻转 —— 否则两条候选会同时以 kp_b 为源，被判成多目标冲突。
CHAIN_NODES = [
    make_node("kp_a", "BFS", description="广度优先搜索（BFS）", confidence=0.5),
    make_node("kp_b", "广度优先遍历", description="一种图遍历算法", confidence=0.8),
    make_node("kp_c", "广度优先搜索", description="一种图遍历算法", confidence=0.9),
    make_node("kp_z", "图", description="由顶点和边组成", confidence=0.9),
]
CHAIN_EDGES = [make_edge("e1", "kp_a", "kp_z", "APPLIES_TO"),
               make_edge("e2", "kp_c", "kp_z", "APPLIES_TO")]


def label_set(graph):
    return sorted(n["label"] for n in graph["nodes"])


def apply_pair(h, pairs):
    """把指定的一对置为 ACCEPTED 并应用（应用后返回 fusion_id 列表）"""
    cid, did = h.course_id, h.document_id
    for src, tgt in pairs:
        sql_db.upsert_fusion_candidates(cid, did, [{
            "source_kp_id": src, "target_kp_id": tgt,
            "source_name": src, "target_name": tgt,
            "source_category": "概念", "target_category": "概念",
            "score": 0.95, "decision": "SAME", "decision_source": "MANUAL"}])
    _, rows = sql_db.list_fusion_candidates(cid, did)
    for r in rows:
        if (r["source_kp_id"], r["target_kp_id"]) in pairs:
            sql_db.set_fusion_candidate_status(r["candidate_id"], "ACCEPTED", reviewed_by=1)
    r = FP.apply(cid, did, dry_run=False)
    assert r["ok"], r.get("message")
    return r


def build_chain_state(h):
    """在副本上造出 A→B、B→C 的已应用状态，返回 (run_id, {('a','b'): fusion_id, ...})"""
    cid, did = h.course_id, h.document_id
    h.set_graph(CHAIN_NODES, CHAIN_EDGES)
    apply_pair(h, [("kp_a", "kp_b"), ("kp_b", "kp_c")])
    maps = sql_db.list_fusion_map(cid, did, status="ACTIVE")
    by_src = {m["source_kp_id"]: m["fusion_id"] for m in maps}
    return by_src


def main():
    print("=" * 72)
    print("文档级知识融合 P2：链式融合的撤销顺序")
    print("=" * 72)

    with Harness() as h:
        cid, did = h.course_id, h.document_id

        # ---------- 0. 建立链式状态 ----------
        print("\nStep 0: 建立 A→B、B→C 两条映射")
        by_src = build_chain_state(h)
        check("两条映射都已写入", set(by_src) == {"kp_a", "kp_b"}, str(by_src))
        g = h.folded_graph
        check("链式归一：A、B 都折进 C（只剩 C 与无关的「图」）",
              label_set(g) == ["图", "广度优先搜索"], str(label_set(g)))
        host = next(n for n in g["nodes"] if n["label"] == "广度优先搜索")
        check("root 的 fused_from 同时含 A 与 B",
              {f["kp_id"] for f in host["properties"].get("fused_from", [])} == {"kp_a", "kp_b"},
              str([f["kp_id"] for f in host["properties"].get("fused_from", [])]))
        check("边改写到 root 并去重为一条", len(g["edges"]) == 1, str(len(g["edges"])))

        full_state = json.dumps(g, sort_keys=True, ensure_ascii=False)

        # ---------- 1. 顺序一：先撤 B→C ----------
        print("\nStep 1: 先撤 B→C → 剩 A→B：A 折进 B，C 独立")
        rv = FP.revoke(cid, did, by_src["kp_b"])
        check("撤销成功", rv["ok"] and rv["data"]["revoked"], str(rv))
        g1 = h.folded_graph
        check("A 折进 B 后：B、C 同时作为独立节点存在",
              {"广度优先遍历", "广度优先搜索"} <= set(label_set(g1)), str(label_set(g1)))
        b_node = next(n for n in g1["nodes"] if n["label"] == "广度优先遍历")
        check("A 折进了 B", [f["kp_id"] for f in b_node["properties"].get("fused_from", [])]
              == ["kp_a"], str(b_node["properties"].get("fused_from")))
        check("C 成为独立节点，未被折进别人",
              not next(n for n in g1["nodes"] if n["label"] == "广度优先搜索")
              ["properties"].get("fused_from"))

        # 再撤 A→B → 回到全部独立
        FP.revoke(cid, did, by_src["kp_a"])
        g1b = h.folded_graph
        check("两条都撤后回到全部独立（4 个节点）", len(g1b["nodes"]) == 4,
              str(label_set(g1b)))
        check("两条都撤后图中不再有 fused_from",
              not any(n["properties"].get("fused_from") for n in g1b["nodes"]),
              str(label_set(g1b)))
        check("两条都撤后边数为 2", len(g1b["edges"]) == 2, str(len(g1b["edges"])))
        check("映射行仍在（只改状态不删行）",
              sql_db.count_fusion_map(cid, did) == 2, str(sql_db.count_fusion_map(cid, did)))
        check("两条都是 REVOKED",
              all(m["status"] == "REVOKED" for m in sql_db.list_fusion_map(cid, did)))

        # ---------- 2. 顺序二：先撤 A→B ----------
        print("\nStep 2: 先撤 A→B → 剩 B→C：B 折进 C，A 独立")
        sql_db.delete_fusion_by_document(cid, did)
        by_src2 = build_chain_state(h)
        rv = FP.revoke(cid, did, by_src2["kp_a"])
        check("撤销成功", rv["ok"] and rv["data"]["revoked"])
        g2 = h.folded_graph
        check("B 折进 C 后：A、C 同时作为独立节点存在",
              {"BFS", "广度优先搜索"} <= set(label_set(g2)), str(label_set(g2)))
        c_node = next(n for n in g2["nodes"] if n["label"] == "广度优先搜索")
        check("B 折进了 C", [f["kp_id"] for f in c_node["properties"].get("fused_from", [])]
              == ["kp_b"], str(c_node["properties"].get("fused_from")))
        check("A 成为独立节点", not next(n for n in g2["nodes"] if n["label"] == "BFS")
              ["properties"].get("fused_from"))

        # 再撤 B→C → 全部独立
        FP.revoke(cid, did, by_src2["kp_b"])
        g2b = h.folded_graph
        check("两条都撤后同样回到全部独立", len(g2b["nodes"]) == 4, str(label_set(g2b)))
        check("两种撤销顺序的最终结果一致",
              json.dumps(g1b, sort_keys=True, ensure_ascii=False)
              == json.dumps(g2b, sort_keys=True, ensure_ascii=False))

        # ---------- 3. 整批撤销 ----------
        print("\nStep 3: 整批撤销（按 run）")
        sql_db.delete_fusion_by_document(cid, did)
        build_chain_state(h)
        _, runs = sql_db.list_fusion_runs(cid, did)
        rid = runs[0]["run_id"]
        r = FP.undo_run(cid, did, rid)
        check("整批撤销成功", r["ok"] and r["data"]["revoked_count"] == 2, str(r))
        check("整批撤销后回到全部独立", len(h.folded_graph["nodes"]) == 4)
        check("run 状态标记为 REVOKED",
              sql_db.get_fusion_run(rid)["status"] == "REVOKED",
              sql_db.get_fusion_run(rid)["status"])
        check("整批撤销不删行",
              sql_db.count_fusion_map(cid, did) == 2)
        check("整批撤销后所有映射均为 REVOKED",
              all(m["status"] == "REVOKED" for m in sql_db.list_fusion_map(cid, did)))

        # ---------- 4. 撤销后重新应用 ----------
        print("\nStep 4: 撤销后重新应用，结果必须与首次一致")
        sql_db.set_fusion_candidate_status(
            sql_db.list_fusion_candidates(cid, did)[1][0]["candidate_id"], "ACCEPTED",
            reviewed_by=1)
        sql_db.set_fusion_candidate_status(
            sql_db.list_fusion_candidates(cid, did)[1][1]["candidate_id"], "ACCEPTED",
            reviewed_by=1)
        r2 = FP.apply(cid, did, dry_run=False)
        check("重新应用成功", r2["ok"], str(r2.get("message")))
        check("重新应用后图谱与首次融合完全一致",
              json.dumps(h.folded_graph, sort_keys=True, ensure_ascii=False) == full_state,
              f"planned={r2['data'].get('planned_count')}")

        # ---------- 5. 撤销不丢历史 ----------
        print("\nStep 5: 撤销不得丢失审核结论 / 历史映射 / 冲突记录")
        sql_db.insert_fusion_conflict(cid, did, "MULTI_TARGET", "kp_a", "kp_c", "测试冲突")
        _, cands = sql_db.list_fusion_candidates(cid, did)
        accepted_ids = {c["candidate_id"] for c in cands if c["status"] == "ACCEPTED"}
        maps_before = {m["fusion_id"]: dict(m) for m in sql_db.list_fusion_map(cid, did)}

        for m in sql_db.list_fusion_map(cid, did, status="ACTIVE"):
            FP.revoke(cid, did, m["fusion_id"])

        _, cands_after = sql_db.list_fusion_candidates(cid, did)
        check("候选的审核结论逐条保留",
              {c["candidate_id"] for c in cands_after if c["status"] == "ACCEPTED"}
              == accepted_ids)
        check("映射历史逐行保留（只是状态变了）",
              set(m["fusion_id"] for m in sql_db.list_fusion_map(cid, did))
              == set(maps_before))
        check("撤销没有抹掉映射的属性快照",
              all(m["source_name"] for m in sql_db.list_fusion_map(cid, did)))
        total_conf, conflicts = sql_db.list_fusion_conflicts(cid, did)
        check("冲突记录不受撤销影响", total_conf >= 1, str(total_conf))

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
