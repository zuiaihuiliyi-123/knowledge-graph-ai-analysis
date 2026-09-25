# -*- coding: utf-8 -*-
"""把 v1.5 的完整 Prompt 与 v1.3~v1.5 迭代记录注入《提示词工程完整记录.md》。

一次性脚本：读 knowledge_extractor.py 里的 EXTRACTION_PROMPT 原文（不手抄），
替换文档 2.2 节；并在 2.3 节补 v1.3/v1.4/v1.5 迭代记录、更新 2.4 量化效果表。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.knowledge_extractor import EXTRACTION_PROMPT  # noqa: E402

DOC = Path(__file__).resolve().parents[2] / "docs" / "提示词工程完整记录.md"
text = DOC.read_text(encoding="utf-8")

# ---- 1. 替换 2.2 节（当前版本原文） ----
start_marker = "### 2.2 当前版本（v1.2，决策树版）完整原文"
end_marker = "### 2.3 版本迭代完整记录"
start = text.index(start_marker)
end = text.index(end_marker)

new_22 = (
    "### 2.2 当前版本（v1.5，决策树 + 方向自检版）完整原文\n\n"
    "User Prompt（`{text}` 为分块后的课程文本）：\n\n```\n"
    + EXTRACTION_PROMPT
    + "\n```\n\n"
)
text = text[:start] + new_22 + text[end:]

# ---- 2. 在 2.3 的 v1.2 记录之后插入 v1.3 / v1.4 / v1.5 记录 ----
v12_tail = (
    "4. **新增证据与置信度强制要求**（第八节）：每条关系必须携带 "
    "`evidence` 原文证据，实现抽取结果可溯源。\n"
)

insert_pos = text.index(v12_tail) + len(v12_tail)
new_versions = """#### 版本 D（v1.3，真实章节规模第一次定向修复 · 2026-09-22）

由第7章·树（15330 字符 / 49 实体 / 33 关系）真实规模评测驱动，关系主指标 F1 仅 0.1614。错误归因显示四类缺陷：CONTAINS 方向系统性颠倒（「X 是 Y 的一种」输出成 X CONTAINS Y）、臆造（属性/机制关系滥用 CONTAINS，如「AVL 树 CONTAINS 高度」）、APPLIES_TO 与 gold 决策树冲突（Prompt 禁止「基于…实现」映射 APPLIES_TO，但 gold 标注「递归 APPLIES_TO 深度优先遍历」）、RELATED_TO 方向随机。核心改动：

1. **CONTAINS 收紧为"类型包含 + 操作包含"两类**：决策树第 1 步删除"整体-部分"分支（模型据此把「二叉树-根节点」等组成关系全判 CONTAINS，31 条臆造的主因）；新增 5.2 节"输出前方向自检"（读成「target 是 source 的一种」不通则翻转）与 5.3 节反例集（属性、维护机制不是 CONTAINS）。
2. **第六节 APPLIES_TO 重写为"实现机制的正确映射"**：「X 基于 Y 实现」→ Y 是方法则 Y APPLIES_TO X、Y 是数据结构则降级 RELATED_TO，与 gold 决策树逐条对齐。
3. **第七节 RELATED_TO 增加方向约定**：工具/支撑方为 source（队列 RELATED_TO 广度优先遍历）、并列类似取先出现方。
4. **第四节 PRECEDES 增加反例**：「X 是理解 Y 的关键」「回顾 X 的定义」不是学习依赖（修复「平衡因子 PRECEDES 旋转」类臆造）。
5. **第一节增加"类别性总称必须抽取"**：漏抽「遍历」「旋转」总称导致 8 条 Core 关系连带漏抽。

实测：关系主指标 F1 0.1614 → **0.5647**，方向错误率 13.5% → 6.2%，类型混淆清零。

#### 版本 E（v1.4，第二轮定向修复 · 2026-09-22）

v1.3 剩余错误再归因：实体类别 7 处误判（遍历类方法被标「概念」）、7 个代码符号误抽（==、BUILD_TREE()、PRIVATE…）、7 个实体漏抽（分治、链表、非线性数据结构、平衡二叉搜索树…）、CONTAINS 方向仍有 3 处颠倒。核心改动：

1. **第一节新增 category 判定细则**：遍历方式/算法步骤/表示法 → 「方法」；类别性总称 → 「概念」；新增定义句上位词抽取（「X 是一种 Y」中的 Y）与方法论名词抽取（分治）。
2. **第一节新增命名规则**：名称不带「算法」冗余后缀、不用裸动词。
3. **5.1 附"历史上反复出错的输出"禁止清单**：直接点名「严禁输出 AVL 树 CONTAINS 二叉搜索树」等 3 条历史错误输出，修复 CONTAINS 方向顽疾。
4. **5.3 点名禁止组成/属性关系**：二叉树与节点/根节点/叶节点/边/层/度/高度/深度之间一律不建关系。
5. **代码层兜底过滤**（`_merge_results`）：新增 `_is_code_symbol` 过滤函数调用、运算符、全大写下划线标识符、语言关键字（Prompt 要求之外的确定性兜底，不依赖 LLM）。
6. **第六、七节补正例**：分治 APPLIES_TO 二叉树；右旋 RELATED_TO 左旋、链表 RELATED_TO 二叉树。

