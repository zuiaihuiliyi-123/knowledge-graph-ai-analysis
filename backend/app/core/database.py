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
        ]
        for stmt in statements:
            try:
                self.query(stmt)
            except Exception:
                # 不同 Neo4j 版本对 IF NOT EXISTS 支持略有差异，忽略重复创建报错
                pass

    def create_knowledge_node(self, course_id: str, name: str, category: str,
                              description: str = "", properties: dict = None):
        """创建/更新知识点节点（KnowledgePoint），按 (course_id, name) MERGE 幂等"""
        kp_id = generate_kp_id(course_id)
        cypher = """
        MERGE (kp:KnowledgePoint {course_id: $course_id, name: $name})
        ON CREATE SET kp.kp_id = $kp_id, kp.created_at = $now
        SET kp.category = $category,
            kp.description = $description,
            kp.updated_at = $now,
            kp += $props
        RETURN kp.kp_id AS kp_id, kp.name AS name
        """
        return self.query(cypher, {
            "course_id": course_id,
            "name": name,
            "kp_id": kp_id,
            "category": category,
            "description": description,
            "now": _now_iso(),
            "props": properties or {},
        })

    def create_relationship(self, course_id: str, source: str, target: str,
                            rel_type: str, properties: dict = None):
        """创建知识点关系：英文 Cypher 类型 + relation_type 中文属性"""
        # 校验关系类型，非法则回退 RELATED_TO
        if rel_type not in VALID_RELATION_TYPES:
            rel_type = "RELATED_TO"
        label = RELATION_TYPE_LABELS.get(rel_type, rel_type)

        cypher = f"""
        MATCH (a:KnowledgePoint {{course_id: $course_id, name: $source}})
        MATCH (b:KnowledgePoint {{course_id: $course_id, name: $target}})
        MERGE (a)-[r:{rel_type}]->(b)
        ON CREATE SET r.created_at = $now
        SET r.relation_type = $label,
            r.course_id = $course_id,
            r.updated_at = $now,
            r += $props
        RETURN type(r) AS type, a.name AS source, b.name AS target
        """
        return self.query(cypher, {
            "course_id": course_id,
            "source": source,
            "target": target,
            "label": label,
            "now": _now_iso(),
            "props": properties or {},
        })

    def get_full_graph(self, course_id: str = None):
        """获取完整知识图谱"""
        if course_id:
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


# 全局数据库实例
db = Neo4jDB()
