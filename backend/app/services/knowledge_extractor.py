"""
LLM知识提取服务：实体识别 + 关系提取
"""
import json
import re
import asyncio
from typing import List, Tuple
from openai import OpenAI
from ..core.config import settings
from ..core.database import VALID_RELATION_TYPES
from ..utils.text_processor import chunk_text_for_llm


# 知识提取的 Prompt 模板（决策树式关系判定，强化学习依赖与文本顺序的区分）
EXTRACTION_PROMPT = """你是高校课程知识图谱构建专家。请从给定课程文本中抽取「可用于知识图谱与学习路径推荐」的知识点实体，并识别实体之间明确存在的语义关系。

## 核心原则
宁可少抽取，也不要臆造关系。禁止仅凭文本先后顺序、常识或名称相似性推断关系。
若对某条关系证据不足，直接不输出该关系。

## 一、实体要求
每个实体包含：
- name：知识点名称，简洁、规范、稳定
- category：只能是「概念」「定理」「公式」「方法」之一（无法归类时用「概念」）
- description：一句话描述该知识点在课程中的含义

实体粒度：
- 一个实体应是一个「独立可学习的知识点」，不要过度拆分，也不要堆砌冗余修饰。
- 同义词、英文缩写与中文全称属于同一知识点时，只保留一个规范名称。例如 CNN 与「卷积神经网络」合并为「卷积神经网络」。
- 不要抽取：人名、学校、公司、页码、章节标题本身、「本节」「上述方法」等指代词、普通名词、单纯例子名。

## 二、关系类型（仅限 4 种，英文大写，方向严格）
PRECEDES / CONTAINS / RELATED_TO / APPLIES_TO

## 三、关系判定必须按下述顺序（决策树）
对任意两个知识点，按顺序判断：

1. 上下位 / 整体-部分 / 类别-成员？ → CONTAINS（source 是 target 的上位概念/整体/类别，方向 source→target）
2. 否则，文本明确表达「学习 source 是理解 target 的必要前提」？ → PRECEDES（方向 source→target）
3. 否则，source 是方法/算法/技术，且文本明确说明其用于解决 target 代表的问题/对象？ → APPLIES_TO（方向 source→target，判定边界见第六节）
4. 否则，文本明确说明二者存在密切关联？ → RELATED_TO
5. 以上均不满足或证据不足 → 不输出关系

## 四、PRECEDES 只表示「学习依赖」，不表示「文本顺序」
只有文本明确表达以下语义才用 PRECEDES：学习 B 前须掌握 A；A 是理解 B 的基础；B 建立在 A 之上；掌握 A 后才能学 B。
典型触发词：「基础」「先修」「前提」「掌握…之后」「进一步学习」。

反例（以下禁止推断为 PRECEDES）：
- 「本章首先介绍线性表，然后介绍栈」→ 这是叙述顺序，不是学习依赖，不得输出「线性表 PRECEDES 栈」。
- 「卷积神经网络是神经网络的一种」→ 是包含关系，必须用「神经网络 CONTAINS 卷积神经网络」，不得用 PRECEDES。
- 「算法运行后生成结果」→ 时间先后，不是 PRECEDES。

## 五、CONTAINS 与 PRECEDES 的关键区分
- 「X 是 Y 的一种 / 特殊形式 / 变体 / 属于 Y」「Y 包含 X」→ Y CONTAINS X（绝不 PRECEDES）
  例：「栈是一种操作受限的线性表」→ 线性表 CONTAINS 栈
- 「要学习 X，必须先掌握 Y」→ Y PRECEDES X
  例：「要学习多态，必须先掌握继承」→ 继承 PRECEDES 多态

## 六、APPLIES_TO 与「实现 / 构成」关系的区分
只有当 source 被用于解决、处理、计算、优化或完成 target 所代表的问题/任务时，才能使用 APPLIES_TO。

以下表达不得自动视为 APPLIES_TO：
- 「X 通过 Y 实现」「X 由 Y 实现」「X 使用 Y 构成」「X 建立在 Y 之上」「X 包含 Y」「X 可以利用 Y 实现」

若文本表达的是「Y 是 X 的实现机制、组成部分或实现方式」，而当前关系集合中没有专门的 IMPLEMENTED_BY 关系：
- 不要强行转换成 APPLIES_TO；
- 根据上下文判断是否为 CONTAINS；
- 若无法确定，则不输出关系。

不要为了覆盖所有语义而强行映射到现有四类关系；证据不足时宁可不输出。

## 七、RELATED_TO 严格限制
仅用于「明确密切相关、但既非上下位、也非学习前置、也非应用」的情况，且文本有明确关联表述。
不要仅因两个概念出现在同一段就输出 RELATED_TO。

## 八、每条关系必须携带证据与置信度
- evidence：支持该关系的原文短句（必须来自输入文本，不得编造）
- confidence：0~1 的置信度，如实反映证据强弱，不确定就降低

## 九、输出约束
- source 与 target 必须都出现在 entities 中，且 source ≠ target
- 不重复输出相同 (source, type, target)；文本中因分块出现的重复段落，同一知识点/关系只输出一次
- type 只能是 PRECEDES / CONTAINS / RELATED_TO / APPLIES_TO
- 严格只输出 JSON，不要 Markdown 代码块，不要解释文字

## 十、输出格式
{
  "entities": [
    {"name": "实体名", "category": "概念", "description": "一句话描述"}
  ],
  "relations": [
    {"source": "源实体名", "target": "目标实体名", "type": "PRECEDES", "evidence": "原文证据", "confidence": 0.95}
  ]
}

## 课程文本
{text}

请只输出 JSON，不要包含任何其他内容。"""


