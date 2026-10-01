"""融合模块测试用的公共支架（**不是测试脚本，不参与 `test_*.py` 的执行**）。

为什么单独抽出来：后续多个测试都需要同样的两件事 ——
  1. 把 `sql_db` 指向 `app.db` 的**副本**（绝不碰线上库）；
  2. 用**合成图**替换 Neo4j 读取（绝不往 Neo4j 写测试数据）。
四五个脚本各抄一份太容易抄错，尤其"忘了恢复单例"会造成后续脚本误写线上库。

用法：
    with Harness() as h:
        h.set_graph(nodes=[...], edges=[...])
        r = fusion_pipeline.apply(COURSE, DOC, dry_run=False)
"""
import os
import shutil
import tempfile

from app.core import sql_database as sd
from app.core.config import settings
from app.core.database import db
from app.services import kg_manager

# 测试用的隔离作用域，绝不会与线上数据撞号
COURSE_ID = 900001
DOC_ID = 900002


def make_node(kp_id, name, category="概念", description="", confidence=0.9, is_manual=False):
    return {
        "id": kp_id, "label": name,
        "type": {"概念": "concept", "定理": "theorem", "公式": "formula",
                 "方法": "method"}.get(category, "concept"),
        "description": description,
        "properties": {"category": category, "confidence": confidence,
                       "is_manual": is_manual,
                       "created_at": "2026-01-01T00:00:00Z"},
    }


def make_edge(eid, source, target, rtype="RELATED_TO", confidence=0.9):
    return {"id": eid, "source": source, "target": target, "type": rtype,
            "label": rtype, "properties": {"confidence": confidence, "is_manual": False}}


class FakeDisambiguator:
    """按预设映射作答的消歧器；未命中时返回 UNCERTAIN。

    `drop=True` 模拟"消歧器整个不可用"（未配置 key / 网络不通）。
    """

    def __init__(self, answers=None, drop=False):
        self.answers = answers or {}
        self.drop = drop
        self.calls = []

    def judge(self, pair):
        self.calls.append(pair)
        if self.drop:
            return {"decision": "UNCERTAIN", "confidence": None,
                    "reason": "模拟消歧器不可用", "error": "unavailable"}
        key = frozenset((pair["a"]["name"], pair["b"]["name"]))
        ans = self.answers.get(key)
        if not ans:
            return {"decision": "UNCERTAIN", "confidence": None,
                    "reason": "未预设", "error": None}
        return {"decision": ans, "confidence": 0.9, "reason": "预设答案", "error": None}


