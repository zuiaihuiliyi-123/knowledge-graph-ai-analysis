"""候选实体召回与评分。

**纯计算，不碰数据库、不调 LLM** —— 输入是已读出来的 nodes / edges / 上下文，
输出是候选对列表。这样它可以被穷尽地单测，也可以在管理端"预览"时不落任何库。

作用域：调用方**必须**只传入同一个 `(course_id, document_id)` 的节点与边。
本模块不再做跨文档校验（那是调用方与 API 层的职责），但所有输出都只包含
传入的节点 id —— 不给跨文档融合留任何口子。
"""
import difflib
import logging

from . import fusion_config
from .entity_context import context_similarity
from .entity_normalizer import extract_aliases, gloss_pairs, normalize

_logger = logging.getLogger(__name__)

# jieba 首次分词要加载词典，按规范名缓存分词结果避免重复开销
_TOKEN_CACHE = {}


def _tokens(text: str):
    if not text:
        return set()
    if text in _TOKEN_CACHE:
        return _TOKEN_CACHE[text]
    try:
        import jieba
        toks = {t.strip().lower() for t in jieba.lcut(text) if t.strip()}
    except Exception:  # noqa: BLE001 - 分词器不可用时退回整串，不影响正确性
        toks = {text.lower()}
    _TOKEN_CACHE[text] = toks
    return toks


def build_edge_index(edges: list) -> tuple:
    """返回 `(degree, pair_types)`：
    degree[kp_id] 度数；pair_types[frozenset({a,b})] = 该对实体之间出现过的关系类型集合。
    """
    degree, pair_types = {}, {}
    for e in edges or []:
        s, t = e.get("source"), e.get("target")
        if not s or not t or s == t:
            continue
        degree[s] = degree.get(s, 0) + 1
        degree[t] = degree.get(t, 0) + 1
        key = frozenset((s, t))
        pair_types.setdefault(key, set()).add(e.get("type"))
    return degree, pair_types


def build_profiles(nodes: list, edges: list, contexts: dict = None) -> list:
    """把图节点整理成统一的内部实体上下文对象（EntityProfile）。

    字段全部**来自图谱真实存在的属性**；图谱里没有的信息（例如 chunk 溯源）
    一律留空或标记为不可用，**不伪造**。
    """
    degree, _ = build_edge_index(edges)
    contexts = contexts or {}
    profiles = []
    for n in nodes or []:
        props = n.get("properties") or {}
        name = n.get("label") or ""
        description = n.get("description") or ""
        kp_id = n.get("id")
        if not kp_id or not name:
            continue
        ctx = contexts.get(kp_id)
        profiles.append({
            "kp_id": kp_id,
            "name": name,
            "original_name": name,
            "norm": normalize(name),
            "category": props.get("category") or "",
            "type": n.get("type") or "",
            "description": description,
            "is_manual": bool(props.get("is_manual")),
            "confidence": props.get("confidence"),
            "created_at": props.get("created_at"),
            "degree": degree.get(kp_id, 0),
            "aliases": extract_aliases(name, description),
            "context": ctx,
        })
    return profiles


def _lexical(a: dict, b: dict) -> float:
    """词面相似度：分词 Jaccard 与序列比值的较大者，再按子串关系打折。

    「原子 / 原子核」「金属 / 类金属」这种一个是另一个真子串的情况，
    序列比值会高达 0.8+，但它们在知识图谱里绝大多数是**上下位**而非同义。
    按长度比区分：短名只是长名的一个修饰前缀（比值小）→ 重罚；
    两者长度接近、只差一个后缀（「…命名法」/「…命名」）→ 轻罚。
    """
    na, nb = a["norm"], b["norm"]
    if not na or not nb:
        return 0.0
    ta, tb = _tokens(na), _tokens(nb)
    jac = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    base = max(jac, ratio)

    if na != nb and (na in nb or nb in na):
        shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
        len_ratio = len(shorter) / max(1, len(longer))
        penalty = (fusion_config.SUBSTRING_PENALTY_FAR
                   if len_ratio < fusion_config.SUBSTRING_RATIO
                   else fusion_config.SUBSTRING_PENALTY_NEAR)
        base *= penalty
    return base


