"""融合映射的读取入口。**两个入口，降级方向相反** —— 这是刻意的。

- `strict=False`（普通读图 / 问答）：SQLite 读失败时降级为**未融合图谱**，
  保证「融合表坏了不会让所有人看不到知识图谱」。**但必须记 WARNING**：
  静默吞异常是本项目的顽固模式，会让「融合悄悄不生效」伪装成「本来就没融合」。
- `strict=True`（scan / review / apply / revoke / undo 等管理操作）：读失败**直接抛错**。
  管理操作是在**真实状态未知**的前提下做决策，把异常当成「无映射」继续跑，
  可能写出互相矛盾的映射行（例如重复插入已被撤销的融合）。宁可失败关闭。

关于文档隔离：折叠只作用于 `get_graph_v1(course_id, document_id)` 这一个读取口径，
而映射表本身就按 `(course_id, document_id)` 建行 —— **没有映射的文档天然是 no-op**。
因此这里不再额外维护「本进程内处理过的文档」白名单：那种白名单在服务重启后为空，
会让已融合的文档悄悄退回未融合状态，反而制造出一个更隐蔽的 bug。
"""
import logging

from ...core.sql_database import sql_db

_logger = logging.getLogger(__name__)


class FusionMapUnavailable(RuntimeError):
    """融合状态不可读（SQLite 异常 / 表不存在）。管理操作遇到它必须失败关闭。"""


def load_active_mapping(course_id, document_id, strict: bool = False) -> dict:
    """读取该文档当前的 ACTIVE 融合映射，返回 {source_kp_id: target_kp_id}。

    strict=False：异常 → 返回 {} 并记 WARNING（读图/问答路径）
    strict=True ：异常 → 抛 FusionMapUnavailable（管理操作路径）
    """
    try:
        rows = sql_db.list_active_fusion_map(course_id, document_id)
    except Exception as e:
        if strict:
            raise FusionMapUnavailable(
                f"融合映射不可用 course_id={course_id} document_id={document_id}: {e}"
            ) from e
        _logger.warning(
            "读取融合映射失败，本次降级为未融合图谱 course_id=%s document_id=%s: %s",
            course_id, document_id, e, exc_info=True,
        )
        return {}

    mapping = {}
    for r in rows:
        src, tgt = r.get("source_kp_id"), r.get("target_kp_id")
        if src and tgt and src != tgt:
            mapping[src] = tgt
    return mapping


def active_fused_sources(course_id, document_id, strict: bool = False) -> set:
    """该文档被融合掉的源 kp_id 集合。

    供 RAG 侧扣除这些节点（向量索引与关键词检索都不应再单独命中它们），
    以及让检索到的规范节点带上融合别名。降级策略同上。
    """
    try:
        return sql_db.fused_source_kp_ids(course_id, document_id)
    except Exception as e:
        if strict:
            raise FusionMapUnavailable(
                f"融合源集合不可用 course_id={course_id} document_id={document_id}: {e}"
            ) from e
        _logger.warning(
            "读取融合源集合失败，本次不做融合过滤 course_id=%s document_id=%s: %s",
            course_id, document_id, e, exc_info=True,
        )
        return set()
