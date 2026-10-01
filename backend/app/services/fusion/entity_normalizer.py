"""实体名称规范化与别名抽取。

**复用而非重写**：规范化的口径直接取自冻结的 V1.1 抽取器
（`knowledge_extractor._normalize_entity_name`）—— 抽取阶段用它把实体名对齐，
融合阶段若另起一套口径，两边对"什么算同一个写法"的判断就会不一致
（例如抽取器把纯英文名统一大写，融合却保留原样，会出现明明同名却匹配不上的怪象）。

**不覆盖原名**：规范化结果只用于候选召回与分桶，原始名称始终保留在
`EntityProfile.original_name` 与 Neo4j 节点属性里，供展示与审计。

**明确不做**：
- 不做无差别删标点 —— `C++` 与 `C` 必须保持不同（抽取器保留 `+`，这里也不动）。
- 不把名称压成不可读的串（那会让审计无从下手）。
"""

import re

# 复用冻结抽取器的规范化口径（只导入，不修改该文件）
from ..knowledge_extractor import _normalize_entity_name

# 2~6 位纯 ASCII 字母：可能是缩写（BFS / CNN / AVL），用于「与中文全称互含」判定。
# 上限 6 是经验值：更长的纯英文 token 通常是完整单词而非缩写。
_ABBREV_RE = re.compile(r"^[A-Za-z]{2,6}$")

# 名称后的括号注释：全称（缩写）/ 缩写（全称）两种写法都常见
_BRACKET_RE = re.compile(r"[（(]([^（()）]{1,40})[)）]")

# 中文里显式的等价说法
_ALIAS_PATTERNS = (
    re.compile(r"^(?P<a>.+?)[，,]\s*(?:又|也)?(?:称|叫|称为|称作)\s*(?P<b>.+)$"),
    re.compile(r"^(?P<a>.+?)\s*(?:即|也就是|或称)\s*(?P<b>.+)$"),
)


def normalize(name: str) -> str:
    """规范名（与抽取器同口径）。空/None 输入返回空串。"""
    return _normalize_entity_name(name or "")


def normalize_key(name: str) -> str:
    """分桶用的粗粒度键：在规范名基础上再去掉空白并统一小写。

    只用于「缩小比较范围」，**不用于判定相等** —— 是否同一实体永远由
    评分 + 硬负门 + （必要时）LLM 决定，绝不靠名字上的键相等。
    """
    return re.sub(r"\s+", "", normalize(name)).casefold()


def is_probable_abbreviation(name: str) -> bool:
    """是否可能是纯 ASCII 缩写（BFS / CNN / AVL）"""
    return bool(_ABBREV_RE.match((name or "").strip()))


# 「甲（乙）」形式的配对：甲是本名，乙是括号里的另一种写法。
# 左侧刻意限制长度与字符集，避免把「（见图 3.1）」这类括注当成等价式。
_GLOSS_PAIR_RE = re.compile(
    r"([^\s，,。；;：:、（）()\[\]【】]{1,20})\s*[（(]\s*([^（()）]{1,40}?)\s*[)）]")


def gloss_pairs(text: str) -> list:
    """抽出文本里**显式写出**的等价对 `[(甲, 乙), ...]`。

    覆盖两种写法：
      - `卷积神经网络（CNN）`  → ("卷积神经网络", "CNN")
      - `广度优先搜索，又称广度优先遍历` → ("广度优先搜索", "广度优先遍历")

    这是本模块**最强**的零依赖信号：它不是在"猜"两个名字像不像，
    而是文本本身**直接陈述了**这两种写法指同一个东西。
    """
    out = []
    for m in _GLOSS_PAIR_RE.finditer(text or ""):
        a, b = m.group(1).strip(), m.group(2).strip()
        if a and b and a != b:
            out.append((a, b))
    for pat in _ALIAS_PATTERNS:
        m = pat.match((text or "").strip())
        if m:
            a, b = m.group("a").strip(), m.group("b").strip()
            if a and b and a != b:
                out.append((a, b))
    return out


def extract_aliases(name: str, description: str = None) -> list:
    """从名称与描述里抽取别名候选（**只作为候选生成的线索，不直接当成同义**）。

    支持的显式写法：
      - 广度优先搜索（BFS） / 广度优先搜索(BFS)      → 名称括号里的部分
      - 广度优先搜索，又称广度优先遍历                → 中文等价说法
      - 描述里出现「X（Y）」形式的括号注释             → 描述括号里的部分

    返回值已去重、剔除与原名规范化后相同的项、保持出现顺序。
    """
    text = (name or "").strip()
    out = []

    for m in _BRACKET_RE.finditer(text):
        out.append(m.group(1).strip())
    for pat in _ALIAS_PATTERNS:
        m = pat.match(text)
        if m:
            out.append(m.group("b").strip())

    if description:
        head = description.strip()[:120]
        for m in _BRACKET_RE.finditer(head):
            out.append(m.group(1).strip())

    # 比对的基准既含完整名称，也含"去掉括号部分"的主干：
    # 「BFS（BFS）」这种别名与主干重复的写法应当被剔除，
    # 而「广度优先搜索（BFS）」里 BFS 相对主干是新增信息，要保留。
    seen = {normalize(text), normalize(_BRACKET_RE.sub("", text))}
    result = []
    for a in out:
        a = a.strip(" 　,，。;;")
        if not a:
            continue
        key = normalize(a)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(a)
    return result


def names_for_matching(name: str, aliases: list = None) -> list:
    """参与文本命中检索的名称集合：原名 + 规范化名 + 别名（去重，保持顺序）"""
    out, seen = [], set()
    for n in [name, normalize(name)] + list(aliases or []):
        n = (n or "").strip()
        if not n:
            continue
        key = normalize(n)
        if key in seen:
            continue
        seen.add(key)
        out.append(n)
    return out