def _type_factor(a: dict, b: dict) -> float:
    """类别一致性 —— **乘性惩罚**，不是加项。

    做成加项时，一个类别几乎全是「概念」的文档会给所有实体对白送一层地板分，
    实测直接产出 300 多对候选。做成乘子后，类别相同不奖不罚、跨类才打折。
    """
    ca, cb = a.get("category"), b.get("category")
    if not ca or not cb:
        return fusion_config.TYPE_FACTOR_SAME
    if ca == cb:
        return fusion_config.TYPE_FACTOR_SAME
    if {ca, cb} == {"概念", "方法"}:
        # 「概念」与「方法」在不同分块里常被模型判成不同类别
        return fusion_config.TYPE_FACTOR_NEAR
    return fusion_config.TYPE_FACTOR_CONFLICT


def _name_terms(p: dict) -> set:
    """该实体的全部称呼（规范化后的集合）：名称 + 规范名 + 别名"""
    return {normalize(t) for t in
            ([p.get("name"), p.get("norm")] + list(p.get("aliases") or [])) if t}


def _self_gloss_terms(p: dict) -> set:
    """**它自己**的定义/名称里给出的、与它等价的其它写法。

    例：`CNN` 的定义是「卷积神经网络（CNN）是一类…」，文本里
    `卷积神经网络（CNN）` 这一对直接说明了两者指同一个东西 —— 于是
    CNN 的等价写法集合里就有「卷积神经网络」。
    """
    nk = normalize(p.get("name") or "")
    if not nk:
        return set()
    out = set()
    for text in (p.get("description") or "", p.get("name") or ""):
        for x, y in gloss_pairs(text):
            if normalize(x) == nk:
                out.add(normalize(y))
            elif normalize(y) == nk:
                out.add(normalize(x))
    # **正文里**的等价式也要算（`运动学（Kinematics）` 这种常写在正文而非定义里）。
    # 少了这一支，「中文名 ↔ 英文名/缩写」这一整类真同义对会整体漏掉。
    ctx = p.get("context") or {}
    for t in (ctx.get("gloss_partners") or []):
        out.add(normalize(t))
    return {t for t in out if t}


def _equivalence_signal(a: dict, b: dict) -> tuple:
    """**显式等价式**：文本直接陈述了「甲（乙）」，而不是"名字看起来像"。

    这是本模块最强的零依赖信号 —— 它不依赖任何外部分词/词典/向量，
    也不需要模型。代价是它只能覆盖"作者写出来了"的那部分，召回有限。
    """
    ta = _self_gloss_terms(a) & _name_terms(b)
    tb = _self_gloss_terms(b) & _name_terms(a)
    if ta and tb:
        return 1.0, f"双方定义互相给出对方写法（{sorted(ta)[:1]} / {sorted(tb)[:1]}）"
    if ta:
        return 1.0, f"本方定义中直接写出对方名称（{sorted(ta)[0]}）"
    if tb:
        return 1.0, f"对方定义中直接写出本方名称（{sorted(tb)[0]}）"
    return 0.0, ""


def _containment_signal(a: dict, b: dict) -> tuple:
    """弱信号：名称与对方描述只是**普通包含**。

    **"包含"太容易成立了** —— 2 字的中文词（「原子」「元素」）几乎会出现在
    任何相关条目里，正是候选噪声的主要来源。故要求词长 ≥ DESC_MIN_TERM_LEN，
    且强度只给 0.5（永远不足以单独促成 SAME）。
    """
    txt_a = a.get("description") or ""
    txt_b = b.get("description") or ""
    for t in _name_terms(a):
        if len(t) >= fusion_config.DESC_MIN_TERM_LEN and t in txt_b:
            return 0.5, f"「{t}」出现在对方定义中"
    for t in _name_terms(b):
        if len(t) >= fusion_config.DESC_MIN_TERM_LEN and t in txt_a:
            return 0.5, f"「{t}」出现在本方定义中"
    return 0.0, ""


