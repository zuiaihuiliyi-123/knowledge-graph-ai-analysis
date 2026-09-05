"""
文档上传与处理服务（对齐规划文档 6.2.1 上传文档接口 + 3.2.1 知识抽取数据流）

流程：校验课程归属(整数 course_id) -> 存文件 -> 建文档记录 -> 解析 -> 抽取 -> 入图 -> 回填状态
"""
import os
import hashlib

from ..core.config import settings
from ..core.database import db
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


def _doc_payload(doc: dict) -> dict:
    """把 t_document 行映射为对外文档结构（不含 file_path/file_sha256 等内部字段）"""
    return {
        "doc_id": doc["doc_id"],
        "course_id": doc["course_id"],
        "file_name": doc["file_name"],
        "file_type": doc["file_type"],
        "file_size": doc["file_size"],
        "parse_status": doc["parse_status"],
        "extract_status": doc["extract_status"],
        "entity_count": doc["entity_count"],
        "relation_count": doc["relation_count"],
        "vector_collection": doc.get("vector_collection"),
        "created_at": doc["created_at"],
        "updated_at": doc.get("updated_at"),
    }


class DocumentService:
    """文档上传与知识图谱构建服务"""

    @staticmethod
    async def process_upload(content: bytes, filename: str, course_id: int,
                             teacher_id: int = None) -> dict:
        """
        处理文档上传全流程（上传到已存在课程；不再自动建课）。

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

        # 4. 校验课程存在且归属当前教师（Phase 5：上传必须指定已存在课程，禁止自动建课）
        course = sql_db.get_course(course_id)
        if course is None:
            return {"ok": False, "code": 2001, "message": f"课程不存在: course_id={course_id}"}
        if course["teacher_id"] != teacher_id:
            return {"ok": False, "code": 4003, "message": "无权限：仅该课程所属教师可上传文档"}

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

        # 9. 入图（整数 course_id + 文档级 document_id，Phase 8A：图谱按文档隔离）
        KnowledgeGraphManager.build_graph(course_id, doc_id, entities, relations)

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

    @staticmethod
    def list_documents(course_id: int, user_id: int, role: str = "teacher") -> dict:
        """某课程文档列表。

        Phase 7：学生端需读取可学习课程的文档。当前项目无选课/班级体系，
        沿用现有「学生可见全部课程」规则——学生可读任意课程文档；教师仍校验课程归属。
        """
        course = sql_db.get_course(course_id)
        if course is None:
            return {"ok": False, "code": 2001, "message": f"课程不存在: course_id={course_id}"}
        if role == "teacher" and course["teacher_id"] != user_id:
            return {"ok": False, "code": 4003, "message": "无权限：仅该课程所属教师可查看文档列表"}
        docs = sql_db.list_documents_by_course(course_id)
        return {"ok": True, "code": 0, "message": "success",
                "data": [_doc_payload(d) for d in docs]}

    @staticmethod
    def get_document_detail(doc_id: int, teacher_id: int) -> dict:
        """文档详情（校验文档所属课程归属）"""
        doc = sql_db.get_document(doc_id)
        if doc is None:
            return {"ok": False, "code": 2002, "message": f"文档不存在: doc_id={doc_id}"}
        course = sql_db.get_course(doc["course_id"])
        if course is None or course["teacher_id"] != teacher_id:
            return {"ok": False, "code": 4003, "message": "无权限：仅该文档所属课程的教师可查看"}
        return {"ok": True, "code": 0, "message": "success",
                "data": _doc_payload(doc)}

    @staticmethod
    def delete_document(doc_id: int, teacher_id: int) -> dict:
        """删除文档及其全部文档级资源（校验课程归属）。

        Phase 8C：按顺序清理 Neo4j 图谱 → SQLite 向量 → 学习记录 → 收藏 → 本地文件 → 文档记录。
        每步幂等可重试；任一步失败返回明确错误，不假装全部删除成功。
        绝不误删同课程其他文档的图谱/向量/学习记录/收藏（均按 course_id + document_id 限定）。
        """
        doc = sql_db.get_document(doc_id)
        if doc is None:
            return {"ok": False, "code": 2002, "message": f"文档不存在: doc_id={doc_id}"}
        course = sql_db.get_course(doc["course_id"])
        if course is None or course["teacher_id"] != teacher_id:
            return {"ok": False, "code": 4003, "message": "无权限：仅该文档所属课程的教师可删除"}

        cid = doc["course_id"]

        # 1. Neo4j 图谱（节点 + 关系；仅当前文档）
        try:
            removed_nodes, removed_edges = db.delete_document_graph(cid, doc_id)
        except Exception as e:
            return {"ok": False, "code": 5002, "message": f"删除文档图谱失败: {str(e)}"}

        # 2. SQLite 向量
        try:
            removed_embeddings = sql_db.delete_embeddings_by_document(cid, doc_id)
        except Exception as e:
            return {"ok": False, "code": 5002, "message": f"删除文档向量失败: {str(e)}"}

        # 3. 学习记录
        try:
            removed_records = sql_db.delete_learning_records_by_document(cid, doc_id)
        except Exception as e:
            return {"ok": False, "code": 5002, "message": f"删除文档学习记录失败: {str(e)}"}

        # 4. 收藏
        try:
            removed_favorites = sql_db.delete_favorites_by_document(cid, doc_id)
        except Exception as e:
            return {"ok": False, "code": 5002, "message": f"删除文档收藏失败: {str(e)}"}

        # 5. 本地文件（不存在视为已删，幂等）
        path = doc.get("file_path")
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

        # 6. 文档记录
        sql_db.delete_document(doc_id)

        return {"ok": True, "code": 0, "message": "success", "data": {
            "doc_id": doc_id,
            "course_id": cid,
            "deleted": True,
            "removed_nodes": removed_nodes,
            "removed_edges": removed_edges,
            "removed_embeddings": removed_embeddings,
            "removed_records": removed_records,
            "removed_favorites": removed_favorites,
        }}
