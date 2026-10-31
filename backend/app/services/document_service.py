"""
文档上传与处理服务（对齐规划文档 6.2.1 上传文档接口 + 3.2.1 知识抽取数据流）

流程：校验课程归属(整数 course_id) -> 存文件 -> 建文档记录 -> 解析 -> 抽取 -> 入图 -> 回填状态
"""
import asyncio
import logging
import os
import hashlib

from ..core.config import settings
from ..core.database import db
from ..core.permissions import Permissions
from ..core.sql_database import sql_db
from ..core.storage import document_relative_path, resolve_document_path
from .document_parser import DocumentParser
from .knowledge_extractor import KnowledgeExtractor
from .kg_manager import KnowledgeGraphManager

_logger = logging.getLogger(__name__)

# 批量上传的后台处理并发上限。
# 知识抽取是 LLM 外部调用，同时跑太多文档会触发供应商限流；同时前端批量上传不再
# 等请求返回，必须由后端控制并发。取 2：单文档内 KnowledgeExtractor 自己还会分块
# 并发（_MAX_CONCURRENCY），两者相乘仍处于可控范围。
_PROCESS_CONCURRENCY = 2

# 后台任务的 semaphore / 强引用 / 正在处理的 doc_id。
# 全部是进程内状态：本项目只有单进程 uvicorn，刻意不引入 Redis/Celery。
_semaphore_state = {"loop": None, "sem": None}
_running_docs = set()
_background_tasks = set()


def _get_semaphore() -> asyncio.Semaphore:
    """按当前事件循环惰性创建 semaphore（测试里 asyncio.run 会换循环）。"""
    loop = asyncio.get_running_loop()
    if _semaphore_state["sem"] is None or _semaphore_state["loop"] is not loop:
        _semaphore_state["sem"] = asyncio.Semaphore(_PROCESS_CONCURRENCY)
        _semaphore_state["loop"] = loop
    return _semaphore_state["sem"]


def _parse_file_sync(path: str) -> str:
    """在线程中执行文档解析。

    DocumentParser.parse 声明为 async，但内部是同步文件/PDF 读取；直接 await 会阻塞
    事件循环，导致批量任务无法真正并发，故放进线程并各自跑一个临时事件循环。
    """
    return asyncio.run(DocumentParser.parse(path))


def _write_file_sync(path: str, content: bytes) -> None:
    with open(path, "wb") as f:
        f.write(content)


# 扩展名 -> 文档类型（对齐规划文档表格 10 的 file_type ENUM）
EXT_TO_FILE_TYPE = {".pdf": "PDF", ".txt": "TXT", ".docx": "DOCX", ".md": "MD"}

# 文档类型 -> HTTP Content-Type（供在线阅读接口使用；不声明 charset：
# TXT/MD 的真实编码由前端读取字节后自行探测，避免后端声明一个可能错误的具体编码）
FILE_TYPE_TO_MEDIA_TYPE = {
    "PDF": "application/pdf",
    "TXT": "text/plain",
    "MD": "text/markdown",
    "DOCX": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _media_type_for(file_type: str) -> str:
    """文件类型 -> Content-Type；未知类型退化为二进制流"""
    return FILE_TYPE_TO_MEDIA_TYPE.get(file_type, "application/octet-stream")


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
        # 图谱入库阶段：批量上传下「上传成功」≠「图谱构建成功」，
        # 前端只有看到 graph_status=COMPLETED 才标记该文件处理成功。
        "graph_status": doc.get("graph_status") or "PENDING",
        # 失败原因：教师定位问题的唯一线索，按文档返回
        "error_message": doc.get("error_message"),
        "entity_count": doc["entity_count"],
        "relation_count": doc["relation_count"],
        "vector_collection": doc.get("vector_collection"),
        "created_at": doc["created_at"],
        "updated_at": doc.get("updated_at"),
    }


