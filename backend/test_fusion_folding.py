"""文档级知识融合（Fusion）P0：读图折叠 `fold_graph` 的纯函数语义

运行方式（在 backend 目录下执行）：
    python test_fusion_folding.py

本脚本**完全离线**（不起 Neo4j、不写任何库）：折叠是一个纯函数，
所有边界都可以用构造的数据穷尽覆盖。末尾有一个依赖 Neo4j 的**只读**一致性探针，
Neo4j 不可用时记为 SKIP（不计入通过）。
"""
import copy
import json
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.services.fusion.graph_folder import fold_graph, resolve_root

failures = []
skipped = []


def check(label, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def skip(label, detail=""):
    print(f"  [SKIP] {label}" + (f" — {detail}" if detail else ""))
    skipped.append(label)


def node(kp_id, name, category="概念", description="", confidence=0.9, is_manual=False):
    return {
        "id": kp_id, "label": name,
        "type": {"概念": "concept", "定理": "theorem", "公式": "formula",
                 "方法": "method"}.get(category, "concept"),
        "description": description,
        "properties": {"category": category, "confidence": confidence,
                       "is_manual": is_manual},
    }


def edge(eid, source, target, rtype="RELATED_TO", confidence=0.9, is_manual=False):
    return {"id": eid, "source": source, "target": target, "type": rtype,
            "label": rtype, "properties": {"confidence": confidence,
                                           "is_manual": is_manual}}


def ids(nodes):
    return [n["id"] for n in nodes]


def ekeys(edges):
    return [(e["source"], e["target"], e["type"]) for e in edges]


def main():
    print("=" * 72)
    print("文档级知识融合 P0：读图折叠 fold_graph（纯函数）")
    print("=" * 72)

    # ---------- 1. 空映射 = 纯 no-op ----------
    print("\nStep 1: 空映射必须逐字节 no-op（返回入参本身）")
    nodes = [node("kp_b", "BFS"), node("kp_s", "广度优先搜索")]
    edges = [edge("e1", "kp_b", "kp_s")]
    r_nodes, r_edges = fold_graph(nodes, edges, {})
    check("空 dict 映射：返回的就是入参对象本身",
          r_nodes is nodes and r_edges is edges)
    check("空 dict 映射：JSON 逐字节相同",
          json.dumps(r_nodes, sort_keys=True) == json.dumps(nodes, sort_keys=True)
          and json.dumps(r_edges, sort_keys=True) == json.dumps(edges, sort_keys=True))
    r_nodes2, r_edges2 = fold_graph(nodes, edges, None)
    check("None 映射同样 no-op", r_nodes2 is nodes and r_edges2 is edges)

    # ---------- 2. 单条映射 ----------
    print("\nStep 2: 单条映射 A→B（B 占据 A 的位置，A 折进 B）")
    nodes = [node("kp_a", "BFS", "概念", "广度优先搜索的英文缩写"),
             node("kp_b", "广度优先搜索", "概念", ""),
             node("kp_c", "图", "概念", "一种数据结构")]
    edges = [edge("e1", "kp_a", "kp_c", "APPLIES_TO"),
             edge("e2", "kp_b", "kp_c", "APPLIES_TO")]
    rep = {}
    out_n, out_e = fold_graph(nodes, edges, {"kp_a": "kp_b"}, report=rep)

    check("节点数 3 -> 2", len(out_n) == 2, f"实际 {len(out_n)}")
    check("root(kp_b) 占据了 kp_a 的原始位置（首位）", ids(out_n)[0] == "kp_b", str(ids(out_n)))
    check("被折掉的 kp_a 不再作为独立节点出现", "kp_a" not in ids(out_n))
    host = out_n[0]
    check("宿主节点名称仍是 canonical name（未被覆盖）", host["label"] == "广度优先搜索")
    check("宿主 description 未被覆盖（仍是它自己的）", host["description"] == "")
    fused = host["properties"].get("fused_from") or []
    check("fused_from 记录了源节点", len(fused) == 1 and fused[0]["kp_id"] == "kp_a")
    check("fused_from 保留了源的名称与描述（信息不丢）",
          fused[0]["name"] == "BFS" and fused[0]["description"] == "广度优先搜索的英文缩写")
    check("fused_count 已置", host["properties"].get("fused_count") == 1)

    check("两条边折叠后重复，只留一条", len(out_e) == 1, str(ekeys(out_e)))
    check("留下的边指向 root", out_e[0]["source"] == "kp_b" and out_e[0]["target"] == "kp_c")
    check("report.deduped 记了 1 条", len(rep.get("deduped") or []) == 1, str(rep.get("deduped")))

    # ---------- 3. 入参不被修改 ----------
    print("\nStep 3: 纯函数 —— 不得修改入参")
    nodes = [node("kp_a", "BFS"), node("kp_b", "广度优先搜索")]
    edges = [edge("e1", "kp_a", "kp_b")]
    snapshot_n = json.dumps(nodes, sort_keys=True)
    snapshot_e = json.dumps(edges, sort_keys=True)
    fold_graph(nodes, edges, {"kp_a": "kp_b"})
    check("入参 nodes 未被改写", json.dumps(nodes, sort_keys=True) == snapshot_n)
    check("入参 edges 未被改写", json.dumps(edges, sort_keys=True) == snapshot_e)

    # ---------- 4. 链式 A→B→C ----------
    print("\nStep 4: 链式映射 A→B、B→C 归一到 C")
    chain = {"kp_a": "kp_b", "kp_b": "kp_c"}
    check("resolve_root(A) == C", resolve_root("kp_a", chain) == "kp_c")
    check("resolve_root(B) == C", resolve_root("kp_b", chain) == "kp_c")
    check("resolve_root(C) == C", resolve_root("kp_c", chain) == "kp_c")

    nodes = [node("kp_a", "BFS"), node("kp_b", "广度优先遍历"), node("kp_c", "广度优先搜索"),
             node("kp_d", "队列")]
    edges = [edge("e1", "kp_b", "kp_d", "APPLIES_TO")]
    rep = {}
    out_n, out_e = fold_graph(nodes, edges, chain, report=rep)
    check("链式折叠后只剩 2 个节点", len(out_n) == 2, str(ids(out_n)))
    check("C 占据了该分量首次出现的位置", ids(out_n)[0] == "kp_c", str(ids(out_n)))
    host = out_n[0]
    check("C 的 fused_from 同时含 A 与 B",
          {f["kp_id"] for f in host["properties"]["fused_from"]} == {"kp_a", "kp_b"},
          str(host["properties"]["fused_from"]))
    check("边被改写到 root C", out_e and out_e[0]["source"] == "kp_c", str(ekeys(out_e)))

    # ---------- 5. 撤销语义：链的不同组合 ----------
    print("\nStep 5: 链式在不同撤销组合下的结果（映射不扁平化 → 任意子集都自洽）")
    base_nodes = [node("kp_a", "A"), node("kp_b", "B"), node("kp_c", "C")]
    # 初始 A→B、B→C。映射**不做写时扁平化**，所以撤销任意子集都自洽：
    #   撤掉 B→C  → 剩 A→B → A 折进 B，C 独立        → 节点 {B, C}
    #   撤掉 A→B  → 剩 B→C → B 折进 C，A 独立        → 节点 {A, C}
    combos = [
        ({"kp_a": "kp_b", "kp_b": "kp_c"}, "kp_c", {"kp_c"}),
        ({"kp_b": "kp_c"}, "kp_c", {"kp_a", "kp_c"}),
        ({"kp_a": "kp_b"}, "kp_b", {"kp_b", "kp_c"}),
        ({}, None, {"kp_a", "kp_b", "kp_c"}),
    ]
    for mapping, expected_root, expected_set in combos:
        out_n, _ = fold_graph(base_nodes, [], mapping)
        got = set(ids(out_n))
        # 被折掉的源不在结果里；root 用它的名字作为 label
        label_of_root = None
        if expected_root:
            label_of_root = {"kp_a": "A", "kp_b": "B", "kp_c": "C"}[expected_root]
        ok = got == expected_set and (label_of_root is None or
                                      any(n["label"] == label_of_root for n in out_n))
        check(f"映射 {mapping or '（空）'} → 节点 {sorted(got)}",
              ok, f"期望 {sorted(expected_set)}")

    # ---------- 6. 陈旧守卫 ----------
    print("\nStep 6: 陈旧映射（端点已不在图中）必须静默失效")
    nodes = [node("kp_new", "重抽后的节点")]
    rep = {}
    out_n, out_e = fold_graph(nodes, [], {"kp_old": "kp_gone"}, report=rep)
    check("端点不在图中 → 不做任何折叠", ids(out_n) == ["kp_new"])
    check("记入 report.stale", len(rep.get("stale") or []) == 1, str(rep.get("stale")))

    # 只有一端缺失也要失效
    nodes = [node("kp_a", "A"), node("kp_b", "B")]
    rep = {}
    out_n, _ = fold_graph(nodes, [], {"kp_a": "kp_missing"}, report=rep)
    check("目标端缺失 → 同样失效", ids(out_n) == ["kp_a", "kp_b"] and len(rep["stale"]) == 1)

    # ---------- 7. 自环丢弃 ----------
    print("\nStep 7: 折叠后产生的自环必须丢弃")
    nodes = [node("kp_a", "BFS"), node("kp_b", "广度优先搜索")]
    edges = [edge("e1", "kp_a", "kp_b", "RELATED_TO")]
    rep = {}
    out_n, out_e = fold_graph(nodes, edges, {"kp_a": "kp_b"}, report=rep)
    check("自环被丢弃（边数归零）", out_e == [], str(ekeys(out_e)))
    check("记入 report.self_loops", len(rep.get("self_loops") or []) == 1, str(rep["self_loops"]))

    # ---------- 8. 重复边去重的确定性 ----------
    print("\nStep 8: 重复边去重 —— tie-break 必须确定")
    nodes = [node("kp_a", "BFS"), node("kp_b", "广度优先搜索"), node("kp_c", "图")]
    # 两条同型边：一条 confidence 高、一条低 → 留高的
    edges = [edge("e_low", "kp_a", "kp_c", "RELATED_TO", confidence=0.5),
             edge("e_high", "kp_b", "kp_c", "RELATED_TO", confidence=0.95)]
    out_n, out_e = fold_graph(nodes, edges, {"kp_a": "kp_b"})
    check("保留 confidence 更高的那条", len(out_e) == 1 and out_e[0]["id"] == "e_high",
          str([(e["id"], e["properties"]["confidence"]) for e in out_e]))

    # 完全并列 → 按 id 升序，且与输入顺序无关
    edges = [edge("e_b", "kp_a", "kp_c", "RELATED_TO", confidence=0.9),
             edge("e_a", "kp_b", "kp_c", "RELATED_TO", confidence=0.9)]
    _, out1 = fold_graph(nodes, edges, {"kp_a": "kp_b"})
    _, out2 = fold_graph(nodes, list(reversed(edges)), {"kp_a": "kp_b"})
    check("并列时按 id 升序取小者", out1[0]["id"] == "e_a", out1[0]["id"])
    check("去重结果与输入顺序无关（确定性）",
          [e["id"] for e in out1] == [e["id"] for e in out2],
          f"{[e['id'] for e in out1]} vs {[e['id'] for e in out2]}")

    # is_manual 优先
    edges = [edge("e_auto", "kp_a", "kp_c", "RELATED_TO", confidence=0.9, is_manual=False),
             edge("e_man", "kp_b", "kp_c", "RELATED_TO", confidence=0.9, is_manual=True)]
    _, out = fold_graph(nodes, edges, {"kp_a": "kp_b"})
    check("同分时手工边优先", out[0]["id"] == "e_man", out[0]["id"])

    # 不同类型不算重复
    edges = [edge("e1", "kp_a", "kp_c", "RELATED_TO"), edge("e2", "kp_b", "kp_c", "PRECEDES")]
    _, out = fold_graph(nodes, edges, {"kp_a": "kp_b"})
    check("关系类型不同不去重", len(out) == 2, str(ekeys(out)))

    # ---------- 9. 环检测（写时已防，这里是防御性切断） ----------
    print("\nStep 9: 环检测（防御性：写时防环已拦住，这里仍要不死循环）")
    nodes = [node("kp_x", "X"), node("kp_y", "Y")]
    rep = {}
    out_n, _ = fold_graph(nodes, [], {"kp_x": "kp_y", "kp_y": "kp_x"}, report=rep)
    check("有环时不挂死且结点数不膨胀", len(out_n) <= 2, str(ids(out_n)))
    check("环已记入 report.cycles", len(rep.get("cycles") or []) >= 1, str(rep.get("cycles")))

    # ---------- 10. 节点顺序稳定 ----------
    print("\nStep 10: 节点顺序稳定（按 root 首次出现的位置）")
    nodes = [node("kp_1", "一"), node("kp_a", "A"), node("kp_2", "二"), node("kp_b", "B")]
    out_n, _ = fold_graph(nodes, [], {"kp_a": "kp_b"})
    check("顺序为 [一, B(=root), 二]", ids(out_n) == ["kp_1", "kp_b", "kp_2"], str(ids(out_n)))

    # ---------- 11. 与悬挂边不变量的衔接 ----------
    print("\nStep 11: 折叠后每条边的端点都能在节点集合里找到")
    nodes = [node("kp_a", "A"), node("kp_b", "B"), node("kp_c", "C"), node("kp_d", "D")]
    edges = [edge("e1", "kp_a", "kp_c"), edge("e2", "kp_d", "kp_a")]
    out_n, out_e = fold_graph(nodes, edges, {"kp_a": "kp_b"})
    node_ids = {n["id"] for n in out_n}
    dangling = [e for e in out_e if e["source"] not in node_ids or e["target"] not in node_ids]
    check("无悬挂边", not dangling, str(ekeys(dangling)))

    # ---------- 12. 真实图的只读一致性探针 ----------
    print("\nStep 12: 线上库只读探针（融合表为空 → 折叠必须是 no-op）")
    try:
        from app.core.sql_database import sql_db
        from app.core.database import db
        from app.services.kg_manager import KnowledgeGraphManager

        n_map = sql_db._query_one("SELECT count(*) AS c FROM t_kp_fusion_map")["c"]
        check("线上融合映射表为空（前提）", n_map == 0, f"实际 {n_map} 行")

        row = sql_db._query_one(
            "SELECT course_id, doc_id FROM t_document "
            "WHERE extract_status = 'COMPLETED' ORDER BY doc_id LIMIT 1")
        if not row:
            skip("线上无已完成抽取的文档，跳过真实图对比")
        else:
            cid, did = row["course_id"], row["doc_id"]
            g = KnowledgeGraphManager.get_graph_v1(cid, did, limit=2000)
            raw = db.get_full_graph(cid, did)
            raw_ids = {r["n"]["kp_id"] for r in raw if r.get("n")}
            raw_ids |= {r["m"]["kp_id"] for r in raw if r.get("m")}
            check(f"course={cid} doc={did}：折叠后节点集合与原始 Cypher 完全一致",
                  {n["id"] for n in g["nodes"]} == raw_ids,
                  f"折叠后 {len(g['nodes'])} 个，原始 {len(raw_ids)} 个")
            check("无融合记录时 fused_from 不出现",
                  not any("fused_from" in (n.get("properties") or {}) for n in g["nodes"]))
    except Exception as e:
        skip("线上库探针未执行", f"{type(e).__name__}: {e}")

    print("\n" + "=" * 72)
    if skipped:
        print(f"跳过 {len(skipped)} 项（**不计入通过**）：")
        for s in skipped:
            print("  -", s)
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
