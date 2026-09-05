"""
学习路径推荐服务：基于知识图谱的个性化推荐
（对齐规划文档：KnowledgePoint 节点 + PRECEDES 英文关系类型，按 course_id + document_id 隔离）

关系方向约定（务必遵守）：
    A -[:PRECEDES]-> B 表示「A 是 B 的前置知识」，即先学 A 再学 B。
    因此：
    - B 的前置知识 = 入边来源 = (A)-[:PRECEDES]->(B)
    - B 解锁的后继 = 出边目标 = (B)-[:PRECEDES]->(C)
    - 根节点（无前置） = 无入边 = NOT ()-[:PRECEDES]->(B)

Phase 8B：所有查询按 course_id + document_id 隔离，结果不跨文档；
document_id 为 None 时退化为课程级（仅教师教学监测等课程级汇总场景使用，不用于知识点访问）。
"""
from typing import List, Set
from ..core.database import db


def _coerce_id(value):
    """course_id / document_id 统一为整数（对齐 Neo4j 存储类型）；空值返回 None 表示不过滤"""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _scope(cid, did):
    """构建节点属性作用域。返回 (props_str, params)：
    props_str 形如 "course_id: $course_id, document_id: $document_id"（不含花括号），
    可直接拼接到 `(n:KnowledgePoint {<props_str>})`。
    did 为 None 时退化为课程级（仅教师监测等汇总场景）。
    """
    parts, params = [], {}
    if cid is not None:
        parts.append("course_id: $course_id")
        params["course_id"] = cid
        if did is not None:
            parts.append("document_id: $document_id")
            params["document_id"] = did
    return ", ".join(parts), params


def _node(cid, did, var, extra=()):
    """返回 (node_fragment, params)。node_fragment 形如
    "(n:KnowledgePoint {course_id: $course_id, document_id: $document_id})"；
    extra 为 [(key, value)] 追加属性（如 name）。
    """
    props, params = _scope(cid, did)
    for key, val in extra:
        props = (props + ", " if props else "") + f"{key}: ${key}"
        params[key] = val
    if props:
        return f"({var}:KnowledgePoint {{{props}}})", params
    return f"({var}:KnowledgePoint)", params


