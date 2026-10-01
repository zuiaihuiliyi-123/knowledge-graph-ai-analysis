# -*- coding: utf-8 -*-
"""《系统使用说明文档》Markdown → LaTeX 转换器

用法：
    python md2tex.py docs/系统使用说明文档.md docs/系统使用说明文档.tex

为什么自己写而不直接用 pandoc：
1) 原文含大量 emoji（📷🅿️✅❌☐），XeLaTeX 无法渲染，需按语义替换为 LaTeX 宏；
2) 中文文档的管道表格需转成 tabularx 才能自动换行，否则必然溢出页面；
3) 块引用需转成带色块的提示框，且其中的表格需换成不可分页的版本。

对应关系（与《系统使用说明文档.md》的占位符约定一致）：
    📷 → \\shotmark{}   截图待补
    🅿️ → \\todomark{}   数据待补
    🔍 → \\verifymark{} 待确认
    ⚠️ → \\warnmark{}   注意
"""
import re
import sys

# ---------------------------------------------------------------- 字符映射

# emoji：XeLaTeX 无彩色 emoji 字体，必须按语义替换
EMOJI = {
    "\U0001F4F7": r"\shotmark{}",     # 📷 截图
    "\U0001F17F": r"\todomark{}",     # 🅿️ 数据待补
    "\U0001F50D": r"\verifymark{}",   # 🔍 待确认
    "⚠":     r"\warnmark{}",     # ⚠️ 注意
    "✅":     r"$\checkmark$",    # ✅
    "❌":     r"$\times$",        # ❌
    "☐":     r"$\square$",       # ☐
    "⬜":     r"$\square$",       # ⬜
    "️":     "",                 # 变体选择符，直接丢弃
}

# 带圈数字：U+2460-24FF 不在 xeCJK 默认 CJK 字符集内，
# 会落到西文字体上导致缺字，故统一降级为 (1) 形式
CIRCLED = {chr(0x2460 + i): "(%d)" % (i + 1) for i in range(20)}

# LaTeX 特殊字符。逐字符替换，插入的命令不会被二次处理
_SPECIAL = {
    "\\": r"\textbackslash{}",
    "{": r"\{", "}": r"\}",
    "$": r"\$", "&": r"\&", "#": r"\#",
    "^": r"\textasciicircum{}", "_": r"\_",
    "%": r"\%", "~": r"\textasciitilde{}",
}

# 表格单元格里被转义的竖线（\|）占位符，避免与分隔符混淆
PIPE = "\x00"

# 目录树 / 架构图使用的制表符，需等宽字体支持
BOXCHARS = set("─│├└┌┐┘┬┴┼┤┏┓┗┛")


def esc(s: str) -> str:
    """转义 LaTeX 特殊字符"""
    return "".join(_SPECIAL.get(c, c) for c in s)


def post(s: str) -> str:
    """在转义完成之后做字符替换（这些替换会引入 LaTeX 命令，不能再被转义）"""
    for k, v in EMOJI.items():
        s = s.replace(k, v)
    for k, v in CIRCLED.items():
        s = s.replace(k, v)
    return s.replace(PIPE, r"\textbar{}")


# ---------------------------------------------------------------- 行内元素

_CODE_RE = re.compile(r"`([^`]*)`")
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")


def inline(s: str) -> str:
    """渲染行内 Markdown：行内代码、加粗、斜体、链接。

    链接降级为纯文本：原文档的锚点（#3-2-3-…）是按手写编号生成的，
    而 LaTeX 会自动重排章节号，保留超链接反而会出现「文字写 3.2.3、
    实际跳转到别处」的错位。
    """
    out = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "`":
            j = s.find("`", i + 1)
            if j == -1:
                out.append(esc(c)); i += 1
            else:
                out.append(r"\texttt{" + esc(s[i + 1:j]) + "}")
                i = j + 1
        elif s.startswith("**", i):
            j = s.find("**", i + 2)
            if j == -1:
                out.append(esc("**")); i += 2
            else:
                out.append(r"\textbf{" + inline(s[i + 2:j]) + "}")
                i = j + 2
        elif c == "[":
            m = _LINK_RE.match(s, i)
            if m:
                out.append(inline(m.group(1)))       # 丢弃链接目标，保留文字
                i = m.end()
            else:
                out.append(esc(c)); i += 1
        elif c == "*":
            j = s.find("*", i + 1)
            if 0 < j < i + 200:
                out.append(r"\emph{" + inline(s[i + 1:j]) + "}")
                i = j + 1
            else:
                out.append(esc(c)); i += 1
        else:
            j = i
            while j < n and s[j] not in "`*[":
                j += 1
            out.append(esc(s[i:j]))
            i = j
    return "".join(out)


