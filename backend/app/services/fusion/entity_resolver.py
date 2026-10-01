"""规范节点选择 + 分档决策（+ P2 的 LLM 语义消歧）。

## 规范节点（canonical）由谁决定

**由本模块的规则梯子决定，LLM 不参与。** LLM 只回答「这两个是不是同一个知识点」，
不回答「谁该当规范节点」。把后者交给模型，等于让一个随机性来源决定图谱的
节点身份与名称，不可复现也不可审计。

梯子逐条比较、前一条平局才看下一条，全链路确定性；**名称长度与名称字典序不参与**
（字典序只在最后一条以 kp_id 兜底，作用是保证"同样输入永远同样输出"，不是判断依据）。

## 分档

`score >= AUTO_SAME` → SAME；`[REVIEW, AUTO_SAME)` → 送 LLM（LLM 不可用则 UNCERTAIN）；
`[RECALL, REVIEW)` → UNCERTAIN（进人工复核池）。低于 RECALL 的连候选行都不建。
"""

import json
import logging

from . import fusion_config

_logger = logging.getLogger(__name__)


def _num(v, default=-1.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def choose_canonical(a: dict, b: dict) -> tuple:
    """决定谁是规范节点（target），返回 `(source, target, canonical_reason)`。

    source 是**将被折进** target 的那一个。
    """
    # 1) 人工确认过的节点优先保留
    if bool(a.get("is_manual")) != bool(b.get("is_manual")):
        winner, loser = (a, b) if a.get("is_manual") else (b, a)
        return loser, winner, "人工确认过（is_manual）的节点作为规范节点"

    # 2) 节点自身置信度更高者
    ca, cb = _num(a.get("confidence")), _num(b.get("confidence"))
    if ca != cb:
        winner, loser = (a, b) if ca > cb else (b, a)
        return loser, winner, f"confidence 更高（{_num(winner.get('confidence')):.2f}）"

    # 3) 定义非空者（**只比有无，不比长度** —— 更长不等于更规范）
    da = bool((a.get("description") or "").strip())
    db = bool((b.get("description") or "").strip())
    if da != db:
        winner, loser = (a, b) if da else (b, a)
        return loser, winner, "定义非空，信息更完整（只比较有无，不比较长度）"

    # 4) 关系度数更高者当规范节点 —— 需要改写的边更少，影响面最小
    ga, gb = int(a.get("degree") or 0), int(b.get("degree") or 0)
    if ga != gb:
        winner, loser = (a, b) if ga > gb else (b, a)
        return loser, winner, (
            f"关系度数更高（{int(winner.get('degree') or 0)} > "
            f"{int(loser.get('degree') or 0)}），需改写的边更少")

    # 5) 创建时间更早者（两者都有时间时才比较，缺失不做猜测）
    ta, tb = a.get("created_at") or "", b.get("created_at") or ""
    if ta and tb and ta != tb:
        winner, loser = (a, b) if ta < tb else (b, a)
        return loser, winner, f"创建时间更早（{winner.get('created_at')}）"

    # 6) 最终兜底：kp_id 字典序。**只为确定性**，不是判断依据。
    winner, loser = (a, b) if str(a.get("kp_id")) <= str(b.get("kp_id")) else (b, a)
    return loser, winner, "以上判据全部并列，按 kp_id 字典序确定（仅为结果确定性）"


def target_rank(t: dict):
    """规范节点之间的排序键（越小越优先）。与 choose_canonical 的梯子同口径，
    仅第 6 项（kp_id 字典序）只用于保证确定性。"""
    return (
        0 if t.get("is_manual") else 1,
        -_num(t.get("confidence")),
        0 if (t.get("description") or "").strip() else 1,
        -int(t.get("degree") or 0),
        t.get("created_at") or "~",
        str(t.get("kp_id")),
    )


def resolve_multi_target(entries: list) -> tuple:
    """同一源同时命中多个 SAME 目标时，**只允许一个**成为规范节点。

    entries: `[{"target": profile, "score": float, ...}]`
    返回 `(chosen, superseded)`：chosen 为选中的那一条，superseded 为其余。

    规则：先按分数降序；分数并列时按 `target_rank`（人工 → 置信度 → 定义非空 →
    度数 → 创建时间 → kp_id）。**绝不"随便挑一个"** —— 多目标会同时违反
    「目标唯一」与「结果确定」两条不变量。
    """
    if not entries:
        return None, []
    ordered = sorted(entries, key=lambda e: (-float(e.get("score") or 0.0),
                                             target_rank(e["target"])))
    return ordered[0], ordered[1:]


# ---------- 分档 ----------

BAND_SAME = "SAME"
BAND_REVIEW = "REVIEW"
BAND_UNCERTAIN = "UNCERTAIN"
BAND_REJECT = "REJECT"


def band_of(score: float) -> str:
    if score >= fusion_config.AUTO_SAME_THRESHOLD:
        return BAND_SAME
    if score >= fusion_config.REVIEW_THRESHOLD:
        return BAND_REVIEW
    if score >= fusion_config.RECALL_THRESHOLD:
        return BAND_UNCERTAIN
    return BAND_REJECT


def decide_by_rules(score: float, allow_review: bool = True) -> tuple:
    """规则层的决策，返回 `(decision, band)`。

    decision ∈ SAME / UNCERTAIN / None（None = 落 REVIEW 档，由 LLM 层接手）。
    `allow_review=False`（LLM 关闭）时 REVIEW 档直接落 UNCERTAIN ——
    **绝不落 SAME**：宁可让人工多看一眼，也不把不确定性当成同义。
    """
    band = band_of(score)
    if band == BAND_SAME:
        return "SAME", band
    if band == BAND_REVIEW:
        return (None, band) if allow_review else ("UNCERTAIN", band)
    if band == BAND_UNCERTAIN:
        return "UNCERTAIN", band
    return None, band


# ---------- LLM 语义消歧（P2 实现，接口在 P1 就固定下来以便注入假实现） ----------


class Disambiguator:
    """消歧器接口。任何实现都只需提供 `judge(pair) -> dict`。

    返回值约定：`{"decision": "SAME"|"DIFFERENT"|"UNCERTAIN", "confidence": float|None,
    "reason": str, "error": str|None}`。

    **实现必须在一切异常路径上返回 UNCERTAIN**（超时 / 非法 JSON / 服务不可用 /
    未配置 key），永远不得返回 SAME —— 见 `fusion_config` 与测试 `test_fusion_resolver.py`。
    """

    def judge(self, pair: dict) -> dict:  # pragma: no cover - 接口
        raise NotImplementedError


DISAMBIGUATION_PROMPT = """你在做课程知识图谱的**实体消歧**。给你同一份课程文档中抽出的两个知识点，\
判断它们**是不是指同一个知识概念**。

【实体 A】
名称：{a_name}
类别：{a_category}
定义：{a_description}
可用于判断的文本上下文（**注意：这是事后重建的命中位置，不是原始抽取证据**）：
{a_context}

【实体 B】
名称：{b_name}
类别：{b_category}
定义：{b_description}
可用于判断的文本上下文：
{b_context}

【判断规则】
1. **名称相似不等于同一个概念**。必须结合定义与上下文判断。
2. 以下**不算**同一个概念：
   - 上位/下位关系（「树」与「决策树」、「原子」与「原子核」）
   - 整体与部分（「化合物」与「离子化合物」）
   - 属性与对象（「原子质量」与「原子」）
   - 依赖关系（「优先级调度」与「高响应比优先调度」）
3. 以下**算**同一个概念：同一概念的缩写与全称（BFS 与 广度优先搜索）、\
同一概念的两种中文说法、纯拼写差异（AVL树 与 AVL 树）。
4. 如果两者的定义互相矛盾（指的是不同的东西），返回 false。
5. **证据不足时必须返回 uncertain，不要猜。** 尤其在上下文不可用时。

【输出】只输出一个 JSON 对象，不要任何其他内容：
{{"same_entity": true 或 false, "confidence": 0到1的小数, "reason": "不超过50字的理由"}}
若两者是否同一概念**无法确定**，请把 confidence 设为 0 到 0.5 之间，并在 reason 里说明不确定的原因。"""


class LlmDisambiguator(Disambiguator):
    """调用 LLM 做语义消歧。**只对已召回的少量候选调用**，绝不全图扫描。

    降级契约（**每一条都必须落 UNCERTAIN，绝不落 SAME**）：
      - 未配置 `LLM_API_KEY` → 直接 UNCERTAIN，不发请求；
      - 请求超时 / 抛异常 → UNCERTAIN；
      - 返回内容不是合法 JSON / 字段不合规 → 串行重试 `LLM_RETRY` 次，仍失败则 UNCERTAIN。
    宁可让人工多看一眼，也不把"模型没说清"当成"是同一个实体"。
    """

    def __init__(self, client=None, model: str = None, timeout: int = None,
                 retries: int = None):
        from ...core.config import settings
        self._settings = settings
        self._model = model or settings.LLM_MODEL
        self._timeout = timeout or fusion_config.LLM_TIMEOUT
        self._retries = fusion_config.LLM_RETRY if retries is None else retries
        if client is not None:
            self.client = client
        elif settings.LLM_API_KEY:
            from openai import OpenAI
            self.client = OpenAI(api_key=settings.LLM_API_KEY,
                                 base_url=settings.LLM_API_BASE)
        else:
            self.client = None

    @property
    def available(self) -> bool:
        return self.client is not None

    @staticmethod
    def _render(pair: dict) -> str:
        def side(s):
            ctx = s.get("reconstructed_context") or {}
            if not ctx.get("reconstruction_ok"):
                text = f"（不可用：{ctx.get('reason') or '重建失败'}）"
            elif not ctx.get("chunk_indices"):
                text = "（该实体的名称未在正文中被命中）"
            else:
                snips = " / ".join(ctx.get("snippets") or []) or "（无片段）"
                text = f"命中分块 {ctx.get('chunk_indices')}：{snips}"
            return {"a_name": s.get("name") or "", "a_category": s.get("category") or "未知",
                    "a_description": (s.get("description") or "（无定义）")[:300],
                    "a_context": text}

        def side_b(s):
            d = side(s)
            return {"b_name": d["a_name"], "b_category": d["a_category"],
                    "b_description": d["a_description"], "b_context": d["a_context"]}

        text = DISAMBIGUATION_PROMPT.format(**side(pair["a"]), **side_b(pair["b"]))
        return text

    def judge(self, pair: dict) -> dict:
        if not self.available:
            return {"decision": "UNCERTAIN", "confidence": None,
                    "reason": "LLM 未配置（LLM_API_KEY 为空），无法做语义消歧",
                    "error": "llm_unavailable"}

        prompt = self._render(pair)
        last_error = None
        for attempt in range(self._retries + 1):
            try:
                from ...core.metrics import track_llm_call  # 复用既有 LLM 埋点
                with track_llm_call("fusion_disambiguation"):
                    resp = self.client.chat.completions.create(
                        model=self._model,
                        messages=[
                            {"role": "system",
                             "content": "你是严谨的知识图谱实体消歧助手，只输出 JSON。"},
                            {"role": "user", "content": prompt},
                        ],
                        temperature=0,
                        max_tokens=300,
                        timeout=self._timeout,
                    )
                content = (resp.choices[0].message.content or "").strip()
                return validate_verdict(parse_strict_json(content))
            except Exception as e:  # noqa: BLE001 - 一切异常都必须降级为 UNCERTAIN
                last_error = f"{type(e).__name__}: {e}"
                _logger.warning("融合消歧调用失败（第 %d/%d 次）: %s",
                                attempt + 1, self._retries + 1, last_error)

        return {"decision": "UNCERTAIN", "confidence": None,
                "reason": f"LLM 消歧失败（{last_error}），保守判为不确定",
                "error": last_error}


def parse_strict_json(content: str) -> dict:
    """严格解析消歧用的 JSON。

    与冻结抽取器的 `_parse_json` 同策略（剥 markdown 代码块 → 直接解析 → 裁出 `{...}`），
    但**本地实现、不 import 私有方法**：两边对"容错到什么程度"的要求不同，
    耦合在一起会让日后任何一侧的调整都互相牵连。
    """
    if not content:
        raise ValueError("空响应")
    text = content.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    import re
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        return json.loads(m.group(0))
    raise ValueError("无法从响应中解析出 JSON")


def validate_verdict(raw) -> dict:
    """把 LLM 的原始返回校验成内部结构；任何不合规都抛 ValueError。

    校验点：`same_entity` 必须是 bool、`confidence`（若有）必须落在 [0,1]。
    宁可判为「非法 → UNCERTAIN」，也不猜模型的意图。
    """
    if not isinstance(raw, dict):
        raise ValueError("响应不是 JSON 对象")
    if "same_entity" not in raw:
        raise ValueError("缺少 same_entity 字段")
    same = raw.get("same_entity")
    if not isinstance(same, bool):
        raise ValueError(f"same_entity 不是布尔值: {same!r}")

    conf = raw.get("confidence")
    if conf is not None:
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            raise ValueError(f"confidence 不是数字: {conf!r}")
        if conf < 0 or conf > 1:
            raise ValueError(f"confidence 越界: {conf}")

    reason = raw.get("reason") or ""
    if not isinstance(reason, str):
        reason = str(reason)

    return {
        "decision": "SAME" if same else "DIFFERENT",
        "confidence": conf,
        "reason": reason.strip()[:200],
        "error": None,
    }