class PathRecommender:
    """学习路径推荐器"""

    @staticmethod
    def get_prerequisites(knowledge_name: str, course_id=None, document_id=None) -> List[dict]:
        """
        获取某个知识点的所有前置知识（递归），沿 PRECEDES 入边向上遍历。
        若没有 PRECEDES 前置，则降级返回其 CONTAINS 上层概念（建议先了解）。
        每个结果携带 reason 字段，供前端区分展示。
        """
        cid = _coerce_id(course_id)
        did = _coerce_id(document_id)
        target, tp = _node(cid, did, "n", [("name", knowledge_name)])
        prereq, pp = _node(cid, did, "prereq")

        records = db.query(
            f"""
            MATCH path = {prereq} -[:PRECEDES*1..5]-> {target}
            RETURN prereq.name AS name, prereq.category AS category,
                   prereq.description AS description, length(path) AS depth
            ORDER BY depth
            """,
            {**pp, **tp},
        )
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
        parent, pop = _node(cid, did, "parent")
        records = db.query(
            f"""
            MATCH {parent} -[:CONTAINS]-> {target}
            RETURN parent.name AS name, parent.category AS category, parent.description AS description
            """,
            {**pop, **tp},
        )
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
    def recommend_next(mastered_knowledge: List[str], course_id=None, document_id=None) -> List[dict]:
        """
        根据已掌握的知识点，推荐下一步应学习的知识点

        逻辑：找到所有"前置知识已全部满足"的节点
        """
        mastered = [m for m in (mastered_knowledge or []) if m]
        cid = _coerce_id(course_id)
        did = _coerce_id(document_id)

        if not mastered:
            # 没有已掌握的知识，推荐入门根节点（无前置知识的节点，即无 PRECEDES 入边）
            n, np_ = _node(cid, did, "n")
            incoming, ip = _node(cid, did, "pre")
            records = db.query(
                f"""
                MATCH {n}
                WHERE NOT EXISTS {{
                    MATCH {incoming}-[:PRECEDES]->(n)
                }}
                RETURN n.name AS name, n.category AS category, n.description AS description
                LIMIT 10
                """,
                {**np_, **ip},
            )
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
        known, kp = _node(cid, did, "known")
        nxt, nxp = _node(cid, did, "next")
        records = db.query(
            f"""
            MATCH {known}-[:PRECEDES]->{nxt}
            WHERE known.name IN $mastered AND NOT next.name IN $mastered
            RETURN next.name AS name, next.category AS category, next.description AS description,
                   collect(known.name) AS prereqs_satisfied
            """,
            {**kp, **nxp, "mastered": mastered},
        )

        recommendations = []
        for r in records:
            name = r.get("name")
            prereqs = r.get("prereqs_satisfied") or []

            # 检查该节点的所有前置知识（PRECEDES 入边）是否都已掌握
            prereq, prp = _node(cid, did, "prereq")
            target, tp = _node(cid, did, "n", [("name", name)])
            all_prereqs = db.query(
                f"""
                MATCH {prereq} -[:PRECEDES]-> {target}
                RETURN prereq.name AS name
                """,
                {**prp, **tp},
            )
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

        # 降级：已掌握知识点未解锁任何新知识点时，返回该作用域的入门根节点（排除已掌握）
        n, np_ = _node(cid, did, "n")
        incoming, ip = _node(cid, did, "pre")
        records = db.query(
            f"""
            MATCH {n}
            WHERE NOT EXISTS {{
                MATCH {incoming}-[:PRECEDES]->(n)
            }} AND NOT n.name IN $mastered
            RETURN n.name AS name, n.category AS category, n.description AS description
            LIMIT 10
            """,
            {**np_, **ip, "mastered": mastered},
        )
        return [
            {
                "name": r.get("name"),
                "category": r.get("category") or "",
                "description": r.get("description") or "",
                "reason": "未解锁新知识点，以下为入门知识点（可先掌握）",
            }
            for r in records
        ]

    @staticmethod
    def get_learning_path(target_knowledge: str, course_id=None, document_id=None) -> dict:
        """
        生成到达目标知识点的学习路径（可能有多个），沿 PRECEDES 从根节点（无前置）走到目标节点。
        若目标点没有 PRECEDES 路径，则降级返回该点本身 + 其 RELATED_TO 相关概念，供前端提示。

        返回 {"paths": [...], "fallback": bool, "target": dict|None,
              "related": [...], "reason": str|None}
        """
        cid = _coerce_id(course_id)
        did = _coerce_id(document_id)
        start, sp_ = _node(cid, did, "start")
        target, tp = _node(cid, did, "target", [("name", target_knowledge)])
        incoming, ip = _node(cid, did, "pre")

        records = db.query(
            f"""
            MATCH path = {start} -[:PRECEDES*1..10]-> {target}
            WHERE NOT EXISTS {{
                MATCH {incoming}-[:PRECEDES]->(start)
            }}
            RETURN [node in nodes(path) | {{name: node.name, category: node.category}}] AS steps,
                   length(path) AS path_length
            ORDER BY path_length
            LIMIT 5
            """,
            {**sp_, **tp, **ip},
        )
        paths = []
        for r in records:
            steps = r.get("steps") or []
            if steps:
                paths.append(steps)
        if paths:
            return {"paths": paths, "fallback": False, "target": None, "related": [], "reason": None}

        # 降级：查目标点本身 + RELATED_TO 相关概念
        n, np_ = _node(cid, did, "n", [("name", target_knowledge)])
        m, mp_ = _node(cid, did, "m")

        target_recs = db.query(
            f"""
            MATCH {n}
            RETURN n.name AS name, n.category AS category, n.description AS description
            LIMIT 1
            """,
            np_,
        )
        target = target_recs[0] if target_recs else None
        if target is None:
            # 目标知识点本身不存在，交给前端提示"知识点不存在"
            return {"paths": [], "fallback": False, "target": None, "related": [], "reason": None}

        related_recs = db.query(
            f"""
            MATCH {n} -[r:RELATED_TO]- {m}
            RETURN m.name AS name, m.category AS category, m.description AS description
            """,
            {**np_, **mp_},
        )
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
