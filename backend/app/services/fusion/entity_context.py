"""重建「实体命中的文本上下文」。

## 为什么是「重建」而不是「原始证据」

V1.1 抽取器**没有保留 chunk 溯源**：`extract()` 把文本切块后逐块送 LLM，
`_merge_results` 再按规范名跨块合并，块号与原文偏移在返回前就被丢掉了。
因此本模块只能**重新分块 + 按名称在文本中定位**来重建上下文。

这件事必须说清楚，并在 API / 前端 / 报告里一律标注为
**「重建的文本命中上下文」**，不得表述为「原始抽取证据」。

## 诚实边界

- 重建用 `utils/text_processor.chunk_text_for_llm`（纯函数，不在冻结文件内），
  分块参数**从冻结的抽取器导入**，保证与抽取时同一套切法；
- 重建失败（文件缺失 / 解析异常 / 路径不可解析）时，`reconstruction_ok=False`
  且上下文为空 —— **调用方必须把 `w_ctx` 权重置 0，且不得据此推断实体同义**；
- overlap 会让同一实体命中多块，这是正常的：`chunk_indices` 给全量供展示，
  最小索引用于分档。
"""
import logging
import re
from datetime import datetime, timezone

from ...core.sql_database import sql_db
from ...core.storage import resolve_document_path
from ...utils.text_processor import chunk_text_for_llm
from ..document_parser import DocumentParser
from ..knowledge_extractor import _CHUNK_MAX_TOKENS, _OVERLAP_TOKENS
from . import fusion_config
from .entity_normalizer import extract_aliases, gloss_pairs, names_for_matching, normalize

_logger = logging.getLogger(__name__)

# 每个命中片段截取的最大字符数（够人工判断即可，避免把整页塞进响应）
_SNIPPET_CHARS = 160


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def empty_context(reason: str) -> dict:
    """重建失败时的上下文对象：显式标记不可用，绝不伪装成「没有命中」"""
    return {
        "reconstruction_ok": False,
        "discriminative": False,
        "method": fusion_config.CONTEXT_METHOD,
        "hit_terms": [],
        "chunk_indices": [],
        "snippets": [],
        "reconstructed_at": None,
        "reason": reason,
    }


async def load_chunks(course_id, document_id) -> tuple:
    """重新解析该文档并按抽取时的参数分块，返回 `(chunks, error)`。

    成功：`(list[str], None)`；失败：`(None, 原因字符串)`。
    **不抛异常** —— 上下文只是评分的一个分项，拿不到就降级，不该阻断整个扫描。
    """
    try:
        doc = sql_db.get_document(document_id)
    except Exception as e:  # noqa: BLE001
        return None, f"读取文档记录失败: {type(e).__name__}"
    if not doc:
        return None, "文档记录不存在"
    if doc.get("course_id") != course_id:
        # 隔离校验：不允许拿别的课程的文档来重建上下文
        return None, "文档不属于该课程"

    path = resolve_document_path(doc)
    if not path:
        return None, "无法解析文档路径"
    try:
        text = await DocumentParser.parse(path)
    except Exception as e:  # noqa: BLE001
        _logger.warning("重建上下文：解析文档失败 doc_id=%s: %s", document_id, e)
        return None, f"文档解析失败: {type(e).__name__}"
    if not text or not text.strip():
        return None, "文档解析结果为空"

    # 与抽取阶段同一套分块参数（从冻结文件导入，避免两处口径漂移）
    chunks = chunk_text_for_llm(text, max_tokens=_CHUNK_MAX_TOKENS,
                                overlap_tokens=_OVERLAP_TOKENS)
    if not chunks:
        return None, "分块结果为空"
    return chunks, None


