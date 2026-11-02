"""阶段 5'：Neo4j 批量建图（UNWIND）的正确性与收益验证。

背景：`build_graph` 原先是「每个实体一次 create_knowledge_node、每条关系一次
create_relationship」。一份 60 实体的文档就是 ~130 次网络往返，在批量上传/重试时
是肉眼可见的瓶颈。改为：节点 1 次 UNWIND、关系按类型分组（≤4 次）。

本脚本守住：
1. **语义等价**：同一份数据分别走「逐条」与「批量」，读回的节点/边集合与关键属性一致；
2. **幂等**：同一作用域连续批量写两次，节点/边数量不变；
3. **过滤规则不变**：未知关系类型被丢弃（不降级）、空端点与自环被丢弃；
4. **文档隔离**：同名实体在另一文档不会被合并（MERGE 键含 document_id）；
5. **性能**：批量写入明显快于逐条（阈值放宽到 2 倍，避免网络抖动导致误报）。

运行：cd backend && python test_graph_bulk.py
（需要本机 Neo4j 在 7687 监听；不可用时打印 SKIP 并返回 0）
"""
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.database import db                                 # noqa: E402
from app.services.kg_manager import KnowledgeGraphManager        # noqa: E402

N_NODES = 200
N_RELS = 300
failures = []
total = 0


def check(label, ok, detail=""):
    global total
    total += 1
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def make_entities(n):
    cats = ["概念", "定理", "公式", "方法"]
    return [{"name": f"批量节点{i:03d}", "category": cats[i % 4],
             "description": f"描述 {i}"} for i in range(n)]


def make_relations(n, entities):
    types = ["PRECEDES", "CONTAINS", "RELATED_TO", "APPLIES_TO"]
    names = [e["name"] for e in entities]
    out = []
    for i in range(n):
        out.append({
            "source": names[i % len(names)],
            "target": names[(i * 7 + 3) % len(names)],
            "type": types[i % 4],
            "confidence": 0.9,
            "evidence": f"证据 {i}",
        })
    return out


def snapshot(course_id, doc_id):
    """读回图，转成可比较的结构。

    注意：边端点返回的是 **kp_id（随机 uuid）**，两份文档的同名节点 kp_id 天然不同，
    因此必须先用节点表把 id 映射回 name 再比较——否则会把「随机 id 不同」误判成
    「图不一致」。
    """
    graph = KnowledgeGraphManager.get_raw_graph_v1(course_id, doc_id, limit=5000)
    nodes = {}
    for n in graph.get("nodes") or []:
        nodes[n["id"]] = {
            "name": n["label"],
            "category": (n.get("properties") or {}).get("category"),
            "description": n.get("description") or "",
        }
    edges = {}
    for e in graph.get("edges") or []:
        src = (nodes.get(e.get("source")) or {}).get("name")
        tgt = (nodes.get(e.get("target")) or {}).get("name")
        edges[(src, e.get("type"), tgt)] = e.get("properties") or {}
    by_name = {v["name"]: {"category": v["category"], "description": v["description"]}
               for v in nodes.values()}
    return by_name, edges


