"""知识抽取诊断（v1.4）：只观察，不筛选。

本模块回答三个问题：

1. 模型给的 evidence 能不能在原文里找到？分四档：
   ``exact_match`` / ``normalized_match`` / ``paraphrase`` / ``missing``。
2. 关系有没有给 ``direction_basis``？是缺失、空值还是有内容？
3. ``unmatched_mentions`` 是否格式合法、是否重复、数量是否失控？

设计边界（务必保持）：

- **不修改抽取结果**。所有函数只读取传入的 dict/list，返回新的诊断结构。
- **不删除实体或关系**。``paraphrase`` / ``missing`` 只是诊断分类，不是删除理由；
  是否丢弃仍由 ``knowledge_extractor._merge_results`` 的既有规则决定。
- **不做语义归一化**。中文实体名保持原样；这里只做 Unicode NFKC 与空白折叠，
  用于判断「字符串能否匹配」，不用于改写名称。
- **不依赖 LLM、不读写数据库**，可在任意离线测试中直接调用。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

EVIDENCE_EXACT = "exact_match"
EVIDENCE_NORMALIZED = "normalized_match"
EVIDENCE_PARAPHRASE = "paraphrase"
EVIDENCE_MISSING = "missing"

EVIDENCE_CATEGORIES = (
    EVIDENCE_EXACT,
    EVIDENCE_NORMALIZED,
    EVIDENCE_PARAPHRASE,
    EVIDENCE_MISSING,
)

# Prompt 里建议最多 10 条；Python 侧放宽到 50 条，既兜住异常膨胀，
# 又不至于把模型合理给出的诊断线索全部截掉。
MAX_UNMATCHED_MENTIONS = 50

# 诊断明细最多保留条数（计数始终是精确值，截断只影响明细列表）。
MAX_DIAGNOSTIC_ITEMS = 500

# 单个文本字段（evidence / direction_basis / reason）写入诊断时的长度上限，
# 避免异常长文本把结果文件或日志撑爆。
MAX_TEXT_FIELD = 300

_CJK_RE = re.compile(r"[一-鿿]")
_WHITESPACE_RE = re.compile(r"\s+")
_OPERATOR_RE = re.compile(r"[=+\-*/%<>!&|^~]+")
_FUNCTION_CALL_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\s*\([^)]*\)")


# ---------------------------------------------------------------------------
# 基础规范化（只用于匹配，不用于改名）
# ---------------------------------------------------------------------------


def normalize_for_match(value: str) -> str:
    """NFKC（含全角转半角）+ 空白折叠，返回用于字符串匹配的文本。

    注意：这不是实体名归一化函数。实体名仍由
    ``knowledge_extractor._normalize_entity_name`` 处理，本函数不会写回结果。
    """
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKC", value)
    return _WHITESPACE_RE.sub(" ", value).strip()


def _compact(value: str) -> str:
    """去掉全部空白后的规范化文本，用于处理中文里多余空格导致的匹配失败。"""
    return _WHITESPACE_RE.sub("", normalize_for_match(value))


@dataclass(frozen=True)
class _PreparedSource:
    raw: str
    normalized: str
    compact: str


def _prepare_source(source_text: str) -> _PreparedSource:
    raw = source_text if isinstance(source_text, str) else ""
    normalized = normalize_for_match(raw)
    return _PreparedSource(raw=raw, normalized=normalized, compact=_compact(raw))


def _classify_with_prepared(evidence: Any, source: _PreparedSource) -> str:
    if not isinstance(evidence, str) or not evidence.strip():
        return EVIDENCE_MISSING
    if not source.raw:
        # 没有原文可核对时，不能武断判成 missing；归入「无法通过字符串匹配确认」。
        return EVIDENCE_PARAPHRASE
    if evidence in source.raw:
        return EVIDENCE_EXACT
    evidence_normalized = normalize_for_match(evidence)
    if evidence_normalized and evidence_normalized in source.normalized:
        return EVIDENCE_NORMALIZED
    evidence_compact = _compact(evidence)
    if evidence_compact and evidence_compact in source.compact:
        return EVIDENCE_NORMALIZED
    return EVIDENCE_PARAPHRASE


def classify_evidence(evidence: Any, source_text: str) -> str:
    """把证据分为 exact_match / normalized_match / paraphrase / missing。

    只做字符串判断：``paraphrase`` 不等于错误，不能作为删除实体或关系的依据。
    """
    return _classify_with_prepared(evidence, _prepare_source(source_text))


# ---------------------------------------------------------------------------
# 字段清洗
# ---------------------------------------------------------------------------


def _safe_text(value: Any, limit: int = MAX_TEXT_FIELD) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return value[:limit]


def _safe_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return _WHITESPACE_RE.sub(" ", value).strip()


def sanitize_direction_basis(value: Any) -> str:
    """把模型给的 direction_basis 规整成字符串。

    允许字符串，也允许结构化 dict（序列化为紧凑 JSON）。其他类型降级为空串，
    但**不因此删除关系**——缺失/空值只是诊断计数。
    """
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, Mapping):
        try:
            return _safe_text(json.dumps(value, ensure_ascii=False, sort_keys=True))
        except (TypeError, ValueError):
            return ""
    return ""


def _looks_like_code_symbol(name: str) -> bool:
    """轻量兜底：明显是代码符号的提及不进 unmatched_mentions。

    只做最保守的判断（纯运算符、函数调用写法、全大写下划线标识符），
    不碰正常中文知识点，避免把诊断线索误杀。
    """
    if not name:
        return True
    if _OPERATOR_RE.fullmatch(name):
        return True
    if _FUNCTION_CALL_RE.fullmatch(name):
        return True
    compact = name.replace(" ", "")
    return compact.isascii() and compact.isupper() and "_" in compact


def sanitize_unmatched_mentions(
    raw: Any,
    max_items: int = MAX_UNMATCHED_MENTIONS,
) -> list[dict[str, str]]:
    """清洗 ``unmatched_mentions``：去重、限长、限数量、过滤明显代码符号。

    ``unmatched_mentions`` 只是诊断线索，不会被补进 entities，也不会用于建关系。
    格式异常（非 list、元素不是 dict/str、缺少 name）一律跳过，不抛异常。
    """
    if not isinstance(raw, list):
        return []

    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for item in raw:
        if len(result) >= max(0, int(max_items)):
            break
        if isinstance(item, str):
            name, evidence, reason = item, "", ""
        elif isinstance(item, Mapping):
            name = item.get("name") if isinstance(item.get("name"), str) else ""
            evidence = item.get("evidence") if isinstance(item.get("evidence"), str) else ""
            reason = item.get("reason") if isinstance(item.get("reason"), str) else ""
        else:
            continue

        name = _safe_name(name)
        if not name or len(name) > 100 or _looks_like_code_symbol(name):
            continue
        evidence = _safe_text(evidence)
        reason = _safe_text(reason)
        key = (normalize_for_match(name), normalize_for_match(evidence))
        if key in seen:
            continue
        seen.add(key)
        result.append({"name": name, "evidence": evidence, "reason": reason})

    return result


def merge_unmatched_mentions(
    items: Sequence[Mapping[str, Any]],
    max_items: int = MAX_UNMATCHED_MENTIONS,
) -> list[dict[str, str]]:
    """跨分块合并 unmatched_mentions：按 (name, evidence) 去重并限数量。"""
    return sanitize_unmatched_mentions(list(items or []), max_items=max_items)


# ---------------------------------------------------------------------------
# 诊断构建
# ---------------------------------------------------------------------------


def _default_normalize_name(name: str) -> str:
    """与抽取器同名规则的轻量副本（不含 _SYNONYM_MAP），仅用于诊断记录。

    实际调用时由抽取器把 ``_normalize_entity_name`` 传进来，保证与真正入库的
    规范化结果一致；这里保留默认实现，方便模块独立使用。
    """
    value = normalize_for_match(name)
    if value and not _CJK_RE.search(value) and " " not in value:
        value = value.upper()
    return value


def _count_categories(values: Sequence[str]) -> dict[str, int]:
    counts = {category: 0 for category in EVIDENCE_CATEGORIES}
    for value in values:
        if value in counts:
            counts[value] += 1
    return counts


def build_diagnostics(
    payload: Mapping[str, Any],
    source_text: str,
    normalize_name: Callable[[str], str] | None = None,
    max_items: int = MAX_DIAGNOSTIC_ITEMS,
) -> dict[str, Any]:
    """为一次抽取（整篇或单个分块）构建诊断数据。

    ``payload`` 需要包含 ``entities`` / ``relations``，可包含 ``unmatched_mentions``。
    返回结构只含诊断信息，不修改 ``payload``。

    当 ``source_text`` 为空（例如旧测试只做内存合并）时，非空 evidence 会被归为
    ``paraphrase``（无法核对），并在 ``summary.source_available`` 中标记 False。
    """
    entities = payload.get("entities") if isinstance(payload.get("entities"), list) else []
    relations = payload.get("relations") if isinstance(payload.get("relations"), list) else []
    unmatched = sanitize_unmatched_mentions(payload.get("unmatched_mentions"))

    normalize = normalize_name or _default_normalize_name
    source = _prepare_source(source_text)

    entity_items: list[dict[str, Any]] = []
    entity_evidence_values: list[str] = []
    name_changed = 0
    name_in_source = 0
    name_not_in_source = 0

    for entity in entities:
        if not isinstance(entity, Mapping):
            continue
        original_name = _safe_name(entity.get("name"))
        normalized_name = _safe_name(normalize(original_name)) if original_name else ""
        changed = bool(original_name and normalized_name and original_name != normalized_name)
        name_match = _classify_with_prepared(original_name, source) if original_name else EVIDENCE_MISSING
        in_source = name_match in (EVIDENCE_EXACT, EVIDENCE_NORMALIZED)
        evidence = _safe_text(entity.get("evidence"))
        evidence_match = _classify_with_prepared(evidence, source)

        entity_evidence_values.append(evidence_match)
        name_changed += int(changed)
        name_in_source += int(in_source)
        name_not_in_source += int(bool(original_name) and not in_source)

        if len(entity_items) < max_items:
            entity_items.append({
                "name": original_name,
                "normalized_name": normalized_name,
                "name_changed": changed,
                "name_in_source": in_source,
                "evidence": evidence,
                "evidence_match": evidence_match,
            })

    relation_items: list[dict[str, Any]] = []
    relation_evidence_values: list[str] = []
    direction_present = 0
    direction_empty = 0
    direction_missing = 0

    for relation in relations:
        if not isinstance(relation, Mapping):
            continue
        evidence = _safe_text(relation.get("evidence"))
        evidence_match = _classify_with_prepared(evidence, source)
        relation_evidence_values.append(evidence_match)

        has_field = "direction_basis" in relation
        direction_text = sanitize_direction_basis(relation.get("direction_basis"))
        if has_field:
            direction_present += 1
            if not direction_text:
                direction_empty += 1
        else:
            direction_missing += 1

        if len(relation_items) < max_items:
            relation_items.append({
                "source": _safe_name(relation.get("source")),
                "target": _safe_name(relation.get("target")),
                "type": str(relation.get("type") or "").strip().upper(),
                "evidence": evidence,
                "evidence_match": evidence_match,
                "direction_basis": direction_text,
                "direction_basis_present": has_field,
                "direction_basis_empty": bool(has_field and not direction_text),
            })

    summary = {
        "source_available": bool(source.raw),
        "entities": {
            "total": len(entities),
            "evidence": _count_categories(entity_evidence_values),
            "name_changed": name_changed,
            "name_in_source": name_in_source,
            "name_not_in_source": name_not_in_source,
        },
        "relations": {
            "total": len(relations),
            "evidence": _count_categories(relation_evidence_values),
            "direction_basis": {
                "present": direction_present,
                "empty": direction_empty,
                "missing": direction_missing,
            },
        },
        "unmatched_mentions": {"total": len(unmatched)},
    }

    return {
        "summary": summary,
        "entities": entity_items,
        "relations": relation_items,
        "unmatched_mentions": unmatched,
    }


def summarize_diagnostics(diagnostics: Mapping[str, Any] | None) -> dict[str, Any]:
    """从 build_diagnostics 结果中取 summary；缺失时返回空结构。"""
    if isinstance(diagnostics, Mapping):
        summary = diagnostics.get("summary")
        if isinstance(summary, Mapping):
            return dict(summary)
    return {
        "source_available": False,
        "entities": {
            "total": 0,
            "evidence": _count_categories([]),
            "name_changed": 0,
            "name_in_source": 0,
            "name_not_in_source": 0,
        },
        "relations": {
            "total": 0,
            "evidence": _count_categories([]),
            "direction_basis": {"present": 0, "empty": 0, "missing": 0},
        },
        "unmatched_mentions": {"total": 0},
    }
