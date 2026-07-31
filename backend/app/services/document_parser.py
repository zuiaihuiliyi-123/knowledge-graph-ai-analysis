"""
文档解析服务：支持 PDF / DOCX / TXT / Markdown
"""
import os
from typing import List


class DocumentParser:
    """文档解析器"""

    @staticmethod
    async def parse(file_path: str) -> str:
        """根据文件类型自动选择解析器"""
        ext = os.path.splitext(file_path)[1].lower()

        if ext == '.pdf':
            return await DocumentParser._parse_pdf(file_path)
        elif ext == '.docx':
            return await DocumentParser._parse_docx(file_path)
        elif ext in ('.txt', '.md'):
            return await DocumentParser._parse_text(file_path)
        else:
            raise ValueError(f"不支持的文件格式: {ext}")

    @staticmethod
    async def _parse_pdf(file_path: str) -> str:
        """解析 PDF 文件"""
        text = ""
        try:
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
        except ImportError:
            # 回退到 PyPDF2
            from PyPDF2 import PdfReader
            reader = PdfReader(file_path)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        return text

    @staticmethod
    async def _parse_docx(file_path: str) -> str:
        """解析 DOCX 文件"""
        from docx import Document
        doc = Document(file_path)
        text = "\n".join([para.text for para in doc.paragraphs])
        return text

    @staticmethod
    async def _parse_text(file_path: str) -> str:
        """解析纯文本 / Markdown 文件"""
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