def locate(chunks: list, name: str, description: str = None, aliases: list = None) -> dict:
    """在一份分块结果里定位某个实体的命中位置。**纯函数**。

    返回 {hit_terms, chunk_indices, snippets}；无命中时三者皆空。
    """
    if not chunks:
        return {"hit_terms": [], "chunk_indices": [], "snippets": []}

    # 检索词 = 原名 + 规范名 + 别名；描述里的括号注释（「…（BFS）」）也一并纳入，
    # 因为抽取结果里单看 name 时常常拿不到缩写。
    desc_aliases = extract_aliases("", description)
    terms = names_for_matching(name, list(aliases or []) + desc_aliases)

    hit_terms, idxs, snippets = [], [], []
    for term in terms:
        if not term:
            continue
        found_any = False
        for i, chunk in enumerate(chunks):
            pos = chunk.find(term)
            if pos < 0:
                continue
            found_any = True
            if i not in idxs:
                idxs.append(i)
            if len(snippets) < 5:
                start = max(0, pos - _SNIPPET_CHARS // 3)
                snippets.append(chunk[start:start + _SNIPPET_CHARS].replace("\n", " ").strip())
        if found_any:
            hit_terms.append(term)

    idxs.sort()
    return {"hit_terms": hit_terms, "chunk_indices": idxs, "snippets": snippets}


def gloss_partners(chunks: list, names: list) -> list:
    """在正文分块里找出与该实体构成「甲（乙）」等价式的**另一侧**写法。

    为什么必须在正文里找、而不是只看实体自己的定义：抽取结果的 description
    往往不含英文名/缩写，而等价式恰恰写在正文里（`运动学（Kinematics）`）。
    只查定义会让「中文名 ↔ 英文名」这一整类真同义对全部漏掉 ——
    实测漏掉 19/20 条正样本，就是这条路径缺失导致的。
    """
    keys = {normalize(n) for n in (names or []) if n}
    if not chunks or not keys:
        return []
    out, seen = [], set()
    for chunk in chunks:
        for left, right in gloss_pairs(chunk):
            nl, nr = normalize(left), normalize(right)
            if nl in keys and nr and nr not in keys:
                cand = right
            elif nr in keys and nl and nl not in keys:
                cand = left
            else:
                continue
            # 括号里可能是「halogens，第17族」这类复合写法，取第一段
            cand = re.split(r"[，,、]", cand)[0].strip()
            key = normalize(cand)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(cand)
    return out


def build_for(kp_id: str, name: str, description: str, aliases: list,
              chunks: list, error: str = None) -> dict:
    """组装单个实体的 `reconstructed_context`"""
    if chunks is None:
        return empty_context(error or "上下文重建不可用")
    loc = locate(chunks, name, description, aliases)
    hits = loc["chunk_indices"]
    # 「有区分度」= 这份上下文**能区分不同的实体对**。
    # 两个反例都会让它退化成常数，从而给所有实体对垫一层无意义的分：
    #   1) 文档只切出 1 块 —— 任何两个实体都"命中同一块"；
    #   2) 该实体命中**全部**分块 —— 它出现在哪都不构成证据。
    discriminative = len(chunks) >= 2 and 0 < len(hits) < len(chunks)
    return {
        "reconstruction_ok": True,
        "discriminative": discriminative,
        "method": fusion_config.CONTEXT_METHOD,
        "hit_terms": loc["hit_terms"],
        "chunk_indices": hits,
        "snippets": loc["snippets"],
        # 正文里与它构成「甲（乙）」等价式的另一侧写法（真实正文，非模型生成）
        "gloss_partners": gloss_partners(chunks, names_for_matching(name, aliases)),
        "reconstructed_at": _now_iso(),
        "reason": None if discriminative else "本文档的分块结果无法区分实体对（单块或命中全部块）",
    }


def context_similarity(a_ctx: dict, b_ctx: dict) -> tuple:
    """两个实体的上下文重合度，返回 `(分数 or None, 说明)`。

    - 任一方 `reconstruction_ok=False` → 返回 `(None, 原因)`：
      **调用方必须把 w_ctx 权重置 0**，不得把「没有上下文」当成「上下文不匹配」，
      否则分数会无理由变低，把本该进入复核的候选压到阈值以下。
    - 双方都有 chunk：同一块 = 1.0；相邻块 = 0.6；更远 = 0.2；完全不重合 = 0.0
    """
    if not a_ctx or not a_ctx.get("reconstruction_ok"):
        return None, "实体 A 的上下文不可用"
    if not b_ctx or not b_ctx.get("reconstruction_ok"):
        return None, "实体 B 的上下文不可用"
    if not a_ctx.get("discriminative") or not b_ctx.get("discriminative"):
        # 单块文档 / 命中全部块 → 这项对比对所有实体对都一样，"命中同一块"说明不了任何事
        return None, "上下文无区分度（单块文档或命中全部分块）"
    ai, bi = a_ctx.get("chunk_indices") or [], b_ctx.get("chunk_indices") or []
    if not ai or not bi:
        return None, "双方至少一方未在文本中命中"
    if set(ai) & set(bi):
        return 1.0, "命中同一分块"
    dist = min(abs(x - y) for x in ai for y in bi)
    if dist == 1:
        return 0.6, "命中相邻分块"
    return 0.2, f"命中分块相距 {dist}"
