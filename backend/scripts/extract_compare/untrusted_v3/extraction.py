"""
Knowledge Extraction Service v3.0
Two-stage extraction: entities -> relations.
Compatible public entry point:
    result = await KnowledgeExtractor().extract(text, domain="computer_science")

Expected project dependencies (same as the supplied service):
- app.core.config.settings
- app.core.database.VALID_RELATION_TYPES
- app.utils.text_processor.chunk_text_for_llm
- app.services.extraction_cache

The final result keeps the legacy top-level keys: entities, relations, error,
raw_relation_count, dropped_relations, dropped_counts, chunk_count, etc.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from openai import OpenAI

from app.core.config import settings
from app.core.database import VALID_RELATION_TYPES
from app.utils.text_processor import chunk_text_for_llm
from app.services import extraction_cache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CHUNK_MAX_TOKENS = 6000
OVERLAP_TOKENS = 400
MAX_CONCURRENCY = 3
EXTRACTION_RETRY = 1

ENTITY_MAX_OUTPUT_TOKENS = 6000
RELATION_MAX_OUTPUT_TOKENS = 6000

ENTITY_TEMPERATURE = 0.0
RELATION_TEMPERATURE = 0.1

RELATION_CONFIDENCE_THRESHOLDS = {
    "PRECEDES": 0.60,
    "CONTAINS": 0.50,
    "APPLIES_TO": 0.50,
    "RELATED_TO": 0.60,
}
DEFAULT_RELATION_CONFIDENCE = 0.80
MAX_DROPPED_RECORDED = 500

ENTITY_CATEGORIES = {"概念", "定理", "公式", "方法"}
RELATION_TYPES = {"PRECEDES", "CONTAINS", "RELATED_TO", "APPLIES_TO"}

# Keep this empty until aliases have been manually verified against the course
# context. Do not automatically populate it from model-generated self-loops.
SYNONYM_MAP: Dict[str, str] = {}

CODE_KEYWORDS = {
    "PUBLIC", "PRIVATE", "PROTECTED", "CLASS", "VOID", "STATIC",
    "FINAL", "NEW", "RETURN", "INT", "DOUBLE", "FLOAT", "BOOLEAN",
    "STRING", "NULL", "TRUE", "FALSE", "THIS", "CONST",
}

# ---------------------------------------------------------------------------
# Prompt modules
# ---------------------------------------------------------------------------

BASE_ENTITY_PROMPT = r"""
你是高校课程知识图谱的实体抽取专家。你的任务是从输入课程原文中识别
可独立学习、可用于课程知识图谱的知识点实体。

【核心原则】
1. 只抽取原文实际表达或明确出现的知识点，不得根据学科常识补造原文没有的实体。
2. 兼顾召回率与准确率：定义中的上位类别词、明确讲解的基础对象也要抽取。
3. 不抽取人名、学校、公司、页码、普通名词、章节标题本身、指代词和纯例子名称。
4. 不抽取代码变量、函数名、类名、关键字、运算符及代码注释中的临时操作短语。
5. 同义词、缩写和中文全称确认为同一知识点时，使用原文中最规范且实际出现的名称；
   不得仅凭字符串相似就合并可能有歧义的术语。

【实体字段】
每个实体必须包含：
- name：简洁、稳定、在原文中有依据的知识点名称
- category：只能是「概念」「定理」「公式」「方法」
- description：一句话说明该知识点在当前课程语境中的含义
- evidence：支持该实体存在的原文短句，必须逐字来自输入文本

【类别判定】
- 数据结构、术语、性质、类别总称：概念
- 具体算法、遍历方式、操作步骤、表示方法、方法论：方法
- 有明确数学/科学命题性质的定理：定理
- 公式、方程、定量表达式：公式
- 无法确定时归为概念，不要为了细分而臆造类别。

【粒度】
- 一个实体应是一个独立可学习的知识点。
- 「遍历」「旋转」等类别性总称与「前序遍历」「右旋」等具体方法区分处理。
- 定义句中的上位类别词必须考虑抽取，例如「树是一种非线性数据结构」中的
  「非线性数据结构」。
- 不要使用原文没有出现的实体名。例如只出现「密立根」不能自行创造「油滴实验」。
- 不要将「初始化二叉树」「构建搜索树」等代码用途描述自动提升为知识点。
- 动作名称应包含对象，例如「插入节点」，不要只写「插入」。