class Harness:
    """副本库 + 合成图。进入时备份、退出时恢复，异常路径也不会污染线上库。"""

    def __init__(self, course_id=COURSE_ID, document_id=DOC_ID):
        self.course_id = course_id
        self.document_id = document_id
        self._tmp_dir = None
        self._orig_db_path = None
        self._orig_raw = None
        self._orig_v1 = None
        self._orig_get_document = None
        self.nodes = []
        self.edges = []

    # ---- 生命周期 ----

    def __enter__(self):
        live = settings.SQLITE_DB_PATH
        self._tmp_dir = tempfile.mkdtemp(prefix="kg_fusion_test_")
        tmp_db = os.path.join(self._tmp_dir, "app_copy.db")
        shutil.copy2(live, tmp_db)

        self._orig_db_path = sd.sql_db.db_path
        sd.sql_db.db_path = tmp_db

        # 造一条属于本测试作用域的课程 + 文档记录，供 _check_scope 通过。
        # 必须成对创建：t_document.course_id 是物理外键，只插文档会 FK 报错。
        owner = sd.sql_db._query_one(
            "SELECT user_id FROM t_user ORDER BY user_id LIMIT 1")["user_id"]
        sd.sql_db._execute(
            "INSERT INTO t_course (course_id, course_code, course_name, teacher_id, status, "
            "join_code, join_mode) VALUES (?, 'FUSION_TEST', '融合测试课程', ?, 1, "
            "'FUSIONTESTCODE', 'approval')",
            (self.course_id, owner))
        sd.sql_db._execute(
            "INSERT INTO t_document (doc_id, course_id, uploader_id, file_name, file_type, "
            "file_size, parse_status, extract_status, entity_count, relation_count) "
            "VALUES (?, ?, ?, ?, 'TXT', 100, 'PARSED', 'COMPLETED', 0, 0)",
            (self.document_id, self.course_id, owner, "fusion_test_doc.txt"))

        # 合成图：替换 Neo4j 读取，全程不碰真实图库
        self._orig_raw = kg_manager.KnowledgeGraphManager.get_raw_graph_v1
        self._orig_v1 = kg_manager.KnowledgeGraphManager.get_graph_v1
        self._orig_db_query = db.query
        db.query = self._fake_query

        def _raw(cid, did, limit=500, node_type=None):
            return {"nodes": [dict(n) for n in self.nodes], "edges": [dict(e) for e in self.edges]}

        def _v1(cid, did, limit=500, node_type=None, apply_fusion=True):
            base = _raw(cid, did)
            if not apply_fusion:
                return base
            from app.services.fusion.fusion_map import load_active_mapping
            from app.services.fusion.graph_folder import fold_graph
            mapping = load_active_mapping(cid, did)
            if not mapping:
                return base
            nodes, edges = fold_graph(base["nodes"], base["edges"], mapping)
            return {"nodes": nodes, "edges": edges}

        kg_manager.KnowledgeGraphManager.get_raw_graph_v1 = staticmethod(_raw)
        kg_manager.KnowledgeGraphManager.get_graph_v1 = staticmethod(_v1)
        return self

    def __exit__(self, *exc):
        kg_manager.KnowledgeGraphManager.get_raw_graph_v1 = self._orig_raw
        kg_manager.KnowledgeGraphManager.get_graph_v1 = self._orig_v1
        db.query = self._orig_db_query
        if self._orig_db_path is not None:
            sd.sql_db.db_path = self._orig_db_path
        if self._tmp_dir:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
        return False

    # ---- 合成图上的 Cypher 解释器 ----
    #
    # 只用得上有限几种查询形态（读 kp_id 集合、按 kp_id 批量回查、按关键字 CONTAINS），
    # 所以不做通用解析：认得出就按语义返回，认不出的形态直接返回空，
    # 免得"看起来跑通了"其实是打到了真实图库。

    @staticmethod
    def _node_view(n):
        return {
            "kp_id": n["id"], "name": n["label"],
            "category": (n.get("properties") or {}).get("category") or "",
            "description": n.get("description") or "",
        }

    def _fake_query(self, cypher, params=None):
        params = params or {}
        cid = params.get("cid", params.get("course_id"))
        did = params.get("did", params.get("document_id"))
        if cid is not None and int(cid) != int(self.course_id):
            return []
        if did is not None and int(did) != int(self.document_id):
            return []

        ids = set(params.get("ids") or []) if params.get("ids") is not None else None
        fused = set(params.get("fused") or [])
        keyword = params.get("keyword")
        limit = params.get("top_k") or params.get("limit")

        rows = []
        for n in self.nodes:
            if ids is not None and n["id"] not in ids:
                continue
            if fused and n["id"] in fused:
                continue          # 对应 NOT n.kp_id IN $fused
            if keyword and keyword not in (n["label"] + (n.get("description") or "")):
                continue          # 对应 name CONTAINS / description CONTAINS
            # `RETURN n.kp_id AS kp_id, ...` 与 `RETURN n` 两种形态的返回结构不同，
            # 与真实驱动保持一致：后者是 {"n": 节点}，调用方都在用它。
            rows.append({"kp_id": n["id"], "name": n["label"],
                         "category": (n.get("properties") or {}).get("category") or "",
                         "description": n.get("description") or ""}
                        if "AS kp_id" in cypher else {"n": self._node_view(n)})
        return rows[:limit] if limit else rows

    # ---- 数据 ----

    def set_graph(self, nodes, edges):
        self.nodes = [dict(n) for n in nodes]
        self.edges = [dict(e) for e in edges]

    @property
    def folded_graph(self):
        """当前（按 ACTIVE 映射折叠后的）图 —— 等同于前端会看到的结果"""
        from app.services.kg_manager import KnowledgeGraphManager
        return KnowledgeGraphManager.get_graph_v1(self.course_id, self.document_id, limit=500)

    @property
    def raw_graph(self):
        from app.services.kg_manager import KnowledgeGraphManager
        return KnowledgeGraphManager.get_raw_graph_v1(self.course_id, self.document_id, limit=500)


# 一个覆盖「改写 + 去重 + 自环」三种情形的标准场景
def default_scenario():
    """kp_a(BFS) 应向 kp_b(广度优先搜索) 折叠。

    折叠前边：a→c(APPLIES_TO)、b→c(APPLIES_TO)、b→d(APPLIES_TO)、a→b(RELATED_TO)
    折叠后：  b→c 去重为一条、b→d 保留、a→b 变成自环被丢弃
    """
    nodes = [
        make_node("kp_a", "BFS", description="广度优先搜索（BFS）是一种图遍历算法", confidence=0.9),
        make_node("kp_b", "广度优先搜索", description="一种图遍历算法", confidence=0.9),
        make_node("kp_c", "图", description="由顶点和边组成的数据结构"),
        make_node("kp_d", "队列", description="先进先出的线性表"),
    ]
    edges = [
        make_edge("e1", "kp_a", "kp_c", "APPLIES_TO", 0.7),
        make_edge("e2", "kp_b", "kp_c", "APPLIES_TO", 0.95),
        make_edge("e3", "kp_b", "kp_d", "APPLIES_TO"),
        make_edge("e4", "kp_a", "kp_b", "RELATED_TO"),
    ]
    return nodes, edges
