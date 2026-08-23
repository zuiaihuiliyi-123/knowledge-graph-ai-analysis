"""
测试文档解析模块：TXT + PDF 文本提取

运行方式（在 backend 目录下执行）：
    python test_parser.py [可选：PDF 文件路径]
"""
import asyncio
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.services.document_parser import DocumentParser

SAMPLE_TXT = os.path.join("data", "sample_docs", "数据结构第一章.txt")
DEFAULT_PDF = r"C:\Users\20472\Downloads\03会议材料-2026赛题手册.pdf"


def preview(text: str, n: int = 200) -> str:
    """截取文本预览，换行转义便于一行展示"""
    return text[:n].replace("\n", "\\n") + ("..." if len(text) > n else "")


async def main():
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PDF

    # 1. TXT 解析
    print("=" * 60)
    print("1. TXT 解析")
    print("=" * 60)
    if os.path.exists(SAMPLE_TXT):
        txt = await DocumentParser.parse(SAMPLE_TXT)
        print(f"  字符数: {len(txt)}")
        print(f"  预览: {preview(txt)}")

        chunks = await DocumentParser.parse_to_chunks(SAMPLE_TXT)
        print(f"  章节块数: {len(chunks)}")
        for c in chunks:
            print(f"    - [{c['title']}] {len(c['content'])} 字")
    else:
        print(f"  [跳过] 样本文件不存在: {SAMPLE_TXT}")

    # 2. PDF 解析
    print("\n" + "=" * 60)
    print("2. PDF 解析")
    print("=" * 60)
    if os.path.exists(pdf_path):
        pdf_text = await DocumentParser.parse(pdf_path)
        print(f"  文件: {os.path.basename(pdf_path)}")
        print(f"  字符数: {len(pdf_text)}")
        print(f"  预览: {preview(pdf_text)}")
    else:
        print(f"  [跳过] PDF 文件不存在: {pdf_path}")
        print("  （可传入路径：python test_parser.py 你的PDF路径）")

    # 3. 不支持格式校验
    print("\n" + "=" * 60)
    print("3. 不支持格式校验")
    print("=" * 60)
    try:
        await DocumentParser.parse("test.xyz")
    except ValueError as e:
        print(f"  正确抛出异常: {e}")


if __name__ == "__main__":
    asyncio.run(main())
