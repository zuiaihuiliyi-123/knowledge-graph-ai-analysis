"""
将 Hello 算法（CC BY-NC-SA 4.0）章节的 Markdown 源文件转换为纯文本，
供知识抽取测试与图谱构建示例使用。

用法:
    python scripts/build_chapter_text.py <章节目录名> <输出文件名>

示例:
    python scripts/build_chapter_text.py chapter_tree "第7章_树.txt"

转换规则:
    - 跳过 mkdocs 多语言 tab 标记（=== "C++" 等）
    - 代码块只保留 Python 实现（title="*.py"），其余语言版本丢弃
    - 图片保留 alt 说明文字；HTML 标签、加粗、行内代码、列表序号去除
    - 章节标题按 7.1 / 7.2 顺序编号拼接
"""
import re
import sys

BASE = "https://raw.githubusercontent.com/krahets/hello-algo/main/docs"

# 各章正文文件（不含 exercises 练习题，练习题不属于知识讲解）
CHAPTER_FILES = {
    "chapter_stack_and_queue": ["stack", "queue", "deque", "summary"],
    "chapter_tree": [
        "binary_tree",
        "binary_tree_traversal",
        "binary_search_tree",
        "avl_tree",
        "array_representation_of_tree",
        "summary",
    ],
}


def md_to_text(md: str) -> str:
    lines = md.split("\n")
    out = []
    in_code = False
    keep_code = False
    for line in lines:
        s = line.strip()
        if s.startswith("==="):  # mkdocs 语言 tab 标记
            continue
        if s.startswith("```"):
            if in_code:
                in_code = False
                keep_code = False
                out.append("")
            else:
                in_code = True
                lang = s[3:].strip()
                keep_code = "python" in lang and "title" in lang
                if keep_code:
                    out.append("示例代码：")
            continue
        if in_code:
            if keep_code:
                out.append(line)
            continue
        if not s:
            continue
        s = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", s)   # 图片 -> alt 文字
        s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)    # 链接 -> 文字
        s = re.sub(r"<[^>]+>", "", s)                     # HTML 标签
        s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)          # 加粗
        s = re.sub(r"`([^`]+)`", r"\1", s)                # 行内代码
        s = re.sub(r"^#{1,6}\s*", "", s)                  # 标题符号
        s = re.sub(r"^[0-9]+\.\s*", "", s)                # 有序列表序号
        s = s.strip()
        if s:
            out.append(s)
    return "\n".join(out)


def build_chapter(chapter: str, title: str) -> str:
    import urllib.request
    import ssl

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    parts = [title, ""]
    for i, name in enumerate(CHAPTER_FILES[chapter], start=1):
        url = f"{BASE}/{chapter}/{name}.md"
        req = urllib.request.Request(url, headers={"User-Agent": "chapter-builder"})
        md = urllib.request.urlopen(req, context=ctx).read().decode("utf-8")
        text = md_to_text(md)
        parts.append(f"{i} {text.strip()}")
        parts.append("")
        print(f"  [{i}] {name}.md -> {len(text)} 字符")
    return "\n".join(parts)


if __name__ == "__main__":
    chapter = sys.argv[1]
    out_path = sys.argv[2]
    title = "第7章 树" if chapter == "chapter_tree" else "第5章 栈与队列"
    print(f"构建章节: {title} ({chapter})")
    full = build_chapter(chapter, title)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(full)
    print(f"输出: {out_path} ({len(full)} 字符)")
