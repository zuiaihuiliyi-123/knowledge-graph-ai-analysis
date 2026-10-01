"""文档级知识融合（Fusion）P1：重建的文本命中上下文及其边界

运行方式（在 backend 目录下执行）：
    python test_fusion_context_rebuild.py

本脚本要守住的核心边界是**诚实标注**：抽取阶段没有保留任何 chunk 溯源，
融合层给出的上下文是「重新分块 + 按名称命中」**重建**出来的，
既不得冒充原始抽取证据，也不得在重建失败时被当成"上下文不匹配"来用。
"""
import asyncio
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.services.fusion import entity_context as E
from app.services.fusion import entity_candidates as EC
from app.services.fusion import fusion_config as C
from app.services.fusion.entity_normalizer import extract_aliases, normalize

failures = []
skipped = []


def check(label, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def skip(label, detail=""):
    print(f"  [SKIP] {label}" + (f" — {detail}" if detail else ""))
    skipped.append(label)


def main():
    print("=" * 72)
    print("文档级知识融合 P1：重建上下文（reconstructed_context）")
    print("=" * 72)

    # ---------- 1. 规范化边界 ----------
    print("\nStep 1: 规范化不得把 C++ 与 C 混为一谈")
    check("C++ 与 C 规范名不同", normalize("C++") != normalize("C"),
          f"{normalize('C++')!r} vs {normalize('C')!r}")
    check("AVL树 与 AVL 树 规范名仍不同（只折叠空白，不删空格）",
          normalize("AVL树") != normalize("AVL 树"),
          f"{normalize('AVL树')!r} vs {normalize('AVL 树')!r}")
    check("纯英文大小写归一", normalize("bfs") == normalize("BFS") == "BFS")
    check("全角转半角", normalize("ＢＦＳ") == "BFS", normalize("ＢＦＳ"))
    check("多余空白折叠", normalize("  AVL   树  ") == "AVL 树", normalize("  AVL   树  "))
    check("空输入不炸", normalize("") == "" and normalize(None) == "")

    # ---------- 2. 别名抽取 ----------
    print("\nStep 2: 别名抽取（只作为召回线索，不直接当同义）")
    check("全角括号：广度优先搜索（BFS）",
          "BFS" in extract_aliases("广度优先搜索（BFS）"), str(extract_aliases("广度优先搜索（BFS）")))
    check("半角括号：广度优先搜索(BFS)",
          "BFS" in extract_aliases("广度优先搜索(BFS)"))
    check("「又称」写法",
          extract_aliases("广度优先搜索，又称广度优先遍历") == ["广度优先遍历"],
          str(extract_aliases("广度优先搜索，又称广度优先遍历")))
    check("描述里的括号注释也被捞出来",
          "BFS" in extract_aliases("卷积网络", "广度优先搜索（BFS）是一种遍历算法"),
          str(extract_aliases("卷积网络", "广度优先搜索（BFS）是一种遍历算法")))
    check("别名与原名相同则剔除",
          extract_aliases("BFS（BFS）") == [], str(extract_aliases("BFS（BFS）")))
    check("无别名返回空表", extract_aliases("树") == [])

    # ---------- 3. locate 定位 ----------
    print("\nStep 3: locate 在分块文本中定位命中位置")
    chunks = [
        "第一章 绪论。本章介绍基本概念。",
        "广度优先搜索（BFS）是一种图遍历算法，它按层次逐层扩展。",
        "第二章 树。二叉树是每个节点最多有两个子节点的树结构。",
        "深度优先搜索（DFS）沿着一条路径走到底再回溯。",
    ]
    loc = E.locate(chunks, "广度优先搜索")
    check("命中第 1 块（0 基）", loc["chunk_indices"] == [1], str(loc["chunk_indices"]))
    check("hit_terms 记录了命中的词", "广度优先搜索" in loc["hit_terms"], str(loc["hit_terms"]))
    check("片段逐字来自原文", loc["snippets"] and "广度优先搜索" in loc["snippets"][0],
          str(loc["snippets"][:1]))

    loc_abbr = E.locate(chunks, "BFS")
    check("缩写也能命中", loc_abbr["chunk_indices"] == [1], str(loc_abbr["chunk_indices"]))

    loc_miss = E.locate(chunks, "完全不存在的概念")
    check("未命中时三者皆空",
          loc_miss == {"hit_terms": [], "chunk_indices": [], "snippets": []}, str(loc_miss))

    loc_empty = E.locate([], "树")
    check("空分块列表不炸", loc_empty["chunk_indices"] == [])

    # ---------- 4. 重建失败必须显式标记 ----------
    print("\nStep 4: 重建失败 → reconstruction_ok=False，不得伪装成「没有命中」")
    bad = E.build_for("kp_1", "树", "", [], None, error="文档解析失败: FileNotFoundError")
    check("reconstruction_ok 为 False", bad["reconstruction_ok"] is False)
    check("带上失败原因", "FileNotFoundError" in (bad["reason"] or ""), str(bad["reason"]))
    check("命中的三项保持为空（没有伪造）",
          bad["hit_terms"] == [] and bad["chunk_indices"] == [] and bad["snippets"] == [])

    good = E.build_for("kp_1", "广度优先搜索", "", [], chunks)
    check("重建成功时 method 固定为 rechunk_exact_name_hit",
          good["reconstruction_ok"] and good["method"] == C.CONTEXT_METHOD, str(good["method"]))
    check("重建成功时带 reconstructed_at", bool(good["reconstructed_at"]))

    # ---------- 5. context_similarity 的降级 ----------
    print("\nStep 5: 上下文相似度 —— 任一方不可用即返回 None（调用方据此剔除权重）")
    def C_(idx, disc=True):
        return {"reconstruction_ok": True, "discriminative": disc, "chunk_indices": idx}

    ok1, ok2 = C_([3]), C_([3])
    ok_far = C_([9])
    ok_nohit = C_([])
    bad_ctx = {"reconstruction_ok": False, "discriminative": False, "chunk_indices": []}
    flat_ctx = C_([1], disc=False)

    s, note = E.context_similarity(ok1, ok2)
    check("同块 = 1.0", s == 1.0, f"{s} {note}")
    s, note = E.context_similarity(C_([3]), ok_far)
    check("相距远 = 0.2", s == 0.2, f"{s} {note}")
    check("相邻块 = 0.6", E.context_similarity(C_([3]), C_([4]))[0] == 0.6)
    check("A 不可用 → None", E.context_similarity(bad_ctx, ok2)[0] is None)
    check("B 不可用 → None", E.context_similarity(ok1, bad_ctx)[0] is None)
    check("双方都没命中 → None（不猜测）",
          E.context_similarity(ok_nohit, ok_nohit)[0] is None)
    check("无区分度的上下文（单块文档）→ None，不得当作「命中同一块」",
          E.context_similarity(flat_ctx, flat_ctx)[0] is None,
          str(E.context_similarity(flat_ctx, flat_ctx)))

    # ---------- 6. 上下文不可用不得制造相似度 ----------
    print("\nStep 6: 上下文不可用时，绝不能凭它推断同义")
    nodes = [{"id": "kp_1", "label": "快速排序", "type": "concept", "description": "分治排序",
              "properties": {"category": "概念"}},
             {"id": "kp_2", "label": "红黑树", "type": "concept", "description": "自平衡树",
              "properties": {"category": "概念"}}]
    no_ctx = {"kp_1": E.empty_context("不可用"), "kp_2": E.empty_context("不可用")}
    profiles = EC.build_profiles(nodes, [], no_ctx)
    check("无关实体在上下文不可用时仍不产生候选",
          EC.generate_candidates(profiles, []) == [])
    detail = EC.score_pair(profiles[0], profiles[1], {})
    check("分数远低于召回下限", detail["score"] < C.RECALL_THRESHOLD, f"{detail['score']:.4f}")

    # ---------- 7. 分块参数与冻结抽取器一致 ----------
    print("\nStep 7: 重建用的分块参数必须与抽取阶段同源")
    import inspect
    from app.services import knowledge_extractor as KE
    src = inspect.getsource(E)
    check("entity_context 从冻结抽取器导入分块参数（而不是自己写死一份）",
          "_CHUNK_MAX_TOKENS" in src and "_OVERLAP_TOKENS" in src)
    check("抽取器的分块上限常量存在且为正整数",
          isinstance(KE._CHUNK_MAX_TOKENS, int) and KE._CHUNK_MAX_TOKENS > 0,
          str(KE._CHUNK_MAX_TOKENS))

    # ---------- 8. 真实文档的解析探针 ----------
    print("\nStep 8: 真实文档加载探针（只读）")
    from app.core.sql_database import sql_db

    row = sql_db._query_one(
        "SELECT doc_id, course_id, file_name FROM t_document "
        "WHERE parse_status = 'PARSED' ORDER BY doc_id LIMIT 1")
    if not row:
        skip("线上无已解析文档")
    else:
        chunks, err = asyncio.run(E.load_chunks(row["course_id"], row["doc_id"]))
        if err:
            check(f"真实文档 doc={row['doc_id']} 能重建分块", False, f"err={err}")
        else:
            check(f"真实文档 doc={row['doc_id']} 重建出 {len(chunks)} 块", len(chunks) > 0)

    # 缺失文档 / 归属不符必须返回原因而不是抛异常
    chunks, err = asyncio.run(E.load_chunks(1, 99999999))
    check("文档不存在 → 返回原因、不抛异常", chunks is None and err, str(err))

    if row:
        chunks, err = asyncio.run(E.load_chunks(int(row["course_id"]) + 100000, row["doc_id"]))
        check("文档不属于该课程 → 拒绝", chunks is None and "不属于" in (err or ""), str(err))

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
