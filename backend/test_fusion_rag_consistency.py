"""文档级知识融合（Fusion）P3：RAG 与向量索引的一致性

运行方式（在 backend 目录下执行）：
    python test_fusion_rag_consistency.py

要守住的三件事：

  1. **三处过滤一致**：`_current_kp_ids`（新鲜度基准）、`_load_kp_records`（实际写入）、
     以及它的 `t_kp_text` 回落分支，必须扣除**同一批**被融合的源节点。
     只改其中一处 → 基准集合永远不等于写入集合 → **每次问答都全量重建索引**。
  2. **apply / revoke 后索引状态正确更新**，且只重建一次、不是每次查询都重建。
  3. **检索结果与图谱一致**：关键词检索命中「已被折掉的源节点名」时要改写为规范节点，
     上下文里要带上融合别名。

隔离手段同 fusion_testkit：app.db 副本 + 合成图；embedding 用假客户端，不联网。
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.sql_database import sql_db
from app.services.embedding import KnowledgeEmbedder
from app.services.fusion import fusion_pipeline as FP
from app.services.fusion.rag_bridge import RagFusionView
from app.services.qa_service import QAService, _format_node
from fusion_testkit import Harness, make_edge, make_node

failures = []


def check(label, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


class FakeEmbedding:
    """假 embedding 客户端：可用、确定性、**记录被调用次数**"""

    available = True

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


def seed(h, cid, did):
    # confidence 刻意拉开：让规范节点梯子的第 2 条就定死方向（kp_a → kp_b），
    # 否则三条判据全并列时会落到 kp_id 字典序，方向与本题意图相反
    h.set_graph(
        nodes=[make_node("kp_a", "BFS", description="广度优先搜索（BFS）", confidence=0.5),
               make_node("kp_b", "广度优先搜索", description="一种图遍历算法", confidence=0.9),
               make_node("kp_c", "图", description="由顶点和边组成")],
        edges=[make_edge("e1", "kp_a", "kp_c", "APPLIES_TO"),
               make_edge("e2", "kp_b", "kp_c", "APPLIES_TO")])
    sql_db.upsert_fusion_candidates(cid, did, [{
        "source_kp_id": "kp_a", "target_kp_id": "kp_b", "source_name": "BFS",
        "target_name": "广度优先搜索", "source_category": "概念", "target_category": "概念",
        "score": 0.9, "decision": "SAME", "decision_source": "MANUAL"}])
    _, rows = sql_db.list_fusion_candidates(cid, did)
    sql_db.set_fusion_candidate_status(rows[0]["candidate_id"], "ACCEPTED", reviewed_by=1)


def main():
    print("=" * 72)
    print("文档级知识融合 P3：RAG 与向量索引一致性")
    print("=" * 72)

    with Harness() as h:
        cid, did = h.course_id, h.document_id

        # ---------- 1. 未融合时行为不变 ----------
        print("\nStep 1: 没有融合记录时，索引集合与图谱一致")
        h.set_graph(nodes=[make_node("kp_a", "A"), make_node("kp_b", "B")], edges=[])
        base_ids = KnowledgeEmbedder._current_kp_ids(cid, did)
        base_recs = {r["kp_id"] for r in KnowledgeEmbedder._load_kp_records(cid, did)}
        check("基准集合 == 写入集合", base_ids == base_recs == {"kp_a", "kp_b"},
              f"{base_ids} / {base_recs}")

        # ---------- 2. 应用融合后三处一致 ----------
        print("\nStep 2: 应用融合后，三处过滤必须扣除同一批源节点")
        seed(h, cid, did)
        r = FP.apply(cid, did, dry_run=False)
        check("融合应用成功", r["ok"] and r["data"]["applied_count"] == 1, str(r.get("message")))

        cur = KnowledgeEmbedder._current_kp_ids(cid, did)
        recs = {x["kp_id"] for x in KnowledgeEmbedder._load_kp_records(cid, did)}
        check("基准集合剔除了被折掉的 kp_a", "kp_a" not in cur and "kp_b" in cur, str(cur))
        check("写入集合同样剔除 kp_a", "kp_a" not in recs, str(recs))
        check("**基准集合 == 写入集合**（否则每次问答都会重建索引）",
              cur == recs, f"{sorted(cur)} vs {sorted(recs)}")

        # t_kp_text 回落分支也必须过滤
        sql_db.upsert_kp_text_batch(cid, [
            {"kp_id": "kp_a", "document_id": did, "name": "BFS", "category": "概念",
             "description": "旧名"},
            {"kp_id": "kp_b", "document_id": did, "name": "广度优先搜索", "category": "概念",
             "description": "规范"},
        ])
        fallback = {x["kp_id"] for x in KnowledgeEmbedder._load_kp_records(cid, did)}
        check("t_kp_text 回落分支也剔除了 kp_a（三处口径一致）",
              "kp_a" not in fallback, str(fallback))

        # ---------- 3. 只重建一次 ----------
        print("\nStep 3: apply 之后只重建一次，不是每次查询都重建")
        fake = FakeEmbedding()
        idx = KnowledgeEmbedder()
        idx.embedding = fake

        idx.ensure_index(cid, did)
        first = fake.calls
        check("首次调用触发一次重建", first >= 1, f"embed 调用 {first} 次")

        idx.ensure_index(cid, did)
        idx.ensure_index(cid, did)
        check("连续两次查询不再重建（集合与 epoch 都一致）",
              fake.calls == first, f"{first} -> {fake.calls}")

        # ---------- 4. revoke 后重新重建 ----------
        print("\nStep 4: 撤销后索引重新与图谱对齐")
        maps = sql_db.list_fusion_map(cid, did, status="ACTIVE")
        FP.revoke(cid, did, maps[0]["fusion_id"])
        idx.ensure_index(cid, did)
        after_revoke = fake.calls
        check("撤销触发了重建", after_revoke > first, f"{first} -> {after_revoke}")
        cur2 = KnowledgeEmbedder._current_kp_ids(cid, did)
        check("撤销后基准集合重新包含 kp_a", "kp_a" in cur2, str(cur2))

        idx.ensure_index(cid, did)
        check("撤销后同样只重建一次", fake.calls == after_revoke,
              f"{after_revoke} -> {fake.calls}")

        # ---------- 5. epoch 机制：集合碰巧一致时也能发现融合变过 ----------
        print("\nStep 5: 融合 epoch 能捕捉「集合碰巧一致但融合状态变过」")
        scope = sql_db.fusion_scope_get(cid, did)
        check("融合过的文档有 scope 行", scope is not None, str(scope))
        check("epoch 随 apply/revoke 递增", scope["epoch"] >= 2, str(scope["epoch"]))
        sql_db.fusion_scope_mark_indexed(cid, did, -1)   # 人为把 indexed_epoch 打回旧值
        idx.ensure_index(cid, did)
        check("indexed_epoch 落后于 epoch 时会重建",
              fake.calls > after_revoke, f"{after_revoke} -> {fake.calls}")
        check("重建后 indexed_epoch 追平",
              sql_db.fusion_scope_get(cid, did)["indexed_epoch"]
              == sql_db.fusion_scope_get(cid, did)["epoch"])

        # 未曾融合的文档不得因为建索引而凭空多出 scope 行
        sql_db._execute("INSERT INTO t_document (doc_id, course_id, uploader_id, file_name, "
                        "file_type, file_size, parse_status, extract_status) "
                        "VALUES (?, ?, (SELECT user_id FROM t_user ORDER BY user_id LIMIT 1), "
                        "'other.txt', 'TXT', 10, 'PARSED', 'COMPLETED')", (did + 1, cid))
        h.set_graph(nodes=[make_node("kp_x", "X")], edges=[])
        idx.ensure_index(cid, did + 1)
        check("未融合过的文档不会凭空多出 scope 行",
              sql_db.fusion_scope_get(cid, did + 1) is None)

        # ---------- 6. RagFusionView ----------
        print("\nStep 6: RagFusionView 的折叠与别名")
        seed(h, cid, did)
        sql_db.delete_fusion_by_document(cid, did)
        FP.apply(cid, did, dry_run=True)   # 候选已被删，这里只为确认不报错
        # 直接造两条链式映射，验证链式归一
        sql_db.insert_fusion_map(cid, did, "kp_a", "kp_b", "BFS", "广度优先搜索")
        sql_db.insert_fusion_map(cid, did, "kp_b", "kp_c", "广度优先搜索", "图")
        fusion = RagFusionView(cid, did)
        check("链式归一：kp_a 的 root 是 kp_c", fusion.root_of("kp_a") == "kp_c",
              fusion.root_of("kp_a"))
        check("kp_c 是 root 自身", fusion.root_of("kp_c") == "kp_c")
        check("别名按 root 归并", sorted(fusion.aliases_of("kp_c")) == ["BFS", "广度优先搜索"],
              str(fusion.aliases_of("kp_c")))
        check("excludes 能识别被折掉的源", fusion.excludes("kp_a") and not fusion.excludes("kp_c"))

        # ---------- 7. 上下文与来源卡片 ----------
        print("\nStep 7: 上下文与来源卡片带上融合别名")
        node_with_alias = {"name": "广度优先搜索", "category": "概念",
                           "description": "一种图遍历算法", "fused_aliases": ["BFS"]}
        text = _format_node(node_with_alias)
        check("喂给 LLM 的上下文包含别名", "又称：BFS" in text, text)
        node_plain = {"name": "图", "category": "概念", "description": "由顶点和边组成"}
        check("未融合时上下文格式**逐字节不变**",
              _format_node(node_plain) == "[概念] 图: 由顶点和边组成", _format_node(node_plain))
        check("未融合时来源卡片不带 fused_aliases 字段",
              "fused_aliases" not in QAService._node_dict(node_plain),
              str(QAService._node_dict(node_plain)))
        check("融合后来源卡片带 fused_aliases",
              QAService._node_dict(node_with_alias, ["BFS"]).get("fused_aliases") == ["BFS"])

        # ---------- 8. 关键词检索命中源节点名时改写为规范节点 ----------
        print("\nStep 8: 关键词检索命中「已被折掉的源节点名」时改写为规范节点")
        sql_db.delete_fusion_by_document(cid, did)
        sql_db.insert_fusion_map(cid, did, "kp_a", "kp_b", "BFS", "广度优先搜索")
        fusion = RagFusionView(cid, did)

        records = [{"n": {"kp_id": "kp_a", "name": "BFS", "category": "概念",
                          "description": "旧名"}}]
        roots = QAService._fold_to_root(records, fusion, cid, did)
        check("命中源节点时改写为规范节点", len(roots) == 1 and roots[0]["kp_id"] == "kp_b",
              str(roots))
        check("改写后的名字是规范名", roots[0]["name"] == "广度优先搜索", roots[0]["name"])
        check("来源卡片带上了被折掉的旧名", roots[0].get("fused_aliases") == ["BFS"],
              str(roots[0].get("fused_aliases")))

        # 未融合时行为不变
        records2 = [{"n": {"kp_id": "kp_c", "name": "图", "category": "概念",
                           "description": "由顶点和边组成"}}]
        roots2 = QAService._fold_to_root(records2, fusion, cid, did)
        check("未命中融合时原样返回", roots2[0]["kp_id"] == "kp_c"
              and "fused_aliases" not in roots2[0], str(roots2))

        # 去重：多个源折到同一 root 时只保留一条
        records3 = [{"n": {"kp_id": "kp_a", "name": "BFS", "category": "概念",
                           "description": ""}},
                    {"n": {"kp_id": "kp_b", "name": "广度优先搜索", "category": "概念",
                           "description": ""}}]
        roots3 = QAService._fold_to_root(records3, fusion, cid, did)
        check("同一 root 只出现一次", len(roots3) == 1 and roots3[0]["kp_id"] == "kp_b",
              str([r["kp_id"] for r in roots3]))

        # ---------- 9. 未命中任何关键字时的兜底也要排除源节点 ----------
        print("\nStep 9: 关键词检索的兜底分支也不得把已折掉的源节点捞回来")
        captured = {}

        class FakeDB:
            """只认「按关键字命中 BFS」的那条路径，并**如实执行** $fused 排除条件"""

            @staticmethod
            def query(cypher, params=None):
                captured.setdefault("cyphers", []).append(cypher)
                captured["cypher"] = cypher
                captured["params"] = params
                params = params or {}
                ids = params.get("ids")
                if ids is not None and "kp_b" not in set(ids):
                    return []
                if params.get("fused") and "kp_a" in set(params["fused"]):
                    return []          # 兜底分支会带上 NOT n.kp_id IN $fused
                node = {"kp_id": "kp_a", "name": "BFS", "category": "概念", "description": ""}
                if ids is not None:
                    return [{"n": node}] if "kp_a" in set(ids) else [
                        {"n": {"kp_id": "kp_b", "name": "广度优先搜索",
                               "category": "概念", "description": ""}}]
                return [{"n": node}]

        import app.services.qa_service as qs
        orig = qs.db.query
        qs.db.query = FakeDB.query
        try:
            out = QAService()._keyword_search("BFS", cid, did, top_k=5)
        finally:
            qs.db.query = orig
        check("兜底查询带上了融合源排除条件",
              "NOT n.kp_id IN $fused" in captured["cypher"], captured["cypher"])
        check("排除参数确实是当前的融合源集合",
              set(captured["params"]["fused"]) == {"kp_a"}, str(captured["params"].get("fused")))
        check("最终结果里出现的仍是规范节点 kp_b",
              len(out) == 1 and out[0]["kp_id"] == "kp_b", str(out))

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
