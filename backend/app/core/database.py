"""
Neo4j 图数据库连接管理（对齐规划文档第四章：KnowledgePoint 节点 + 英文关系类型）
"""
import uuid
from datetime import datetime, timezone

from neo4j import GraphDatabase

from .config import settings

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
    """Neo4j 数据库管理类"""

    def __init__(self):
        self.driver = GraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
        )

    def close(self):
        self.driver.close()

    def query(self, cypher: str, params: dict = None):
        """执行查询"""
        with self.driver.session() as session:
            return list(session.run(cypher, params or {}))

    def init_schema(self):
        """初始化索引与约束（对齐规划文档 4.1.3）"""
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
            except Exception:
                # 不同 Neo4j 版本对 IF NOT EXISTS 支持略有差异，忽略重复创建报错
                pass

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

    def get_full_graph(self, course_id: str = None, document_id=None):
        """获取完整知识图谱（document_id 为 None 时回退课程级，仅兼容旧聚合入口）"""
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
