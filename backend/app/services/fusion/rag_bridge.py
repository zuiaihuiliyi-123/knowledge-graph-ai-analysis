"""问答（RAG）侧对融合状态的只读视图。

要让「检索到的上下文」与「图谱上看到的」是同一回事，问答链路需要知道两件事：
  1. 哪些节点已被折掉（不能再单独召回它们）；
  2. 某个规范节点吸收过哪些旧名称（喂给 LLM 的上下文要把别名带上）。

**降级方向与读图一致（tolerant）**：融合表读不出来就退化为"没有融合"，
问答继续可用，但会记 WARNING —— 静默吞掉会让「融合没生效」伪装成「本来就没融合」。
"""
import logging

from ...core.sql_database import sql_db
from .graph_folder import resolve_root

_logger = logging.getLogger(__name__)


class RagFusionView:
    """某个 (course_id, document_id) 的融合只读视图。"""

    def __init__(self, course_id, document_id):
        self.course_id = course_id
        self.document_id = document_id
        self.mapping = {}
        self.aliases_by_target = {}
        self.sources = set()
        self.available = True

        if course_id is None or document_id is None:
            return
        try:
            rows = sql_db.list_active_fusion_map(course_id, document_id)
        except Exception as e:  # noqa: BLE001
            self.available = False
            _logger.warning(
                "读取融合映射失败，本次按「未融合」处理 course_id=%s document_id=%s: %s",
                course_id, document_id, e, exc_info=True)
            return

        for r in rows:
            s, t = r.get("source_kp_id"), r.get("target_kp_id")
            if s and t and s != t:
                self.mapping[s] = t
        self.sources = set(self.mapping.keys())
        for r in rows:
            s = r.get("source_kp_id")
            if not s:
                continue
            root = resolve_root(s, self.mapping)
            if root == s:
                continue
            name = r.get("source_name") or s
            bucket = self.aliases_by_target.setdefault(root, [])
            if name not in bucket:
                bucket.append(name)

    @property
    def active(self) -> bool:
        return bool(self.mapping)

    def root_of(self, kp_id: str) -> str:
        """把已被折掉的 kp_id 换成它的规范节点；未融合的原样返回"""
        if not self.mapping or not kp_id:
            return kp_id
        return resolve_root(kp_id, self.mapping)

    def aliases_of(self, kp_id: str) -> list:
        """该规范节点吸收过的旧名称（供 LLM 上下文与来源卡片显示）"""
        return list(self.aliases_by_target.get(kp_id) or [])

    def excludes(self, kp_id: str) -> bool:
        """该 kp_id 是否已被折掉（不应再作为独立知识点出现）"""
        return kp_id in self.sources
