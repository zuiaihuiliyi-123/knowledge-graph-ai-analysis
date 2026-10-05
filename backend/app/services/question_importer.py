"""
试题文档导入：确定性规则解析（Scope D / P2）

目标：把「题目区 + 答案区」版式的试题文档（如 OpenStax 中文版"测试题与答案"）
解析成可直接入库的题目结构，**零 LLM 成本、可复现**；解析不确定的题目只做标记，
绝不让错误内容静默进池。

真实版式（实测样本 `第1章_语句_测试题与答案_中文版.txt`）：
    第1章 题目
    ##1.1 背景 (Backgrounds)
    1 . 动画中描述了多少种程序？        ← 题号格式不统一："1 ." 与 "31."
       a. 3                            ← 选项缩进混乱（3~8 空格），字母大小写混用
          b. 4
    30. ... ```python ... ```          ← 题干里夹代码块，必须保护
    ##答案
     1.b                              ← 答案区是紧凑 "题号.字母"，只有字母没有选项文本
     2.a

因此解析分四步：
    ① 切「题目区 / 答案区」 ② 题目区按题号切块（保护代码块）
    ③ 答案区解析出 题号→字母/文字  ④ 关联 + 题型判定 + 质量标记 + 去重
"""

import re

# ---------- 正则（对真实版式做过适配） ----------

# 中文数字：章节号与分节序号都要用（第1章 / 第五章 / 二、）
_CN_NUM = r"[0-9一二三四五六七八九十百零]+"

# 答案区起始行：##答案 / 第3章 答案 / 答案 / 参考答案 / 答案与解析 / Answer Key
# 实测版式比想象的杂，除裸标题外还出现过两类：
#   ① 中文分节序号：`## 二、参考答案`（中文文档里极常见的分节写法）
#   ② 章节标题夹描述词：`第五章 定积分单元测试 参考答案`
# 共同约束：**整行以「答案」收尾**（其后最多再跟一个冒号）。这条是防误判的关键——
# 文档标题行 `第1章 …—— 测试题与答案（中文版）`、正文行 `答案：78个质子；117个中子`、
# `4. 检查你的答案。答案合理吗？单位正确吗？` 都因「不以答案收尾」而被排除；
# 描述词一段再排除句读标点（。！？；），避免把正文句子误当分界。
ANSWER_SECTION_RE = re.compile(
    r"^\s*#{0,4}\s*"
    r"(?:(?:第\s*" + _CN_NUM + r"\s*[章节篇部分]?|[（(]?\s*" + _CN_NUM
    + r"\s*[)）]?\s*[、.．])\s*)?"
    r"[^：:\n。！？；]{0,24}?"
    r"(?:参考)?答案(?:与解析|及解析)?"
    r"\s*[:：]?\s*$"
    r"|^\s*#{0,4}\s*Answer\s*Key\s*[:：]?\s*$",
    re.I,
)
# 题号起始：兼容 "1 ." / "31." / "1、"
QUESTION_NO_RE = re.compile(r"^\s*(\d{1,3})\s*[.、．]\s*")
# 选项：行首 `A.` / `A、` / `A)`，以及括号式 `（A）` / `(A)`。
# 括号式**只认大写**：教材正文里 `(a)(b)` 是小题号而非选项，把小写也收进来会把
# 正文小问误判成选项（实测 doc 107 / 159 正是这种写法）。
OPTION_RE = re.compile(
    r"^\s*(?:([A-Ha-h])\s*[.、．)）]\s*(.*)$"
    r"|[（(]\s*([A-H])\s*[)）]\s*(.*)$)"
)
# 章节标题：##1.1 背景 (Backgrounds)
SECTION_RE = re.compile(r"^\s*#{0,4}\s*\d+\.\d+.*$")
# 答案条目：1.b / 1. b / 1、B
ANSWER_ENTRY_RE = re.compile(r"^\s*(\d{1,3})\s*[.、．]?\s*([a-hA-H])\s*$")
# 一个题号跟多个字母（多选答案）：`7. A、C` / `7. A C` / `7. AC`
# 连写形态只认大写（`7.AC`、`7.ABC`）：小写连写更可能是文字答案（如 `2. face`），
# 大写才是选项键——与 OPTION_RE 里「括号式只认大写」是同一条判断标准。
ANSWER_LETTERS_RE = re.compile(
    r"^\s*(\d{1,3})\s*[.、．]\s*("
    r"[A-H]{2,}"
    r"|[a-hA-H](?:\s*[、,，/\s]\s*[a-hA-H])+"
    r")\s*$")
