"""
文档上传与处理服务（对齐规划文档 6.2.1 上传文档接口 + 3.2.1 知识抽取数据流）

流程：建课程记录(整数 course_id) -> 存文件 -> 建文档记录 -> 解析 -> 抽取 -> 入图 -> 回填状态
"""
import os
import hashlib

from ..core.config import settings
from ..core.sql_database import sql_db
from .document_parser import DocumentParser
from .knowledge_extractor import KnowledgeExtractor
from .kg_manager import KnowledgeGraphManager

# 扩展名 -> 文档类型（对齐规划文档表格 10 的 file_type ENUM）
EXT_TO_FILE_TYPE = {".pdf": "PDF", ".txt": "TXT", ".docx": "DOCX", ".md": "MD"}


def _clean_filename(filename: str) -> str:
    """清洗文件名：仅保留 basename，防止路径穿越攻击（对齐规划文档 6.2.1 校验规则）"""
    return os.path.basename(filename.replace("\\", "/"))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class DocumentService:
    """文档上传与知识图谱构建服务"""

    @staticmethod
    async def process_upload(content: bytes, filename: str, course_name: str,
                             teacher_id: int = None) -> dict:
        """
        处理文档上传全流程。

        返回 {"ok": bool, "code": int, "message": str, "data": dict}
        - ok=True：data 含 document_id/course_id/filename/status/counts
        - ok=False：code/message 为业务错误码与描述（错误码见规划文档 6.2.1，2005 为解析失败扩展码）
        """
        # 1. 文件名清洗 + 格式校验
        filename = _clean_filename(filename)
        ext = os.path.splitext(filename)[1].lower()
        if ext not in EXT_TO_FILE_TYPE:
            return {"ok": False, "code": 1001,
                    "message": f"文件格式不支持，仅支持 PDF/TXT/DOCX/MD，收到: {ext or '无扩展名'}"}
        file_type = EXT_TO_FILE_TYPE[ext]

        # 2. 空文件 / 大小校验
        if not content:
            return {"ok": False, "code": 1003, "message": "文件为空"}
        if len(content) > settings.MAX_UPLOAD_SIZE:
            return {"ok": False, "code": 1002,
                    "message": f"文件大小超限，最大 {settings.MAX_UPLOAD_SIZE // 1024 // 1024}MB"}

        # 3. 教师（由 JWT 注入，缺省即报错；显式传入但不存在则报错）
        if teacher_id is None:
            return {"ok": False, "code": 1004, "message": "缺少教师信息"}
        elif sql_db.get_user_by_id(teacher_id) is None:
            return {"ok": False, "code": 1004, "message": f"教师账号不存在: user_id={teacher_id}"}

        # 4. 建课程记录，拿到整数 course_id（对齐决策：course_id 统一整数）
        course_id = sql_db.create_course(course_name=course_name, teacher_id=teacher_id)

        # 5. 建文档记录（file_path 保存后回填）
        doc_id = sql_db.create_document(
            course_id=course_id, uploader_id=teacher_id,
            file_name=filename, file_type=file_type, file_size=len(content),
            file_sha256=_sha256(content),
        )

        # 6. 保存文件到 ./uploads/{course_id}/{doc_id}_{文件名}（按课程分目录，对齐 6.2）
        save_path = None
        try:
            save_dir = os.path.join(settings.UPLOAD_DIR, str(course_id))
            os.makedirs(save_dir, exist_ok=True)
            save_path = os.path.join(save_dir, f"{doc_id}_{filename}")
            with open(save_path, "wb") as f:
                f.write(content)
        except OSError as e:
            return {"ok": False, "code": 5001, "message": f"文件存储失败: {str(e)}"}
        sql_db.update_document(doc_id, file_path=save_path)

        # 7. 解析（parse_status: UPLOADED -> PARSING -> PARSED/FAILED）
        sql_db.update_document(doc_id, parse_status="PARSING")
        try:
            text = await DocumentParser.parse(save_path)
        except Exception as e:
            sql_db.update_document(doc_id, parse_status="FAILED", error_message=f"解析失败: {str(e)}")
            return {"ok": False, "code": 2005, "message": f"文档解析失败: {str(e)}"}
        sql_db.update_document(doc_id, parse_status="PARSED")
        # 解析结果为空（如扫描版 PDF 无文本层）时提前报错，避免静默产出 0 知识点
        if not text or not text.strip():
            sql_db.update_document(doc_id, parse_status="FAILED", error_message="解析结果为空")
            return {"ok": False, "code": 2005,
                    "message": "文档解析结果为空（可能是扫描版 PDF 无文本层，无法抽取知识）"}

        # 8. 抽取（extract_status: PENDING -> EXTRACTING -> COMPLETED/FAILED）
        sql_db.update_document(doc_id, extract_status="EXTRACTING")
        try:
            result = await KnowledgeExtractor().extract(text)
        except Exception as e:
            sql_db.update_document(doc_id, extract_status="FAILED", error_message=f"抽取失败: {str(e)}")
            return {"ok": False, "code": 3003, "message": f"知识抽取失败: {str(e)}"}

        # 抽取结果带 error（JSON 解析失败 / LLM 异常等）时，不应静默当作 0 实体成功
        if result.get("error"):
            sql_db.update_document(doc_id, extract_status="FAILED", error_message=result["error"])
            return {"ok": False, "code": 3003, "message": f"知识抽取失败: {result['error']}"}

        entities = result.get("entities", [])
        relations = result.get("relations", [])

        # 9. 入图（整数 course_id）
        KnowledgeGraphManager.build_graph(course_id, entities, relations)

        # 10. 回填抽取结果统计
        sql_db.update_document(
            doc_id,
            extract_status="COMPLETED",
            entity_count=len(entities),
            relation_count=len(relations),
        )

        doc = sql_db.get_document(doc_id)
        return {
            "ok": True, "code": 0, "message": "success",
            "data": {
                "document_id": doc_id,
                "course_id": course_id,
                "filename": filename,
                "status": "completed",
                "parse_status": doc["parse_status"],
                "extract_status": doc["extract_status"],
                "entity_count": doc["entity_count"],
                "relation_count": doc["relation_count"],
                "created_at": doc["created_at"],
            },
        }