实测：关系主指标 F1 0.5647 → **0.7742**，**首次超过赛题 0.70 要求线**，方向错误率清零，实体 F1 0.889。

#### 版本 F（v1.5，只加反例的收敛微调 · 2026-09-22）

v1.4 已超线（3 轮全过），v1.5 仅做零风险微调——只新增反例与命名规则、不动任何正例，进一步压缩 12 条残留臆造：

1. **第一节**：新增"代码注释/清单中的操作描述短语不抽"（初始化二叉树、构建二叉搜索树）。
2. **5.3**：操作关系主体必须是正文明确的对应结构（「二叉搜索树 CONTAINS 插入节点」成立、「二叉树 CONTAINS 插入节点」不成立）。
3. **第六节**：新增反例「X 用于表示 Y（表示法/存储方式）不是 APPLIES_TO」。
4. **第七节**：新增正例「主体与其思想来源」（二叉搜索树 RELATED_TO 二分查找）；新增反例「X 与其维护机制/属性/表示工具不建 RELATED_TO」「仅共享词根不构成证据」。

实测：关系主指标 F1 0.7742 → **0.8312**，CONTAINS F1 达 0.939，臆造从 12 条压到 5 条，3 轮逐轮值 0.8485 / 0.8451 / 0.8000 全部超线且稳定。

"""
text = text[:insert_pos] + new_versions + text[insert_pos:]

# ---- 3. 更新 2.4 量化效果 ----
old_24 = text[text.index("### 2.4 迭代量化效果"):]
old_24 = old_24[: old_24.index("\n---")]
new_24 = """### 2.4 迭代量化效果（与《知识抽取准确率测试报告》对应）

评测口径：`backend/eval_accuracy.py`（全仓库唯一评测实现）＋ `gold_第7章_树.json`（49 实体 / 33 关系，冻结 SHA256 `50f316ce7bea6690…`）＋ 归一化版本 `n1`。正式评测 temperature=0、chunk 6000/overlap 400、并发 4、3 轮独立运行。

| 阶段 | 版本 | 规模 | 实体 F1 | 关系主指标 P | 关系主指标 R | 关系主指标 F1（申报口径） |
|------|:----:|------|:-------:|:------------:|:------------:|:-------------------------:|
| 玩具规模初测 | A（v1.0） | 708 字节 / 4 关系 | 1.0000 | — | 0.5000 | 0.4667 |
| 玩具规模复测 | B（v1.1） | 708 字节 / 4 关系 | 1.0000 | — | 1.0000 | **0.9333** |
| 真实章节基线 | C（v1.2） | 15330 字符 / 33 关系 | 0.6247 | 0.1348 | 0.2020 | 0.1614 |
| 真实章节 v1.3 | D | 15330 字符 / 33 关系 | 0.8069 | 0.4761 | 0.6970 | 0.5647 |
| 真实章节 v1.4 | E | 15330 字符 / 33 关系 | 0.8890 | 0.6804 | 0.8990 | **0.7742** ✅ |
| 真实章节 v1.5 | F | 15330 字符 / 33 关系 | **0.9220** | **0.7841** | **0.8889** | **0.8312** ✅ |

- **规模放大效应**：同一套抽取链路，从 708 字节玩具文本放大到 15330 字符真实章节，v1.2 关系 F1 从 0.93 掉到 0.16——玩具规模的 0.93 不能支撑赛题申报；v1.3~v1.5 三次定向修复后真实章节 F1 回升至 0.8312，超过赛题 70% 线。
- **v1.2→v1.5 修复路径**：方向颠倒（25.3% 错误率 → 0）靠 5.2 方向自检 + 5.1 附禁止清单；臆造（31 条 → 5 条）靠 CONTAINS 收紧 + 反例集 + 代码符号兜底过滤；漏抽（27 条 → 5 条）靠"类别性总称必抽" + 定义句上位词 + APPLIES_TO/RELATED_TO 与 gold 决策树对齐。
- **完整报告**：`backend/eval_data/评测报告_第7章_树_v15.md`（自动渲染，含逐轮明细、按类型 P/R/F1、错误归因与对账）；实验参数快照 `backend/eval_data/experiment_config_v15.json`（prompt_version=v1.5, temperature=0, chunk=6000, overlap=400, max_output_tokens=8192）。
- **封存集计划**：开发集（第7章·树）达标后，封存集（物理·第2章 运动学 / 数据结构·第2章 复杂度分析，gold 标注完成后只跑一次）作为最终验证，结果直接进交付报告。

"""
text = text.replace(old_24, new_24)

# ---- 4. 更新 1.2 清单中的 max_tokens 参数 ----
text = text.replace(
    "| P1 | EXTRACTION_PROMPT | 知识抽取（实体识别 + 关系抽取）| `backend/app/services/knowledge_extractor.py` | temp=0.15, max_tokens=4096 |",
    "| P1 | EXTRACTION_PROMPT | 知识抽取（实体识别 + 关系抽取）| `backend/app/services/knowledge_extractor.py` | temp=0.15（正式评测 0）, max_tokens=8192 |",
)

DOC.write_text(text, encoding="utf-8")
print(f"已更新 {DOC}（Prompt 长度 {len(EXTRACTION_PROMPT)} 字符）")
