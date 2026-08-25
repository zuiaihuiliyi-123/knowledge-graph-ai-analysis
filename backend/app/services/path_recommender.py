"""
学习路径推荐服务：基于知识图谱的个性化推荐
（对齐规划文档：KnowledgePoint 节点 + PRECEDES 英文关系类型，按 course_id 隔离）
"""
from typing import List, Set
from ..core.database import db


def _coerce_course_id(course_id):
    """course_id 统一为整数（对齐 Neo4j 存储类型）；空值返回 None 表示不过滤"""
    if course_id is None or course_id == "":
        return None
    try:
        return int(course_id)
    except (TypeError, ValueError):
        return None


class PathRecommender:
    """学习路径推荐器"""

    @staticmethod
    def get_prerequisites(knowledge_name: str, course_id=None) -> List[dict]:
        """
        获取某个知识点的所有前置知识（递归），沿 PRECEDES 关系向上遍历
        """
        cid = _coerce_course_id(course_id)
        if cid is not None:
            cypher = """
            MATCH path = (n:KnowledgePoint {name: $name, course_id: $course_id})
                         -[:PRECEDES*1..5]->(prereq:KnowledgePoint {course_id: $course_id})
            RETURN prereq.name AS name, prereq.category AS category,
                   prereq.description AS description, length(path) AS depth
            ORDER BY depth DESC
            """
            params = {"name": knowledge_name, "course_id": cid}
        else:
            cypher = """
            MATCH path = (n:KnowledgePoint {name: $name})-[:PRECEDES*1..5]->(prereq:KnowledgePoint)
            RETURN prereq.name AS name, prereq.category AS category,
                   prereq.description AS description, length(path) AS depth
            ORDER BY depth DESC
            """
            params = {"name": knowledge_name}

        records = db.query(cypher, params)
        return [
            {
                "name": r.get("name"),
                "category": r.get("category") or "",
                "description": r.get("description") or "",
                "depth": r.get("depth") or 1,
            }
            for r in records
        ]

    @staticmethod
    def recommend_next(mastered_knowledge: List[str], course_id=None) -> List[dict]:
        """
        根据已掌握的知识点，推荐下一步应学习的知识点

        逻辑：找到所有"前置知识已全部满足"的节点
        """
        mastered = [m for m in (mastered_knowledge or []) if m]
        cid = _coerce_course_id(course_id)

        if not mastered:
            # 没有已掌握的知识，推荐入门的根节点（没有前置知识的节点）
            if cid is not None:
                cypher = """
                MATCH (n:KnowledgePoint {course_id: $course_id})
                WHERE NOT (n)-[:PRECEDES]->(:KnowledgePoint)
                RETURN n.name AS name, n.category AS category, n.description AS description
                LIMIT 10
                """
                params = {"course_id": cid}
            else:
                cypher = """
                MATCH (n:KnowledgePoint)
                WHERE NOT (n)-[:PRECEDES]->(:KnowledgePoint)
                RETURN n.name AS name, n.category AS category, n.description AS description
                LIMIT 10
                """
                params = {}
            records = db.query(cypher, params)
            return [
                {
                    "name": r.get("name"),
                    "category": r.get("category") or "",
                    "description": r.get("description") or "",
                    "reason": "入门知识点（无需前置知识）",
                }
                for r in records
            ]

        # 找到所有已掌握节点的直接后继（被 PRECEDES 指向的节点）
        if cid is not None:
            cypher = """
            MATCH (known:KnowledgePoint {course_id: $course_id})-[:PRECEDES]->(next:KnowledgePoint {course_id: $course_id})
            WHERE known.name IN $mastered AND NOT next.name IN $mastered
            RETURN next.name AS name, next.category AS category, next.description AS description,
                   collect(known.name) AS prereqs_satisfied
            """
            params = {"mastered": mastered, "course_id": cid}
        else:
            cypher = """
            MATCH (known:KnowledgePoint)-[:PRECEDES]->(next:KnowledgePoint)
            WHERE known.name IN $mastered AND NOT next.name IN $mastered
            RETURN next.name AS name, next.category AS category, next.description AS description,
                   collect(known.name) AS prereqs_satisfied
            """
            params = {"mastered": mastered}

        records = db.query(cypher, params)

        recommendations = []
        for r in records:
            name = r.get("name")
            prereqs = r.get("prereqs_satisfied") or []

            # 检查该节点的所有前置知识是否都已掌握
            if cid is not None:
                all_prereqs = db.query("""
                MATCH (n:KnowledgePoint {name: $name, course_id: $course_id})-[:PRECEDES]->(prereq:KnowledgePoint {course_id: $course_id})
                RETURN prereq.name AS name
                """, {"name": name, "course_id": cid})
            else:
                all_prereqs = db.query("""
                MATCH (n:KnowledgePoint {name: $name})-[:PRECEDES]->(prereq:KnowledgePoint)
                RETURN prereq.name AS name
                """, {"name": name})

            all_prereq_names = {p.get("name") for p in all_prereqs}

            if all_prereq_names.issubset(set(mastered)):
                recommendations.append({
                    "name": name,
                    "category": r.get("category") or "",
                    "description": r.get("description") or "",
                    "reason": f"前置知识已掌握: {', '.join(prereqs)}",
                    "priority": len(all_prereq_names),  # 前置越多越应该先学
                })

        # 按优先级排序（前置条件多的优先）
        recommendations.sort(key=lambda x: -x["priority"])

        return recommendations[:10]

    @staticmethod
    def get_learning_path(target_knowledge: str, course_id=None) -> List[List[dict]]:
        """
        生成到达目标知识点的学习路径（可能有多个）
        使用 BFS 找到从任意起始节点（无前置）到目标节点的路径
        """
        cid = _coerce_course_id(course_id)
        if cid is not None:
            cypher = """
            MATCH path = (start:KnowledgePoint {course_id: $course_id})
                         -[:PRECEDES*1..10]->(target:KnowledgePoint {name: $target, course_id: $course_id})
            WHERE NOT (start)-[:PRECEDES]->(:KnowledgePoint)
            RETURN [node in nodes(path) | {name: node.name, category: node.category}] AS steps,
                   length(path) AS path_length
            ORDER BY path_length
            LIMIT 5
            """
            params = {"target": target_knowledge, "course_id": cid}
        else:
            cypher = """
            MATCH path = (start:KnowledgePoint)-[:PRECEDES*1..10]->(target:KnowledgePoint {name: $target})
            WHERE NOT (start)-[:PRECEDES]->(:KnowledgePoint)
            RETURN [node in nodes(path) | {name: node.name, category: node.category}] AS steps,
                   length(path) AS path_length
            ORDER BY path_length
            LIMIT 5
            """
            params = {"target": target_knowledge}

        records = db.query(cypher, params)
        paths = []
        for r in records:
            steps = r.get("steps") or []
            if steps:
                paths.append(steps)
        return paths
