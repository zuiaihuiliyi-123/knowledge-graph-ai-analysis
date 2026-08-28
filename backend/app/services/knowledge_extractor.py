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


# 知识提取的 Prompt 模板（修改版）
EXTRACTION_PROMPT = """你是一个教育领域的知识图谱构建专家。请从以下课程文本中提取知识点实体和它们之间的关系。

## 实体要求
每个实体包含：
- name：知识点名称（简洁规范，去除冗余修饰）
- category：类别，只能是「概念」「定理」「公式」「方法」之一（无法归类时用「概念」）
- description：一句话描述

## 关系要求（严格遵循以下优先级规则）
关系只能使用以下 4 种类型，必须使用英文大写标识符，且方向严格遵循约定：

### 规则1：PRECEDES（前置知识）
- 定义：source 是 target 的前置知识，即学习 target 之前需先掌握 source。
- 方向：source → target
- **重要：只有当两个知识点之间存在明确的"必须先掌握 A 才能学习 B"的先后依赖关系时，才使用 PRECEDES。**
- **关键排除规则：以下情况严禁使用 PRECEDES，应使用 CONTAINS：**
  - "X 是 Y 的一种" → Y CONTAINS X
  - "X 是 Y 的特殊形式" → Y CONTAINS X
  - "X 是 Y 的变体" → Y CONTAINS X
- 典型触发词："基础"、"先修"、"前提"、"掌握…之后"、"进一步学习"、"入门"、"进阶"

### 规则2：CONTAINS（包含关系）
- 定义：source 包含 target，即 source 是上层概念，target 是其子概念或组成部分。
- 方向：source → target
- 示例：神经网络 CONTAINS 卷积神经网络

### 规则3：RELATED_TO（相关概念）
- 定义：source 与 target 密切相关，但**不存在先后学习顺序**。
- 方向：无方向
- **只有确认不存在先后顺序时，才使用 RELATED_TO**

### 规则4：APPLIES_TO（应用关系）
- 定义：source 应用到 target。
- 方向：source → target
- 示例：梯度下降 APPLIES_TO 损失函数优化

## PRECEDES 示例（特别注意，优先产出此类关系）
以下示例帮助你识别什么情况下必须输出 PRECEDES：

示例1：
  文本："要学习面向对象编程，必须先理解类和对象的概念。"
  输出：{"source": "类和对象", "target": "面向对象编程", "type": "PRECEDES"}

示例2：
  文本："学习递归之前，需先掌握函数调用的概念。"
  输出：{"source": "函数调用", "target": "递归", "type": "PRECEDES"}

示例3：
  文本："学习机器学习之前，建议先掌握线性代数和概率论。"
  输出：
    {"source": "线性代数", "target": "机器学习", "type": "PRECEDES"},
    {"source": "概率论", "target": "机器学习", "type": "PRECEDES"}

示例4（关键区分）：
  文本："卷积神经网络是神经网络的一种，常用于图像识别任务。"
  输出：
    {"source": "神经网络", "target": "卷积神经网络", "type": "CONTAINS"},
    {"source": "卷积神经网络", "target": "图像识别", "type": "APPLIES_TO"}
  （注意：这里"神经网络→卷积神经网络"是包含关系，没有先后顺序，所以用 CONTAINS 而非 PRECEDES）

示例5（关键区分：CONTAINS vs PRECEDES，必须理解）：
  文本："栈是一种操作受限的线性表。"
  正确输出：{"source": "线性表", "target": "栈", "type": "CONTAINS"}
  错误输出：{"source": "线性表", "target": "栈", "type": "PRECEDES"}  ← 这是错的！"是一种"表示包含，不是先后学习顺序。

  文本："要学习多态，必须先掌握继承。"
  正确输出：{"source": "继承", "target": "多态", "type": "PRECEDES"}

## 约束
- 不要输出 source 与 target 相同的自环关系
- 不要输出重复关系（相同 source、type、target 只保留一条）
- 关系的 source 和 target 必须出现在实体列表中
- 同一段文本中，PRECEDES 占比不应为 0。如果存在任何先后顺序表述，必须产出 PRECEDES。

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


# 单文档多分块并发抽取的最大并发数（控制同时发往 LLM 的请求量，避免触发限流）
_MAX_CONCURRENCY = 4


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
            return await asyncio.to_thread(self._extract_single, chunks[0])

        # 并发抽取：同步 LLM 客户端放在线程池中并行执行，避免逐块串行阻塞事件循环
        sem = asyncio.Semaphore(_MAX_CONCURRENCY)

        async def _run_one(chunk: str) -> dict:
            async with sem:
                return await asyncio.to_thread(self._extract_single, chunk)

        results = await asyncio.gather(*[_run_one(c) for c in chunks])

        # 并发下偶发限流/超时会导致个别分块失败：对失败分块串行重试一次，提升成功率
        retried = []
        for i, r in enumerate(results):
            if r.get("error"):
                retried.append(await asyncio.to_thread(self._extract_single, chunks[i]))
            else:
                retried.append(r)

        return self._merge_results(retried)

    def _extract_single(self, text: str) -> dict:
        """对单个文本块执行提取（同步；由 extract 通过线程池并发调用）"""
        try:
            response = self.client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": "你是一个精确的知识图谱构建助手。请只输出JSON格式的结果。"},
                    {"role": "user", "content": EXTRACTION_PROMPT.replace("{text}", text)}
                ],
                temperature=0.15,  # 关系类型判别需稳定，0.15 在稳定性与判断力之间取平衡
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
        """合并多个提取结果：实体按名称去重、关系按三元组去重并过滤自环；透传分块错误"""
        seen_entities = set()
        merged_entities = []
        seen_relations = set()
        merged_relations = []
        errors = []

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

            if result.get("error"):
                errors.append(result["error"])

        merged = {"entities": merged_entities, "relations": merged_relations}
        # 分块错误透传（最多携带前 3 条，避免信息过长），供上层判断抽取是否真正成功
        if errors:
            merged["error"] = "；".join(errors[:3])
        return merged

    print(f"[DEBUG] LLM_API_KEY = '{settings.LLM_API_KEY}'")
    print(f"[DEBUG] LLM_API_BASE = '{settings.LLM_API_BASE}'")