# ---------------------------------------------------------------- 表格

def split_row(line: str):
    """切分管道表格的一行；\\| 视为字面竖线"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    cells, cur, i = [], [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur.append(PIPE); i += 2          # 转义竖线，稍后还原
        elif s[i] == "|":
            cells.append("".join(cur)); cur = []; i += 1
        else:
            cur.append(s[i]); i += 1
    cells.append("".join(cur))
    return [c.strip() for c in cells]


def is_sep_row(line: str) -> bool:
    """判断是否表格分隔行 |---|---|"""
    body = line.strip().strip("|")
    return bool(body) and set(body) <= set("-: |")


def render_table(rows, inside_box: bool) -> str:
    """管道表格 → tabularx。

    全部列都用 X 列（等宽自动换行），否则中文内容必然溢出页面。
    框内的表格不能用 xltabular/longtable 系（它们无法放入 tcolorbox），
    因此统一用不可分页的 tabularx——本项目的表格均短于一页，安全。
    """
    header = split_row(rows[0])
    ncol = len(header)
    if ncol == 0:
        return ""

    body = [r for r in rows[1:] if not is_sep_row(r)]

    size = r"\small" if ncol <= 4 else r"\footnotesize"
    colspec = "".join(["X"] * ncol)

    lines = [
        r"\begin{center}",
        size,
        r"\begin{tabularx}{\linewidth}{@{}" + colspec + r"@{}}",
        r"\toprule",
        " & ".join(inline(c) for c in header) + r" \\",
        r"\midrule",
    ]
    for r in body:
        cells = split_row(r)
        cells += [""] * (ncol - len(cells))      # 补齐缺列
        lines.append(" & ".join(inline(c) for c in cells[:ncol]) + r" \\")
    lines += [r"\bottomrule", r"\end{tabularx}", r"\end{center}"]
    return "\n".join(lines)


# ---------------------------------------------------------------- 主转换

class Converter:
    def __init__(self, text: str):
        self.lines = text.split("\n")
        # front：正文之前（标题、修订记录、快速阅读）；appendix：附录
        self.front = True
        self.appendix = False
        self.toc_done = False
        self.title_done = False
        self.subtitle_done = False

    # ---------- 标题 ----------
    def heading(self, level: int, text: str) -> str:
        if self.front:
            # 第一个一级标题 = 文档主标题
            if level == 1 and not self.title_done:
                self.title_done = True
                return ("\n\\begin{center}\n{\\Huge\\bfseries %s\\par}\n"
                        "\\end{center}\n" % inline(text))
            # 紧随其后的第一个二级标题 = 副标题
            if level == 2 and self.title_done and not self.subtitle_done:
                self.subtitle_done = True
                return ("\n\\begin{center}\n{\\LARGE %s\\par}\n"
                        "\\end{center}\n\\vspace{1em}\n" % inline(text))
            if level == 1:
                # 出现第二个一级标题 = 正文开始（如「第一章 系统概述」），
                # 结束前置部分，交由下面的正文分支处理
                self.front = False
            else:
                # 前置部分的其余小节不参与编号
                return "\n\\%s*{%s}\n" % (
                    ("section", "subsection", "subsubsection")[level - 2], inline(text))

        # 进入正文：第一个「第X章」之前插入目录
        if level == 1:
            if not self.toc_done:
                self.toc_done = True
                self.front = False
                toc = "\\tableofcontents\n\\clearpage\n\n"
            else:
                toc = ""
            if text.strip() == "附录":
                self.appendix = True
                return toc + "\n\\chapter*{%s}\n\\addcontentsline{toc}{chapter}{%s}\n" % (
                    inline(text), inline(text))
            # 去掉「第一章」前缀，交由 LaTeX 自动编号
            title = re.sub(r"^第[一二三四五六七八九十百]+章\s*", "", text)
            return toc + "\n\\chapter{%s}\n" % inline(title)

        names = {2: "section", 3: "subsection", 4: "subsubsection"}
        name = names.get(level, "subsubsection")
        if self.appendix:
            # 附录内不编号，保留「附录 A」这类手写标识
            return "\n\\%s*{%s}\n" % (name, inline(text))
        # 正文标题里的手写编号（如「2.4.1」）必须去掉：
        # LaTeX 会按层级自动编号，保留就会出现「2.4.1 2.4.1 安装并启动 Neo4j」
        stripped = re.sub(r"^\d+(\.\d+)*[.、]?\s+", "", text)
        return "\n\\%s{%s}\n" % (name, inline(stripped))

    # ---------- 列表 ----------
    def render_list(self, items) -> str:
        out, stack = [], []
        for indent, ordered, text in items:
            while stack and indent < stack[-1][0]:
                out.append(r"\end{%s}" % ("enumerate" if stack[-1][1] else "itemize"))
                stack.pop()
            if not stack or indent > stack[-1][0]:
                env = "enumerate" if ordered else "itemize"
                out.append(r"\begin{%s}" % env)
                stack.append((indent, ordered))
            elif ordered != stack[-1][1]:
                out.append(r"\end{%s}" % ("enumerate" if stack[-1][1] else "itemize"))
                stack.pop()
                env = "enumerate" if ordered else "itemize"
                out.append(r"\begin{%s}" % env)
                stack.append((indent, ordered))
            out.append(r"\item " + inline(text))
        while stack:
            out.append(r"\end{%s}" % ("enumerate" if stack[-1][1] else "itemize"))
            stack.pop()
        return "\n".join(out)

    # ---------- 块级渲染 ----------
    def render(self, lines, inside_box=False) -> str:
        out, i, n = [], 0, len(lines)
        while i < n:
            raw = lines[i]
            line = raw.rstrip()
            stripped = line.strip()

            if not stripped:
                i += 1
                continue

            # --- 跳过手写的「目录」小节（由 \tableofcontents 替代）---
            if stripped.startswith("##") and stripped.lstrip("#").strip() == "目录":
                i += 1
                while i < n and not lines[i].lstrip().startswith("#"):
                    i += 1
                continue

            # --- 代码块 ---
            if stripped.startswith("```"):
                i += 1
                buf = []
                while i < n and not lines[i].strip().startswith("```"):
                    buf.append(lines[i]); i += 1
                i += 1
                out.append(self.verbatim(buf))
                continue

            # --- 标题 ---
            m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
            if m:
                if inside_box:
                    # 提示框内部不产生章节命令（会破坏编号，且部分盒子不接受），
                    # 只渲染为加粗小标题
                    out.append("\n\\textbf{%s}\\par\n" % inline(m.group(2).strip()))
                else:
                    out.append(self.heading(len(m.group(1)), m.group(2).strip()))
                i += 1
                continue

            # --- 水平线（仅作装饰，丢弃）---
            if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
                i += 1
                continue

            # --- 块引用 ---
            if stripped.startswith(">"):
                buf = []
                while i < n and lines[i].lstrip().startswith(">"):
                    buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                    i += 1
                out.append(self.callout(buf, inside_box))
                continue

            # --- 表格 ---
            if stripped.startswith("|"):
                buf = []
                while i < n and lines[i].strip().startswith("|"):
                    buf.append(lines[i]); i += 1
                out.append(render_table(buf, inside_box))
                continue

            # --- 列表 ---
            if re.match(r"^\s*([-*+]|\d+\.)\s+", raw):
                items = []
                while i < n:
                    l = lines[i]
                    if not l.strip():
                        # 列表中的空行：仅当下一行仍是列表项时才继续
                        if i + 1 < n and re.match(r"^\s*([-*+]|\d+\.)\s+", lines[i + 1]):
                            i += 1; continue
                        break
                    mm = re.match(r"^(\s*)([-*+]|\d+\.)\s+(.*)$", l)
                    if not mm:
                        break
                    indent = len(mm.group(1).replace("\t", "    "))
                    ordered = mm.group(2)[0].isdigit()
                    items.append((indent, ordered, mm.group(3)))
                    i += 1
                out.append(self.render_list(items))
                continue

            # --- 普通段落 ---
            buf = []
            while i < n:
                l = lines[i]
                s = l.strip()
                if (not s or s.startswith("#") or s.startswith("|")
                        or s.startswith(">") or s.startswith("```")
                        or re.match(r"^(-{3,})$", s)
                        or re.match(r"^\s*([-*+]|\d+\.)\s+", l)):
                    break
                buf.append(s); i += 1
            if buf:
                out.append("".join(inline(b) for b in buf) + "\n")

        return "\n".join(out)

    # ---------- 代码块 ----------
    def verbatim(self, buf) -> str:
        body = "\n".join(buf)
        is_diagram = bool(set(body) & BOXCHARS)
        opts = ["breaklines=true", "breakanywhere=true", "fontsize=\\small",
                "frame=single", "framesep=4pt", "rulecolor=\\color{codeframe}"]
        if is_diagram:
            # 架构图/目录树：不折行，否则框线会错位
            opts = ["fontsize=\\small", "frame=single", "framesep=4pt",
                    "rulecolor=\\color{codeframe}"]
        return "\\begin{Verbatim}[%s]\n%s\n\\end{Verbatim}\n" % (",".join(opts), body)

    # ---------- 提示框 ----------
    def callout(self, buf, inside_box) -> str:
        while buf and not buf[0].strip():
            buf.pop(0)
        while buf and not buf[-1].strip():
            buf.pop()
        if not buf:
            return ""

        joined = "\n".join(buf)
        has_code = any(l.strip().startswith("```") for l in buf)
        has_table = any(l.strip().startswith("|") for l in buf)

        # 块引用里嵌代码块：Verbatim 放进 tcolorbox 容易出错，降级为普通缩进块
        if has_code:
            return "\\begin{quote}\n\\small\n%s\n\\end{quote}\n" % self.render(buf, True)

        kind = "todobox"
        if not any(k in joined for k in ("\U0001F4F7", "\U0001F17F", "\U0001F50D", "⚠")):
            kind = "notebox"
        if has_table and kind == "notebox":
            kind = "todobox"      # 含表格的块统一走 todobox（配色无所谓，主要是排版参数）

        return "\\begin{%s}\n%s\n\\end{%s}\n" % (kind, self.render(buf, True), kind)

    def run(self) -> str:
        return self.render(self.lines)


PREAMBLE = r"""% !TEX program = xelatex
%=========================================================================
%  基于 AIGC 的课程知识图谱智能构建与学习导航系统 · 系统使用说明文档
%
%  由 md2tex.py 自动生成，请勿直接编辑本文件——
%  修订请改 docs/系统使用说明文档.md 后重新运行：
%      python md2tex.py docs/系统使用说明文档.md docs/系统使用说明文档.tex
%
%  ★ 编译方式：必须使用 XeLaTeX（Overleaf：Menu → Compiler → XeLaTeX）
%    默认的 pdfLaTeX 无法处理中文，会直接编译失败。
%=========================================================================
\documentclass[UTF8,a4paper,11pt]{ctexrep}