【输出要求】
只输出 JSON：
{
  "entities": [
    {
      "name": "实体名",
      "category": "概念",
      "description": "一句话描述",
      "evidence": "原文中的实体证据"
    }
  ]
}

不得输出关系，不得输出 Markdown，不得输出解释。
"""

BASE_RELATION_PROMPT = r"""
你是高校课程知识图谱的关系判定专家。现在给你：
1. 一段课程原文；
2. Phase 1 从该文本中抽取的实体候选池。

你只负责识别原文明确支持的实体关系，不负责重新自由发现全部实体。

【总原则】
- 证据优先，宁缺毋滥。禁止仅凭常识、名称相似、共同出现或文本先后顺序推断关系。
- 每条关系必须能由 evidence 原文支持，evidence 必须是输入原文的连续片段。
- source、target 必须来自候选实体池；如果发现原文中确有重要实体但候选池遗漏，
  放入 unmatched_mentions，不得擅自将其作为关系端点。
- 不得为了达到关系数量或关系密度而补关系。
- 只使用以下四种关系：PRECEDES、CONTAINS、RELATED_TO、APPLIES_TO。

【关系决策顺序】
依次判断 CONTAINS、PRECEDES、APPLIES_TO、RELATED_TO；均不满足则不输出。

一、CONTAINS
表示分类/上下位关系，以及本项目既有标注规范明确允许的典型操作挂载。
方向固定：source 是上位概念，target 是下位概念或被允许挂载的典型操作。
方向检查：target 是 source 的一种/子类/典型操作。
- 「X 是 Y 的一种/子类/特殊形式」=> Y CONTAINS X
- 「Y 包括 X / Y 的类型有 X」=> Y CONTAINS X
- 只有明确对某结构执行的基本操作才可按既有口径挂载到该结构。
- 不把一般整体-部分、属性、维护机制、计算工具误判为 CONTAINS。
- 读成“target 是 source 的一种/子类”不成立时，不得输出该方向。

二、PRECEDES
只表示学习依赖：学习 target 前必须掌握 source。
- 必须有明确先修、基础、前提或掌握后才能学习的语义。
- 「先介绍 A，再介绍 B」只是文本顺序，不是 PRECEDES。
- 时间先后、操作执行顺序、复习引用、对理解有帮助均不自动构成 PRECEDES。
方向：前置知识 -> 后续知识。

三、APPLIES_TO
表示方法/算法/技术被用于实现、解决或完成某个对象代表的任务。
方向：方法/技术 -> 应用对象。
- 「X 基于/通过/借助 Y 实现」且 Y 是方法/算法/技术时，Y APPLIES_TO X。
- 若 Y 是数据结构等非方法概念，按既有口径考虑 RELATED_TO，不得机械套用 APPLIES_TO。
- 不把「表示工具」「存储方式」自动判成 APPLIES_TO。
- 不得将实现机制与被实现对象方向颠倒。

四、RELATED_TO
只表示原文明示的直接关联，不能作为无法判断时的兜底关系。
方向子规则：
- 工具/支撑方 -> 被支撑主体。
- 并列类似/对称关系：按照项目既有 Gold 口径决定方向；若 Gold 未规定且关系确为对称，
  source 取原文先出现的一方。
- 不能因同段出现、共享词根、间接关联、属性关系或名称派生而输出。
- 只能输出原文明确支持的直接关系，不允许通过第三个实体传递推断。

【方向依据】
每条关系必须填写 direction_basis：
{
  "source_role": "上位概念/前置知识/应用方法/工具支撑方/原文先出现方/其他",
  "target_role": "下位概念/后续知识/应用对象/被支撑主体/原文后出现方/其他",
  "semantic_check": "一句简短、可核验的方向释义",
  "passed": true
}
该字段是可审查的结构化说明，不代表真实内部推理过程。不能确认方向时不要输出该关系。

【置信度】
confidence 仅用于排序和诊断，不等于真实准确率：
- 0.90-1.00：原文直接、关系类型与方向均无歧义
- 0.75-0.89：证据明确，但需结合相邻句理解
- 0.60-0.74：存在一定解释空间；通常应谨慎输出
- 低于 0.60：不输出
不得为了通过系统阈值虚报置信度。

