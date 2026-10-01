"""融合模块的**唯一配置源**：所有权重、阈值、上限都在这里定义。

为什么集中：评分权重和阈值一旦散落到业务逻辑里，改一个数就要翻遍代码，
评测（`eval_entity_resolution.py`）也无法在不改业务代码的前提下做参数扫描。
每次 apply 会把本文件的当前取值快照进 `t_kp_fusion_run.config_json`，
事后可复现「这条融合当时用的什么参数」。

关于阈值的诚实说明：下面这些数值是**保守的工程参数**，不是经过验证的最优值。
它们的作用是「把自动融合压到极少数高置信场景，其余全部转人工」——
对本项目而言，错误融合比漏融合代价高得多。
"""

# ---------- 决策分档阈值 ----------

# 达到此分才允许**自动**判 SAME（且不得命中硬负门）
AUTO_SAME_THRESHOLD = 0.86
# [REVIEW_LO, AUTO_SAME) 之间送 LLM 语义消歧；LLM 不可用时落 UNCERTAIN
REVIEW_THRESHOLD = 0.62
# [RECALL_LO, REVIEW_THRESHOLD) 之间只作为人工复核候选；低于 RECALL_LO 不建候选行
RECALL_THRESHOLD = 0.45

# ---------- 评分权重 ----------
#
# 只有**有区分度**的信号才进加权平均：词面、上下文、名称-描述互含、embedding。
# 类别一致性**刻意不做加项**，而是乘性惩罚（见 TYPE_FACTOR_*）——
# 实测：在一个 68 个知识点、类别几乎全是「概念」的文档里，把「类别相同」当成 +0.15
# 的加项，等于给**所有**实体对垫了一层地板分，一跑就是 300 多对候选。
# 同理，「本文档只有一个分块」时上下文对任何一对都恒为 1.0，也必须判为不可用。
W_LEXICAL = 0.40    # 词面相似度（jieba 分词 Jaccard 与 difflib 序列比值的较大者）
W_CONTEXT = 0.20    # 重建的文本命中上下文是否落在同一/相邻 chunk
W_DESC = 0.30       # 名称与对方描述互含（缩写↔全称的主要零依赖信号）
W_SEMANTIC = 0.10   # embedding 余弦（可选层，不可用时该项权重按比例归零）

# ---------- 类别不一致的乘性惩罚 ----------

TYPE_FACTOR_SAME = 1.0    # 类别相同：不奖不罚
TYPE_FACTOR_NEAR = 0.85   # 概念 ↔ 方法（分块不同时模型常给出不同类别）
TYPE_FACTOR_CONFLICT = 0.60  # 其余跨类

# ---------- 子串关系惩罚 ----------
#
# 「原子 / 原子核」「金属 / 类金属」「树 / 决策树」在知识图谱里绝大多数是**上下位**，
# 但纯词面相似度对它们给分极高（序列比值 0.8+）。按长度比区分两种情形：
#   短名只是长名的一个修饰前缀（长度比小）→ 大概率是上/下位，重罚
#   两者长度接近、只差一个后缀（如「…命名法」/「…命名」）→ 可能是同一实体的两种写法，轻罚
SUBSTRING_RATIO = 0.70
SUBSTRING_PENALTY_FAR = 0.35
SUBSTRING_PENALTY_NEAR = 0.75

# ---------- 名称-描述互含的强度 ----------

# 纯包含式命中要求词至少有这么多字符：2 字的中文词（「原子」「元素」）
# 几乎会出现在任何相关条目里，作为同义证据太弱，反而是噪声的主要来源。
DESC_MIN_TERM_LEN = 3

# 命中「显式等价式」（文本里直接写出「甲（乙）」）时给分数的**托底值**。
# 落在 REVIEW 档（0.62 ~ 0.86）：既不丢真阳性，也不会仅凭一条括号注释就自动融合。
STRONG_EQUIVALENCE_FLOOR = 0.70

# 两个节点**规范化后同名**（如 cnn / CNN，MERGE 键用的是原始名，所以它们能并存）
# 时的托底值。这类是抽取阶段按原始名建节点的漏网之鱼，恰恰是融合最该处理的一类，
# 不能因为类别被判得不一致就压到召回线以下、连人工复核都看不到。
SAME_NORM_FLOOR = 0.70

# ---------- 候选上限（控制 LLM 调用量与响应体积） ----------

# 每个源实体最多保留的候选数
TOP_K_PER_SOURCE = 5
# 单次扫描最多产出的候选对数
MAX_CANDIDATES_PER_RUN = 200
# 单次扫描最多送 LLM 的候选对数
MAX_LLM_PAIRS = 60
# 文档内实体数超过此值时才启用分桶 blocking（小文档直接两两比较更快）
BLOCKING_THRESHOLD = 400

# ---------- 硬负门 ----------

# 两实体间存在这些关系时，它们更可能是上下位/依赖关系而非同义，禁止自动融合
NEGATIVE_RELATION_TYPES = ("CONTAINS", "PRECEDES", "APPLIES_TO")
# 手工创建的节点（is_manual）默认不允许被当作**源**折掉
BLOCK_MANUAL_AS_SOURCE = True

# ---------- LLM 语义消歧 ----------

# 关闭时，REVIEW 档直接落 UNCERTAIN（**绝不落 SAME**）
LLM_ENABLED = True
# 单次请求超时（秒）。与抽取共用 LLM_* 配置，但超时单独给，避免拖长管理端等待。
LLM_TIMEOUT = 30
# 非法 JSON 时的串行重试次数（与抽取器的「失败块重试一次」同一策略）
LLM_RETRY = 1

# ---------- 重建上下文 ----------

# 重建方式标识。**必须**在 API 与前端标注为「重建的文本命中上下文」，
# 不得表述为「原始抽取证据」——抽取阶段根本没有保留 chunk 溯源。
CONTEXT_METHOD = "rechunk_exact_name_hit"


def snapshot() -> dict:
    """把当前阈值/权重冻结成可落库的 JSON 快照（写入 run.config_json）"""
    return {
        "auto_same_threshold": AUTO_SAME_THRESHOLD,
        "review_threshold": REVIEW_THRESHOLD,
        "recall_threshold": RECALL_THRESHOLD,
        "weights": {
            "lexical": W_LEXICAL,
            "context": W_CONTEXT,
            "desc": W_DESC,
            "semantic": W_SEMANTIC,
        },
        "type_factor": {
            "same": TYPE_FACTOR_SAME,
            "near": TYPE_FACTOR_NEAR,
            "conflict": TYPE_FACTOR_CONFLICT,
        },
        "substring_penalty": {
            "ratio": SUBSTRING_RATIO,
            "far": SUBSTRING_PENALTY_FAR,
            "near": SUBSTRING_PENALTY_NEAR,
        },
        "strong_equivalence_floor": STRONG_EQUIVALENCE_FLOOR,
        "desc_min_term_len": DESC_MIN_TERM_LEN,
        "top_k_per_source": TOP_K_PER_SOURCE,
        "max_candidates_per_run": MAX_CANDIDATES_PER_RUN,
        "max_llm_pairs": MAX_LLM_PAIRS,
        "llm_enabled": LLM_ENABLED,
    }
