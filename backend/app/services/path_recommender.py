"""
学习路径推荐服务：基于知识图谱的个性化推荐
（对齐规划文档：KnowledgePoint 节点 + PRECEDES 英文关系类型，按 course_id 隔离）

关系方向约定（务必遵守）：
    A -[:PRECEDES]-> B 表示「A 是 B 的前置知识」，即先学 A 再学 B。
    因此：
    - B 的前置知识 = 入边来源 = (A)-[:PRECEDES]->(B)
    - B 解锁的后继 = 出边目标 = (B)-[:PRECEDES]->(C)
    - 根节点（无前置） = 无入边 = NOT ()-[:PRECEDES]->(B)
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
        获取某个知识点的所有前置知识（递归），沿 PRECEDES 入边向上遍历。
        若没有 PRECEDES 前置，则降级返回其 CONTAINS 上层概念（建议先了解）。
        每个结果携带 reason 字段，供前端区分展示。
        """
        cid = _coerce_course_id(course_id)
        if cid is not None:
            cypher = """
            MATCH path = (prereq:KnowledgePoint {course_id: $course_id})
                         -[:PRECEDES*1..5]->(n:KnowledgePoint {name: $name, course_id: $course_id})
            RETURN prereq.name AS name, prereq.category AS category,
                   prereq.description AS description, length(path) AS depth
            ORDER BY depth
            """
            params = {"name": knowledge_name, "course_id": cid}
        else:
            cypher = """
            MATCH path = (prereq:KnowledgePoint)-[:PRECEDES*1..5]->(n:KnowledgePoint {name: $name})
            RETURN prereq.name AS name, prereq.category AS category,
                   prereq.description AS description, length(path) AS depth
            ORDER BY depth
            """
            params = {"name": knowledge_name}

        records = db.query(cypher, params)
        if records:
            return [
                {
                    "name": r.get("name"),
                    "category": r.get("category") or "",
                    "description": r.get("description") or "",
                    "depth": r.get("depth") or 1,
                    "reason": f"第 {r.get('depth') or 1} 级前置",
                }
                for r in records
            ]

        # 降级：无 PRECEDES 前置时，返回 CONTAINS 上层概念（"建议先了解"）
        if cid is not None:
            cypher = """
            MATCH (parent:KnowledgePoint {course_id: $course_id})
                  -[:CONTAINS]->(n:KnowledgePoint {name: $name, course_id: $course_id})
            RETURN parent.name AS name, parent.category AS category, parent.description AS description
            """
            params = {"name": knowledge_name, "course_id": cid}
        else:
            cypher = """
            MATCH (parent:KnowledgePoint)-[:CONTAINS]->(n:KnowledgePoint {name: $name})
            RETURN parent.name AS name, parent.category AS category, parent.description AS description
            """
            params = {"name": knowledge_name}

        records = db.query(cypher, params)
        return [
            {
                "name": r.get("name"),
                "category": r.get("category") or "",
                "description": r.get("description") or "",
                "depth": None,
                "reason": "建议先了解的上层概念",
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
            # 没有已掌握的知识，推荐入门根节点（没有前置知识的节点，即无 PRECEDES 入边）
            if cid is not None:
                cypher = """
                MATCH (n:KnowledgePoint {course_id: $course_id})
                WHERE NOT ()-[:PRECEDES]->(n)
                RETURN n.name AS name, n.category AS category, n.description AS description
                LIMIT 10
                """
                params = {"course_id": cid}
            else:
                cypher = """
                MATCH (n:KnowledgePoint)
                WHERE NOT ()-[:PRECEDES]->(n)
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

        # 找到所有「某前置已掌握」的候选后继节点（known 是 next 的前置）
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

            # 检查该节点的所有前置知识（PRECEDES 入边）是否都已掌握
            if cid is not None:
                all_prereqs = db.query("""
                MATCH (prereq:KnowledgePoint {course_id: $course_id})
                      -[:PRECEDES]->(n:KnowledgePoint {name: $name, course_id: $course_id})
                RETURN prereq.name AS name
                """, {"name": name, "course_id": cid})
            else:
                all_prereqs = db.query("""
                MATCH (prereq:KnowledgePoint)-[:PRECEDES]->(n:KnowledgePoint {name: $name})
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
        if recommendations:
            return recommendations[:10]

        # 降级：已掌握知识点未解锁任何新知识点时，返回该课程的入门根节点（排除已掌握）
        if cid is not None:
            cypher = """
            MATCH (n:KnowledgePoint {course_id: $course_id})
            WHERE NOT ()-[:PRECEDES]->(n) AND NOT n.name IN $mastered
            RETURN n.name AS name, n.category AS category, n.description AS description
            LIMIT 10
            """
            params = {"course_id": cid, "mastered": mastered}
        else:
            cypher = """
            MATCH (n:KnowledgePoint)
            WHERE NOT ()-[:PRECEDES]->(n) AND NOT n.name IN $mastered
            RETURN n.name AS name, n.category AS category, n.description AS description
            LIMIT 10
            """
            params = {"mastered": mastered}
        records = db.query(cypher, params)
        return [
            {
                "name": r.get("name"),
                "category": r.get("category") or "",
                "description": r.get("description") or "",
                "reason": "未解锁新知识点，以下为课程入门知识点（可先掌握）",
            }
            for r in records
        ]

    @staticmethod
    def get_learning_path(target_knowledge: str, course_id=None) -> dict:
        """
        生成到达目标知识点的学习路径（可能有多个），沿 PRECEDES 从根节点（无前置）走到目标节点。
        若目标点没有 PRECEDES 路径，则降级返回该点本身 + 其 RELATED_TO 相关概念，供前端提示。

        返回 {"paths": [...], "fallback": bool, "target": dict|None,
              "related": [...], "reason": str|None}
        """
        cid = _coerce_course_id(course_id)
        if cid is not None:
            cypher = """
            MATCH path = (start:KnowledgePoint {course_id: $course_id})
                         -[:PRECEDES*1..10]->(target:KnowledgePoint {name: $target, course_id: $course_id})
            WHERE NOT ()-[:PRECEDES]->(start)
            RETURN [node in nodes(path) | {name: node.name, category: node.category}] AS steps,
                   length(path) AS path_length
            ORDER BY path_length
            LIMIT 5
            """
            params = {"target": target_knowledge, "course_id": cid}
        else:
            cypher = """
            MATCH path = (start:KnowledgePoint)-[:PRECEDES*1..10]->(target:KnowledgePoint {name: $target})
            WHERE NOT ()-[:PRECEDES]->(start)
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
        if paths:
            return {"paths": paths, "fallback": False, "target": None, "related": [], "reason": None}

        # 降级：查目标点本身 + RELATED_TO 相关概念
        if cid is not None:
            target_cypher = """
            MATCH (n:KnowledgePoint {name: $target, course_id: $course_id})
            RETURN n.name AS name, n.category AS category, n.description AS description
            LIMIT 1
            """
            related_cypher = """
            MATCH (n:KnowledgePoint {name: $target, course_id: $course_id})
                  -[r:RELATED_TO]-(m:KnowledgePoint {course_id: $course_id})
            RETURN m.name AS name, m.category AS category, m.description AS description
            """
            params = {"target": target_knowledge, "course_id": cid}
        else:
            target_cypher = """
            MATCH (n:KnowledgePoint {name: $target})
            RETURN n.name AS name, n.category AS category, n.description AS description
            LIMIT 1
            """
            related_cypher = """
            MATCH (n:KnowledgePoint {name: $target})-[r:RELATED_TO]-(m:KnowledgePoint)
            RETURN m.name AS name, m.category AS category, m.description AS description
            """
            params = {"target": target_knowledge}

        target_recs = db.query(target_cypher, params)
        target = target_recs[0] if target_recs else None
        if target is None:
            # 目标知识点本身不存在，交给前端提示"知识点不存在"
            return {"paths": [], "fallback": False, "target": None, "related": [], "reason": None}

        related_recs = db.query(related_cypher, params)
        related = [
            {
                "name": r.get("name"),
                "category": r.get("category") or "",
                "description": r.get("description") or "",
            }
            for r in related_recs
        ]
        return {
            "paths": [],
            "fallback": True,
            "target": {
                "name": target.get("name"),
                "category": target.get("category") or "",
                "description": target.get("description") or "",
            },
            "related": related,
            "reason": "该知识点未抽取到先修路径，以下为相关概念，可先了解",
        }
