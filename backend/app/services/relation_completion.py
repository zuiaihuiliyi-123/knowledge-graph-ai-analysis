"""
关系补全服务：基于 LLM 对课程知识图谱补全缺失的 PRECEDES（前置知识）边。

与「抽取」分离：不重新抽取实体，而是读取课程现有知识点清单，让 LLM 依据教学逻辑
补全 PRECEDES 关系。补全的边带 is_inferred=True 标记，可识别、可回滚。

用法（backend 目录下）：
    python -m app.services.relation_completion 5 4               # 补全 course_id=5 的 document_id=4
    python -m app.services.relation_completion 5 4 --rollback    # 回滚该文档的补全边
"""
import asyncio
import json
import re

from openai import OpenAI

from ..core.config import settings
from ..core.database import db

# 补全 Prompt：只补 PRECEDES，严格排除「包含/相关/应用」；宁缺毋滥
COMPLETION_PROMPT = """你是一名教育领域知识图谱专家。下面是一个课程的知识点清单（名称 + 类别 + 一句话描述）：

{node_list}

请根据教学逻辑，补全这些知识点之间**缺失的「前置知识（PRECEDES）」关系**。

## 严格规则
- PRECEDES 方向为 source → target，表示「必须先掌握 source，才能学习 target」。
- **只有当两个知识点之间存在明确的先后学习依赖时，才输出 PRECEDES。**
- 以下情况**严禁**输出（这些不是前置关系）：
  - "X 是 Y 的一种 / 特殊形式 / 变体 / 组成部分"（这是包含关系 CONTAINS）
  - 两个知识点只是相关、并列、同层（这是相关关系 RELATED_TO，无先后顺序）
  - "X 用于 / 应用于 Y"（这是应用关系 APPLIES_TO）
  - 你不确定、或先后依赖不明显的
- 宁缺毋滥：只输出你**高度确信**的 PRECEDES 关系。
- source 与 target 必须是清单中**已有的知识点名称**，且不能相同。

## 输出格式（严格 JSON，只输出 JSON，不要输出任何解释）
{{
  "relations": [
    {{"source": "继承", "target": "多态"}}
  ]
}}

如果没有需要补充的关系，输出 {{"relations": []}}。"""


def _coerce_course_id(course_id) -> int:
    """course_id 统一为整数（对齐 Neo4j 存储类型）"""
    if course_id is None or course_id == "":
        raise ValueError("course_id 不能为空")
    return int(course_id)


def _coerce_document_id(document_id) -> int:
    """document_id 统一为整数（对齐 Neo4j 存储类型）"""
    if document_id is None or document_id == "":
        raise ValueError("document_id 不能为空")
    return int(document_id)