\usepackage{geometry}
\geometry{left=2.4cm,right=2.4cm,top=2.6cm,bottom=2.6cm}

\usepackage{xcolor}
\definecolor{notebar}{HTML}{4F6EF7}    % 提示框左侧竖条（蓝）
\definecolor{notebg}{HTML}{F2F5FF}     % 提示框底色
\definecolor{todobar}{HTML}{E6A23C}    % 待办框左侧竖条（橙）
\definecolor{todobg}{HTML}{FFF8EC}     % 待办框底色
\definecolor{codeframe}{HTML}{D0D7E2}  % 代码块边框

\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{booktabs}
\usepackage{tabularx}
\usepackage{array}
\usepackage{enumitem}
\usepackage{fvextra}       % 提供可换行的 Verbatim
\usepackage{newunicodechar}
\usepackage{needspace}

% ★ 等宽字体：必须换成一个含制表符（─│┌┐└┘）的字体，默认的 Latin Modern Mono 没有这些字形。
%   Scale=0.83 是刻意的：让西文宽度恰为中文字符的一半，
%   这样文档里的架构图/目录树才能对齐（原文按「中文占 2 个西文字符宽」书写）。
%   若报「字体找不到」，把下面一行换成：\setmonofont{FreeMono}[Scale=0.83]
\setmonofont{DejaVu Sans Mono}[Scale=0.83]