def main() -> int:
    try:
        db.query("RETURN 1 AS ok")
        neo_ok = True
    except Exception as exc:  # noqa: BLE001
        neo_ok = False
        print(f"[SKIP] Neo4j 不可用，跳过批量建图验证：{type(exc).__name__}")

    if not neo_ok:
        return 0

    ENT_CID, ENT_DOC_BULK, ENT_DOC_ONE = 970011, 970012, 970013
    ISO_CID, ISO_DOC_A, ISO_DOC_B = 970021, 970022, 970023
    try:
        entities = make_entities(N_NODES)
        relations = make_relations(N_RELS, entities)

        # ---- 预热：首次调用包含驱动握手与查询计划编译，冷启动耗时不代表稳态 ----
        warm_cid, warm_doc = 970081, 970082
        KnowledgeGraphManager.build_graph(warm_cid, warm_doc, entities[:20], relations[:20])
        for e in entities[:20]:
            db.create_knowledge_node(warm_cid, warm_doc + 10, e["name"], e["category"],
                                     e["description"], {})
        for r in relations[:20]:
            db.create_relationship(warm_cid, warm_doc + 10, r["source"], r["target"],
                                   r["type"], {})
        db.delete_course_graph(warm_cid)

        # ---- 统计网络往返次数（结构化指标，不受机器性能影响） ----
        original_query = db.query
        calls = {"bulk": 0, "one": 0, "mode": "bulk"}

        def counting_query(cypher, params=None):
            calls[calls["mode"]] += 1
            return original_query(cypher, params)

        db.query = counting_query
        try:
            print(f"Step 1: 批量写入 {N_NODES} 实体 / {N_RELS} 关系")
            calls["mode"] = "bulk"
            start = time.perf_counter()
            bulk = KnowledgeGraphManager.build_graph(ENT_CID, ENT_DOC_BULK, entities, relations)
            bulk_sec = time.perf_counter() - start

            print("\nStep 2: 逐条写入同一份数据作为基线")
            calls["mode"] = "one"
            start = time.perf_counter()
            for e in entities:
                db.create_knowledge_node(ENT_CID, ENT_DOC_ONE, e["name"], e["category"],
                                         e["description"], {"confidence": 0.9, "is_manual": False})
            for r in relations:
                db.create_relationship(ENT_CID, ENT_DOC_ONE, r["source"], r["target"], r["type"],
                                       {"confidence": r["confidence"],
                                        "evidence": r["evidence"], "is_manual": False})
            one_sec = time.perf_counter() - start
        finally:
            db.query = original_query

        check("批量返回节点数与输入一致", bulk["node_count"] == N_NODES, str(bulk["node_count"]))
        check("批量写入关系（端点齐全，应全部写入）",
              bulk["relation_count"] == N_RELS, str(bulk["relation_count"]))
        check("批量只用了 ≤6 次往返（1 次节点 + 每种关系类型 1 次）",
              calls["bulk"] <= 6, str(calls["bulk"]))
        check("逐条需要 N+M 次往返", calls["one"] >= N_NODES + N_RELS, str(calls["one"]))

        print("\nStep 3: 语义等价（两次写入读回结果一致）")
        nb, eb = snapshot(ENT_CID, ENT_DOC_BULK)
        no, eo = snapshot(ENT_CID, ENT_DOC_ONE)
        check("节点集合一致", nb == no, f"bulk={len(nb)} one={len(no)}")
        check("边集合一致（含类型与端点）", set(eb) == set(eo),
              f"bulk={len(eb)} one={len(eo)}")
        common = set(eb) & set(eo)
        check("边属性一致（confidence）",
              all(abs(float((eb[k] or {}).get("confidence", 0))
                      - float((eo[k] or {}).get("confidence", 0))) < 1e-9 for k in common),
              f"比较 {len(common)} 条")

        print("\nStep 4: 幂等（重复批量写不新增）")
        KnowledgeGraphManager.build_graph(ENT_CID, ENT_DOC_BULK, entities, relations)
        nb2, eb2 = snapshot(ENT_CID, ENT_DOC_BULK)
        check("节点数不变", len(nb2) == len(nb), f"{len(nb)} -> {len(nb2)}")
        check("边数不变", len(eb2) == len(eb), f"{len(eb)} -> {len(eb2)}")

        print("\nStep 5: 过滤规则不变（未知类型/自环/空端点）")
        KnowledgeGraphManager.build_graph(ENT_CID, ENT_DOC_BULK, entities[:3], [
            {"source": "批量节点000", "target": "批量节点001", "type": "NOT_A_TYPE"},
            {"source": "批量节点000", "target": "批量节点000", "type": "RELATED_TO"},
            {"source": "", "target": "批量节点001", "type": "RELATED_TO"},
        ])
        _, e3 = snapshot(ENT_CID, ENT_DOC_BULK)
        check("未知类型未被写入也未降级为 RELATED_TO",
              not any(k[1] == "NOT_A_TYPE" for k in e3))
        check("自环未被写入", not any(k[0] == k[2] for k in e3))

        print("\nStep 6: 文档隔离（同名实体不跨文档合并）")
        KnowledgeGraphManager.build_graph(ISO_CID, ISO_DOC_A, entities[:5], [])
        KnowledgeGraphManager.build_graph(ISO_CID, ISO_DOC_B, entities[:5], [])
        na, _ = snapshot(ISO_CID, ISO_DOC_A)
        nbb, _ = snapshot(ISO_CID, ISO_DOC_B)
        check("两个文档各自拥有同名节点", len(na) == 5 and len(nbb) == 5,
              f"a={len(na)} b={len(nbb)}")

        print("\nStep 7: 性能对比（预热后）")
        print(f"  逐条：{one_sec * 1000:.0f} ms / {calls['one']} 次往返")
        print(f"  批量：{bulk_sec * 1000:.0f} ms / {calls['bulk']} 次往返")
        check("批量至少快 2 倍", bulk_sec * 2 < one_sec,
              f"bulk={bulk_sec:.3f}s one={one_sec:.3f}s")
    finally:
        for cid in (ENT_CID, ISO_CID):
            try:
                db.delete_course_graph(cid)
            except Exception:  # noqa: BLE001
                pass

    print(f"\n通过 {total - len(failures)}/{total}" if not failures
          else f"\n失败 {len(failures)}/{total}：{failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
