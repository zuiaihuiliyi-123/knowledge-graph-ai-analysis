"""
LLM知识提取服务：实体识别 + 关系提取
"""
import json
import logging
import re
import asyncio
from typing import List, Tuple
from openai import OpenAI
from ..core.config import settings
from ..core.database import VALID_RELATION_TYPES
from ..core.metrics import track_llm_call
from ..utils.text_processor import chunk_text_for_llm

_logger = logging.getLogger(__name__)


# 知识提取的 Prompt 模板（决策树式关系判定，强化学习依赖与文本顺序的区分）
# v1.3：对齐人工标注口径（gold_第7章_树）。收紧 CONTAINS（删整体-部分）、重写
# APPLIES_TO 与 RELATED_TO 的方向规则、PRECEDES 增加反例、实体增加类别总称要求。
EXTRACTION_PROMPT = """你是高校课程知识图谱构建专家。请从给定课程文本中抽取「可用于知识图谱与学习路径推荐」的知识点实体，并识别实体之间明确存在的语义关系。

## 核心原则
宁可少抽取，也不要臆造关系。禁止仅凭文本先后顺序、常识或名称相似性推断关系。
若对某条关系证据不足，直接不输出该关系。
每输出一条关系前自问：「换一个领域专家，只看 evidence 原文，会不会认同这条关系的类型与方向？」不确定就删除。

## 一、实体要求
每个实体包含：
- name：知识点名称，简洁、规范、稳定
- category：只能是「概念」「定理」「公式」「方法」之一（无法归类时用「概念」）
- description：一句话描述该知识点在课程中的含义

category 判定（易错区，务必注意）：
- 具体的遍历方式、算法步骤、操作、表示法 → 「方法」。例如：前序遍历、中序遍历、后序遍历、层序遍历、广度优先遍历、深度优先遍历、旋转、插入节点、数组表示，全部属于「方法」，不得标为「概念」。
- 「遍历」「旋转」这类类别性总称 → 「概念」。
- 数据结构、术语定义、性质名词 → 「概念」。例如：二叉树、队列、平衡因子、高度。

实体粒度：
- 一个实体应是一个「独立可学习的知识点」，不要过度拆分，也不要堆砌冗余修饰。
- 同义词、英文缩写与中文全称属于同一知识点时，只保留一个规范名称。例如 CNN 与「卷积神经网络」合并为「卷积神经网络」。
- 名称不要带「算法」「方法」「结构」等冗余后缀，除非该词是固定术语本身（如「二分查找」不要写成「二分查找算法」）。
- 名称不要用裸动词（如「插入」「删除」「查找」），必须带操作对象（如「插入节点」「删除节点」）。
- 不要抽取：人名、学校、公司、页码、章节标题本身、「本节」「上述方法」等指代词、普通名词、单纯例子名。
- 不要抽取代码示例中的函数名、变量名、运算符、类名、关键字等程序符号（如 ==、equals()、BUILD_TREE()、public、private），它们不是课程知识点。
- 不要抽取代码注释/代码清单里出现的操作描述短语（如「初始化二叉树」「构建二叉搜索树」），它们只是某段代码的用途说明，不是独立知识点。
- 注意抽取文中明确出现的「类别性总称」：当文本说「前序、中序、后序遍历统称/属于遍历」时，「遍历」这类上位总称本身也是一个实体，必须单独抽取（它是关系链的上位节点，漏抽会连带漏掉一批关系）。同理「右旋与左旋统称旋转」中的「旋转」。
- 注意抽取定义句中的上位类别词：如「树是一种非线性数据结构」中的「非线性数据结构」、「红黑树是一种平衡二叉搜索树」中的「平衡二叉搜索树」，这类总称同样是关系链的上位节点。
- 文中明确提出的方法论/思想（如「分治」）若作为方法讲解，应抽取为「方法」。

## 二、关系类型（仅限 4 种，英文大写，方向严格）
PRECEDES / CONTAINS / RELATED_TO / APPLIES_TO

## 三、关系判定必须按下述顺序（决策树）
对任意两个知识点，按顺序判断：

1. 「X 是 Y 的一种/子类/特殊形式/变体/属于 Y」或「X 是 Y 的典型操作」？ → CONTAINS（source 是上位概念 Y，方向 source→target，详见第五节）
2. 否则，文本明确表达「学习 source 是理解 target 的必要前提」？ → PRECEDES（方向 source→target）
3. 否则，source 是方法/算法/技术，且文本明确说明其用于实现/解决/完成 target 代表的问题/任务？ → APPLIES_TO（方向 source→target，判定边界见第六节）
4. 否则，文本明确说明二者存在密切关联（工具-主体、并列类似）？ → RELATED_TO（方向约定见第七节）
5. 以上均不满足或证据不足 → 不输出关系

## 四、PRECEDES 只表示「学习依赖」，不表示「文本顺序」
只有文本明确表达以下语义才用 PRECEDES：学习 B 前须掌握 A；A 是理解 B 的基础；B 建立在 A 之上；掌握 A 后才能学 B。
典型触发词：「基础」「先修」「前提」「掌握…之后」「进一步学习」。

反例（以下禁止推断为 PRECEDES）：
- 「本章首先介绍线性表，然后介绍栈」→ 这是叙述顺序，不是学习依赖，不得输出「线性表 PRECEDES 栈」。
- 「卷积神经网络是神经网络的一种」→ 是包含关系，必须用「神经网络 CONTAINS 卷积神经网络」，不得用 PRECEDES。
- 「算法运行后生成结果」→ 时间先后，不是 PRECEDES。
- 「X 是理解 Y 的关键」「通过 X 判断 Y」「X 是 Y 的重要指标」→ 这只是"X 有助于理解 Y"，不等于"必须掌握 X 才能学 Y"，不得输出 X PRECEDES Y。
- 「回顾 X 的定义，下面介绍 Y」「上一节介绍了 X」→ 章节引用/复习指令，不是学习依赖声明。

## 五、CONTAINS 的方向与边界（最高频错误区，务必逐条核对）
### 5.1 方向规则（source 是上位概念，target 是下位概念）
- 「X 是 Y 的一种 / 特殊形式 / 变体 / 属于 Y」「Y 包含 X」「Y 包括 X」「Y 的常见类型有 X」→ **Y CONTAINS X**（上位概念在前，绝不反向）
  例：「栈是一种操作受限的线性表」→ 线性表 CONTAINS 栈
  例：「AVL 树是二叉搜索树的一种」→ 二叉搜索树 CONTAINS AVL 树
  例：「前序遍历是深度优先遍历的一种」→ 深度优先遍历 CONTAINS 前序遍历
  例：「完美二叉树也是一棵完全二叉树」→ 完全二叉树 CONTAINS 完美二叉树
- 「X 是 Y 的操作」（句式如「Y 的 X 操作」「Y 支持 X 操作」）→ **Y CONTAINS X**
  仅当 X 是直接对 Y 执行的基本操作（插入、删除、查找、遍历等，句式以 Y 为操作对象）。
  例：「二叉搜索树支持插入节点、查找节点、删除节点操作」→ 二叉搜索树 CONTAINS 插入节点、二叉搜索树 CONTAINS 查找节点、二叉搜索树 CONTAINS 删除节点
  反例：「AVL 树通过旋转保持平衡」——旋转是维护平衡的手段、不是对 AVL 树执行的增删查操作，不得输出 AVL 树 CONTAINS 旋转。

### 5.1 附：历史上反复出错的输出（逐条核对，严禁出现）
- 严禁输出「AVL 树 CONTAINS 二叉搜索树」——正确为 二叉搜索树 CONTAINS AVL 树
- 严禁输出「AVL 树 CONTAINS 平衡二叉树」——正确为 平衡二叉树 CONTAINS AVL 树
- 严禁输出「完美二叉树 CONTAINS 完全二叉树」——正确为 完全二叉树 CONTAINS 完美二叉树

### 5.2 输出前方向自检（每条 CONTAINS 必做）
把关系读成「target 是 source 的一种/子类/典型操作」，若语义不通，而读成「source 是 target 的一种」才通，
说明方向写反了，必须翻转 source 与 target。翻转后仍不确定就删除该关系。

### 5.3 不是 CONTAINS 的情况（严禁）
- 整体-部分结构（「二叉树由根节点、叶节点组成」「链表由节点构成」）：部分只是结构的组成单元、而非"X 是 Y 的一种"类型关系时，不得输出 CONTAINS。只有当"X 是 Y 的一种/子类"（如「完全二叉树是二叉树的一种」）才成立。
  点名禁止（这些是"组成/属性"而非"类型包含"，一律不建立任何关系）：
  - 二叉树 与 节点/根节点/叶节点/左子节点/右子节点/边/层/度/高度/深度 之间
  - 节点 与 左子节点/右子节点 之间
  - 操作关系中的主体必须是正文明确的对应结构（如「二叉搜索树的插入操作」→ 二叉搜索树 CONTAINS 插入节点），不得把操作挂到泛化主体上（「二叉树 CONTAINS 插入节点」不成立）
- 「X 具有属性/特征/指标 Y」（如「二叉树具有高度」「AVL 树用平衡因子衡量平衡」）：Y 是 X 的属性而非 X 的一种，不得输出 X CONTAINS Y。
- 「X 通过 Y 机制维持性质」（如「AVL 树通过旋转保持平衡」）：Y 是维护机制而非 X 的类型，不得输出 X CONTAINS Y（但「右旋、左旋是旋转的两种基本操作」→ 旋转 CONTAINS 右旋 成立）。
- 「X 使用公式/函数 Y 进行表示或计算」（如「数组表示使用映射公式」）：Y 是 X 的计算工具，不得输出 X CONTAINS Y。
- 「要学习 X，必须先掌握 Y」→ Y PRECEDES X，不是 CONTAINS
  例：「要学习多态，必须先掌握继承」→ 继承 PRECEDES 多态

## 六、APPLIES_TO 的判定（含「实现机制」的正确映射）
只有当 source 是方法/算法/技术类知识点、且文本明确说明其被用于实现、解决、完成 target 代表的问题/任务时，才能使用 APPLIES_TO（方向 source→target）。

「X 基于 Y 实现」「X 通过 Y 实现」「X 借助 Y 实现」「X 用 Y 实现」句式：
- Y 是方法/算法/技术 → **Y APPLIES_TO X**
  例：「深度优先遍历通常基于递归实现」→ 递归 APPLIES_TO 深度优先遍历（严禁输出反向）
  例：「前序遍历基于递归实现」→ 递归 APPLIES_TO 前序遍历
- Y 是数据结构等非方法类概念 → 降级为 RELATED_TO（见第七节）；证据不足则不输出
  例：「广度优先遍历借助队列实现」→ 队列 RELATED_TO 广度优先遍历，**严禁 APPLIES_TO**

「X 可用于实现 Y」（X 是技术/结构，Y 是应用对象）→ X APPLIES_TO Y
  例：「二叉搜索树可用于实现多级索引」→ 二叉搜索树 APPLIES_TO 多级索引

方法论思想贯穿某对象的设计（如「二叉树体现分治思想」「分治思想应用于二叉树」）→ 方法 APPLIES_TO 对象
  例：「二叉树体现一分为二的分治逻辑」→ 分治 APPLIES_TO 二叉树

严禁的输出：
- 「X 用于表示 Y」「X 是 Y 的存储方式/表示法」（如「数组表示用于存储完全二叉树」）：表示法与被表示对象的关系不是 APPLIES_TO，若证据不足则不输出。

严禁的输出（违反方向）：
- 深度优先遍历 APPLIES_TO 递归、广度优先遍历 APPLIES_TO 队列、中序遍历 APPLIES_TO 深度优先遍历（这些方向全部颠倒；「X 是 Y 的一种」走 CONTAINS，不是 APPLIES_TO）

不要为了覆盖所有语义而强行映射到现有四类关系；证据不足时宁可不输出。

## 七、RELATED_TO 的方向约定（从严输出）
仅用于「明确密切相关、但既非上下位、也非学习前置、也非应用」的情况，且文本有明确关联表述。
方向约定：
- 工具/支撑方为 source，主体/被支撑方为 target：
  例：「广度优先遍历借助队列实现」→ 队列 RELATED_TO 广度优先遍历
  例：「二叉树可用链表表示」→ 链表 RELATED_TO 二叉树
- 并列类似关系（「与…类似」「工作原理一致」「互为镜像」「互为对称」）时，source 取正文中先出现的一方：
  例：「红黑树与 AVL 树类似」→ AVL 树 RELATED_TO 红黑树
  例：「右旋与左旋互为镜像对称」→ 右旋 RELATED_TO 左旋
- 主体与其思想来源（「X 的查找思想源于二分查找」）→ X RELATED_TO 思想来源
  例：「二叉搜索树的查找思路与二分查找一致」→ 二叉搜索树 RELATED_TO 二分查找
- 方向无法确定时宁可不输出。

严禁的输出：
- 仅因两个概念出现在同一段就输出 RELATED_TO。
- 「X 与 X 序列」这类名称派生关系（如 中序遍历 与 中序遍历序列）不构成 RELATED_TO 证据。
- 「X 的时间复杂度」「X 的性能」等属性关联不得输出。
- 两个知识点仅通过第三方间接联系（如完美二叉树与广度优先遍历仅都关联层序遍历）→ 不输出直接关系。
- 「X 与其维护机制/属性/表示工具」（如 AVL 树与旋转、完美二叉树与高度、数组表示与映射公式）不建 RELATED_TO。
- 仅共享词根不构成关联证据（如「二分查找」与「查找节点」都含"查找"二字，不代表二者存在 RELATED_TO 关系）。

## 八、每条关系必须携带证据与置信度
- evidence：支持该关系的原文短句（必须来自输入文本，不得编造）
- confidence：0~1 的置信度，如实反映证据强弱，不确定就降低

## 九、输出约束
- source 与 target 必须都出现在 entities 中，且 source ≠ target
- 不重复输出相同 (source, type, target)；文本中因分块出现的重复段落，同一知识点/关系只输出一次
- type 只能是 PRECEDES / CONTAINS / RELATED_TO / APPLIES_TO
- 关系密度控制：参考密度为每 1000 字符正文 2~4 条关系。若本次输出明显超出该密度，说明混入了证据不足的关系，请删减到最明确、最有价值的子集。
- 输出前对每条关系自检：
  ① CONTAINS：target 是 source 的一种/子类/典型操作？（读反则翻转 source 与 target）
  ② PRECEDES：文本有明确的「学习 target 前必须掌握 source」表述？
  ③ APPLIES_TO：source 是方法/技术，target 是它实现/解决的问题或任务？
  ④ RELATED_TO：source 是工具/支撑方或正文先出现方？
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
# 8192 为 deepseek-chat 单次输出上限：4096 时知识点密集的块（如习题/能量章节）
# 会在 JSON 数组中途被截断（finish_reason=length），导致整块解析失败
_MAX_OUTPUT_TOKENS = 8192

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

# dropped_relations 里保留的明细条数上限。计数不受此限制（dropped_counts 恒为精确值），
# 仅防止极端情况下把预测文件撑爆。
# 丢弃原因代号（与 eval_accuracy.py 的 DROP_REASON_LABELS 对齐，勿单方面改名）：
#   invalid_relation_type / invalid_endpoint / synonym_split
#   dangling_endpoint / duplicate / low_confidence
_MAX_DROPPED_RECORDED = 500

# 常见缩写/别名 -> 规范名 映射（课程相关，按需扩充）。
# 示例：{"cnn": "卷积神经网络", "bp网络": "反向传播神经网络"}
# 注意：不同课程语境下缩写含义可能不同，默认留空，避免误伤。
_SYNONYM_MAP = {}

# 代码程序符号的黑名单：Java/C++ 等语言关键字。模型会偶发把代码示例里的
# 符号误抽为实体（如 public、private），在合并阶段统一兜底过滤，不依赖 LLM。
_CODE_KEYWORDS = {
    "PUBLIC", "PRIVATE", "PROTECTED", "CLASS", "VOID", "STATIC",
    "FINAL", "NEW", "RETURN", "INT", "DOUBLE", "FLOAT", "BOOLEAN",
    "STRING", "NULL", "TRUE", "FALSE", "THIS", "CONST",
}


def _is_code_symbol(name: str) -> bool:
    """判断实体名是否为程序符号（函数调用、运算符、关键字），而非课程知识点。"""
    if not name:
        return True
    if "(" in name or ")" in name:  # 函数调用风格：BUILD_TREE()、equals()
        return True
    if re.fullmatch(r"[=+\-*/%<>!&|^~]+", name):  # 纯运算符：== 等
        return True
    compact = name.replace(" ", "")
    if compact.isupper() and compact.isascii() and "_" in compact:
        # 全大写下划线标识符：UPDATE_HEIGHT 等
        return True
    if compact in _CODE_KEYWORDS:
        return True
    return False


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

        # 并发抽取：同步 LLM 客户端放在线程池中并行执行，避免逐块串行阻塞事件循环
        sem = asyncio.Semaphore(_MAX_CONCURRENCY)

        async def _run_one(chunk: str) -> dict:
            async with sem:
                return await asyncio.to_thread(self._extract_single, chunk)

        results = list(await asyncio.gather(*[_run_one(c) for c in chunks]))

        # 并发下偶发限流/超时/非法 JSON 会导致个别分块失败：对失败分块串行重试一次。
        # 单块文档同样要重试 —— 原先单块走的是「直接返回、不重试」的捷径，
        # 一次坏响应就让整篇文档判失败（报「无法从 LLM 返回内容中解析出 JSON」），
        # 而当时没有重新抽取的入口，用户只能删掉文档重新上传。
        for i, r in enumerate(results):
            if r.get("error"):
                results[i] = await asyncio.to_thread(self._extract_single, chunks[i])

        # 单块与多块统一走 _merge_results：实体按名去重、关系白名单/置信度校验都在那里
        # （其 docstring 声明「数据完整性约束不依赖 LLM，全部在此兜底」）。
        # 原实现单块直接返回原始解析结果，绕过了这些约束，会出现
        # 「DB 记 48 个实体、图里只有 33 个节点」（重复名在入图 MERGE 时被折叠）这类对不上的账。
        return self._merge_results(results)

    def _extract_single(self, text: str) -> dict:
        """对单个文本块执行提取（同步；由 extract 通过线程池并发调用）"""
        try:
            # 只包住「发起请求」这一行：解析失败仍算本次 LLM 调用成功（见 core/metrics 说明）
            with track_llm_call("extraction"):
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

        # 解析失败时留下原文片段：截断/夹杂说明文字的响应光看报错无法定位原因
        _logger.warning(
            "LLM 返回内容无法解析为 JSON（长度 %d），开头 300 字符：%s",
            len(content), content[:300],
        )
        raise ValueError("无法从 LLM 返回内容中解析出 JSON")

    def _merge_results(self, results: List[dict]) -> dict:
        """
        合并多个提取结果（数据完整性约束不依赖 LLM，全部在此兜底）：
        - 实体：轻量规范化后按规范名去重
        - 关系：类型白名单、source/target 实体存在、自环、重复、confidence 阈值过滤

        可观测性：本函数丢弃的每条关系都记入返回值的 dropped_relations /
        dropped_counts，连同原因与原始端点。此前这些 continue 全是静默的——
        关系一进这里就消失，上层看到的只是"少了 N 条"，无从知道少了什么、
        为什么少。评测侧据此才能把"抽到了但被阈值砍掉"（low_confidence）
        与"压根没抽到"（missed_core）区分开，否则后者会被系统性高估。
        """
        # 第一遍：规范化实体并按规范名去重（保留首个出现的字段）
        merged_entities = {}  # 规范名 -> entity dict
        dropped_entity_count = 0
        for result in results:
            for entity in result.get("entities", []):
                name = _normalize_entity_name(entity.get("name", ""))
                if not name:
                    dropped_entity_count += 1
                    continue
                # 代码程序符号兜底过滤（Prompt 已要求不抽，这里是数据完整性兜底）
                if _is_code_symbol(name):
                    dropped_entity_count += 1
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
        raw_relation_count = 0
        dropped = []
        dropped_counts = {}

        for result in results:
            for relation in result.get("relations", []):
                raw_relation_count += 1

                source = _normalize_entity_name(relation.get("source", ""))
                target = _normalize_entity_name(relation.get("target", ""))
                rel_type = (relation.get("type", "") or "").strip().upper()

                def _drop(reason_code: str, detail: str = "") -> None:
                    dropped_counts[reason_code] = dropped_counts.get(reason_code, 0) + 1
                    if len(dropped) < _MAX_DROPPED_RECORDED:
                        dropped.append({
                            "source": source or str(relation.get("source") or ""),
                            "type": rel_type,
                            "target": target or str(relation.get("target") or ""),
                            "reason_code": reason_code,
                            "detail": detail,
                        })

                # 关系类型白名单（不依赖 LLM）
                if rel_type not in VALID_RELATION_TYPES:
                    _drop("invalid_relation_type", f"type={rel_type!r}")
                    continue
                # 过滤空值
                if not source or not target:
                    _drop("invalid_endpoint", "端点为空")
                    continue
                # 归一化后构成自环 = "同义拆分"（模型输出了两个本应归一为同一实体的写法）。
                # 丢弃本身是正确行为，但必须记账：否则评测的错误归因里这一类恒为 0。
                if source == target:
                    _drop("synonym_split", f"归一化后自环: {source}")
                    continue
                # source/target 必须存在于实体列表（避免孤立关系 / 悬空边）
                if source not in entity_names or target not in entity_names:
                    _drop("dangling_endpoint", "端点不在 entities 中")
                    continue
                # 按 (source, type, target) 去重
                key = (source, rel_type, target)
                if key in seen_relations:
                    _drop("duplicate", "重复三元组（多因分块重叠）")
                    continue
                seen_relations.add(key)

                # confidence 解析 + 阈值过滤
                confidence = _parse_confidence(relation.get("confidence"))
                threshold = _RELATION_CONFIDENCE_THRESHOLDS.get(rel_type, 0.5)
                if confidence is not None and confidence < threshold:
                    _drop("low_confidence", f"confidence={confidence} < 阈值 {threshold}")
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

        merged = {
            "entities": list(merged_entities.values()),
            "relations": merged_relations,
            # 可观测性字段：不参与入图，仅供统计与准确率归因
            "raw_relation_count": raw_relation_count,
            "dropped_relations": dropped,
            "dropped_counts": dropped_counts,
            "dropped_entity_count": dropped_entity_count,
        }
        # 分块错误透传（最多携带前 3 条，避免信息过长），供上层判断抽取是否真正成功
        if errors:
            merged["error"] = "；".join(errors[:3])
        return merged
