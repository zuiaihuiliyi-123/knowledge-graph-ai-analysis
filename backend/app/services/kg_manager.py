"""
知识图谱管理服务：构建、存储、查询
"""
from typing import List, Dict
from ..core.database import db


class KnowledgeGraphManager:
    """知识图谱管理器"""

    @staticmethod
    def build_graph(course_id: str, entities: List[dict], relations: List[dict]) -> dict:
        """
        根据提取的实体和关系构建知识图谱（对齐规划文档：KnowledgePoint 节点 + 英文关系类型）
        """
        node_count = 0
        relation_count = 0

        # 创建节点
        for entity in entities:
            name = entity.get("name", "").strip()
            if not name:
                continue
            db.create_knowledge_node(
                course_id=course_id,
                name=name,
                category=entity.get("category", "概念"),
                description=entity.get("description", ""),
                properties={"confidence": 0.9, "is_manual": False}
            )
            node_count += 1

        # 创建关系
        for rel in relations:
            source = rel.get("source", "").strip()
            target = rel.get("target", "").strip()
            rel_type = rel.get("type", "RELATED_TO")
            # 应用层校验：过滤空值与自环（对齐规划文档「表格4」）
            if not source or not target or source == target:
                continue
            try:
                db.create_relationship(
                    course_id=course_id,
                    source=source,
                    target=target,
                    rel_type=rel_type,
                    properties={"confidence": 0.9, "is_manual": False}
                )
                relation_count += 1
            except Exception:
                pass  # 忽略无效关系（如目标节点不存在）

        return {
            "course_id": course_id,
            "node_count": node_count,
            "relation_count": relation_count
        }

    @staticmethod
    def get_graph_data(course_id: str = None) -> dict:
        """
        获取知识图谱数据，转为 ECharts 格式
        返回 {"nodes": [...], "links": [...]}
        """
        records = db.get_full_graph(course_id)

        nodes = {}
        links = []

        for record in records:
            # 处理起始节点
            node_n = record.get("n")
            if node_n:
                node_id = node_n.get("name")
                if node_id not in nodes:
                    nodes[node_id] = {
                        "name": node_id,
                        "category": node_n.get("category", "其他"),
                        "description": node_n.get("description", ""),
                        "symbolSize": 40
                    }

            # 处理目标节点
            node_m = record.get("m")
            if node_m:
                node_id = node_m.get("name")
                if node_id not in nodes:
                    nodes[node_id] = {
                        "name": node_id,
                        "category": node_m.get("category", "其他"),
                        "description": node_m.get("description", ""),
                        "symbolSize": 40
                    }

            # 处理关系
            rel = record.get("r")
            if rel:
                links.append({
                    "source": record.get("n").get("name"),
                    "target": node_m.get("name"),
                    "type": rel.type if rel else "RELATED_TO"
                })

        return {
            "nodes": list(nodes.values()),
            "links": links
        }

    @staticmethod
    def update_node(name: str, properties: dict):
        """更新节点属性"""
        db.query(
            "MATCH (n:KnowledgeNode {name: $name}) SET n += $props RETURN n",
            {"name": name, "props": properties}
        )

    @staticmethod
    def delete_node(name: str):
        """删除节点及其所有关系"""
        db.query(
            "MATCH (n:KnowledgeNode {name: $name}) DETACH DELETE n",
            {"name": name}
        )

    @staticmethod
    def delete_relationship(source: str, target: str, rel_type: str = None):
        """删除关系"""
        if rel_type:
            cypher = """
            MATCH (a:KnowledgeNode {name: $source})-[r:$rel_type]->(b:KnowledgeNode {name: $target})
            DELETE r
            """
        else:
            cypher = """
            MATCH (a:KnowledgeNode {name: $source})-[r]->(b:KnowledgeNode {name: $target})
            DELETE r
            """
        db.query(cypher, {"source": source, "target": target, "rel_type": rel_type})
