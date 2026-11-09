"""
Neo4j 图数据库连接管理（对齐规划文档第四章：KnowledgePoint 节点 + 英文关系类型）
"""
import logging
import time
import uuid
from datetime import datetime, timezone

from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, SessionExpired

from .config import settings

logger = logging.getLogger(__name__)

# 连接超时（秒）：Neo4j 5.x 驱动默认 30s，图库宕机时会把每次调用都拖到 30s。
# 本项目「图库不可用 → 静默降级」的路径很多（graph_context / 召回 R3 / kp_labeler 目录），
# 必须把失败时延压到可接受范围。
_CONNECTION_TIMEOUT = 2.0
# 熔断窗口（秒）：一旦连接失败，在该时间窗内直接抛错、不再尝试建立连接。
# 没有熔断时，一次评测/一次推荐里的 N 次图调用会各等一个连接超时（实测 4.11s × 90 次 ≈ 6 分钟）；
# 有了熔断，第一个失败之后的所有调用**瞬时返回**，降级路径才真正可用。
_CIRCUIT_OPEN_SECONDS = 30.0

# 关系类型英文标识符 -> 中文标签映射（对齐规划文档「表格4/表格5」）
RELATION_TYPE_LABELS = {
    "PRECEDES": "前置知识",
    "CONTAINS": "包含",
    "RELATED_TO": "相关概念",
    "APPLIES_TO": "应用",
}

# 合法关系类型集合，用于校验（防止非法类型/注入）
VALID_RELATION_TYPES = set(RELATION_TYPE_LABELS.keys())


