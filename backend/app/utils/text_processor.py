"""
文本预处理工具：分段、清洗、Token估算
"""
import re
from typing import List


def clean_text(text: str) -> str:
    """清洗文本：去除多余空白，保留必要格式"""
    text = re.sub(r'\r\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    text = re.sub(r'\t+', ' ', text)
    return text.strip()


def split_by_chapter(text: str) -> List[dict]:
    """
    按章节分割文本
    匹配模式：第X章、第X节、Chapter X、X.、X． 等
    """
    chapter_patterns = [
        r'(第[一二三四五六七八九十\d]+章)',
        r'(第[一二三四五六七八九十\d]+节)',
        r'(Chapter\s+\d+)',
        r'(\d+[\.\．]\s)',
    ]

    # 简单按段落分割 + 合并短段落
    paragraphs = text.split('\n\n')
    chunks = []
    current_chunk = {"title": "前言", "content": ""}

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # 检查是否为新章节标题
        is_chapter = False
        for pattern in chapter_patterns:
            if re.match(pattern, para):
                if current_chunk["content"]:
                    chunks.append(current_chunk)
                current_chunk = {"title": para[:50], "content": para + "\n"}
                is_chapter = True
                break

        if not is_chapter:
            current_chunk["content"] += para + "\n"

    if current_chunk["content"]:
        chunks.append(current_chunk)

    return chunks


def estimate_tokens(text: str) -> int:
    """估算文本的 token 数（中文约1.5字符/token，英文约4字符/token）"""
    chinese_chars = len(re.findall(r'[一-鿿]', text))
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.5 + other_chars / 4)


def chunk_text_for_llm(text: str, max_tokens: int = 4000) -> List[str]:
    """
    将长文本分割成适合LLM处理的块
    按段落边界分割，每块不超过 max_tokens
    """
    paragraphs = text.split('\n')
    chunks = []
    current_chunk = ""
    current_tokens = 0

    for para in paragraphs:
        para_tokens = estimate_tokens(para)

        if current_tokens + para_tokens > max_tokens and current_chunk:
            chunks.append(current_chunk.strip())
            current_chunk = para + '\n'
            current_tokens = para_tokens
        else:
            current_chunk += para + '\n'
            current_tokens += para_tokens

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks
