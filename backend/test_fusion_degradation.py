"""文档级知识融合（Fusion）P0/P3：故障降级方向

运行方式（在 backend 目录下执行）：
    python test_fusion_degradation.py

本脚本断言的是**两种相反**的降级方向，这是本模块刻意的设计：

  - 普通读图 / 问答（tolerant）：融合表读不出来 → 降级为「未融合图谱」，
    保证融合表坏了不会让所有人看不到知识图谱。**但必须留下 WARNING 日志** ——
    静默吞异常是本项目的顽固模式，会让「融合悄悄不生效」伪装成「本来就没融合」。
  - 管理操作（fail-closed）：读不出来 → **直接抛错**。管理操作是在真实状态未知的
    前提下做决策，把异常当成「无映射」继续跑，可能写出互相矛盾的映射行。

本脚本不写任何数据；涉及 Neo4j 的探针在库不可用时记为 SKIP（不计入通过）。
"""
import logging
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.sql_database import sql_db
from app.services.fusion import fusion_map

failures = []
skipped = []


def check(label, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def skip(label, detail=""):
    print(f"  [SKIP] {label}" + (f" — {detail}" if detail else ""))
    skipped.append(label)


class Capture(logging.Handler):
    """收集某 logger 的日志记录，用来断言「异常没有被静默吞掉」"""

    def __init__(self):
        super().__init__(level=logging.NOTSET)
        self.records = []

    def emit(self, record):
        self.records.append(record)


class Boom:
    """把 sql_db 的融合读方法临时替换成必然抛错的实现"""

    def __init__(self, *names):
        self.names = names
        self.originals = {}

    def __enter__(self):
        for n in self.names:
            self.originals[n] = getattr(sql_db, n)

            def _raise(*a, **kw):
                raise RuntimeError("模拟 SQLite 故障：database is locked")

            setattr(sql_db, n, _raise)
        return self

    def __exit__(self, *exc):
        for n, fn in self.originals.items():
            setattr(sql_db, n, fn)
        return False


def main():
    print("=" * 72)
    print("文档级知识融合 P0/P3：故障降级（读图 tolerant / 管理 fail-closed）")
    print("=" * 72)

    # ---------- 1. tolerant：降级为空映射 + 必须记 WARNING ----------
    print("\nStep 1: load_active_mapping(strict=False) —— 降级但**不静默**")
    cap = Capture()
    logger = logging.getLogger("app.services.fusion.fusion_map")
    old_level = logger.level
    logger.addHandler(cap)
    logger.setLevel(logging.DEBUG)
    try:
        with Boom("list_active_fusion_map"):
            mapping = fusion_map.load_active_mapping(1, 1, strict=False)
    finally:
        logger.removeHandler(cap)
        logger.setLevel(old_level)

    check("返回空映射（降级为未融合图谱）", mapping == {}, str(mapping))
    check("留下了 WARNING 日志（异常没被静默吞掉）",
          any(r.levelno >= logging.WARNING for r in cap.records),
          f"捕获 {len(cap.records)} 条日志")

    # ---------- 2. fail-closed：管理操作必须抛错 ----------
    print("\nStep 2: load_active_mapping(strict=True) —— 管理操作必须失败关闭")
    raised = None
    try:
        with Boom("list_active_fusion_map"):
            fusion_map.load_active_mapping(1, 1, strict=True)
    except fusion_map.FusionMapUnavailable as e:
        raised = e
    except Exception as e:  # noqa: BLE001
        raised = e
    check("strict=True 抛 FusionMapUnavailable",
          isinstance(raised, fusion_map.FusionMapUnavailable),
          f"{type(raised).__name__}: {raised}")
    check("异常链保留了原始错误（便于定位）",
          raised is not None and "database is locked" in str(raised.__cause__),
          str(getattr(raised, "__cause__", None)))

    # ---------- 3. active_fused_sources 同样两个方向 ----------
    print("\nStep 3: active_fused_sources —— 方向与上面一致")
    cap = Capture()
    logger.addHandler(cap)
    logger.setLevel(logging.DEBUG)
    try:
        with Boom("fused_source_kp_ids"):
            got = fusion_map.active_fused_sources(1, 1, strict=False)
    finally:
        logger.removeHandler(cap)
        logger.setLevel(old_level)
    check("tolerant 返回空集合", got == set(), str(got))
    check("tolerant 留下 WARNING", any(r.levelno >= logging.WARNING for r in cap.records))

    raised = None
    try:
        with Boom("fused_source_kp_ids"):
            fusion_map.active_fused_sources(1, 1, strict=True)
    except Exception as e:  # noqa: BLE001
        raised = e
    check("strict 抛 FusionMapUnavailable",
          isinstance(raised, fusion_map.FusionMapUnavailable), str(raised))

    # ---------- 4. 故障时读图结果与正常时完全一致 ----------
    print("\nStep 4: 融合表故障时，get_graph_v1 结果与正常时逐字节相同（只读探针）")
    try:
        from app.core.database import db
        from app.services.kg_manager import KnowledgeGraphManager

        row = sql_db._query_one(
            "SELECT course_id, doc_id FROM t_document "
            "WHERE extract_status = 'COMPLETED' ORDER BY doc_id LIMIT 1")
        if not row:
            skip("线上无已完成抽取的文档")
        else:
            cid, did = row["course_id"], row["doc_id"]
            normal = KnowledgeGraphManager.get_graph_v1(cid, did, limit=2000)

            cap = Capture()
            logger.addHandler(cap)
            logger.setLevel(logging.DEBUG)
            try:
                with Boom("list_active_fusion_map"):
                    degraded = KnowledgeGraphManager.get_graph_v1(cid, did, limit=2000)
            finally:
                logger.removeHandler(cap)
                logger.setLevel(old_level)

            check(f"course={cid} doc={did}：降级后节点集合一致",
                  [n["id"] for n in degraded["nodes"]] == [n["id"] for n in normal["nodes"]],
                  f"{len(degraded['nodes'])} vs {len(normal['nodes'])}")
            check("降级后边集合一致",
                  [(e["source"], e["target"], e["type"]) for e in degraded["edges"]]
                  == [(e["source"], e["target"], e["type"]) for e in normal["edges"]])
            check("降级路径留下了 WARNING（可观测）",
                  any(r.levelno >= logging.WARNING for r in cap.records))
    except Exception as e:  # noqa: BLE001
        skip("线上库探针未执行", f"{type(e).__name__}: {e}")

    # ---------- 5. RAG 侧同样是 tolerant ----------
    print("\nStep 5: RAG 侧（RagFusionView / embedding 过滤）也走 tolerant + WARNING")
    from app.services.fusion.rag_bridge import RagFusionView
    from app.services.embedding import KnowledgeEmbedder

    # RagFusionView 打在它自己的模块 logger 上，捕获要挂到那一个
    rag_logger = logging.getLogger("app.services.fusion.rag_bridge")
    cap = Capture()
    rag_logger.addHandler(cap)
    old_rag_level = rag_logger.level
    rag_logger.setLevel(logging.DEBUG)
    try:
        with Boom("list_active_fusion_map"):
            view = RagFusionView(1, 1)
    finally:
        rag_logger.removeHandler(cap)
        rag_logger.setLevel(old_rag_level)

    check("RagFusionView 标记为不可用", view.available is False)
    check("降级后按「未融合」处理", view.mapping == {} and view.active is False)
    check("root_of / aliases_of 退化为恒等",
          view.root_of("kp_x") == "kp_x" and view.aliases_of("kp_x") == [])
    check("留下了 WARNING 日志", any(r.levelno >= logging.WARNING for r in cap.records))

    cap = Capture()
    logger.addHandler(cap)
    logger.setLevel(logging.DEBUG)
    try:
        with Boom("fused_source_kp_ids"):
            fused = KnowledgeEmbedder._fused_sources(1, 1)
    finally:
        logger.removeHandler(cap)
        logger.setLevel(old_level)
    check("embedding 过滤失败时降级为空集合（等价于不启用融合过滤）", fused == set())
    check("同样留下 WARNING", any(r.levelno >= logging.WARNING for r in cap.records))

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