# 单文档多分块并发抽取的最大并发数（控制同时发往 LLM 的请求量，避免触发限流）
_MAX_CONCURRENCY = 4

# 分块与输出参数（正式评测需冻结，由 eval_config.make_config 记录到 experiment_config.json）
_CHUNK_MAX_TOKENS = 6000
_OVERLAP_TOKENS = 400
_MAX_OUTPUT_TOKENS = 4096

# 各关系类型的最低置信度阈值：LLM 明确给出且低于阈值时丢弃。
# PRECEDES 是学习路径的基础、RELATED_TO 最易泛化，二者阈值相对高（偏向高精度）。
_RELATION_CONFIDENCE_THRESHOLDS = {
    "PRECEDES": 0.6,
    "CONTAINS": 0.5,
    "APPLIES_TO": 0.5,
    "RELATED_TO": 0.6,
}

# LLM 未提供 confidence 时的默认值。
# 语义澄清：这不是"系统认为该关系有 80% 准确率"，而只是缺省值填充（保证边有 confidence 字段入图），
# 不参与 Precision / Recall / F1 的准确率计算。真实准确率仅由 Gold 标注 + eval_accuracy.py 计算。
_DEFAULT_RELATION_CONFIDENCE = 0.8

# 常见缩写/别名 -> 规范名 映射（课程相关，按需扩充）。
# 示例：{"cnn": "卷积神经网络", "bp网络": "反向传播神经网络"}
# 注意：不同课程语境下缩写含义可能不同，默认留空，避免误伤。
_SYNONYM_MAP = {}


def _fullwidth_to_halfwidth(text: str) -> str:
    """全角字符转半角（空格/字母/数字/常见 ASCII 符号），例如 ＣＮＮ -> CNN"""
    out = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:  # 全角空格
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:  # 全角 ASCII 区
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def _normalize_entity_name(name: str) -> str:
    """轻量实体名规范化：全角转半角、折叠空白、去首尾；纯英文缩写统一大写"""
    if not name:
        return ""
    name = _fullwidth_to_halfwidth(name)
    name = re.sub(r"\s+", " ", name).strip()
    # 纯英文（不含中文）且无空格：统一为大写，合并 CNN/cnn/ＣＮＮ 等写法
    if name and not re.search(r"[一-鿿]", name) and " " not in name:
        name = name.upper()
    # 常见缩写映射（默认空表，可按课程扩充）
    key = name.lower()
    if key in _SYNONYM_MAP:
        return _SYNONYM_MAP[key]
    return name


