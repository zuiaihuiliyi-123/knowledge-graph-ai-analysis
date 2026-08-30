"""
将 Hello 算法（开源教材）某章 Markdown 转换为纯文本测试素材
- 去除 mkdocs 多语言 tab 标记，仅保留 Python 实现代码
- 图片保留说明文字，表格转为文本行
- 输出整章 txt + 按项目分块逻辑切好的 chunks（含 token 统计）
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.utils.text_processor import clean_text, estimate_tokens, chunk_text_for_llm


def md_to_text(md: str) -> str:
    """Hello 算法风格 markdown -> 干净纯文本"""
    lines = md.split('\n')
    out = []
    in_code = False
    keep_code = False
    in_table = False

    for raw in lines:
        s = raw.strip()

        # 代码块边界
        if s.startswith('```'):
            if in_code:
                in_code = False
                keep_code = False
            else:
                in_code = True
                lang = s[3:].strip()
                # 只保留 Python 实现（title="xxx.py"），其余语言跳过
                keep_code = 'python' in lang and 'title' in lang
                if keep_code:
                    out.append('示例代码：')
            continue

        if in_code:
            if keep_code:
                out.append(raw.rstrip())
            continue

        # mkdocs 多语言 tab 标记
        if s.startswith('==='):
            continue

        # 表格：转为 "单元格 | 单元格" 文本行
        if s.startswith('|'):
            cells = [c.strip() for c in s.strip('|').split('|')]
            cells = [c for c in cells if not re.fullmatch(r':?-{3,}:?', c)]  # 分隔行
            if cells:
                in_table = True
                out.append('、'.join(cells))
                continue
        in_table = False

        if not s:
            continue

        # 图片 -> 保留说明文字；链接 -> 保留文字
        s = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'\1', s)
        s = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', s)
        # HTML 标签、加粗、行内代码、标题标记、有序列表序号
        s = re.sub(r'<[^>]+>', '', s)
        s = re.sub(r'\*\*([^*]+)\*\*', r'\1', s)
        s = re.sub(r'`([^`]+)`', r'\1', s)
        s = re.sub(r'^#{1,6}\s*', '', s)
        s = re.sub(r'^\d+\.\s*', '', s)
        s = s.strip()
        if s:
            out.append(s)

    return '\n'.join(out)


CHAPTERS = {
    'ch2_complexity': {
        'title': '第2章 复杂度分析',
        'sections': [
            ('2.1 算法效率评估', 'hello_algo_performance_evaluation.md'),
            ('2.2 迭代与递归', 'hello_algo_iteration_and_recursion.md'),
            ('2.3 时间复杂度', 'hello_algo_time_complexity.md'),
            ('2.4 空间复杂度', 'hello_algo_space_complexity.md'),
            ('2.5 本章小结', 'hello_algo_summary.md'),
        ],
        'out_txt': '第2章_复杂度分析.txt',
        'out_chunks': '第2章_复杂度分析_chunks.json',
    },
}


def main(key: str):
    cfg = CHAPTERS[key]
    base = Path(__file__).resolve().parent.parent / 'data' / 'sample_docs'

    parts = [cfg['title'], '']
    for sec_title, fname in cfg['sections']:
        raw = (base / fname).read_text(encoding='utf-8')
        parts += [sec_title, md_to_text(raw), '']
    full = '\n'.join(parts)

    cleaned = clean_text(full)
    (base / cfg['out_txt']).write_text(cleaned, encoding='utf-8')

    est_tokens = estimate_tokens(cleaned)
    chunks = chunk_text_for_llm(cleaned, max_tokens=3000)
    chunk_meta = [
        {'index': i + 1, 'tokens_est': estimate_tokens(c), 'chars': len(c), 'text': c}
        for i, c in enumerate(chunks)
    ]
    (base / cfg['out_chunks']).write_text(
        json.dumps(
            {
                'chapter': cfg['title'],
                'total_chars': len(cleaned),
                'total_tokens_est': est_tokens,
                'chunk_count': len(chunks),
                'chunks': chunk_meta,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    print(f'章节: {cfg["title"]}')
    print(f'字符数: {len(cleaned)}')
    print(f'估算 tokens(项目口径): {est_tokens}')
    print(f'chunks: {len(chunks)} 块, 各块: {[c["tokens_est"] for c in chunk_meta]}')

    # 用 tiktoken 交叉验证真实 token 量级（cl100k_base 近似）
    try:
        import tiktoken
        enc = tiktoken.get_encoding('cl100k_base')
        real = len(enc.encode(cleaned))
        print(f'tiktoken cl100k_base 实际 tokens: {real}')
    except Exception:
        pass


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'ch2_complexity')