def score_pair(a: dict, b: dict, pair_types: dict, semantic=None) -> dict:
    """给一对实体打分。返回 detail 字典（含 score / 各分项 / blocked 原因）。"""
    detail = {
        "lexical": 0.0, "context": None, "type": _type_factor(a, b),
        "desc": 0.0, "semantic": None, "blocked": None,
        "notes": [],
    }

    # ---------- 硬负门：命中即抑制，不看分数 ----------
    rels = (pair_types or {}).get(frozenset((a["kp_id"], b["kp_id"]))) or set()
    neg = rels & set(fusion_config.NEGATIVE_RELATION_TYPES)
    if neg:
        # 用抽取器自己的结构信号当负样本：存在上下位/依赖关系的一对实体，
        # 更可能是「不同的两个知识点」而不是「同一个的两种写法」。成本为零且相当准。
        detail["blocked"] = f"两者之间存在 {sorted(neg)} 关系（上下位/依赖，非同义）"
        detail["score"] = 0.0
        return detail
    if fusion_config.BLOCK_MANUAL_AS_SOURCE and a.get("is_manual"):
        detail["blocked"] = "源节点为人工创建（is_manual），默认不允许自动折叠"
        detail["score"] = 0.0
        return detail

    # ---------- 各分项 ----------
    detail["lexical"] = _lexical(a, b)

    ctx_score, ctx_note = context_similarity(a.get("context"), b.get("context"))
    detail["context"] = ctx_score
    if ctx_note:
        detail["notes"].append(ctx_note)

    equiv_score, equiv_note = _equivalence_signal(a, b)
    if equiv_score:
        detail["desc"] = equiv_score
        detail["strong_equivalence"] = True
        detail["notes"].append(equiv_note)
    else:
        desc_score, desc_note = _containment_signal(a, b)
        detail["desc"] = desc_score
        detail["strong_equivalence"] = False
        if desc_note:
            detail["notes"].append(desc_note)

    if semantic is not None:
        try:
            detail["semantic"] = float(semantic(a, b))
        except Exception:  # noqa: BLE001 - 语义层是可选增强，失败即视为不可用
            detail["semantic"] = None

    # ---------- 加权平均 × 类别惩罚 ----------
    # 不可用的分项（上下文无区分度 / 重建失败 / 未启用 embedding）**按其权重整体剔除**，
    # 而不是当成 0 分 —— 否则「拿不到上下文」会被误当成「上下文不匹配」，
    # 把本该进人工复核的候选压到阈值以下。
    used = [(w, s) for w, s in (
        (fusion_config.W_LEXICAL, detail["lexical"]),
        (fusion_config.W_CONTEXT, detail["context"]),
        (fusion_config.W_DESC, detail["desc"]),
        (fusion_config.W_SEMANTIC, detail["semantic"]),
    ) if s is not None and w > 0]
    total_w = sum(w for w, _ in used)
    base = (sum(w * s for w, s in used) / total_w) if total_w else 0.0
    detail["base"] = round(base, 6)
    score = base * detail["type"]

    # 显式等价式（「卷积神经网络（CNN）」这种直接陈述）托底到复核档。
    # 为什么不直接给到 SAME：这类模式偶尔会命中「（见表 3）」式的括注，
    # 而本项目里错误融合的代价远高于漏融合 —— 托底到复核档，
    # 由人工或 LLM 做最后一步确认，既不丢真阳性，也不会自动写错。
    if detail.get("strong_equivalence"):
        score = max(score, fusion_config.STRONG_EQUIVALENCE_FLOOR)
        detail["identity_level"] = True

    # 规范化后同名（原始名不同）：抽取阶段只按原始名建节点，所以 cnn / CNN 这类
    # 会并存成两个节点。这正是融合最该处理的一类，同样托底到复核档 ——
    # 但**不给到 SAME**，因为"同名不同义"确实存在，必须让人或模型看一眼。
    if a.get("norm") and a["norm"] == b.get("norm") and a.get("name") != b.get("name"):
        score = max(score, fusion_config.SAME_NORM_FLOOR)
        detail["identity_level"] = True
        detail["notes"].append(f"规范化后同名（{a['norm']}），但原始名不同")

    detail["score"] = score
    return detail