# 一行多个答案条目（排版被压扁时出现）：`1. D . 2. A 3. B 4. C 5. C`
# 只在单条目写法都不匹配、且本行能拆出 ≥2 条时才启用，避免拆错单条目行。
ANSWER_INLINE_ENTRIES_RE = re.compile(r"(\d{1,3})\s*[.、．]?\s*([a-hA-H])")
# 答案条目（文字型）：1. 答案：xxx / 5. 参考解答：xxx
ANSWER_TEXT_RE = re.compile(r"^\s*(\d{1,3})\s*[.、．]\s*(?:答案|解答|参考解答|参考答)\s*[:：]?\s*(.+)$")
CODE_FENCE_RE = re.compile(r"^\s*```[A-Za-z0-9_+\-]*\s*$")

JUDGE_TRUE = {"对", "正确", "是", "true", "t", "y", "yes", "1"}
JUDGE_FALSE = {"错", "错误", "否", "false", "f", "n", "no", "0"}
BLANK_MARK_RE = re.compile(r"_{2,}|（）|\(\s*\)")

# 质量标记（写进 t_question.import_status，教师端据此复核）
STATUS_READY = "READY"
STATUS_REVIEW = "NEEDS_REVIEW"
STATUS_ANSWER_MISSING = "ANSWER_MISSING"


def _norm_stem(text: str) -> str:
    """题干归一化（仅用于去重比较）：去题号、空白、标点"""
    text = QUESTION_NO_RE.sub("", text or "")
    text = re.sub(r"[\s　]+", "", text)
    text = re.sub(r"[，。；：、（）()【】\[\]“”\"'？！,.;:!?]", "", text)
    return text[:200]


def _is_answer_entry_line(line: str) -> bool:
    """该行是否"像一条答案"（分界判定的准入条件，与 parse_answers 的写法保持一致）。

    四种写法见 parse_answers 的 docstring；压扁行要求能拆出 ≥2 条才算，
    否则正文里形如 `2. A` 的题号行会被误认为答案区。
    """
    if ANSWER_TEXT_RE.match(line) or ANSWER_ENTRY_RE.match(line) or ANSWER_LETTERS_RE.match(line):
        return True
    return len(ANSWER_INLINE_ENTRIES_RE.findall(line)) >= 2


def split_answer_section(text: str) -> tuple:
    """① 切分「题目区 / 答案区」。

    取**最后一个**满足条件的「答案」标记行：其后续 30 个非空行里至少 2 行像答案条目——
    避免把正文里出现的"答案"二字误当分界。找不到则返回 (全文, "")。
    """
    lines = text.splitlines()
    candidates = [i for i, l in enumerate(lines) if ANSWER_SECTION_RE.match(l)]
    for idx in reversed(candidates):
        look = [l for l in lines[idx + 1: idx + 60] if l.strip()]
        hits = sum(1 for l in look[:30] if _is_answer_entry_line(l))
        if hits >= 2:
            return "\n".join(lines[:idx]), "\n".join(lines[idx + 1:])
    return text, ""


