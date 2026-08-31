"""
数据总览服务：跨 SQLite（用户/课程/文档）+ Neo4j（知识点/关系）聚合全局统计。

设计说明：
- 所有计数缺数据时返回 0，保证前端无需判空、直接展示；
- Neo4j 聚合包裹 try/except：图库不可用时整体退化为 0，不影响前端渲染。
"""
from ..core.database import db, RELATION_TYPE_LABELS
from ..core.sql_database import sql_db

# 知识点类别展示顺序（含兜底「其他」；前端雷达图仅用前 4 个核心类别）
CATEGORY_LABELS = ["概念", "定理", "公式", "方法", "其他"]


class DashboardService:
    """全局数据总览统计"""

    @staticmethod
    def get_stats() -> dict:
        stats = {
            "course_count": sql_db.count_courses(),
            "teacher_count": sql_db.count_users_by_role("teacher"),
            "student_count": sql_db.count_users_by_role("student"),
            "document_count": sql_db.count_documents(),
            "node_count": 0,
            "edge_count": 0,
            "concept_node_count": 0,
            "category_distribution": {c: 0 for c in CATEGORY_LABELS},
            "relation_distribution": {l: 0 for l in RELATION_TYPE_LABELS.values()},
            "per_course": [],
        }

        # 每门课程的节点/关系数（图库不可用时保持 0）
        courses = sql_db.list_courses()
        per_course = [
            {
                "course_id": c["course_id"],
                "course_name": c["course_name"],
                "node_count": 0,
                "edge_count": 0,
                "avg_degree": 0.0,
            }
            for c in courses
        ]
        node_counts = {}
        edge_counts = {}

        try:
            # 节点总数 + 每课程节点数
            for r in db.query(
                "MATCH (n:KnowledgePoint) RETURN n.course_id AS cid, count(n) AS cnt"
            ):
                stats["node_count"] += r["cnt"]
                node_counts[r["cid"]] = r["cnt"]

            # 关系总数 + 每课程关系数（仅统计同课程节点之间的边）
            for r in db.query(
                "MATCH (a:KnowledgePoint)-[r]->(b:KnowledgePoint) "
                "WHERE a.course_id = b.course_id "
                "RETURN a.course_id AS cid, count(r) AS cnt"
            ):
                stats["edge_count"] += r["cnt"]
                edge_counts[r["cid"]] = r["cnt"]

            # 概念节点数（类别为「概念」的知识点）
            recs = db.query(
                "MATCH (n:KnowledgePoint {category: '概念'}) RETURN count(n) AS cnt"
            )
            stats["concept_node_count"] = recs[0]["cnt"] if recs else 0

            # 知识点类别分布（动态聚合，未知类别归入「其他」）
            for r in db.query(
                "MATCH (n:KnowledgePoint) RETURN n.category AS cat, count(n) AS cnt"
            ):
                cat = r["cat"] or "其他"
                if cat not in stats["category_distribution"]:
                    stats["category_distribution"][cat] = 0
                stats["category_distribution"][cat] += r["cnt"]

            # 关系类型分布（英文类型 -> 中文标签）
            for r in db.query(
                "MATCH (:KnowledgePoint)-[r]->(:KnowledgePoint) "
                "RETURN type(r) AS t, count(r) AS cnt"
            ):
                label = RELATION_TYPE_LABELS.get(r["t"], r["t"])
                if label not in stats["relation_distribution"]:
                    stats["relation_distribution"][label] = 0
                stats["relation_distribution"][label] += r["cnt"]
        except Exception:
            # 图库连接失败等异常：保持 0，前端仍可正常展示
            pass

        # 合并每课程统计 + 计算平均关联度（关系数 / 节点数）
        for item in per_course:
            nc = node_counts.get(item["course_id"], 0)
            ec = edge_counts.get(item["course_id"], 0)
            item["node_count"] = nc
            item["edge_count"] = ec
            item["avg_degree"] = round(ec / nc, 2) if nc else 0.0
        stats["per_course"] = per_course

        return stats
