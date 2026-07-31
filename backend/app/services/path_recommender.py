"""
学习路径推荐服务：基于知识图谱的个性化推荐
"""
from typing import List, Set
from ..core.database import db


class PathRecommender:
    """学习路径推荐器"""

    @staticmethod
    def get_prerequisites(knowledge_name: str, course_id: str = None) -> List[dict]:
        """
        获取某个知识点的所有前置知识（递归）
        """
        cypher = """
        MATCH path = (n:KnowledgeNode {name: $name})-[:前置知识*1..5]->(prereq:KnowledgeNode)
        RETURN prereq.name, prereq.category, prereq.description, length(path) as depth
        ORDER BY depth DESC
        """
        records = db.query(cypher, {"name": knowledge_name})
        return [
            {
                "name": r.get("prereq").get("name"),
                "category": r.get("prereq").get("category", ""),
                "description": r.get("prereq").get("description", ""),
                "depth": r.get("depth", 1)
            }
            for r in records
        ]

    @staticmethod
    def recommend_next(mastered_knowledge: List[str], course_id: str = None) -> List[dict]:
        """
        根据已掌握的知识点，推荐下一步应学习的知识点

        逻辑：找到所有"前置知识已全部满足"的节点
        """
        if not mastered_knowledge:
            # 没有已掌握的知识，推荐入门的根节点（没有前置知识的节点）
            cypher = """
            MATCH (n:KnowledgeNode)
            WHERE NOT (n)-[:前置知识]->(:KnowledgeNode)
            RETURN n.name, n.category, n.description
            LIMIT 10
            """
            records = db.query(cypher)
            return [
                {
                    "name": r.get("n").get("name"),
                    "category": r.get("n").get("category", ""),
                    "description": r.get("n").get("description", ""),
                    "reason": "入门知识点（无需前置知识）"
                }
                for r in records
            ]

        # 找到所有已掌握的节点的直接后继
        cypher = """
        MATCH (known:KnowledgeNode)-[:前置知识]->(next:KnowledgeNode)
        WHERE known.name IN $mastered
        AND NOT next.name IN $mastered
        RETURN next.name, next.category, next.description, collect(known.name) as prereqs_satisfied
        """
        records = db.query(cypher, {"mastered": mastered_knowledge})

        recommendations = []
        for r in records:
            node = r.get("next")
            prereqs = r.get("prereqs_satisfied", [])

            # 检查该节点的所有前置知识是否都已掌握
            all_prereqs = db.query("""
            MATCH (n:KnowledgeNode {name: $name})-[:前置知识]->(prereq:KnowledgeNode)
            RETURN prereq.name as name
            """, {"name": node.get("name")})

            all_prereq_names = {p.get("name") for p in all_prereqs}

            if all_prereq_names.issubset(set(mastered_knowledge)):
                recommendations.append({
                    "name": node.get("name"),
                    "category": node.get("category", ""),
                    "description": node.get("description", ""),
                    "reason": f"前置知识已掌握: {', '.join(prereqs)}",
                    "priority": len(all_prereq_names)  # 前置越多越应该先学
                })

        # 按优先级排序（前置条件多的优先）
        recommendations.sort(key=lambda x: -x["priority"])

        return recommendations[:10]

    @staticmethod
    def get_learning_path(target_knowledge: str, course_id: str = None) -> List[List[dict]]:
        """
        生成到达目标知识点的学习路径（可能有多个路径）
        使用BFS找到从任意起始节点到目标节点的路径
        """
        cypher = """
        MATCH path = (start:KnowledgeNode)-[:前置知识*1..10]->(target:KnowledgeNode {name: $target})
        WHERE NOT (start)-[:前置知识]->(:KnowledgeNode)
        RETURN [node in nodes(path) | {name: node.name, category: node.category}] as steps,
               length(path) as path_length
        ORDER BY path_length
        LIMIT 5
        """
        records = db.query(cypher, {"target": target_knowledge})
        paths = []
        for r in records:
            steps = r.get("steps", [])
            if steps:
                paths.append(steps)
        return paths
