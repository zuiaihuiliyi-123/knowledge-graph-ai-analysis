"""
智能问答服务：基于知识图谱 + RAG
"""
from typing import List
from openai import OpenAI
from ..core.config import settings
from ..core.database import db


QA_SYSTEM_PROMPT = """你是一个课程学习助手。请基于提供的课程知识图谱内容回答学生的问题。

## 规则
1. 优先使用提供的知识内容回答问题
2. 如果知识库中没有相关信息，请诚实告知，不要编造
3. 回答要准确、简洁、易于理解
4. 如果合适，可以推荐相关的知识点供进一步学习

## 课程知识内容
{context}

请根据以上内容回答学生的问题。"""


def _coerce_course_id(course_id):
    """course_id 统一为整数（对齐 Neo4j 存储类型）；空值返回 None 表示不过滤"""
    if course_id is None or course_id == "":
        return None
    try:
        return int(course_id)
    except (TypeError, ValueError):
        return None


class QAService:
    """智能问答服务"""

    def __init__(self):
        self.client = OpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_API_BASE
        )

    def search_related_knowledge(self, question: str, course_id=None, top_k: int = 5) -> List[str]:
        """
        从知识图谱中检索与问题相关的知识点（简化版 RAG 检索，第 2 阶段替换为向量检索）
        """
        cid = _coerce_course_id(course_id)
        keyword = (question or "")[:20]

        if cid is not None:
            cypher = """
            MATCH (n:KnowledgePoint {course_id: $course_id})
            WHERE n.name CONTAINS $keyword OR n.description CONTAINS $keyword
            RETURN n
            LIMIT $top_k
            """
            params = {"course_id": cid, "keyword": keyword, "top_k": top_k}
        else:
            cypher = """
            MATCH (n:KnowledgePoint)
            WHERE n.name CONTAINS $keyword OR n.description CONTAINS $keyword
            RETURN n
            LIMIT $top_k
            """
            params = {"keyword": keyword, "top_k": top_k}

        records = db.query(cypher, params)

        contexts = []
        for record in records:
            node = record.get("n")
            if node:
                ctx = f"[{node.get('category', '知识点')}] {node.get('name', '')}"
                if node.get('description'):
                    ctx += f": {node.get('description')}"
                contexts.append(ctx)

        # 如果精确匹配结果少，扩展搜索同课程下的其它节点兜底
        if len(contexts) < top_k:
            existing_names = {c.split('] ', 1)[-1].split(':')[0] for c in contexts}
            if cid is not None:
                records = db.query(
                    "MATCH (n:KnowledgePoint {course_id: $course_id}) RETURN n LIMIT $top_k",
                    {"course_id": cid, "top_k": top_k},
                )
            else:
                records = db.query(
                    "MATCH (n:KnowledgePoint) RETURN n LIMIT $top_k",
                    {"top_k": top_k},
                )
            for record in records:
                node = record.get("n")
                if not node:
                    continue
                name = node.get("name")
                if name in existing_names:
                    continue
                ctx = f"[{node.get('category', '知识点')}] {name}"
                if node.get('description'):
                    ctx += f": {node.get('description')}"
                contexts.append(ctx)
                if len(contexts) >= top_k:
                    break

        return contexts

    async def ask(self, question: str, course_id=None) -> str:
        """
        回答问题（RAG模式）
        """
        # 1. 检索相关知识
        contexts = self.search_related_knowledge(question, course_id)
        context_text = "\n".join(contexts) if contexts else "暂无相关课程知识"

        # 2. 调用LLM生成回答
        try:
            response = self.client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": QA_SYSTEM_PROMPT.format(context=context_text)},
                    {"role": "user", "content": question}
                ],
                temperature=0.3,
                max_tokens=1024,
                timeout=settings.QA_TIMEOUT
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"抱歉，问答服务暂时不可用：{str(e)}"