def _now_iso() -> str:
    """返回 ISO8601 UTC 时间字符串"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_kp_id(course_id: str) -> str:
    """生成知识点业务主键，格式 kp_{course_id}_{uuid_short}"""
    return f"kp_{course_id}_{uuid.uuid4().hex[:8]}"


class Neo4jDB:
    """Neo4j 数据库管理类（含失败熔断，保证「图库不可用」时降级够快）"""

    def __init__(self):
        # 惰性创建驱动：模块导入/应用启动不连库，首次 query（或访问 driver）时才建。
        # 这样测试与离线脚本 import app.main 不会留下未关闭的驱动连接。
        self._driver = None
        self._down_until = 0.0        # 熔断截止时间（time.monotonic 基准）

    @property
    def driver(self):
        """惰性创建并返回驱动；保留 driver 属性以兼容既有脚本的直接访问。"""
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                settings.NEO4J_URI,
                auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
                connection_timeout=_CONNECTION_TIMEOUT,
            )
        return self._driver

    def close(self):
        """关闭驱动（幂等）；关闭后下次访问 driver 会重新创建。"""
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def circuit_open(self) -> bool:
        """当前是否处于熔断期（供调用方判断，不需自己捕获异常）"""
        return time.monotonic() < self._down_until

    def query(self, cypher: str, params: dict = None):
        """执行查询。连接失败会开启熔断窗口，窗口内的调用不再尝试连接、直接抛错。"""
        now = time.monotonic()
        if now < self._down_until:
            # 熔断中：不再付一次连接超时的代价
            raise RuntimeError(
                f"Neo4j 处于熔断期（上次连接失败，{self._down_until - now:.0f}s 后重试）")
        try:
            with self.driver.session() as session:
                return list(session.run(cypher, params or {}))
        except Exception as exc:
            # 只有「连接不上/会话过期」才熔断。Cypher 语法错、约束冲突、权限不足
            # 属于查询本身的问题，不应让全站图查询跟着停摆 30 秒。
            if isinstance(exc, (ServiceUnavailable, SessionExpired)):
                self._down_until = now + _CIRCUIT_OPEN_SECONDS
            raise

    def init_schema(self) -> bool:
        """初始化索引与约束（对齐规划文档 4.1.3）。

        返回 True 表示全部语句执行成功；False 表示图库不可达、已提前跳过剩余语句。
        单条语句失败不阻断启动，但必须留下日志——否则索引缺失会静默拖慢所有 MERGE。
        """
        statements = [
            # 知识点业务主键唯一约束
            "CREATE CONSTRAINT kp_id_unique IF NOT EXISTS "
            "FOR (kp:KnowledgePoint) REQUIRE kp.kp_id IS UNIQUE",
            # 按课程 + 名称复合索引
            "CREATE INDEX kp_course_name IF NOT EXISTS "
            "FOR (kp:KnowledgePoint) ON (kp.course_id, kp.name)",
            # 按课程 + 类别复合索引
            "CREATE INDEX kp_course_category IF NOT EXISTS "
            "FOR (kp:KnowledgePoint) ON (kp.course_id, kp.category)",
            # 前置关系按课程建关系属性索引（用于路径推荐过滤）
            "CREATE INDEX precedes_course IF NOT EXISTS "
            "FOR ()-[r:PRECEDES]-() ON (r.course_id)",
            # 文档级作用域：按课程 + 文档复合索引（文档图隔离）
            "CREATE INDEX kp_course_doc IF NOT EXISTS "
            "FOR (kp:KnowledgePoint) ON (kp.course_id, kp.document_id)",
            # 文档级 MERGE 键索引：课程 + 文档 + 名称
            "CREATE INDEX kp_course_doc_name IF NOT EXISTS "
            "FOR (kp:KnowledgePoint) ON (kp.course_id, kp.document_id, kp.name)",
        ]
        for stmt in statements:
            try:
                self.query(stmt)
            except Exception as exc:
                # 不同 Neo4j 版本对 IF NOT EXISTS 支持略有差异；重复创建/权限不足时
                # 不应阻断启动，但必须留下日志，否则索引缺失会静默拖慢所有 MERGE。
                logger.warning("初始化 Neo4j 约束/索引失败：%s", exc)
                if self.circuit_open():
                    logger.warning("Neo4j 不可达，跳过剩余索引初始化")
                    return False
        return True

    def create_knowledge_node(self, course_id: str, document_id, name: str, category: str,
                              description: str = "", properties: dict = None):
        """创建/更新知识点节点（KnowledgePoint），按 (course_id, document_id, name) MERGE 幂等。

        Phase 8A：MERGE 唯一作用域加入 document_id，同一课程不同文档的同名知识点互相独立；
        kp_id 仍按 (course_id, uuid) 生成，全局唯一，不作为 MERGE key。
        """
        kp_id = generate_kp_id(course_id)
        cypher = """
        MERGE (kp:KnowledgePoint {course_id: $course_id, document_id: $document_id, name: $name})
        ON CREATE SET kp.kp_id = $kp_id, kp.created_at = $now
        SET kp.category = $category,
            kp.description = $description,
            kp.updated_at = $now,
            kp += $props
        RETURN kp.kp_id AS kp_id, kp.name AS name
        """
        return self.query(cypher, {
            "course_id": course_id,
            "document_id": document_id,
            "name": name,
            "kp_id": kp_id,
            "category": category,
            "description": description,
            "now": _now_iso(),
            "props": properties or {},
        })

    def create_relationship(self, course_id: str, document_id, source: str, target: str,
                            rel_type: str, properties: dict = None):
        """创建知识点关系：英文 Cypher 类型 + relation_type 中文属性。

        Phase 8A：两端节点与关系均按 document_id 限定，禁止跨文档合并关系。
        """
        # 校验关系类型，非法则回退 RELATED_TO
        if rel_type not in VALID_RELATION_TYPES:
            rel_type = "RELATED_TO"
        label = RELATION_TYPE_LABELS.get(rel_type, rel_type)

        cypher = f"""
        MATCH (a:KnowledgePoint {{course_id: $course_id, document_id: $document_id, name: $source}})
        MATCH (b:KnowledgePoint {{course_id: $course_id, document_id: $document_id, name: $target}})
        MERGE (a)-[r:{rel_type}]->(b)
        ON CREATE SET r.created_at = $now
        SET r.relation_type = $label,
            r.course_id = $course_id,
            r.document_id = $document_id,
            r.updated_at = $now,
            r += $props
        RETURN type(r) AS type, a.name AS source, b.name AS target
        """
        return self.query(cypher, {
            "course_id": course_id,
            "document_id": document_id,
            "source": source,
            "target": target,
            "label": label,
            "now": _now_iso(),
            "props": properties or {},
        })

    def create_knowledge_nodes_bulk(self, course_id: str, document_id, rows: list) -> int:
        """批量 MERGE 知识点节点（**单条 Cypher + UNWIND**），返回写入条数。

        为什么要有批量版本：一份 60 个实体的文档逐条写入就是 60 次网络往返，
        叠加抽取/向量化后建图成为明显瓶颈；UNWIND 把节点写入压成 1 次往返。

        与 `create_knowledge_node` 语义完全一致：
        - MERGE key 仍是 `(course_id, document_id, name)`（幂等、同文档同名不并存）；
        - `kp_id` 只在 ON CREATE 时生成，重复调用不会改已有节点的 id；
        - `SET` 覆盖 category/description/updated_at，并把 row.props 合并进节点
          （同名实体合并留下的 alt_descriptions / category_variants 就走这里）。

        rows: `[{"name", "category", "description", "properties"}]`；空 name 的行会被跳过。
        """
        payload = []
        for r in rows or []:
            name = (r.get("name") or "").strip()
            if not name:
                continue
            payload.append({
                "name": name,
                "kp_id": generate_kp_id(course_id),
                "category": r.get("category") or "概念",
                "description": r.get("description") or "",
                "props": r.get("properties") or {},
            })
        if not payload:
            return 0

        cypher = """
        UNWIND $rows AS row
        MERGE (kp:KnowledgePoint {course_id: $course_id, document_id: $document_id, name: row.name})
        ON CREATE SET kp.kp_id = row.kp_id, kp.created_at = $now
        SET kp.category = row.category,
            kp.description = row.description,
            kp.updated_at = $now,
            kp += row.props
        RETURN count(kp) AS written
        """
        res = self.query(cypher, {
            "course_id": course_id,
            "document_id": document_id,
            "rows": payload,
            "now": _now_iso(),
        })
        return int(res[0]["written"]) if res else 0

    def create_relationships_bulk(self, course_id: str, document_id, rels: list) -> int:
        """批量 MERGE 关系（**按类型分组后每组一条 UNWIND**），返回写入条数。

        为什么分组而不是一条 Cypher：Neo4j 不支持把关系类型参数化，类型必须拼进语句；
        先把类型收敛到 `VALID_RELATION_TYPES`（非法回退 RELATED_TO），再按类型各发一条，
        最多 4 次往返（当前只有 4 种类型），而不是每条关系一次。

        与 `create_relationship` 语义一致：
        - 两端 MATCH 都带 `course_id + document_id`，禁止跨文档/跨课程连边；
        - 端点不存在的行会被 MATCH 过滤掉（与逐条版本一致，不报错）；
        - `MERGE (a)-[r:TYPE]->(b)` 幂等，`relation_type` 中文标签与 props 会被覆盖/合并。

        rels: `[{"source", "target", "type", "properties"}]`。
        """
        buckets = {}
        for r in rels or []:
            source = (r.get("source") or "").strip()
            target = (r.get("target") or "").strip()
            if not source or not target or source == target:
                continue
            rel_type = (r.get("type") or "").strip().upper()
            if rel_type not in VALID_RELATION_TYPES:
                rel_type = "RELATED_TO"
            buckets.setdefault(rel_type, []).append({
                "source": source,
                "target": target,
                "label": RELATION_TYPE_LABELS.get(rel_type, rel_type),
                "props": r.get("properties") or {},
            })
        if not buckets:
            return 0

        now = _now_iso()
        written = 0
        for rel_type, rows in buckets.items():
            cypher = f"""
            UNWIND $rows AS row
            MATCH (a:KnowledgePoint {{course_id: $course_id, document_id: $document_id, name: row.source}})
            MATCH (b:KnowledgePoint {{course_id: $course_id, document_id: $document_id, name: row.target}})
            MERGE (a)-[r:{rel_type}]->(b)
            ON CREATE SET r.created_at = $now
            SET r.relation_type = row.label,
                r.course_id = $course_id,
                r.document_id = $document_id,
                r.updated_at = $now,
                r += row.props
            RETURN count(r) AS written
            """
            res = self.query(cypher, {
                "course_id": course_id,
                "document_id": document_id,
                "rows": rows,
                "now": now,
            })
            if res:
                written += int(res[0]["written"])
        return written

    def get_full_graph(self, course_id: str = None, document_id=None, course_ids=None):
        """获取完整知识图谱（document_id 为 None 时回退课程级，仅兼容旧聚合入口）。

        course_ids：可访问课程白名单。仅在 course_id 缺省（全库聚合）时生效，
        用于把 legacy 的 /api/kg/all 收敛到「当前用户可读的课程」而不是整库导出。
        空列表语义是「无可见课程」→ 直接返回空，绝不退化成全库查询。
        """
        if course_id is None and course_ids is not None:
            allowed = list(course_ids)
            if not allowed:
                return []
            cypher = """
            MATCH (n:KnowledgePoint)
            WHERE n.course_id IN $course_ids
            OPTIONAL MATCH (n)-[r]->(m:KnowledgePoint)
            WHERE m.course_id IN $course_ids
            RETURN n, r, m
            """
            return self.query(cypher, {"course_ids": allowed})

        if course_id is not None and document_id is not None:
            cypher = """
            MATCH (n:KnowledgePoint {course_id: $course_id, document_id: $document_id})
            OPTIONAL MATCH (n)-[r]->(m:KnowledgePoint {course_id: $course_id, document_id: $document_id})
            RETURN n, r, m
            """
            return self.query(cypher, {"course_id": course_id, "document_id": document_id})
        elif course_id is not None:
            # [deprecated] 课程级聚合（课程列表/统计等汇总场景）
            cypher = """
            MATCH (n:KnowledgePoint {course_id: $course_id})
            OPTIONAL MATCH (n)-[r]->(m:KnowledgePoint {course_id: $course_id})
            RETURN n, r, m
            """
            return self.query(cypher, {"course_id": course_id})
        else:
            cypher = """
            MATCH (n:KnowledgePoint)
            OPTIONAL MATCH (n)-[r]->(m:KnowledgePoint)
            RETURN n, r, m
            """
            return self.query(cypher)

    def delete_course_graph(self, course_id: int):
        """删除指定课程的所有知识点节点与关系，返回 (node_count, edge_count)"""
        node_cnt = self.query(
            "MATCH (n:KnowledgePoint {course_id: $cid}) RETURN count(n) AS cnt",
            {"cid": course_id},
        )[0]["cnt"]
        edge_cnt = self.query(
            "MATCH (:KnowledgePoint {course_id: $cid})-[r]->(:KnowledgePoint {course_id: $cid}) "
            "RETURN count(r) AS cnt",
            {"cid": course_id},
        )[0]["cnt"]
        self.query(
            "MATCH (n:KnowledgePoint {course_id: $cid}) DETACH DELETE n", {"cid": course_id},
        )
        return node_cnt, edge_cnt

    def delete_document_graph(self, course_id: int, document_id) -> tuple:
        """删除指定文档的图谱节点与关系，返回 (node_count, edge_count)。

        Phase 8A：仅删除当前文档（course_id + document_id 双重限定），
        绝不误删同课程其他文档的节点；严禁退化为仅按 course_id 删除。
        """
        node_cnt = self.query(
            "MATCH (n:KnowledgePoint {course_id: $cid, document_id: $did}) RETURN count(n) AS cnt",
            {"cid": course_id, "did": document_id},
        )[0]["cnt"]
        edge_cnt = self.query(
            "MATCH (:KnowledgePoint {course_id: $cid, document_id: $did})-[r]->"
            "(:KnowledgePoint {course_id: $cid, document_id: $did}) RETURN count(r) AS cnt",
            {"cid": course_id, "did": document_id},
        )[0]["cnt"]
        self.query(
            "MATCH (n:KnowledgePoint {course_id: $cid, document_id: $did}) DETACH DELETE n",
            {"cid": course_id, "did": document_id},
        )
        return node_cnt, edge_cnt

    def backfill_document_id(self, course_id, document_id):
        """旧数据迁移：为指定课程的节点/关系回填 document_id（幂等，仅填缺失值）。

        第一阶段历史数据「一课程 == 一文档」，迁移时按该映射回填；
        重复调用不会覆盖已存在的 document_id。
        """
        self.query(
            "MATCH (n:KnowledgePoint {course_id: $cid}) "
            "WHERE n.document_id IS NULL SET n.document_id = $doc",
            {"cid": course_id, "doc": document_id},
        )
        self.query(
            "MATCH (:KnowledgePoint {course_id: $cid})-[r]->(:KnowledgePoint {course_id: $cid}) "
            "WHERE r.document_id IS NULL SET r.document_id = $doc",
            {"cid": course_id, "doc": document_id},
        )


# 全局数据库实例
db = Neo4jDB()