【输出 JSON】
{
  "relations": [
    {
      "source": "候选实体名称",
      "target": "候选实体名称",
      "type": "CONTAINS",
      "direction_basis": {
        "source_role": "上位概念",
        "target_role": "下位概念",
        "semantic_check": "栈是线性表的一种",
        "passed": true
      },
      "evidence": "原文连续短句",
      "confidence": 0.95
    }
  ],
  "unmatched_mentions": [
    {
      "name": "候选池遗漏的实体名称",
      "evidence": "原文证据",
      "reason": "该知识点在原文中明确出现，但候选池中没有"
    }
  ]
}
只输出 JSON，不得输出 Markdown 或解释。
"""

# Domain packs are intentionally modular. Expand/migrate the remaining legacy
# domain-specific Gold rules into these strings without changing their labels.
DOMAIN_RULES: Dict[str, str] = {
    "general": """
【通用领域补充】
只建立原文直接支持的关系。属性、组成、工具、分类必须按关系定义区分。
""",
    "computer_science": """
【计算机科学领域补充】
- 具体遍历方式、算法步骤、操作和表示法通常归为「方法」；
  类别性总称（如「遍历」「旋转」）归为「概念」。
- 「栈是一种操作受限的线性表」=> 线性表 CONTAINS 栈。
- 「AVL 树是二叉搜索树的一种」=> 二叉搜索树 CONTAINS AVL 树。
- 「前序遍历是深度优先遍历的一种」=> 深度优先遍历 CONTAINS 前序遍历。
- 「广度优先遍历借助队列实现」按既有 Gold 口径为
  队列 RELATED_TO 广度优先遍历，不使用 APPLIES_TO。
- 「深度优先遍历基于递归实现」=> 递归 APPLIES_TO 深度优先遍历。
- 不将二叉树与根节点、叶节点、高度、深度等一般组成/属性建立 CONTAINS。
- 不将 AVL 树与旋转等维护机制机械建立 CONTAINS 或 RELATED_TO。
- 不因两个术语共享词根而建立关系。
""",
    "data_structure": """
【数据结构专项】
- 只有明确的分类句式建立 CONTAINS；不要把任意结构组成部分视为子类。
- 若原文明确说某数据结构支持插入、删除、查找等操作，可按原 Gold 口径
  建立「数据结构 CONTAINS 操作」；操作必须挂在原文明确对应的结构上。
- 「二叉树可用链表表示」按既有方向口径为链表 RELATED_TO 二叉树。
""",
    "complexity": """
【复杂度分析专项】
- 复杂度分析 CONTAINS 时间复杂度、空间复杂度。
- 时间复杂度 CONTAINS 常数阶、对数阶、线性阶、线性对数阶、平方阶、
  指数阶、阶乘阶及最佳/最差/平均时间复杂度。
- 空间复杂度 CONTAINS 常数阶、对数阶、线性阶、平方阶、指数阶。
- 递归与递归树、调用栈之间按原文明确关联使用 RELATED_TO，不使用 CONTAINS。
- 递归 APPLIES_TO 分治（仅当原文和当前 Gold 口径支持此映射）。
- 不把「空间复杂度」CONTAINS「暂存空间」；按既有口径使用 RELATED_TO。
""",
    "chemistry": """
【化学专项】
- 元素 CONTAINS 金属、非金属、类金属等元素类别。
- 元素周期表 CONTAINS 周期、族；具体元素类别不要挂在元素周期表下。
- 族 CONTAINS 碱金属、碱土金属、卤素、氧族元素、氮族元素、稀有气体。
- 化学键 CONTAINS 共价键、离子键；酸 CONTAINS 二元酸、含氧酸。
- 原子 CONTAINS 原子核、电子；原子核 CONTAINS 质子、中子。
- 不将质子、中子直接挂在原子下。
- 命名法不擅自细分为原文没有出现的具体命名法。
- 不根据人名创造实验实体；原文没有出现的实验名称不得输出。
""",
    "physics": """