class RelationCompleter:
    """课程图谱 PRECEDES 关系补全器"""

    def __init__(self):
        self.client = OpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_API_BASE,
        )

    # ---------- 主流程 ----------
    async def complete(self, course_id, document_id) -> dict:
        """读取文档节点 → LLM 批量补全 → 校验 → 写入（is_inferred=True）"""
        cid = _coerce_course_id(course_id)
        did = _coerce_document_id(document_id)

        # 1. 读取节点清单
        nodes = self._load_nodes(cid, did)
        if not nodes:
            return {"course_id": cid, "document_id": did, "ok": False, "message": "该文档下没有知识点"}
        node_names = {n["name"] for n in nodes}

        # 2. 现有边（用于去重 / 不覆盖）
        existing_pairs = self._load_existing_pairs(cid, did)

        # 3. LLM 批量补全
        node_list = "\n".join(
            f"- {n['name']}（{n['category']}）：{n['description'] or '无描述'}"
            for n in nodes
        )
        prompt = COMPLETION_PROMPT.format(node_list=node_list)
        content = await asyncio.to_thread(self._call_llm, prompt)
        relations = self._parse_relations(content)

        # 4. 校验 + 写入
        added, skipped = self._apply(cid, did, relations, node_names, existing_pairs)

        return {
            "course_id": cid,
            "document_id": did,
            "ok": True,
            "node_count": len(nodes),
            "existing_edge_count": len(existing_pairs),
            "llm_relation_count": len(relations),
            "added_count": len(added),
            "added": added,
            "skipped": skipped,
        }

    def rollback(self, course_id, document_id) -> dict:
        """回滚：删除该文档所有 is_inferred=True 的 PRECEDES 边"""
        cid = _coerce_course_id(course_id)
        did = _coerce_document_id(document_id)
        recs = db.query(
            "MATCH (:KnowledgePoint {course_id: $cid, document_id: $did})-[r:PRECEDES]->"
            "(:KnowledgePoint {course_id: $cid, document_id: $did}) "
            "WHERE r.is_inferred = true DELETE r RETURN count(r) AS cnt",
            {"cid": cid, "did": did},
        )
        cnt = recs[0]["cnt"] if recs else 0
        return {"course_id": cid, "document_id": did, "deleted_count": cnt}

    # ---------- 数据读取 ----------
    def _load_nodes(self, cid, did):
        """读取文档全部知识点（name/category/description），按名称排序"""
        recs = db.query(
            "MATCH (n:KnowledgePoint {course_id: $cid, document_id: $did}) "
            "RETURN n.name AS name, n.category AS category, n.description AS description "
            "ORDER BY n.name",
            {"cid": cid, "did": did},
        )
        return [
            {
                "name": r.get("name"),
                "category": r.get("category") or "概念",
                "description": (r.get("description") or "").strip(),
            }
            for r in recs
            if r.get("name")
        ]

    def _load_existing_pairs(self, cid, did):
        """读取文档全部现有边（任意类型），返回 (source, target) 集合"""
        recs = db.query(
            "MATCH (a:KnowledgePoint {course_id: $cid, document_id: $did})-[r]->"
            "(b:KnowledgePoint {course_id: $cid, document_id: $did}) "
            "RETURN a.name AS s, b.name AS t",
            {"cid": cid, "did": did},
        )
        return {(r.get("s"), r.get("t")) for r in recs}

    # ---------- LLM 调用与解析 ----------
    def _call_llm(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": "你是一名严谨的知识图谱构建助手，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.15,
            max_tokens=4096,
            timeout=settings.EXTRACTION_TIMEOUT,
        )
        return response.choices[0].message.content.strip()

    @staticmethod
    def _parse_relations(content: str) -> list:
        """稳健解析 LLM 返回：清理 markdown 代码块、剥离多余文本，取 relations 列表"""
        if not content:
            return []
        content = content.strip()

        # 清理 markdown 代码块标记 ```json ... ```
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # 兜底：提取第一个 { 到最后一个 } 之间再解析
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if not match:
                return []
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return []

        if not isinstance(data, dict):
            return []
        return data.get("relations", [])

    # ---------- 校验与写入 ----------
    def _apply(self, cid, did, relations, node_names, existing_pairs):
        """
        校验候选 PRECEDES 关系并写入。
        规则：两端点存在、无自环、批内去重、不覆盖已有边（任意方向任意类型）。
        返回 (added, skipped)。
        """
        added = []
        skipped = []
        seen = set()
        for rel in relations:
            if not isinstance(rel, dict):
                continue
            source = (rel.get("source") or "").strip()
            target = (rel.get("target") or "").strip()
            if not source or not target:
                continue
            if source == target:
                skipped.append({"source": source, "target": target, "reason": "自环"})
                continue
            if source not in node_names or target not in node_names:
                skipped.append({"source": source, "target": target, "reason": "端点不存在"})
                continue

            key = (source, target)
            if key in seen:
                continue  # 批内去重（静默）
            seen.add(key)

            # 不覆盖已有边：任意方向、任意类型已存在则跳过
            if key in existing_pairs or (target, source) in existing_pairs:
                skipped.append({"source": source, "target": target, "reason": "已有边"})
                continue

            db.create_relationship(
                cid, did, source, target, "PRECEDES",
                properties={"confidence": 0.85, "is_manual": False, "is_inferred": True},
            )
            added.append({"source": source, "target": target})

        return added, skipped


if __name__ == "__main__":
    import argparse
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="文档图谱 PRECEDES 关系补全")
    parser.add_argument("course_id", help="课程 ID（整数）")
    parser.add_argument("document_id", help="文档 ID（整数）")
    parser.add_argument("--rollback", action="store_true",
                        help="回滚：删除该文档所有 is_inferred=True 的 PRECEDES 边")
    args = parser.parse_args()

    completer = RelationCompleter()
    if args.rollback:
        result = completer.rollback(args.course_id, args.document_id)
    else:
        result = asyncio.run(completer.complete(args.course_id, args.document_id))
    print(json.dumps(result, ensure_ascii=False, indent=2))