def _collect_question_starts(lines: list) -> tuple:
    """扫描候选题号行 → [(行号, 题号, 该行剩余文本)]，并返回被判为"疑似章节标题"的行

    切块策略的关键：**题号优先 + 连续性约束**。
    - 章节标题（含无 `#` 的 `1.8 xxx`）先行识别并排除，避免被当成题号 1（幽灵题）；
    - 题号必须"等于预期下一题"或"大于上一个题号"（允许新章节从 1 重新开始）；
    - 兜底：题号后无分隔符（`12 如果用户输入…`）仅当等于预期下一题时才认。
    这样代码围栏/状态漂移都不可能再吞掉题号（围栏只在块内做内容处理）。
    """
    starts, sections = [], []
    expected_next, last_no = 1, 0
    for idx, raw in enumerate(lines):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith(">"):
            continue
        if SECTION_RE.match(line):                 # 章节标题优先（含无 # 形式）
            sections.append((idx, line.strip().lstrip("#").strip()))
            continue
        m = QUESTION_NO_RE.match(line)
        rest = QUESTION_NO_RE.sub("", line, count=1) if m else ""
        number = None
        if m:
            number = int(m.group(1))
        else:
            m_seq = re.match(r"^\s*(\d{1,3})\s+(?=\S)", line)     # 无分隔符兜底
            if m_seq and int(m_seq.group(1)) == expected_next:
                number, rest = int(m_seq.group(1)), line[m_seq.end():]
        if number is None:
            continue
        if not (number == 1 or number == expected_next or number > last_no):
            continue                               # 不满足连续性 → 视为题干内容，不算新题
        starts.append((idx, number, rest.strip()))
        expected_next, last_no = number + 1, number
    return starts, sections


def parse_question_blocks(question_text: str) -> tuple:
    """按题号切块（题号优先策略；围栏只用于块内内容处理，不参与切块）"""
    lines = question_text.splitlines()
    starts, sections = _collect_question_starts(lines)
    blocks, preamble = [], []
    if starts and starts[0][0] > 0:
        preamble = [l for l in lines[:starts[0][0]] if l.strip()]

    for i, (idx, number, rest) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(lines)
        body = [rest] + [l.rstrip() for l in lines[idx + 1:end]]
        stem_lines, options, in_code = [], [], False
        for line in body:
            if not line.strip():
                continue
            if CODE_FENCE_RE.match(line):          # 围栏行本身不进题干，块内代码内容保留
                in_code = not in_code
                continue
            # markdown 标题不是题干内容，直接跳过。非 `1.2` 形式的标题（如
            # `## 三、准确度与有效数字`）不会被 SECTION_RE 收走，以前会被并进
            # 上一题的 stem 里（实测：题干尾部挂着一整行 `## 三、…`）。
            # 代码块内的 `#` 是注释，不能跳（in_code 时不生效）。
            if not in_code and line.lstrip().startswith("#"):
                continue
            if not in_code:
                m_opt = OPTION_RE.match(line)
                if m_opt:
                    # OPTION_RE 两个分支各占两组：无括号式用 1/2，括号式用 3/4
                    key = m_opt.group(1) or m_opt.group(3)
                    text_opt = m_opt.group(2) if m_opt.group(2) is not None else m_opt.group(4)
                    options.append({"key": key.upper(),
                                    "text": (text_opt or "").strip()})
                    continue
            stem_lines.append(line)
        # 章节归属：取该题之前最后一个章节标题
        section = None
        for s_idx, s_title in sections:
            if s_idx < idx:
                section = s_title
            else:
                break
        blocks.append({"number": number, "stem_lines": stem_lines,
                       "options": options, "section": section})
    return blocks, preamble, sections