class DocumentService:
    """文档上传与知识图谱构建服务"""

    # ---------- 上传校验 / 落盘（单文件与批量共用） ----------

    @staticmethod
    def _validate_upload(content: bytes, filename: str) -> dict:
        """校验格式 / 空文件 / 大小，返回 {ok, file_name, file_type} 或错误结构。"""
        filename = _clean_filename(filename)
        ext = os.path.splitext(filename)[1].lower()
        if ext not in EXT_TO_FILE_TYPE:
            return {"ok": False, "code": 1001, "file_name": filename,
                    "message": f"文件格式不支持，仅支持 PDF/TXT/DOCX/MD，收到: {ext or '无扩展名'}"}
        if not content:
            return {"ok": False, "code": 1003, "file_name": filename, "message": "文件为空"}
        if len(content) > settings.MAX_UPLOAD_SIZE:
            return {"ok": False, "code": 1002, "file_name": filename,
                    "message": f"文件大小超限，最大 {settings.MAX_UPLOAD_SIZE // 1024 // 1024}MB"}
        return {"ok": True, "file_name": filename, "file_type": EXT_TO_FILE_TYPE[ext]}

    @staticmethod
    async def _persist_document(course_id: int, teacher_id: int, filename: str,
                                file_type: str, content: bytes) -> dict:
        """建文档记录 + 落盘，返回 {ok, document_id, file_sha256}。

        每个文件都拿到自己的 doc_id，后续解析/抽取/入图全部以它为作用域——
        批量上传绝不把多个文档合并成一张图。
        """
        digest = _sha256(content)
        doc_id = sql_db.create_document(
            course_id=course_id, uploader_id=teacher_id,
            file_name=filename, file_type=file_type, file_size=len(content),
            file_sha256=digest,
        )
        save_dir = os.path.join(settings.UPLOAD_DIR, str(course_id))
        save_path = os.path.join(save_dir, f"{doc_id}_{filename}")
        try:
            os.makedirs(save_dir, exist_ok=True)
            await asyncio.to_thread(_write_file_sync, save_path, content)
        except OSError as e:
            # 落盘失败时不要留下一条没有文件的空记录（列表里会永远停在 UPLOADED）
            try:
                sql_db.delete_document(doc_id)
            except Exception:
                pass
            return {"ok": False, "code": 5001, "message": f"文件存储失败: {str(e)}"}
        # 库里存**相对 UPLOAD_DIR 的路径**（换机器/恢复备份后仍能解析），
        # save_path 只用于本次落盘。历史行的绝对路径由 resolve_document_path 兼容。
        sql_db.update_document(
            doc_id, file_path=document_relative_path(course_id, doc_id, filename))
        return {"ok": True, "document_id": doc_id, "file_sha256": digest}

    @staticmethod
    def _success_payload(doc: dict, status: str = "completed") -> dict:
        """上传接口返回结构（保持单文件上传的既有字段）。"""
        return {
            "document_id": doc["doc_id"],
            "course_id": doc["course_id"],
            "filename": doc["file_name"],
            "status": status,
            "parse_status": doc["parse_status"],
            "extract_status": doc["extract_status"],
            "graph_status": doc.get("graph_status") or "PENDING",
            "entity_count": doc["entity_count"],
            "relation_count": doc["relation_count"],
            "created_at": doc["created_at"],
        }

    # ---------- 单文档处理（同步单传与后台批量共用） ----------

    @staticmethod
    async def _process_document(doc_id: int) -> dict:
        """对**一个** document_id 完成 解析 → 抽取 → 入图，逐步写真实状态。

        任何一步失败都立即落库并返回，不静默当成成功；调用方（后台任务）据此
        让失败文件独立结束，不影响其他文档。
        """
        doc = sql_db.get_document(doc_id)
        if doc is None:
            return {"ok": False, "code": 2002, "message": f"文档不存在: doc_id={doc_id}"}
        course_id = doc["course_id"]

        path = resolve_document_path(doc)
        if not path or not os.path.exists(path):
            message = "文档文件不存在或已被移除，无法处理"
            sql_db.update_document(doc_id, parse_status="FAILED", error_message=message)
            return {"ok": False, "code": 2003, "message": message}

        # 1. 解析（UPLOADED -> PARSING -> PARSED/FAILED）
        sql_db.update_document(doc_id, parse_status="PARSING", error_message=None)
        try:
            text = await asyncio.to_thread(_parse_file_sync, path)
        except Exception as e:
            message = f"解析失败: {str(e)}"
            sql_db.update_document(doc_id, parse_status="FAILED", error_message=message)
            return {"ok": False, "code": 2005, "message": f"文档解析失败: {str(e)}"}

        # 解析结果为空（如扫描版 PDF 无文本层）时提前报错，避免静默产出 0 知识点
        if not text or not text.strip():
            message = "解析结果为空（可能是扫描版 PDF 无文本层，无法抽取知识）"
            sql_db.update_document(doc_id, parse_status="FAILED", error_message=message)
            return {"ok": False, "code": 2005, "message": message}
        sql_db.update_document(doc_id, parse_status="PARSED")

        # 解析期间文档可能已被删除（多进程部署、或管理员从另一实例清理）。
        # 此时不再继续抽取，避免白花一次 LLM 调用。
        if not DocumentService._scope_exists(doc_id, course_id):
            _logger.warning("文档在处理期间被删除，终止任务 doc_id=%s", doc_id)
            return {"ok": False, "code": 4009, "message": "文档已被删除，任务终止"}

        # 2. 抽取（PENDING -> EXTRACTING -> COMPLETED/FAILED）
        sql_db.update_document(doc_id, extract_status="EXTRACTING", error_message=None)
        try:
            result = await KnowledgeExtractor().extract(text)
        except Exception as e:
            message = f"抽取失败: {str(e)}"
            sql_db.update_document(doc_id, extract_status="FAILED", error_message=message)
            return {"ok": False, "code": 3003, "message": f"知识抽取失败: {str(e)}"}

        # 抽取结果带 error（JSON 解析失败 / LLM 异常等）时，不应静默当作 0 实体成功
        if result.get("error"):
            message = str(result["error"])
            sql_db.update_document(doc_id, extract_status="FAILED", error_message=message)
            return {"ok": False, "code": 3003, "message": f"知识抽取失败: {message}"}

        entities = result.get("entities", [])
        relations = result.get("relations", [])

        # 3. 入图（整数 course_id + 文档级 document_id；每个文档独立建图）
        # 入图前必须再确认作用域仍存在：删除与后台任务并发时，绝不能向已删除的
        # (course_id, document_id) 写入节点——文档行一旦消失，就再也没有入口能清理它们。
        if not DocumentService._scope_exists(doc_id, course_id):
            _logger.warning("文档在入图前被删除，终止任务 doc_id=%s", doc_id)
            return {"ok": False, "code": 4009, "message": "文档已被删除，任务终止"}

        sql_db.update_document(
            doc_id,
            extract_status="COMPLETED",
            entity_count=len(entities),
            relation_count=len(relations),
            graph_status="BUILDING",
            error_message=None,
        )
        try:
            await asyncio.to_thread(
                KnowledgeGraphManager.build_graph, course_id, doc_id, entities, relations)
        except Exception as e:
            message = f"图谱构建失败: {str(e)}"
            # 失败时作用域若已消失，建图中途落下的部分节点也要清掉
            if not DocumentService._scope_exists(doc_id, course_id):
                DocumentService._cleanup_deleted_scope(course_id, doc_id)
                return {"ok": False, "code": 4009, "message": "文档已被删除，任务终止"}
            sql_db.update_document(doc_id, graph_status="FAILED", error_message=message)
            return {"ok": False, "code": 5002, "message": message}

        # 入图完成后兜底：若删除发生在构建过程中，把本次写入的节点/关系清掉
        if not DocumentService._scope_exists(doc_id, course_id):
            _logger.warning("文档在入图期间被删除，清理残留图谱 doc_id=%s", doc_id)
            DocumentService._cleanup_deleted_scope(course_id, doc_id)
            return {"ok": False, "code": 4009, "message": "文档已被删除，任务终止"}

        # 4. 只有图谱真正入库后才标记完成
        sql_db.update_document(doc_id, graph_status="COMPLETED", error_message=None)

        return {"ok": True, "code": 0, "message": "success",
                "data": DocumentService._success_payload(sql_db.get_document(doc_id))}

    @staticmethod
    async def _run_document_task(doc_id: int) -> None:
        """后台任务的统一入口：受并发 semaphore 约束，异常兜底落库。"""
        try:
            async with _get_semaphore():
                await DocumentService._process_document(doc_id)
        except Exception as e:
            # 走到这里说明状态更新本身也失败了，尽最大努力留下失败原因
            _logger.exception("文档处理异常 doc_id=%s", doc_id)
            try:
                sql_db.update_document(
                    doc_id, graph_status="FAILED", error_message=f"处理异常: {str(e)}")
            except Exception:
                pass
        finally:
            _running_docs.discard(doc_id)

    @staticmethod
    def _schedule_processing(doc_id: int) -> bool:
        """把文档交给后台处理。同一文档重复调度返回 False。"""
        if doc_id in _running_docs:
            return False
        _running_docs.add(doc_id)
        try:
            task = asyncio.create_task(DocumentService._run_document_task(doc_id))
        except Exception:
            _running_docs.discard(doc_id)
            return False
        # 持有强引用，避免任务在完成前被 GC 回收
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        return True

    @staticmethod
    def _scope_exists(doc_id: int, course_id: int) -> bool:
        """文档与其所属课程是否仍然存在（后台任务的写入前置条件）。"""
        return (sql_db.get_document(int(doc_id)) is not None
                and sql_db.get_course(int(course_id)) is not None)

    @staticmethod
    def _cleanup_deleted_scope(course_id: int, doc_id: int) -> None:
        """清理已删除作用域的残留图谱；失败只记日志，任务本身已经终止。"""
        try:
            db.delete_document_graph(int(course_id), int(doc_id))
        except Exception:
            _logger.exception("清理已删除文档的残留图谱失败 course_id=%s doc_id=%s",
                              course_id, doc_id)

    @staticmethod
    def has_running_documents(course_id: int) -> bool:
        """该课程内是否有文档正由本进程的后台任务处理（删除课程前的检查）。"""
        docs = sql_db.list_documents_by_course(int(course_id))
        return any(int(d["doc_id"]) in _running_docs for d in docs)

    # ---------- 单文件上传（保持原有同步语义） ----------

    @staticmethod
    async def process_upload(content: bytes, filename: str, course_id: int,
                             teacher_id: int = None) -> dict:
        """
        处理文档上传全流程（上传到已存在课程；不再自动建课）。

        返回 {"ok": bool, "code": int, "message": str, "data": dict}
        - ok=True：data 含 document_id/course_id/filename/status/counts
        - ok=False：code/message 为业务错误码与描述（错误码见规划文档 6.2.1，2005 为解析失败扩展码）

        该入口保持同步语义（请求返回时图谱已构建完成），供「单文件上传」兼容路径使用；
        批量上传走 process_batch，由后台任务处理。
        """
        # 1. 格式 / 大小校验
        checked = DocumentService._validate_upload(content, filename)
        if not checked["ok"]:
            return checked

        # 2. 教师（由 JWT 注入，缺省即报错；显式传入但不存在则报错）
        if teacher_id is None:
            return {"ok": False, "code": 1004, "message": "缺少教师信息"}
        elif sql_db.get_user_by_id(teacher_id) is None:
            return {"ok": False, "code": 1004, "message": f"教师账号不存在: user_id={teacher_id}"}

        # 3. 校验课程存在且归属当前教师（课程创建者或被邀请的协作教师）
        perm = Permissions.require_course_manage(
            course_id, {"user_id": teacher_id, "role": "teacher"})
        if not perm["ok"]:
            return {"ok": False, "code": perm["code"], "message": perm["message"]}

        # 4. 建记录 + 落盘
        persisted = await DocumentService._persist_document(
            course_id, teacher_id, checked["file_name"], checked["file_type"], content)
        if not persisted["ok"]:
            return persisted

        # 5. 同步处理（与批量共用同一套状态写入，图谱完成才返回成功）
        doc_id = persisted["document_id"]
        if doc_id in _running_docs:
            return {"ok": False, "code": 4009, "message": "该文档正在处理中，请勿重复提交"}
        _running_docs.add(doc_id)
        try:
            async with _get_semaphore():
                return await DocumentService._process_document(doc_id)
        finally:
            _running_docs.discard(doc_id)

    # ---------- 批量上传（快速落库 + 后台限流处理） ----------

    @staticmethod
    async def process_batch(files, course_id: int, teacher_id: int) -> dict:
        """批量上传：校验/落库/去重后立即返回，解析与建图在后台按并发上限执行。

        files 为 FastAPI 的 UploadFile 列表。每个文件独立返回 accepted / duplicate /
        failed 结果；单个文件校验失败不影响其他文件。
        """
        if not files:
            return {"ok": False, "code": 1001, "message": "未选择任何文件"}

        if teacher_id is None or sql_db.get_user_by_id(teacher_id) is None:
            return {"ok": False, "code": 1004, "message": "教师账号不存在或未登录"}
        perm = Permissions.require_course_manage(
            course_id, {"user_id": teacher_id, "role": "teacher"})
        if not perm["ok"]:
            return {"ok": False, "code": perm["code"], "message": perm["message"]}

        items = []
        accepted = duplicates = failed = 0
        # 同一次提交里的重复文件也去重，避免一个批次里同一内容建两遍图
        batch_hashes = {}

        for upload in files:
            original_name = _clean_filename(upload.filename or "未命名")
            try:
                content = await upload.read()
            except Exception as e:
                items.append({"file_name": original_name, "ok": False,
                              "code": 5001, "message": f"读取上传文件失败: {str(e)}"})
                failed += 1
                continue

            checked = DocumentService._validate_upload(content, original_name)
            if not checked["ok"]:
                items.append({"file_name": checked["file_name"], "ok": False,
                              "code": checked["code"], "message": checked["message"]})
                failed += 1
                continue

            digest = _sha256(content)
            if digest in batch_hashes:
                items.append({"file_name": checked["file_name"], "ok": True,
                              "duplicate": True,
                              "document_id": batch_hashes[digest],
                              "message": "本次提交中内容重复，已跳过"})
                duplicates += 1
                continue

            # 同课程内已上传过相同内容：复用原文档，不重复建图（重复提交幂等）
            existing = sql_db.get_document_by_sha256(course_id, digest)
            if existing:
                batch_hashes[digest] = existing["doc_id"]
                items.append({"file_name": checked["file_name"], "ok": True,
                              "duplicate": True,
                              "document_id": existing["doc_id"],
                              "status": "existing",
                              "message": "相同文件已存在于本课程，已跳过重复建图"})
                duplicates += 1
                continue

            persisted = await DocumentService._persist_document(
                course_id, teacher_id, checked["file_name"], checked["file_type"], content)
            if not persisted["ok"]:
                items.append({"file_name": checked["file_name"], "ok": False,
                              "code": persisted["code"], "message": persisted["message"]})
                failed += 1
                continue

            doc_id = persisted["document_id"]
            batch_hashes[digest] = doc_id
            scheduled = DocumentService._schedule_processing(doc_id)
            if not scheduled:
                sql_db.update_document(doc_id, graph_status="FAILED",
                                       error_message="文档已在处理队列中")
            items.append({"file_name": checked["file_name"], "ok": True,
                          "duplicate": False, "document_id": doc_id,
                          "status": "queued" if scheduled else "failed_to_queue",
                          "message": ("上传成功，已进入处理队列" if scheduled
                                      else "文件已保存，但处理任务未能启动，请重试")})
            accepted += 1

        return {
            "ok": True, "code": 0, "message": "success",
            "data": {
                "course_id": course_id,
                "total": len(files),
                "accepted": accepted,
                "duplicates": duplicates,
                "failed": failed,
                # 每个元素都是该文件自己的真实结果；处理进度另由文档列表轮询获取
                "items": items,
            },
        }

    # ---------- 失败重试 ----------

    @staticmethod
    async def retry_document(doc_id: int, teacher_id: int) -> dict:
        """重新处理一篇失败/中断的文档（复用已保存的文件，不重复上传）。

        - 已完整成功的文档直接拒绝，绝不重复处理；
        - 正在处理的文档拒绝，避免同一 doc_id 并发写图；
        - 重试前只清理**该文档**已有的图谱与向量，再走完整流水线。
        """
        perm = Permissions.require_document_manage(
            doc_id, {"user_id": teacher_id, "role": "teacher"})
        if not perm["ok"]:
            return {"ok": False, "code": perm["code"], "message": perm["message"]}

        doc = perm["data"]["document"]
        course_id = doc["course_id"]

        if (doc["parse_status"] == "PARSED" and doc["extract_status"] == "COMPLETED"
                and (doc.get("graph_status") or "") == "COMPLETED"):
            return {"ok": False, "code": 4000,
                    "message": "该文档已处理完成，无需重试"}
        # 只认进程内的任务注册表：DB 里的 PARSING/EXTRACTING 可能是上次进程
        # 崩溃/重启留下的陈旧状态，那种情况恰恰需要允许重试。
        if doc_id in _running_docs:
            return {"ok": False, "code": 4009,
                    "message": "该文档正在处理中，请稍后再试"}

        path = resolve_document_path(doc)
        if not path or not os.path.exists(path):
            return {"ok": False, "code": 2003,
                    "message": "文档源文件不存在，无法重试，请重新上传"}

        # 失败/中断的处理可能已经写入部分节点或旧向量：只清当前文档作用域
        try:
            db.delete_document_graph(course_id, doc_id)
        except Exception as e:
            return {"ok": False, "code": 5002,
                    "message": f"清理旧图谱失败，无法重试: {str(e)}"}
        try:
            sql_db.delete_embeddings_by_document(course_id, doc_id)
        except Exception as e:
            _logger.warning("重试前清理文档向量失败 course_id=%s doc_id=%s: %s",
                            course_id, doc_id, e)

        sql_db.update_document(
            doc_id,
            parse_status="UPLOADED",
            extract_status="PENDING",
            graph_status="PENDING",
            error_message=None,
            entity_count=0,
            relation_count=0,
        )
        if not DocumentService._schedule_processing(doc_id):
            sql_db.update_document(doc_id, graph_status="FAILED",
                                   error_message="文档已在处理队列中")
            return {"ok": False, "code": 4009, "message": "该文档正在处理中，请稍后再试"}

        return {"ok": True, "code": 0, "message": "success",
                "data": {"document_id": doc_id, "course_id": course_id,
                         "status": "queued", "message": "已重新进入处理队列"}}

    @staticmethod
    def list_documents(course_id: int, user_id: int, role: str = "teacher") -> dict:
        """某课程文档列表。

        课程中心改造：由「学生可见全部课程」收紧为「课程成员可见」。
        权限单一事实来源 = Permissions.require_course_content：
        课程创建者 / 协作教师 / 已通过审核的学生成员可读，其余一律 4003。
        """
        perm = Permissions.require_course_content(
            course_id, {"user_id": user_id, "role": role})
        if not perm["ok"]:
            return {"ok": False, "code": perm["code"], "message": perm["message"]}
        docs = sql_db.list_documents_by_course(course_id)
        return {"ok": True, "code": 0, "message": "success",
                "data": [_doc_payload(d) for d in docs]}

    @staticmethod
    def get_document_detail(doc_id: int, user_id: int, role: str = "teacher") -> dict:
        """文档详情（文档 -> 课程 -> 成员关系；课程成员均可查看）"""
        perm = Permissions.require_document_content(
            doc_id, {"user_id": user_id, "role": role})
        if not perm["ok"]:
            return {"ok": False, "code": perm["code"], "message": perm["message"]}
        return {"ok": True, "code": 0, "message": "success",
                "data": _doc_payload(perm["data"]["document"])}

    @staticmethod
    def get_document_content(doc_id: int, user_id: int, role: str = "student") -> dict:
        """在线阅读所需的文件定位信息（只读，不修改任何现有流程）。

        课程中心改造：权限收紧为「课程成员可读」——这正是原先注释里标注的
        「要收紧为『仅已选课程』时，只需改这一处」。教师仅限本人所授课程，
        学生仅限已加入（approved）的课程，二者共用 Permissions 同一判定。

        返回 data 为 {path, file_name, file_type, media_type}，
        仅在后端内部使用真实路径，接口层不会把它返回给客户端。
        """
        perm = Permissions.require_document_content(
            doc_id, {"user_id": user_id, "role": role})
        if not perm["ok"]:
            return {"ok": False, "code": perm["code"], "message": perm["message"]}
        doc = perm["data"]["document"]

        # 路径解析见 core/storage：兼容其他机器写入的绝对路径（随仓库流转的历史行）
        path = resolve_document_path(doc)
        if not path or not os.path.exists(path):
            return {"ok": False, "code": 2003, "message": "文档文件不存在或已被移除，无法在线阅读"}

        return {"ok": True, "code": 0, "message": "success", "data": {
            "path": path,
            "file_name": doc["file_name"],
            "file_type": doc["file_type"],
            "file_size": doc["file_size"],
            "media_type": _media_type_for(doc["file_type"]),
        }}

    @staticmethod
    def delete_document(doc_id: int, teacher_id: int) -> dict:
        """删除文档及其全部文档级资源（**教师入口**：校验课程归属）。

        仅该课程教师（创建者 / 已通过的协作教师）可调用：权限判定走
        Permissions.require_document_manage，学生与课程外的人一律 4003。
        管理员走独立的 delete_document_by_admin（见下），本函数不提供任何
        「跳过校验」的参数——普通业务调用方无法通过构造参数取得管理员权限。
        """
        perm = Permissions.require_document_manage(
            doc_id, {"user_id": teacher_id, "role": "teacher"})
        if not perm["ok"]:
            return {"ok": False, "code": perm["code"], "message": perm["message"]}
        return DocumentService._delete_document_cascade(perm["data"]["document"])

    @staticmethod
    def delete_document_by_admin(doc_id: int, admin: dict) -> dict:
        """删除文档（**管理员入口**：平台资源治理）。

        与教师入口共用同一条 _delete_document_cascade 级联清理，
        差别只在授权方式：

        - 不接受布尔开关。参数是**已认证的主体**（要求 role == 'admin'），
          且本函数自己再断言一次角色——不能靠「传个 True」就获得管理员权限；
        - 调用链固定为 Admin API -> require_admin -> AdminService -> 本函数，
          api/documents.py 与任何教师/学生业务都不经过这里。

        为什么管理员需要独立入口而不是复用 require_document_manage：
        管理员不属于任何课程的成员（can_manage 恒为 False），复用会被一律拒绝。

        注意：这里只放宽「谁是调用者」的判定，**级联清理的顺序与完整性约束
        与教师入口逐字节相同**——不为管理员方便而留下孤立文件或 Neo4j 残留。
        """
        if not admin or admin.get("role") != "admin":
            return {"ok": False, "code": 4003,
                    "message": "无权限：仅管理员可执行平台资源治理"}
        doc = sql_db.get_document(int(doc_id)) if str(doc_id).lstrip("-").isdigit() else None
        if doc is None:
            return {"ok": False, "code": 2002, "message": f"文档不存在: doc_id={doc_id}"}
        _logger.info("管理员 %s 删除文档: doc_id=%s", admin.get("username"), doc_id)
        return DocumentService._delete_document_cascade(doc)

    @staticmethod
    def _delete_document_cascade(doc: dict) -> dict:
        """文档级级联清理的**唯一实现**（教师入口与管理员入口共用）。

        Phase 8C：按顺序清理 Neo4j 图谱 → SQLite 向量 → 学习记录 → 收藏 →
        题库 → 本地文件 → 文档记录。每步幂等可重试；任一步失败返回明确错误，
        不假装全部删除成功。绝不误删同课程其他文档的图谱/向量/学习记录/收藏
        （均按 course_id + document_id 限定）。
        """
        doc_id = doc["doc_id"]
        cid = doc["course_id"]

        # 正在后台处理时拒绝删除：删除先删图、后台任务随后继续入图，节点会写进
        # 已不存在的 (course_id, document_id)，且文档行消失后再无接口能清理它们。
        # 只认进程内任务注册表——上次进程崩溃留下的 EXTRACTING 不在其中，仍允许删除。
        if int(doc_id) in _running_docs:
            return {"ok": False, "code": 4009,
                    "message": "文档正在处理中，请等待处理完成后再删除"}

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

        # 4.5 题库：答题记录 → 题目收藏 → 该文档题目
        # 顺序敏感（t_answer_record / t_question_favorite 均外键指向 t_question）：
        # 必须先删子表再删题目；课程通用题（document_id IS NULL）刻意保留，不随文档删除而消失。
        try:
            removed_answers = sql_db.delete_answers_by_document(cid, doc_id)
            removed_question_favorites = sql_db.delete_question_favorites_by_document(cid, doc_id)
            removed_questions = sql_db.delete_questions_by_document(cid, doc_id)
        except Exception as e:
            return {"ok": False, "code": 5002, "message": f"删除文档题库数据失败: {str(e)}"}

        # 4.8 文档级知识融合数据（run / candidate / map / conflict / scope）
        # 融合映射按 kp_id 指向本文件的节点，文档一删这些 kp_id 就再无意义；
        # 留下不清理会让 t_kp_fusion_map 里堆积指向不存在节点的"僵尸映射"。
        # 纯 SQLite 操作，不触碰 Neo4j；必须排在 DELETE FROM t_document 之前。
        try:
            removed_fusion = sql_db.delete_fusion_by_document(cid, doc_id)
        except Exception as e:
            return {"ok": False, "code": 5002, "message": f"删除文档融合记录失败: {str(e)}"}

        # 5. 本地文件（不存在视为已删，幂等）
        # 同样走路径解析：历史行的 file_path 可能是其他机器的路径，只按原值判断会漏删本机文件
        path = resolve_document_path(doc)
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
            "removed_questions": removed_questions,
            "removed_question_favorites": removed_question_favorites,
            "removed_answers": removed_answers,
            "removed_fusion": removed_fusion,
        }}