def _parse_confidence(value) -> float:
    """把 LLM 返回的 confidence 解析为 float；非法/越界/缺失返回 None（由调用方决定默认值）"""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v < 0 or v > 1:
        return None
    return v


class KnowledgeExtractor:
    """基于LLM的知识提取器"""

    def __init__(self, temperature: float = 0.15):
        self.client = OpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_API_BASE
        )
        # 开发期用 0.15 平衡稳定性与判断力；正式评测（A/B、Gold）传 0 消除采样随机性
        self.temperature = temperature

    async def extract(self, text: str, overlap_tokens: int = _OVERLAP_TOKENS) -> dict:
        """
        从文本中提取知识实体和关系
        返回 {"entities": [...], "relations": [...]}
        overlap_tokens：相邻分块的重叠 token 数，用于解决跨块指代（评测 A/B 时可传 0 对照）
        """
        # 分割长文本（分块上限 _CHUNK_MAX_TOKENS tokens，overlap 解决跨块指代）
        chunks = chunk_text_for_llm(text, max_tokens=_CHUNK_MAX_TOKENS, overlap_tokens=overlap_tokens)

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
                temperature=self.temperature,
                max_tokens=_MAX_OUTPUT_TOKENS,
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
        """
        合并多个提取结果（数据完整性约束不依赖 LLM，全部在此兜底）：
        - 实体：轻量规范化后按规范名去重
        - 关系：类型白名单、source/target 实体存在、自环、重复、confidence 阈值过滤
        """
        # 第一遍：规范化实体并按规范名去重（保留首个出现的字段）
        merged_entities = {}  # 规范名 -> entity dict
        for result in results:
            for entity in result.get("entities", []):
                name = _normalize_entity_name(entity.get("name", ""))
                if not name:
                    continue
                if name not in merged_entities:
                    category = entity.get("category", "概念")
                    if category not in ("概念", "定理", "公式", "方法"):
                        category = "概念"
                    merged_entities[name] = {
                        "name": name,
                        "category": category,
                        "description": (entity.get("description") or "").strip(),
                    }
        entity_names = set(merged_entities.keys())

        # 第二遍：关系校验（source/target 使用同一套规范化，保证与实体名对齐）
        seen_relations = set()
        merged_relations = []
        errors = []
        for result in results:
            for relation in result.get("relations", []):
                source = _normalize_entity_name(relation.get("source", ""))
                target = _normalize_entity_name(relation.get("target", ""))
                rel_type = (relation.get("type", "") or "").strip().upper()

                # 关系类型白名单（不依赖 LLM）
                if rel_type not in VALID_RELATION_TYPES:
                    continue
                # 过滤空值 / 自环
                if not source or not target or source == target:
                    continue
                # source/target 必须存在于实体列表（避免孤立关系 / 悬空边）
                if source not in entity_names or target not in entity_names:
                    continue
                # 按 (source, type, target) 去重
                key = (source, rel_type, target)
                if key in seen_relations:
                    continue
                seen_relations.add(key)

                # confidence 解析 + 阈值过滤
                confidence = _parse_confidence(relation.get("confidence"))
                threshold = _RELATION_CONFIDENCE_THRESHOLDS.get(rel_type, 0.5)
                if confidence is not None and confidence < threshold:
                    continue

                merged_relations.append({
                    "source": source,
                    "target": target,
                    "type": rel_type,
                    "evidence": (relation.get("evidence") or "").strip(),
                    "confidence": confidence if confidence is not None else _DEFAULT_RELATION_CONFIDENCE,
                })

            if result.get("error"):
                errors.append(result["error"])

        merged = {"entities": list(merged_entities.values()), "relations": merged_relations}
        # 分块错误透传（最多携带前 3 条，避免信息过长），供上层判断抽取是否真正成功
        if errors:
            merged["error"] = "；".join(errors[:3])
        return merged