% 箭头、比较号等：交由数学模式渲染，避免西文字体缺字
\newunicodechar{→}{\ensuremath{\rightarrow}}
\newunicodechar{↓}{\ensuremath{\downarrow}}
\newunicodechar{≥}{\ensuremath{\geq}}
\newunicodechar{≤}{\ensuremath{\leq}}
\newunicodechar{⊆}{\ensuremath{\subseteq}}
\newunicodechar{▼}{\ensuremath{\blacktriangledown}}
\newunicodechar{×}{\ensuremath{\times}}
\newunicodechar{·}{\textperiodcentered}
\newunicodechar{…}{\textellipsis}

\usepackage{hyperref}
\hypersetup{hidelinks,bookmarksnumbered,bookmarksopen}
% tcolorbox 官方建议在 hyperref 之后载入
\usepackage{tcolorbox}

% ---- 提示框 ----
% 注意：两个框都刻意做成「不可分页」。
% 可分页盒子（breakable）与 tabularx 组合会出现排版异常，
% 而本文档的提示框均短于一页，不需要分页。
\newtcolorbox{notebox}{
  colback=notebg, colframe=notebar, boxrule=0pt, leftrule=3pt,
  arc=1pt, left=8pt, right=6pt, top=6pt, bottom=6pt, fontupper=\small,
  before skip=8pt, after skip=8pt
}
\newtcolorbox{todobox}{
  colback=todobg, colframe=todobar, boxrule=0pt, leftrule=3pt,
  arc=1pt, left=8pt, right=6pt, top=6pt, bottom=6pt, fontupper=\small,
  before skip=8pt, after skip=8pt
}

