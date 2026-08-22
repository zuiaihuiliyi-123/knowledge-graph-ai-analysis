"""
LLM知识提取服务：实体识别 + 关系提取
"""
import json
import re
import asyncio
from typing import List, Tuple
from openai import OpenAI
from ..core.config import settings
from ..utils.text_processor import chunk_text_for_llm


# 知识提取的 Prompt 模板
EXTRACTION_PROMPT = """你是一个教育领域的知识图谱构建专家。请从以下课程文本中提取知识点实体和它们之间的关系。

## 实体要求
每个实体包含：
- name：知识点名称（简洁规范，去除冗余修饰）
- category：类别，只能是「概念」「定理」「公式」「方法」之一（无法归类时用「概念」）
- description：一句话描述

## 关系要求
关系只能使用以下 4 种类型，必须使用英文大写标识符，且方向严格遵循约定：
1. PRECEDES（前置知识）：source 是 target 的前置知识，即学习 target 之前需先掌握 source。
   例：线性代数 PRECEDES 机器学习
2. CONTAINS（包含）：source 包含 target，即 source 是上层概念，target 是其子概念或组成部分。
   例：神经网络 CONTAINS 卷积神经网络
3. RELATED_TO（相关概念）：source 与 target 密切相关。
   例：过拟合 RELATED_TO 正则化
4. APPLIES_TO（应用）：source 应用到 target。
   例：梯度下降 APPLIES_TO 损失函数优化

## 约束
- 不要输出 source 与 target 相同的自环关系
- 不要输出重复关系（相同 source、type、target 只保留一条）
- 关系的 source 和 target 必须出现在实体列表中

## 输出格式（严格JSON，只输出JSON）
{
  "entities": [
    {"name": "实体名", "category": "概念", "description": "简要描述"}
  ],
  "relations": [
    {"source": "源实体名", "target": "目标实体名", "type": "PRECEDES"}
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
                    {"role": "user", "content": EXTRACTION_PROMPT.replace("{text}", text)}
                ],
                temperature=0.1,  # 低温度保证稳定
                max_tokens=4096,
                timeout=settings.EXTRACTION_TIMEOUT
            )

            content = response.choices[0].message.content.strip()
            return self._parse_json(content)

        except json.JSONDecodeError as e:
            return {"entities": [], "relations": [], "error": f"JSON解析失败: {str(e)}"}
        except Exception as e:
            return {"entities": [], "relations": [], "error": f"提取失败: {str(e)}"}

    @staticmethod
    def _parse_json(content: str) -> dict:
        """稳健地解析 LLM 返回的 JSON：清理 markdown 代码块、剥离多余文本"""
        if not content:
            return {"entities": [], "relations": []}

        content = content.strip()

        # 清理 markdown 代码块标记 ```json ... ```
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        # 直接解析
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # 兜底：提取第一个 { 到最后一个 } 之间的内容再解析
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        raise ValueError("无法从 LLM 返回内容中解析出 JSON")

    def _merge_results(self, results: List[dict]) -> dict:
        """合并多个提取结果：实体按名称去重、关系按三元组去重并过滤自环"""
        seen_entities = set()
        merged_entities = []
        seen_relations = set()
        merged_relations = []

        for result in results:
            for entity in result.get("entities", []):
                name = entity.get("name", "").strip()
                if name and name not in seen_entities:
                    seen_entities.add(name)
                    merged_entities.append(entity)

            for relation in result.get("relations", []):
                source = relation.get("source", "").strip()
                target = relation.get("target", "").strip()
                rel_type = relation.get("type", "").strip()

                # 过滤空值
                if not source or not target or not rel_type:
                    continue
                # 过滤自环（source == target）
                if source == target:
                    continue
                # 按 (source, type, target) 去重
                key = (source, rel_type, target)
                if key in seen_relations:
                    continue
                seen_relations.add(key)
                merged_relations.append(relation)

        return {"entities": merged_entities, "relations": merged_relations}
