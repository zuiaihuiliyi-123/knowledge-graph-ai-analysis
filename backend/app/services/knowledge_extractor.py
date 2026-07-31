"""
LLM知识提取服务：实体识别 + 关系提取
"""
import json
import asyncio
from typing import List, Tuple
from openai import OpenAI
from ..core.config import settings
from ..utils.text_processor import chunk_text_for_llm


# 知识提取的 Prompt 模板
EXTRACTION_PROMPT = """你是一个教育领域的知识图谱构建专家。请从以下课程文本中提取知识点和它们之间的关系。

## 要求
1. 提取所有重要的知识点实体（概念、定理、公式、方法等）
2. 识别实体之间的关系类型，包括但不限于：
   - "前置知识" (prerequisite)：学习B之前需要先掌握A
   - "相关概念" (related_to)：两个概念密切相关
   - "包含" (contains)：知识点包含子知识点
   - "应用" (applies_to)：某个知识点的实际应用
3. 每个实体需要包含 name（名称）、category（类别：概念/定理/公式/方法/其他）、description（一句话描述）
4. 每个关系需要包含 source（源节点名）、target（目标节点名）、type（关系类型）

## 输出格式（严格JSON）
{
  "entities": [
    {"name": "实体名", "category": "概念", "description": "简要描述"}
  ],
  "relations": [
    {"source": "源实体名", "target": "目标实体名", "type": "前置知识"}
  ]
}

## 课程文本
{text}

请只输出JSON，不要包含任何其他内容。"""


class KnowledgeExtractor:
    """基于LLM的知识提取器"""

    def __init__(self):
        self.client = OpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_API_BASE
        )

    async def extract(self, text: str) -> dict:
        """
        从文本中提取知识实体和关系
        返回 {"entities": [...], "relations": [...]}
        """
        # 分割长文本
        chunks = chunk_text_for_llm(text, max_tokens=3000)

        if len(chunks) == 1:
            return await self._extract_single(chunks[0])
        else:
            # 分批提取后合并去重
            all_results = []
            for chunk in chunks:
                result = await self._extract_single(chunk)
                all_results.append(result)

            return self._merge_results(all_results)

    async def _extract_single(self, text: str) -> dict:
        """对单个文本块执行提取"""
        try:
            response = self.client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": "你是一个精确的知识图谱构建助手。请只输出JSON格式的结果。"},
                    {"role": "user", "content": EXTRACTION_PROMPT.format(text=text)}
                ],
                temperature=0.1,  # 低温度保证稳定
                max_tokens=4096,
                timeout=settings.EXTRACTION_TIMEOUT
            )

            content = response.choices[0].message.content.strip()

            # 清理可能的 markdown 代码块标记
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            content = content.strip()

            return json.loads(content)

        except json.JSONDecodeError as e:
            return {"entities": [], "relations": [], "error": f"JSON解析失败: {str(e)}"}
        except Exception as e:
            return {"entities": [], "relations": [], "error": f"提取失败: {str(e)}"}

    def _merge_results(self, results: List[dict]) -> dict:
        """合并多个提取结果，按名称去重"""
        seen_entities = set()
        merged_entities = []
        merged_relations = []

        for result in results:
            for entity in result.get("entities", []):
                name = entity.get("name", "")
                if name and name not in seen_entities:
                    seen_entities.add(name)
                    merged_entities.append(entity)

            for relation in result.get("relations", []):
                merged_relations.append(relation)

        return {"entities": merged_entities, "relations": merged_relations}