【物理专项】
- 矢量 CONTAINS 位移、速度；标量 CONTAINS 距离、速率。
- 速度 CONTAINS 平均速度、瞬时速度；速率 CONTAINS 平均速率、瞬时速率。
- 加速度 CONTAINS 平均加速度、瞬时加速度、重力加速度（以原文为准）。
- 运动学 CONTAINS 一维运动；图像分析 CONTAINS 位置-时间图、速度-时间图、
  加速度-时间图。
- 不把自由落体 CONTAINS 重力加速度；不把平均速度 CONTAINS 瞬时速度。
""",
}

FEW_SHOT: Dict[str, str] = {
    "contains_direction": """
【方向正反例】
原文：栈是一种操作受限的线性表。
正确：线性表 CONTAINS 栈。
错误：栈 CONTAINS 线性表。
原因：target 必须是 source 的一种。
""",
    "precedence_vs_order": """
【学习依赖与叙述顺序正反例】
原文：本章首先介绍线性表，然后介绍栈。
正确：不输出 PRECEDES。
错误：线性表 PRECEDES 栈。
原因：章节叙述顺序不等于学习先修关系。
""",
    "applies_direction": """
【APPLIES_TO 方向正反例】
原文：深度优先遍历通常基于递归实现。
正确：递归 APPLIES_TO 深度优先遍历。
错误：深度优先遍历 APPLIES_TO 递归。
""",
    "related_overgeneration": """
