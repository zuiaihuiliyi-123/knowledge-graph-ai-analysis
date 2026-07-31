"""
Neo4j 图数据库连接管理
"""
from neo4j import GraphDatabase
from .config import settings


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

    def create_knowledge_node(self, name: str, category: str, properties: dict = None):
        """创建知识节点"""
        cypher = """
        MERGE (k:KnowledgeNode {name: $name})
        SET k.category = $category
        SET k += $properties
        RETURN k
        """
        return self.query(cypher, {
            "name": name,
            "category": category,
            "properties": properties or {}
        })

    def create_relationship(self, source: str, target: str, rel_type: str, properties: dict = None):
        """创建知识节点之间的关系"""
        cypher = f"""
        MATCH (a:KnowledgeNode {{name: $source}})
        MATCH (b:KnowledgeNode {{name: $target}})
        MERGE (a)-[r:{rel_type}]->(b)
        SET r += $properties
        RETURN a, r, b
        """
        return self.query(cypher, {
            "source": source,
            "target": target,
            "properties": properties or {}
        })

    def get_full_graph(self, course_id: str = None):
        """获取完整知识图谱"""
        if course_id:
            cypher = """
            MATCH (n:KnowledgeNode {course_id: $course_id})
            OPTIONAL MATCH (n)-[r]->(m:KnowledgeNode {course_id: $course_id})
            RETURN n, r, m
            """
            return self.query(cypher, {"course_id": course_id})
        else:
            cypher = """
            MATCH (n:KnowledgeNode)
            OPTIONAL MATCH (n)-[r]->(m:KnowledgeNode)
            RETURN n, r, m
            """
            return self.query(cypher)


# 全局数据库实例
db = Neo4jDB()