% ---- 占位符标记（对应 Markdown 中的 emoji 约定）----
\newcommand{\shotmark}{\textcolor{todobar}{\textbf{[截图待补]}}}
\newcommand{\todomark}{\textcolor{todobar}{\textbf{[数据待补]}}}
\newcommand{\verifymark}{\textcolor{todobar}{\textbf{[待确认]}}}
\newcommand{\warnmark}{\textcolor{todobar}{\textbf{[注意]}}}

% ---- 版面微调 ----
\setlength{\parskip}{0.35em}
\linespread{1.12}
\renewcommand{\arraystretch}{1.28}
\setlist[itemize]{leftmargin=1.2em,itemsep=2pt,topsep=3pt,parsep=0pt}
\setlist[enumerate]{leftmargin=1.6em,itemsep=2pt,topsep=3pt,parsep=0pt}

\begin{document}
"""

POSTAMBLE = "\n\\end{document}\n"


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "docs/系统使用说明文档.md"
    dst = sys.argv[2] if len(sys.argv) > 2 else "docs/系统使用说明文档.tex"
    with open(src, encoding="utf-8") as f:
        text = f.read()
    body = Converter(text).run()
    with open(dst, "w", encoding="utf-8") as f:
        f.write(PREAMBLE + post(body) + POSTAMBLE)
    print("已生成：%s" % dst)


if __name__ == "__main__":
    main()
