"""
知识图谱管理服务：构建、存储、查询、手动编辑
"""
from datetime import datetime, timezone
from typing import List, Dict
from ..core.database import db, VALID_RELATION_TYPES, RELATION_TYPE_LABELS
from ..core.sql_database import sql_db


def _now_iso() -> str:
    """返回 ISO8601 UTC 时间字符串"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# 类别中文 -> 英文类型映射（对齐规划文档 6.3 节点 type 字段）
CATEGORY_TYPE_MAP = {
    "概念": "concept",
    "定理": "theorem",
    "公式": "formula",
    "方法": "method",
}


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
    def get_graph_v1(course_id: str, limit: int = 500, node_type: str = None) -> dict:
        """
        获取课程图谱数据（对齐规划文档 6.3.2）
        返回 {"nodes": [...], "edges": [...]}，节点以 kp_id 为 id
        """
        params = {"course_id": course_id, "limit": limit}

        if node_type:
            cypher = """
            MATCH (n:KnowledgePoint {course_id: $course_id, category: $node_type})
            WITH n ORDER BY n.name LIMIT $limit
            OPTIONAL MATCH (n)-[r]->(m:KnowledgePoint {course_id: $course_id})
            RETURN n, r, m, elementId(r) AS edge_id
            """
            params["node_type"] = node_type
        else:
            cypher = """
            MATCH (n:KnowledgePoint {course_id: $course_id})
            WITH n ORDER BY n.name LIMIT $limit
            OPTIONAL MATCH (n)-[r]->(m:KnowledgePoint {course_id: $course_id})
            RETURN n, r, m, elementId(r) AS edge_id
            """

        records = db.query(cypher, params)

        nodes = {}
        edges = []

        def node_dict(node) -> dict:
            kp_id = node.get("kp_id") or node.get("name")
            return {
                "id": kp_id,
                "label": node.get("name", ""),
                "type": CATEGORY_TYPE_MAP.get(node.get("category"), "concept"),
                "description": node.get("description", ""),
                "properties": {
                    "category": node.get("category", ""),
                    "confidence": node.get("confidence"),
                    "is_manual": node.get("is_manual", False),
                    "created_at": node.get("created_at"),
                    "updated_at": node.get("updated_at"),
                },
            }

        for record in records:
            n = record.get("n")
            m = record.get("m")
            r = record.get("r")
            if n:
                nodes.setdefault(n.get("kp_id") or n.get("name"), node_dict(n))
            if m:
                nodes.setdefault(m.get("kp_id") or m.get("name"), node_dict(m))
            if r and n and m:
                edges.append({
                    "id": record.get("edge_id") or "",
                    "source": n.get("kp_id") or n.get("name"),
                    "target": m.get("kp_id") or m.get("name"),
                    "type": r.type,
                    "label": r.get("relation_type") or r.type,
                    "properties": {
                        "confidence": r.get("confidence"),
                        "is_manual": r.get("is_manual", False),
                        "created_at": r.get("created_at"),
                        "updated_at": r.get("updated_at"),
                    },
                })

        return {"nodes": list(nodes.values()), "edges": edges}

    # ---------- 教师手动编辑（按 kp_id / edge_id 定位，避免 name 跨课程重名误伤） ----------

    @staticmethod
    def create_node(course_id: str, name: str, category: str = "概念",
                    description: str = "", is_manual: bool = True) -> dict:
        """手动新增知识点（is_manual=True），返回 {kp_id, name, category}"""
        name = (name or "").strip()
        if not name:
            raise ValueError("知识点名称不能为空")
        if category not in CATEGORY_TYPE_MAP:
            category = "概念"
        # 同课程重名校验（MERGE 按 (course_id, name)，手动新增不应静默覆盖已有节点）
        dup = db.query(
            "MATCH (n:KnowledgePoint {course_id: $cid, name: $name}) RETURN n.kp_id AS kp_id",
            {"cid": course_id, "name": name},
        )
        if dup:
            raise ValueError(f"知识点「{name}」已存在")
        recs = db.create_knowledge_node(
            course_id=course_id, name=name, category=category,
            description=description,
            properties={"confidence": 1.0, "is_manual": bool(is_manual)},
        )
        kp_id = recs[0]["kp_id"] if recs else None
        # 节点变更后使向量索引失效，下次问答时懒重建
        sql_db.delete_embeddings_by_course(int(course_id))
        return {"kp_id": kp_id, "name": name, "category": category}

    @staticmethod
    def update_node(course_id: str, kp_id: str, name: str = None,
                    category: str = None, description: str = None) -> dict:
        """按 kp_id 更新知识点属性；name 做同课程重名校验"""
        recs = db.query(
            "MATCH (n:KnowledgePoint {kp_id: $kp_id, course_id: $cid}) RETURN n",
            {"kp_id": kp_id, "cid": course_id},
        )
        if not recs:
            raise ValueError(f"知识点不存在: {kp_id}")
        old = recs[0]["n"]

        props = {}
        if name is not None:
            name = name.strip()
            if not name:
                raise ValueError("知识点名称不能为空")
            if name != old.get("name"):
                dup = db.query(
                    "MATCH (n:KnowledgePoint {course_id: $cid, name: $name}) RETURN n",
                    {"cid": course_id, "name": name},
                )
                if dup:
                    raise ValueError(f"知识点「{name}」已存在")
            props["name"] = name
        if category is not None:
            if category not in CATEGORY_TYPE_MAP:
                raise ValueError(f"非法类别: {category}")
            props["category"] = category
        if description is not None:
            props["description"] = description

        props["is_manual"] = True
        props["updated_at"] = _now_iso()

        db.query(
            "MATCH (n:KnowledgePoint {kp_id: $kp_id, course_id: $cid}) "
            "SET n += $props RETURN n.name AS name, n.category AS category",
            {"kp_id": kp_id, "cid": course_id, "props": props},
        )
        sql_db.delete_embeddings_by_course(int(course_id))
        return {"kp_id": kp_id, "updated": True, **props}

    @staticmethod
    def delete_node(course_id: str, kp_id: str) -> dict:
        """按 kp_id 删除节点及其所有关系"""
        recs = db.query(
            "MATCH (n:KnowledgePoint {kp_id: $kp_id, course_id: $cid}) "
            "DETACH DELETE n RETURN count(n) AS cnt",
            {"kp_id": kp_id, "cid": course_id},
        )
        cnt = recs[0]["cnt"] if recs else 0
        if cnt == 0:
            raise ValueError(f"知识点不存在: {kp_id}")
        sql_db.delete_embeddings_by_course(int(course_id))
        return {"kp_id": kp_id, "deleted": True}

    @staticmethod
    def create_relationship(course_id: str, source: str, target: str,
                            rel_type: str = "RELATED_TO") -> dict:
        """按 kp_id 手动新增关系（source/target 为 kp_id），返回 {edge_id, type, ...}"""
        if rel_type not in VALID_RELATION_TYPES:
            raise ValueError(f"非法关系类型: {rel_type}（可选 {sorted(VALID_RELATION_TYPES)}）")
        if source == target:
            raise ValueError("源和目标知识点不能相同")
        label = RELATION_TYPE_LABELS[rel_type]
        # 关系类型经白名单校验后拼接；Neo4j 不支持参数化关系类型，禁止写 [r:$rel_type]
        cypher = f"""
        MATCH (a:KnowledgePoint {{kp_id: $source, course_id: $cid}})
        MATCH (b:KnowledgePoint {{kp_id: $target, course_id: $cid}})
        MERGE (a)-[r:{rel_type}]->(b)
        ON CREATE SET r.created_at = $now
        SET r.relation_type = $label, r.course_id = $cid, r.updated_at = $now, r.is_manual = true
        RETURN elementId(r) AS edge_id, type(r) AS type, a.name AS source_name, b.name AS target_name
        """
        recs = db.query(cypher, {
            "source": source, "target": target, "cid": course_id,
            "label": label, "now": _now_iso(),
        })
        if not recs:
            raise ValueError("源或目标知识点不存在")
        r = recs[0]
        return {
            "edge_id": r["edge_id"], "type": r["type"],
            "source": source, "target": target,
            "source_name": r["source_name"], "target_name": r["target_name"],
        }

    @staticmethod
    def delete_relationship(course_id: str, edge_id: str) -> dict:
        """按 edge_id（elementId）删除关系"""
        recs = db.query(
            "MATCH (:KnowledgePoint {course_id: $cid})-[r]->(:KnowledgePoint {course_id: $cid}) "
            "WHERE elementId(r) = $edge_id DELETE r RETURN count(r) AS cnt",
            {"cid": course_id, "edge_id": edge_id},
        )
        cnt = recs[0]["cnt"] if recs else 0
        if cnt == 0:
            raise ValueError("关系不存在或已删除")
        return {"edge_id": edge_id, "deleted": True}
