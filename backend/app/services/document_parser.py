"""
文档解析服务：支持 PDF（pdfplumber）/ TXT / DOCX / Markdown
对齐规划文档 5.2：解析 + 文本预处理（清洗、章节切分、过滤过短块）
"""
import os
from typing import List

from ..utils.text_processor import clean_text, split_by_chapter


class DocumentParser:
    """文档解析器"""

    # 支持的文件扩展名
    SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".docx"}

    @staticmethod
    async def parse(file_path: str) -> str:
        """根据文件类型自动选择解析器，返回清洗后的纯文本"""
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".pdf":
            raw = await DocumentParser._parse_pdf(file_path)
        elif ext in (".txt", ".md"):
            raw = await DocumentParser._parse_text(file_path)
        elif ext == ".docx":
            raw = await DocumentParser._parse_docx(file_path)
        else:
            raise ValueError(f"不支持的文件格式: {ext}，仅支持 {DocumentParser.SUPPORTED_EXTENSIONS}")

        return clean_text(raw)

    @staticmethod
    async def parse_to_chunks(file_path: str, min_chunk_chars: int = 20) -> List[dict]:
        """
        解析文档并按章节切分为逻辑块（对齐规划文档 5.2.4）
        返回 [{"title": 章节标题, "content": 文本块}, ...]，过滤过短文本块
        """
        text = await DocumentParser.parse(file_path)
        chunks = split_by_chapter(text)
        return [c for c in chunks if len(c["content"].strip()) >= min_chunk_chars]

    @staticmethod
    async def _parse_pdf(file_path: str) -> str:
        """PDF：pdfplumber 逐页提取文本，单页失败不影响整篇"""
        import pdfplumber

        parts = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                try:
                    page_text = page.extract_text() or ""
                except Exception:
                    # 单页提取失败（如图片页、加密页）不影响整篇
                    page_text = ""
                if page_text.strip():
                    parts.append(page_text)
        return "\n".join(parts)

    @staticmethod
    async def _parse_text(file_path: str) -> str:
        """TXT/MD：直接读取，编码回退 utf-8 → gb18030 → 忽略无法解码字节"""
        for encoding in ("utf-8", "gb18030"):
            try:
                with open(file_path, "r", encoding=encoding) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    @staticmethod
    async def _parse_docx(file_path: str) -> str:
        """DOCX：python-docx 提取段落文本"""
        from docx import Document

        doc = Document(file_path)
        return "\n".join(p.text for p in doc.paragraphs)