def parse_answers(answer_text: str) -> dict:
    """③ 答案区：题号 → {letters:[...], text:str}

    支持四种写法，按优先级判定（前一种匹配上就不再看后面的）：
      1. 文字型：`3. 答案：xxx` / `3. 参考解答：xxx`
      2. 单字母：`3.b` / `3. B`
      3. 一个题号多个字母（多选）：`3. A、C` / `3. AC`
      4. 一行多个条目（排版被压扁时的形态）：`1. D . 2. A 3. B 4. C`
    """
    answers = {}

    def _add(no: int, letter: str) -> None:
        rec = answers.setdefault(no, {"letters": [], "text": ""})
        letter = letter.upper()
        if letter not in rec["letters"]:
            rec["letters"].append(letter)

    for line in (answer_text or "").splitlines():
        if not line.strip():
            continue
        m_text = ANSWER_TEXT_RE.match(line)
        if m_text:
            answers[int(m_text.group(1))] = {"letters": [], "text": m_text.group(2).strip()}
            continue
        m_entry = ANSWER_ENTRY_RE.match(line)
        if m_entry:
            _add(int(m_entry.group(1)), m_entry.group(2))
            continue
        m_letters = ANSWER_LETTERS_RE.match(line)
        if m_letters:
            no = int(m_letters.group(1))
            for ch in re.findall(r"[a-hA-H]", m_letters.group(2)):
                _add(no, ch)
            continue
        # 压扁行：必须能拆出 ≥2 条才认，否则交给上一步/留作未匹配
        pairs = ANSWER_INLINE_ENTRIES_RE.findall(line)
        if len(pairs) >= 2:
            for num, letter in pairs:
                _add(int(num), letter)
    for rec in answers.values():
        rec["letters"] = sorted(rec["letters"])
    return answers


def _assemble(blocks: list, answers: dict) -> list:
    """④ 关联答案 + 题型判定 + 质量标记"""
    items = []
    for b in blocks:
        stem = "\n".join(x for x in b["stem_lines"] if x.strip()).strip()
        if not stem:
            continue
        options = list(b["options"])
        ans = answers.get(b["number"]) or {"letters": [], "text": ""}
        letters, text_ans = ans["letters"], (ans["text"] or "").strip()
        warnings, q_type, answer = [], None, None

        if options:
            if len(letters) == 1:
                q_type, answer = "SINGLE", letters[0]
            elif len(letters) > 1:
                q_type, answer = "MULTI", letters
                warnings.append("multi_type_needs_review")   # 多选自动判定需教师确认
            else:
                q_type, answer = "SINGLE", None              # 有选项无答案 → 待补
        else:
            low = text_ans.lower()
            if text_ans and low in JUDGE_TRUE:
                q_type, answer = "JUDGE", "true"
            elif text_ans and low in JUDGE_FALSE:
                q_type, answer = "JUDGE", "false"
            elif text_ans and BLANK_MARK_RE.search(stem):
                # 有填空标记 + 文字答案 → 填空题（按空标记个数生成空位定义）
                blanks = [{"key": i + 1, "label": f"第{i + 1}空", "hint": "", "score": 0,
                           "answer": text_ans}
                          for i in range(max(1, len(BLANK_MARK_RE.findall(stem))))]
                q_type, options, answer = "FILL", blanks, None
            elif text_ans:
                q_type, answer = "ESSAY", text_ans             # 文字答案且无选项 → 解答题
            elif len(letters) == 1:
                q_type, answer = "JUDGE", ("true" if letters[0] == "A" else "false")
                warnings.append("judge_type_guessed")          # 靠 a/b 猜的判断/是非题
            else:
                q_type, answer = "ESSAY", None                 # 无选项无答案 → 待补
                warnings.append("no_options_no_answer")

        # 填空题的答案存在 options（空位定义）里，answer 按约定为 None——
        # 这里必须看空位，否则每道填了答案的填空题都会被标成「缺答案」
        # （原写法是个恒等于 bool(answer) 的三元式，FILL 必中此坑）。
        if q_type == "FILL":
            has_answer = any((b.get("answer") or "").strip() for b in options)
        else:
            has_answer = bool(answer)
        if not has_answer:
            warnings.append("answer_missing")

        status = STATUS_READY
        if "answer_missing" in warnings:
            status = STATUS_ANSWER_MISSING
        elif warnings:
            status = STATUS_REVIEW

        confidence = 1.0
        if "answer_missing" in warnings:
            confidence -= 0.4
        if not options and q_type != "FILL":
            confidence -= 0.2
        if "multi_type_needs_review" in warnings:
            confidence -= 0.1
        if "judge_type_guessed" in warnings:
            confidence -= 0.15

        items.append({
            "number": b["number"],
            "section": b.get("section"),
            "q_type": q_type,
            "stem": stem,
            "options": options,
            "answer": answer,
            "analysis": "",
            "warnings": warnings,
            "import_status": status,
            "confidence": round(max(0.0, confidence), 2),
        })
    return items


def audit(text: str) -> dict:
    """解析覆盖审计：把「答案区声明的题数」与「实际解析出的题数」对齐。

    真实文档里答案条目数与题号数完全一致，因此可用它作为**真值基线**：
    - missing_numbers：答案区有、但没解析出来的题号（= 漏题）
    - ghost_numbers：解析出来、但答案区没有的题号（= 幽灵题）
    这两个清单会写进预览 stats 并展示给教师，做到"漏了哪几题"透明可见。
    """
    question_text, answer_text = split_answer_section(text or "")
    answers = parse_answers(answer_text)
    blocks, _, _ = parse_question_blocks(question_text)
    got = {b["number"] for b in blocks}
    missing = sorted(set(answers) - got)
    ghost = sorted(n for n in got if n not in answers) if answers else []
    return {
        "answer_entries": len(answers),
        "parsed_questions": len(blocks),
        "missing_numbers": missing,
        "ghost_numbers": ghost,
        "ok": not missing and not ghost,
    }


def parse_text(text: str, max_questions: int = None) -> dict:
    """解析整篇试题文档 → 结构化题目候选（不写库）。

    返回 {"items": [...], "stats": {...}, "source": "RULE"}
    每项含 number/section/q_type/stem/options/answer/warnings/import_status/confidence，
    另有 duplicate_of（与前面某题题干重复时）。
    """
    question_text, answer_text = split_answer_section(text or "")
    blocks, preamble, sections = parse_question_blocks(question_text)
    answers = parse_answers(answer_text)
    items = _assemble(blocks, answers)
    coverage = audit(text)

    seen = {}
    duplicates = 0
    for item in items:
        key = _norm_stem(item["stem"])
        if not key:
            continue
        if key in seen:
            item["duplicate_of"] = seen[key]
            item["warnings"].append("duplicate_stem")
            if item["import_status"] == STATUS_READY:
                item["import_status"] = STATUS_REVIEW
            duplicates += 1
        else:
            seen[key] = item["number"]

    if max_questions:
        items = items[:max_questions]

    by_type, with_answer = {}, 0
    for item in items:
        by_type[item["q_type"]] = by_type.get(item["q_type"], 0) + 1
        if "answer_missing" not in item["warnings"]:
            with_answer += 1

    return {
        "items": items,
        "source": "RULE",
        "stats": {
            "total": len(items),
            "by_type": by_type,
            "with_answer": with_answer,
            "answer_missing": sum(1 for i in items if "answer_missing" in i["warnings"]),
            "needs_review": sum(1 for i in items if i["import_status"] == STATUS_REVIEW),
            "duplicates": duplicates,
            "sections": [s[1] for s in sections[:20]],
            "answer_section_found": bool(answer_text.strip()),
            "preamble_lines": len(preamble),
            # 覆盖审计（答案区声明数 vs 实际解析数；missing/ghost 会展示给教师）
            "answer_entries": coverage["answer_entries"],
            "missing_numbers": coverage["missing_numbers"],
            "ghost_numbers": coverage["ghost_numbers"],
            "coverage_ok": coverage["ok"],
        },
    }