def generate_candidates(profiles: list, edges: list, top_k: int = None,
                        max_pairs: int = None, semantic=None) -> list:
    """生成候选对。

    - 小文档（节点数 < BLOCKING_THRESHOLD）直接两两比较：当前最大的文档也只有几十个
      知识点，n² 是几千次纯字符串计算，比维护倒排索引更简单也更快。
    - 大文档走分桶 blocking，杜绝全量 n²。
    - 每个源只保留分数最高的 top_k 个；全局再截断到 max_pairs。
    """
    top_k = top_k or fusion_config.TOP_K_PER_SOURCE
    max_pairs = max_pairs or fusion_config.MAX_CANDIDATES_PER_RUN
    _, pair_types = build_edge_index(edges)

    n = len(profiles)
    if n < 2:
        return []

    if n < fusion_config.BLOCKING_THRESHOLD:
        pairs = ((i, j) for i in range(n) for j in range(i + 1, n))
    else:
        pairs = _blocked_pairs(profiles)

    best = {}
    for i, j in pairs:
        a, b = profiles[i], profiles[j]
        if a["kp_id"] == b["kp_id"]:
            continue
        detail = score_pair(a, b, pair_types, semantic=semantic)
        if detail["blocked"] or detail["score"] < fusion_config.RECALL_THRESHOLD:
            continue
        # 以「较小 kp_id 作为 source」临时定向；真正的规范节点由 choose_canonical
        # 在生成候选时（以及 apply 时重新）决定，这里只保证同一对只出现一次。
        key = frozenset((a["kp_id"], b["kp_id"]))
        if key not in best or detail["score"] > best[key][0]["score"]:
            best[key] = (detail, a, b)

    out = []
    for detail, a, b in best.values():
        out.append({"a": a, "b": b, "detail": detail})

    # 排序：分数降序 → 端点 kp_id 升序（并列时结果仍然确定）
    out.sort(key=lambda c: (-c["detail"]["score"], c["a"]["kp_id"], c["b"]["kp_id"]))

    # 配给：**任意一端**已经占了 top_k 个候选就跳过。
    # 高分的先入座，所以被挤掉的一定是低分候选 —— 这也顺带限制了后续 LLM 的调用量。
    used, trimmed = {}, []
    for c in out:
        ka, kb = c["a"]["kp_id"], c["b"]["kp_id"]
        if used.get(ka, 0) >= top_k or used.get(kb, 0) >= top_k:
            continue
        used[ka] = used.get(ka, 0) + 1
        used[kb] = used.get(kb, 0) + 1
        trimmed.append(c)
        if len(trimmed) >= max_pairs:
            _logger.warning("候选数达到全局上限 %d，后续低分候选被截断（不会进人工复核）",
                            max_pairs)
            break
    return trimmed


def _blocked_pairs(profiles: list):
    """大文档的分桶：按「规范名键 + 类别的首字符」分桶，桶内两两比较。

    分桶只用来**缩小范围**，不用于判定相等；因此宁可桶划粗一点（漏掉候选比
    错合并更可接受，且被漏掉的仍会在下一轮的其它桶里出现）。
    """
    from .entity_normalizer import normalize_key
    buckets = {}
    for i, p in enumerate(profiles):
        key = normalize_key(p["name"])
        for sig in {key[:2], key[:1], (p.get("category") or "")[:1]}:
            buckets.setdefault(sig, []).append(i)
    seen = set()
    for members in buckets.values():
        for x in range(len(members)):
            for y in range(x + 1, len(members)):
                i, j = members[x], members[y]
                pair = (min(i, j), max(i, j))
                if pair in seen:
                    continue
                seen.add(pair)
                yield pair