【RELATED_TO 泛化正反例】
原文：二叉树具有高度，AVL 树通过旋转保持平衡。
正确：不因这些句子生成二叉树 RELATED_TO 高度或 AVL 树 RELATED_TO 旋转。
原因：属性、维护机制不是本项目允许的直接关联关系。
""",
}

DOMAIN_ALIASES = {
    "cs": "computer_science",
    "computer": "computer_science",
    "data_structures": "data_structure",
    "algorithm": "computer_science",
    "algorithms": "computer_science",
    "math": "general",
}

def _canonical_domain(domain: Optional[str]) -> str:
    key = (domain or "general").strip().lower().replace("-", "_").replace(" ", "_")
    key = DOMAIN_ALIASES.get(key, key)
    return key if key in DOMAIN_RULES else "general"

def _prompt_for_entity(domain: Optional[str]) -> str:
    d = _canonical_domain(domain)
    return BASE_ENTITY_PROMPT + "\n" + DOMAIN_RULES[d]

def _prompt_for_relation(domain: Optional[str], include_few_shot: bool = True) -> str:
    d = _canonical_domain(domain)
    prompt = BASE_RELATION_PROMPT + "\n" + DOMAIN_RULES[d]
    if include_few_shot:
        prompt += "\n" + "\n".join(FEW_SHOT.values())
    return prompt

def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]

# ---------------------------------------------------------------------------
# Normalization and validation
# ---------------------------------------------------------------------------

def _fullwidth_to_halfwidth(value: str) -> str:
    out = []
    for ch in value:
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)

def _normalize_entity_name(name: Any) -> str:
    if not isinstance(name, str):
        return ""
    name = re.sub(r"\s+", " ", _fullwidth_to_halfwidth(name)).strip()
    if name and not re.search(r"[\u4e00-\u9fff]", name) and " " not in name:
        name = name.upper()
    return SYNONYM_MAP.get(name.lower(), name)

def _is_code_symbol(name: str, category: Optional[str] = None) -> bool:
    if not name:
        return True
    if category == "公式" or re.search(r"[\u4e00-\u9fff]", name):
        return False
    if re.fullmatch(r"[=+\-*/%<>!&|^~]+", name.strip()):
        return True
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\s*\([^)]*\)", name.strip()):
        return True
    compact = name.replace(" ", "")
    if compact.isascii() and compact.isupper() and "_" in compact:
        return True
    return compact.upper() in CODE_KEYWORDS

def _parse_confidence(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0.0 <= number <= 1.0 else None

def _normalize_evidence(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()

def _evidence_in_text(evidence: str, source_text: str) -> bool:
    if not evidence.strip():
        return False
    return _normalize_evidence(evidence) in _normalize_evidence(source_text)

def _parse_json(content: str) -> dict:
    content = (content or "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.I)
        content = re.sub(r"\s*```$", "", content)
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("无法从模型响应中定位 JSON 对象")
        payload = json.loads(content[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("JSON 根节点必须是对象")
    return payload

def _validate_entities(payload: dict) -> Tuple[List[dict], int]:
    raw = payload.get("entities")
    if not isinstance(raw, list):
        raise ValueError("实体阶段缺少 entities 数组")
    clean, dropped = [], 0
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            dropped += 1
            continue
        name = _normalize_entity_name(item.get("name"))
        if not name or _is_code_symbol(name, item.get("category")):
            dropped += 1
            continue
        category = item.get("category")
        if category not in ENTITY_CATEGORIES:
            category = "概念"
        description = item.get("description") if isinstance(item.get("description"), str) else ""
        evidence = item.get("evidence") if isinstance(item.get("evidence"), str) else ""
        key = (name, category)
        if key in seen:
            continue
        seen.add(key)
        clean.append({
            "name": name,
            "category": category,
            "description": description.strip(),
            "evidence": evidence.strip(),
        })
    return clean, dropped

def _validate_relations(payload: dict) -> Tuple[List[dict], List[dict], int]:
    raw = payload.get("relations")
    if not isinstance(raw, list):
        raise ValueError("关系阶段缺少 relations 数组")
    clean, unmatched, dropped = [], [], 0
    for item in raw:
        if not isinstance(item, dict):
            dropped += 1
            continue
        source = _normalize_entity_name(item.get("source"))
        target = _normalize_entity_name(item.get("target"))
        rel_type = str(item.get("type") or "").strip().upper()
        if not source or not target or rel_type not in RELATION_TYPES:
            dropped += 1
            continue
        basis = item.get("direction_basis")
        if not isinstance(basis, dict):
            basis = {}
        clean.append({
            "source": source,
            "target": target,
            "type": rel_type,
            "direction_basis": basis,
            "evidence": item.get("evidence") if isinstance(item.get("evidence"), str) else "",
            "confidence": _parse_confidence(item.get("confidence")),
        })
    raw_unmatched = payload.get("unmatched_mentions", [])
    if isinstance(raw_unmatched, list):
        for item in raw_unmatched:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                unmatched.append({
                    "name": _normalize_entity_name(item["name"]),
                    "evidence": item.get("evidence", ""),
                    "reason": item.get("reason", ""),
                })
    return clean, unmatched, dropped

# ---------------------------------------------------------------------------
# LLM client
# ---------------------------------------------------------------------------

class ExtractionSchemaError(ValueError):
    pass

def _error_result(error_type: str, message: str, retryable: bool) -> dict:
    return {
        "entities": [], "relations": [],
        "error": message, "error_type": error_type, "retryable": retryable,
    }

def _classify_error(exc: Exception) -> Tuple[str, str, bool]:
    name = type(exc).__name__.lower()
    msg = str(exc)
    low = msg.lower()
    status = getattr(exc, "status_code", None)
    if status in (401, 403) or "invalid api key" in low or "authentication" in low:
        return "auth", f"鉴权失败: {msg}", False
    if status == 429 or "rate limit" in low:
        return "rate_limit", f"触发限流: {msg}", True
    if "timeout" in name or "timed out" in low or "timeout" in low:
        return "timeout", f"请求超时: {msg}", True
    if "connection" in name or "connection" in low:
        return "network", f"网络连接失败: {msg}", True
    if status in (400, 422):
        return "bad_request", f"请求参数或内容被拒绝: {msg}", False
    return "api_error", f"LLM 接口调用失败: {msg}", True

def _is_json_mode_rejection(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    msg = str(exc).lower()
    return (status in (400, 422) or isinstance(exc, TypeError)) and any(
        x in msg for x in ("response_format", "json_object", "json mode")
    )

class KnowledgeExtractor:
    """Two-stage extractor. Public extract() remains async and backward compatible."""

    def __init__(
        self,
        temperature: float = RELATION_TEMPERATURE,
        entity_temperature: float = ENTITY_TEMPERATURE,
        max_concurrency: int = MAX_CONCURRENCY,
    ):
        self.client = OpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_API_BASE,
        )
        self.entity_temperature = entity_temperature
        self.relation_temperature = temperature
        self.max_concurrency = max(1, int(max_concurrency))

    def _completion(
        self,
        prompt: str,
        text: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        use_json_mode = bool(getattr(settings, "LLM_JSON_MODE", True))
        kwargs = {
            "model": settings.LLM_MODEL,
            "messages": [
                {"role": "system", "content": "你是严谨的高校课程知识图谱抽取助手。只输出合法 JSON。"},
                {"role": "user", "content": prompt + "\n\n【输入原文】\n" + text},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": settings.EXTRACTION_TIMEOUT,
        }
        if use_json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            if not use_json_mode or not _is_json_mode_rejection(exc):
                raise
            kwargs.pop("response_format", None)
            response = self.client.chat.completions.create(**kwargs)

        choice = response.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            raise TruncatedOutputError("模型输出达到 max_tokens 上限")
        content = (choice.message.content or "").strip()
        if not content:
            raise ValueError("模型返回空内容")
        return content

    def _cache_load(self, text: str, stage: str, prompt: str, temperature: float):
        if not extraction_cache.enabled():
            return None
        fp = _fingerprint(stage + prompt)
        key = extraction_cache.build_key(
            text, settings.LLM_MODEL, temperature, fp
        )
        cached = extraction_cache.load(key)
        return cached

    def _cache_store(self, text: str, stage: str, prompt: str, temperature: float, value: dict):
        if not extraction_cache.enabled():
            return
        fp = _fingerprint(stage + prompt)
        key = extraction_cache.build_key(
            text, settings.LLM_MODEL, temperature, fp
        )
        extraction_cache.store(key, value)

    def _extract_entities_sync(self, chunk: str, domain: str) -> dict:
        prompt = _prompt_for_entity(domain)
        cached = self._cache_load(chunk, "entity-v3", prompt, self.entity_temperature)
        if cached is not None:
            return cached
        try:
            raw = self._completion(prompt, chunk, self.entity_temperature, ENTITY_MAX_OUTPUT_TOKENS)
            payload = _parse_json(raw)
            entities, dropped = _validate_entities(payload)
            result = {"entities": entities, "dropped_entity_items": dropped}
            self._cache_store(chunk, "entity-v3", prompt, self.entity_temperature, result)
            return result
        except TruncatedOutputError as exc:
            return _error_result("truncated", str(exc), False)
        except Exception as exc:
            typ, msg, retryable = _classify_error(exc)
            return _error_result(typ, msg, retryable)

    def _extract_relations_sync(self, chunk: str, entities: Sequence[dict], domain: str) -> dict:
        prompt = _prompt_for_relation(domain)
        entity_payload = json.dumps(
            [{"name": e["name"], "category": e["category"], "description": e["description"]}
             for e in entities],
            ensure_ascii=False,
        )
        task_text = (
            "【候选实体池 JSON】\n" + entity_payload
            + "\n\n【注意】source/target 必须来自以上候选实体池。"
        )
        cache_text = task_text + "\n\n【原文】\n" + chunk
        cached = self._cache_load(cache_text, "relation-v3", prompt, self.relation_temperature)
        if cached is not None:
            return cached
        try:
            raw = self._completion(
                prompt, task_text + "\n\n【课程原文】\n" + chunk,
                self.relation_temperature, RELATION_MAX_OUTPUT_TOKENS,
            )
            payload = _parse_json(raw)
            relations, unmatched, dropped = _validate_relations(payload)
            result = {
                "relations": relations,
                "unmatched_mentions": unmatched,
                "dropped_relation_items": dropped,
            }
            self._cache_store(cache_text, "relation-v3", prompt, self.relation_temperature, result)
            return result
        except TruncatedOutputError as exc:
            return _error_result("truncated", str(exc), False)
        except Exception as exc:
            typ, msg, retryable = _classify_error(exc)
            return _error_result(typ, msg, retryable)

    async def _run_with_retry(self, fn, *args):
        result = await asyncio.to_thread(fn, *args)
        for attempt in range(EXTRACTION_RETRY):
            if not result.get("error") or not result.get("retryable", False):
                break
            logger.warning("抽取失败，重试 %d/%d：%s", attempt + 1, EXTRACTION_RETRY, result.get("error"))
            result = await asyncio.to_thread(fn, *args)
        return result

    async def extract(
        self,
        text: str,
        overlap_tokens: int = OVERLAP_TOKENS,
        domain: str = "general",
    ) -> dict:
        """
        Backward-compatible API. `domain` is optional; old callers can continue
        calling extract(text). Suggested values: computer_science, data_structure,
        complexity, chemistry, physics, general.
        """
        if not isinstance(text, str) or not text.strip():
            return _empty_result()

        chunks = chunk_text_for_llm(
            text, max_tokens=CHUNK_MAX_TOKENS, overlap_tokens=overlap_tokens
        )
        if not chunks:
            chunks = [text]

        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def process_chunk(index: int, chunk: str):
            async with semaphore:
                entity_result = await self._run_with_retry(
                    self._extract_entities_sync, chunk, domain
                )
                if entity_result.get("error"):
                    return {
                        "entities": [], "relations": [],
                        "error": entity_result["error"],
                        "error_type": entity_result.get("error_type"),
                        "retryable": entity_result.get("retryable", False),
                        "chunk_index": index,
                    }

                entities = entity_result.get("entities", [])
                relation_result = await self._run_with_retry(
                    self._extract_relations_sync, chunk, entities, domain
                )
                return {
                    "entities": entities,
                    "relations": relation_result.get("relations", []),
                    "unmatched_mentions": relation_result.get("unmatched_mentions", []),
                    "dropped_entity_items": entity_result.get("dropped_entity_items", 0),
                    "dropped_relation_items": relation_result.get("dropped_relation_items", 0),
                    "error": relation_result.get("error"),
                    "error_type": relation_result.get("error_type"),
                    "chunk_index": index,
                }

        results = await asyncio.gather(
            *(process_chunk(i, chunk) for i, chunk in enumerate(chunks))
        )
        merged = self._merge_results(results, source_chunks=chunks)
        merged["domain"] = _canonical_domain(domain)
        merged["unmatched_mentions"] = _merge_unmatched(
            [m for r in results for m in r.get("unmatched_mentions", [])]
        )
        merged["dropped_entity_items"] = sum(r.get("dropped_entity_items", 0) for r in results)
        merged["dropped_relation_items"] = sum(r.get("dropped_relation_items", 0) for r in results)
        return merged

    @staticmethod
    def _merge_results(results: List[dict], source_chunks: Optional[Sequence[str]] = None) -> dict:
        entities_by_name: Dict[str, dict] = {}
        dropped_entity_count = 0
        duplicate_names = []
        entity_duplicates = 0

        for result in results:
            dropped_entity_count += int(result.get("dropped_entity_items", 0) or 0)
            for entity in result.get("entities", []):
                name = _normalize_entity_name(entity.get("name"))
                if not name or _is_code_symbol(name, entity.get("category")):
                    dropped_entity_count += 1
                    continue
                category = entity.get("category") if entity.get("category") in ENTITY_CATEGORIES else "概念"
                description = (entity.get("description") or "").strip()
                evidence = (entity.get("evidence") or "").strip()
                host = entities_by_name.get(name)
                if host is None:
                    entities_by_name[name] = {
                        "name": name,
                        "category": category,
                        "description": description,
                        "descriptions": [description] if description else [],
                        "evidences": [evidence] if evidence else [],
                    }
                else:
                    entity_duplicates += 1
                    if name not in duplicate_names:
                        duplicate_names.append(name)
                    if description and description not in host["descriptions"]:
                        host["descriptions"].append(description)
                    if evidence and evidence not in host["evidences"]:
                        host["evidences"].append(evidence)
                    if category != host["category"]:
                        variants = host.setdefault("category_variants", [])
                        if category not in variants:
                            variants.append(category)

        for entity in entities_by_name.values():
            if len(entity["descriptions"]) > 1:
                entity["alt_descriptions"] = entity["descriptions"][1:]
            if len(entity["evidences"]) > 1:
                entity["alt_evidences"] = entity["evidences"][1:]
            entity.pop("descriptions", None)
            entity.pop("evidences", None)

        entity_names = set(entities_by_name)
        merged_relations = []
        seen = set()
        dropped = []
        dropped_counts: Dict[str, int] = {}
        raw_relation_count = 0

        def drop(reason: str, source: str, rel_type: str, target: str, detail: str = ""):
            dropped_counts[reason] = dropped_counts.get(reason, 0) + 1
            if len(dropped) < MAX_DROPPED_RECORDED:
                dropped.append({
                    "source": source, "type": rel_type, "target": target,
                    "reason_code": reason, "detail": detail,
                })

        for result in results:
            for relation in result.get("relations", []):
                raw_relation_count += 1
                source = _normalize_entity_name(relation.get("source"))
                target = _normalize_entity_name(relation.get("target"))
                rel_type = str(relation.get("type") or "").strip().upper()
                evidence = (relation.get("evidence") or "").strip()
                confidence = _parse_confidence(relation.get("confidence"))

                if rel_type not in VALID_RELATION_TYPES or rel_type not in RELATION_TYPES:
                    drop("invalid_relation_type", source, rel_type, target)
                    continue
                if not source or not target:
                    drop("invalid_endpoint", source, rel_type, target, "端点为空")
                    continue
                if source == target:
                    drop("synonym_split", source, rel_type, target, "规范化后形成自环")
                    continue
                if source not in entity_names or target not in entity_names:
                    drop("dangling_endpoint", source, rel_type, target, "端点不在实体集合")
                    continue

                # Evidence must be a verbatim excerpt from at least one chunk.
                if source_chunks and not any(_evidence_in_text(evidence, c) for c in source_chunks):
                    drop("invalid_evidence", source, rel_type, target, "evidence 不在原文中")
                    continue

                basis = relation.get("direction_basis") or {}
                if basis and basis.get("passed") is False:
                    drop("direction_check_failed", source, rel_type, target)
                    continue

                threshold = RELATION_CONFIDENCE_THRESHOLDS.get(rel_type, 0.5)
                if confidence is not None and confidence < threshold:
                    drop("low_confidence", source, rel_type, target, f"{confidence} < {threshold}")
                    continue

                key = (source, rel_type, target)
                if key in seen:
                    # Keep the strongest confidence/evidence instead of silently losing
                    # all duplicate evidence. The first result remains stable.
                    existing = next((x for x in merged_relations
                                     if (x["source"], x["type"], x["target"]) == key), None)
                    if existing:
                        if confidence is not None and confidence > existing["confidence"]:
                            existing["confidence"] = confidence
                        if evidence and evidence not in existing.get("evidences", []):
                            existing.setdefault("evidences", []).append(evidence)
                    drop("duplicate", source, rel_type, target, "重复三元组，证据已合并")
                    continue
                seen.add(key)
                merged_relations.append({
                    "source": source,
                    "target": target,
                    "type": rel_type,
                    "evidence": evidence,
                    "confidence": confidence if confidence is not None else DEFAULT_RELATION_CONFIDENCE,
                    "direction_basis": basis,
                })

        errors = [r.get("error") for r in results if r.get("error")]
        error_types = sorted({r.get("error_type") for r in results if r.get("error_type")})
        chunk_count = len(results)
        failed_chunk_count = len(errors)

        return {
            "entities": list(entities_by_name.values()),
            "relations": merged_relations,
            "raw_relation_count": raw_relation_count,
            "dropped_relations": dropped,
            "dropped_counts": dropped_counts,
            "dropped_entity_count": dropped_entity_count,
            "chunk_count": chunk_count,
            "failed_chunk_count": failed_chunk_count,
            "succeeded_chunk_count": chunk_count - failed_chunk_count,
            "entity_duplicates": entity_duplicates,
            "duplicate_names": duplicate_names,
            **({"error": "；".join(errors[:3]), "error_types": error_types} if errors else {}),
        }

class TruncatedOutputError(RuntimeError):
    pass

def _merge_unmatched(items: Sequence[dict]) -> List[dict]:
    result, seen = [], set()
    for item in items:
        name = _normalize_entity_name(item.get("name"))
        evidence = (item.get("evidence") or "").strip()
        key = (name, _normalize_evidence(evidence))
        if not name or key in seen:
            continue
        seen.add(key)
        result.append({
            "name": name,
            "evidence": evidence,
            "reason": item.get("reason", ""),
        })
    return result

def _empty_result() -> dict:
    return {
        "entities": [], "relations": [],
        "raw_relation_count": 0, "dropped_relations": [],
        "dropped_counts": {}, "dropped_entity_count": 0,
        "chunk_count": 0, "failed_chunk_count": 0,
        "succeeded_chunk_count": 0, "entity_duplicates": 0,
        "duplicate_names": [], "unmatched_mentions": [],
    }
