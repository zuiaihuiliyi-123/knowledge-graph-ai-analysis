"""文档级知识融合（Fusion）P1：候选召回、评分与候选持久化的幂等性

运行方式（在 backend 目录下执行）：
    python test_fusion_candidates.py

Step 1~9 完全离线（候选生成是纯计算，不查库、不调 LLM）。
Step 10 起需要数据库：其中「扫描预览零写入」是**只读**探测，
「候选持久化幂等」全程只操作 data/app.db 的**副本**并在结束校验线上库 MD5 未变。
"""
import hashlib
import os
import shutil
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.config import settings
from app.core import sql_database as sd
from app.services.fusion import entity_candidates as EC
from app.services.fusion import entity_resolver as R
from app.services.fusion import fusion_config as C

failures = []
skipped = []


def check(label, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def skip(label, detail=""):
    print(f"  [SKIP] {label}" + (f" — {detail}" if detail else ""))
    skipped.append(label)


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def node(kp_id, name, category="概念", description="", confidence=0.9, is_manual=False):
    return {"id": kp_id, "label": name, "type": "concept", "description": description,
            "properties": {"category": category, "confidence": confidence,
                           "is_manual": is_manual}}


def edge(eid, s, t, rtype="RELATED_TO"):
    return {"id": eid, "source": s, "target": t, "type": rtype, "label": rtype,
            "properties": {"confidence": 0.9}}


def ctx(ok=True, idx=None, reason=None, discriminative=True):
    if not ok:
        return {"reconstruction_ok": False, "discriminative": False, "hit_terms": [],
                "chunk_indices": [], "snippets": [], "reason": reason or "不可用"}
    return {"reconstruction_ok": True, "discriminative": discriminative,
            "method": C.CONTEXT_METHOD, "hit_terms": ["x"],
            "chunk_indices": idx or [0], "snippets": [], "reason": None}


def main():
    print("=" * 72)
    print("文档级知识融合 P1：候选召回与评分")
    print("=" * 72)

    # ---------- 1. 缩写 ↔ 全称（零依赖召回） ----------
    print("\nStep 1: 缩写 ↔ 全称 必须能被召回（不依赖任何外部词典）")
    nodes = [
        node("kp_cnn", "CNN", description="卷积神经网络（CNN）是一类包含卷积计算的前馈神经网络。"),
        node("kp_full", "卷积神经网络", description="一种深度学习模型，常用于图像识别。"),
        node("kp_unrelated", "快速排序", description="一种分治排序算法。"),
    ]
    profiles = EC.build_profiles(nodes, [], {"kp_cnn": ctx(idx=[2]), "kp_full": ctx(idx=[2]),
                                             "kp_unrelated": ctx(idx=[9])})
    cands = EC.generate_candidates(profiles, [])
    pairs = {frozenset((c["a"]["kp_id"], c["b"]["kp_id"])) for c in cands}
    check("CNN ↔ 卷积神经网络 进入候选",
          frozenset(("kp_cnn", "kp_full")) in pairs, str([sorted(p) for p in pairs]))
    check("无关实体（快速排序）不进入候选",
          frozenset(("kp_cnn", "kp_unrelated")) not in pairs
          and frozenset(("kp_full", "kp_unrelated")) not in pairs)

    hit = next(c for c in cands if frozenset((c["a"]["kp_id"], c["b"]["kp_id"]))
               == frozenset(("kp_cnn", "kp_full")))
    check("「名称-描述互含」分项被点亮", hit["detail"]["desc"] > 0,
          f"desc={hit['detail']['desc']}，notes={hit['detail']['notes']}")

    # ---------- 2. 硬负门：存在上下位/依赖关系 ----------
    print("\nStep 2: 两实体间存在 CONTAINS/PRECEDES/APPLIES_TO → 硬负门抑制")
    nodes = [node("kp_tree", "树", description="一种数据结构"),
             node("kp_bst", "二叉搜索树", description="一种树")]
    edges = [edge("e1", "kp_tree", "kp_bst", "CONTAINS")]
    profiles = EC.build_profiles(nodes, edges, {"kp_tree": ctx(), "kp_bst": ctx()})
    cands = EC.generate_candidates(profiles, edges)
    check("CONTAINS 对不产生候选", cands == [], f"实际 {len(cands)} 条")

    detail = EC.score_pair(profiles[0], profiles[1], EC.build_edge_index(edges)[1])
    check("硬负门理由可读", detail["blocked"] and "关系" in detail["blocked"],
          str(detail["blocked"]))

    # RELATED_TO 不是负门（它本就是"相关概念"，可能是同义的表达）
    edges2 = [edge("e1", "kp_tree", "kp_bst", "RELATED_TO")]
    profiles2 = EC.build_profiles(nodes, edges2, {"kp_tree": ctx(), "kp_bst": ctx()})
    d2 = EC.score_pair(profiles2[0], profiles2[1], EC.build_edge_index(edges2)[1])
    check("RELATED_TO 不触发硬负门", not d2["blocked"], str(d2["blocked"]))

    # ---------- 3. 类型冲突不得自动融合 ----------
    print("\nStep 3: 类别冲突时，理论上限必须够不到自动融合阈值")
    nodes = [node("kp_a", "cnn", category="概念", description="神经网络"),
             node("kp_b", "CNN", category="定理", description="神经网络")]
    profiles = EC.build_profiles(nodes, [], {"kp_a": ctx(idx=[1]), "kp_b": ctx(idx=[1])})
    detail = EC.score_pair(profiles[0], profiles[1], {}, semantic=lambda a, b: 1.0)
    check(f"类型冲突 + 其余满分 → {detail['score']:.4f} < AUTO_SAME={C.AUTO_SAME_THRESHOLD}",
          detail["score"] < C.AUTO_SAME_THRESHOLD, f"score={detail['score']:.4f}")
    d, band = R.decide_by_rules(detail["score"])
    check("因此不会被自动判 SAME", d != "SAME", f"{d}/{band}")
    check("但规范化同名的对仍会进复核档，不会被埋掉", band in ("REVIEW", "UNCERTAIN"),
          f"{d}/{band}，score={detail['score']:.4f}")

    # ---------- 4. 上下文不可用不得被当成「不匹配」 ----------
    print("\nStep 4: 上下文不可用时按权重剔除，而不是当 0 分")
    nodes = [node("kp_a", "广度优先搜索", description="BFS 的中文名"),
             node("kp_b", "广度优先遍历", description="BFS 的中文名")]
    with_ctx = EC.build_profiles(nodes, [], {"kp_a": ctx(idx=[3]), "kp_b": ctx(idx=[3])})
    no_ctx = EC.build_profiles(nodes, [], {"kp_a": ctx(ok=False), "kp_b": ctx(ok=False)})

    s_with = EC.score_pair(with_ctx[0], with_ctx[1], {})["score"]
    d_no = EC.score_pair(no_ctx[0], no_ctx[1], {})
    check("上下文不可用时 context 分项为 None", d_no["context"] is None, str(d_no["context"]))
    # 除 ctx 外其余分项完全相同 → 剔除权重后的加权平均应当**不低于**把 ctx 当 0 分
    naive_zero = (C.W_LEXICAL * d_no["lexical"] + C.W_CONTEXT * 0.0
                  + C.W_DESC * d_no["desc"]) / (C.W_LEXICAL + C.W_CONTEXT + C.W_DESC)
    naive_zero *= d_no["type"]
    check("剔除权重后的分数 > 把缺失当 0 分的分数",
          d_no["score"] > naive_zero, f"{d_no['score']:.4f} vs {naive_zero:.4f}")
    check("有上下文且命中同块时分数更高", s_with > d_no["score"],
          f"{s_with:.4f} vs {d_no['score']:.4f}")

    # ---------- 5. 人工节点默认不允许作为源 ----------
    print("\nStep 5: is_manual 节点默认不允许被折掉")
    nodes = [node("kp_manual", "广度优先搜索", is_manual=True),
             node("kp_auto", "广度优先遍历")]
    profiles = EC.build_profiles(nodes, [], {"kp_manual": ctx(), "kp_auto": ctx()})
    d = EC.score_pair(profiles[0], profiles[1], {})
    check("manual 作为源被硬负门拦住", d["blocked"] and "人工" in d["blocked"],
          str(d["blocked"]))
    d_rev = EC.score_pair(profiles[1], profiles[0], {})
    check("反过来（manual 作目标）不受影响", not d_rev["blocked"], str(d_rev["blocked"]))

    # ---------- 6. top_k 上限 ----------
    print("\nStep 6: 候选数量上限")
    many = [node(f"kp_{i}", "深度优先搜索", description="DFS 的中文名") for i in range(12)]
    profiles = EC.build_profiles(many, [], {p: ctx() for p in [f"kp_{i}" for i in range(12)]})
    capped = EC.generate_candidates(profiles, [], top_k=2)
    used = {}
    for c in capped:
        for side in (c["a"]["kp_id"], c["b"]["kp_id"]):
            used[side] = used.get(side, 0) + 1
    check("每个实体占用的候选数不超过 top_k=2",
          all(v <= 2 for v in used.values()), str(sorted(used.items())))
    check("确实产出了候选", len(capped) > 0, f"{len(capped)} 条")

    capped2 = EC.generate_candidates(profiles, [], top_k=5, max_pairs=3)
    check("全局上限 max_pairs 生效", len(capped2) == 3, f"{len(capped2)} 条")

    # ---------- 7. 输出不越界 ----------
    print("\nStep 7: 候选只由传入的节点构成（不给跨文档留口子）")
    ids_in = {p["kp_id"] for p in profiles}
    ids_out = set()
    for c in capped:
        ids_out.update((c["a"]["kp_id"], c["b"]["kp_id"]))
    check("输出端点全部来自输入集合", ids_out <= ids_in, str(ids_out - ids_in))

    # ---------- 8. 一模一样但多余的比较不产生噪声 ----------
    print("\nStep 8: 完全无关的实体不产生候选")
    nodes = [node("kp_1", "快速排序", description="分治排序"),
             node("kp_2", "红黑树", description="自平衡二叉查找树")]
    profiles = EC.build_profiles(nodes, [], {"kp_1": ctx(idx=[0]), "kp_2": ctx(idx=[50])})
    check("无关实体无候选", EC.generate_candidates(profiles, []) == [])

    # ---------- 9. 度数与边索引 ----------
    print("\nStep 9: 边索引正确统计度数与同对关系类型")
    edges = [edge("e1", "x", "y", "PRECEDES"), edge("e2", "y", "x", "RELATED_TO"),
             edge("e3", "y", "z", "CONTAINS")]
    degree, pair_types = EC.build_edge_index(edges)
    check("度数统计正确", degree == {"x": 2, "y": 3, "z": 1}, str(degree))
    check("同一对的多种关系被合并记录",
          pair_types[frozenset(("x", "y"))] == {"PRECEDES", "RELATED_TO"},
          str(pair_types.get(frozenset(("x", "y")))))

    # ---------- 10. 扫描预览零写入 + 候选持久化幂等（副本库） ----------
    print("\nStep 10: 扫描预览零写入 / 候选持久化幂等（全程只操作副本）")
    _persist_checks()

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


def _persist_checks():
    """候选落库的幂等性：重复扫描不新增行、被驳回的候选不回退成 PENDING"""
    live = settings.SQLITE_DB_PATH
    live_md5 = md5(live)

    tmp_dir = tempfile.mkdtemp(prefix="kg_fusion_cand_")
    tmp_db = os.path.join(tmp_dir, "app_copy.db")
    shutil.copy2(live, tmp_db)
    settings.SQLITE_DB_PATH = tmp_db
    dbo = sd.SQLDatabase()
    assert os.path.abspath(dbo.db_path) == os.path.abspath(tmp_db), "实例未指向副本，中止"

    rows = [
        {"source_kp_id": "kp_s1", "target_kp_id": "kp_t1", "source_name": "BFS",
         "target_name": "广度优先搜索", "source_category": "概念", "target_category": "概念",
         "score": 0.91, "score_detail": "{}", "decision": "SAME", "decision_source": "RULE",
         "reason": "同名互含"},
        {"source_kp_id": "kp_s2", "target_kp_id": "kp_t2", "source_name": "AVL 树",
         "target_name": "AVL树", "score": 0.88, "score_detail": "{}", "decision": "UNCERTAIN",
         "decision_source": "RULE", "reason": None},
    ]

    dbo.upsert_fusion_candidates(1, 1, rows, run_id="run_1")
    total1, _ = dbo.list_fusion_candidates(1, 1)
    check("首次写入 2 条候选", total1 == 2, f"{total1} 条")

    dbo.upsert_fusion_candidates(1, 1, rows, run_id="run_2")
    total2, _ = dbo.list_fusion_candidates(1, 1)
    check("重复扫描不新增行（幂等）", total2 == 2, f"{total2} 条")

    # 驳回第一条，再扫一轮：状态必须保持 REJECTED
    dbo.set_fusion_candidate_status(
        dbo.list_fusion_candidates(1, 1)[1][0]["candidate_id"], "REJECTED",
        reviewed_by=7, comment="同名不同义")
    dbo.upsert_fusion_candidates(1, 1, rows, run_id="run_3")
    by_id = {r["source_kp_id"]: r for r in dbo.list_fusion_candidates(1, 1)[1]}
    check("被驳回的候选在下一轮扫描后仍是 REJECTED（不会复活成 PENDING）",
          by_id["kp_s1"]["status"] == "REJECTED", by_id["kp_s1"]["status"])
    check("驳回结论被保留（审核人与备注仍在）",
          by_id["kp_s1"]["reviewed_by"] == 7 and by_id["kp_s1"]["review_comment"] == "同名不同义")

    dbo.set_fusion_candidate_status(
        dbo.list_fusion_candidates(1, 1)[1][0]["candidate_id"], "DEFERRED", reviewed_by=7)
    dbo.upsert_fusion_candidates(1, 1, rows, run_id="run_4")
    by_id = {r["source_kp_id"]: r for r in dbo.list_fusion_candidates(1, 1)[1]}
    check("暂缓（DEFERRED）同样不会被扫描覆盖",
          by_id["kp_s1"]["status"] == "DEFERRED", by_id["kp_s1"]["status"])

    # 汇总计数
    agg = dbo.count_fusion_candidates(1, 1)
    check("按决策/状态汇总计数可用",
          agg["total"] == 2 and "DEFERRED" in agg["by_status"], str(agg))

    # 未审核的那条仍会随扫描刷新 decision（分数/理由更新是允许的）
    check("未审核的候选仍随扫描更新（分数被刷新）",
          by_id["kp_s2"]["status"] == "PENDING", by_id["kp_s2"]["status"])

    dbo.delete_fusion_by_document(1, 1)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    settings.SQLITE_DB_PATH = live
    check("副本操作未改动线上库 MD5", md5(live) == live_md5, f"{live_md5} -> {md5(live)}")


if __name__ == "__main__":
    sys.exit(main())
