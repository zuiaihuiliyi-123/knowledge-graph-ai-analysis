"""阶段 4 可靠性（4-1）：任务真正落库 + 断点续跑。

背景：解析（PDF 提取）与抽取（LLM 调用）都很贵，而任务只活在进程内。旧实现遇到重启
只能把文档标成 FAILED，教师点重试就要**从零再解析、再花钱调 LLM**。

本脚本守住：
1. 解析产物（parsed.txt）存在时，resume 跳过解析（解析函数被换成「一调用就炸」）；
2. 抽取产物（extracted.json）存在时，resume 跳过 LLM（抽取器被换成「一调用就炸」）；
3. 整个流水线成功后清空中间产物，任务行落成 stage=DONE / status=DONE；
4. `retry_document` 是「真的重来」——它清掉产物与任务行，不复用；
5. `reconcile_interrupted`：有产物 → 续跑（不改状态）；无产物 / 调度失败 /
   `RESUME_ON_STARTUP=0` → 仍按旧行为标 FAILED；
6. 产物带 sha256 指纹：文件内容对不上就不复用（不能被旧产物污染新文件）。

运行：cd backend && python test_task_resume.py
（临时库 + 临时产物目录 + AUTO_BACKUP=0 + EXTRACT_CACHE=0；不调 LLM / 不连 Neo4j）
"""
import asyncio
import hashlib
import os
import shutil
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="kg_resume_")
os.environ["SQLITE_DB_PATH"] = os.path.join(_TMP, "app.db")
os.environ["UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["TASK_STATE_DIR"] = os.path.join(_TMP, "task_state")
os.environ["AUTO_BACKUP"] = "0"
os.environ["EXTRACT_CACHE"] = "0"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

from app.core.config import settings                            # noqa: E402
from app.core.security import hash_password                     # noqa: E402
from app.core.sql_database import sql_db                        # noqa: E402
from app.services import document_service, task_state           # noqa: E402
from app.services.document_service import DocumentService       # noqa: E402

PASSWORD = "pw123456"
TEXT = "链表是一种线性结构。栈是后进先出。\n"
ENTITIES = [{"name": "链表", "category": "概念", "description": "线性结构"},
            {"name": "栈", "category": "概念", "description": "后进先出"}]
RELATIONS = [{"source": "栈", "target": "链表", "type": "RELATED_TO"}]

failures = []
total = 0


def check(label, ok, detail=""):
    global total
    total += 1
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def boom(*_args, **_kwargs):
    raise AssertionError("不应该被调用（产物已可复用）")


def make_doc(name="讲义.txt"):
    """建一篇文档：真实文件 + sha256 + file_path（相对 UPLOAD_DIR）。"""
    content = (TEXT + name).encode("utf-8")
    sha = hashlib.sha256(content).hexdigest()
    did = sql_db.create_document(COURSE, TEACHER, name, "TXT", len(content))
    rel = f"{COURSE}/{did}_{name}"
    full = os.path.join(settings.UPLOAD_DIR, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(content)
    sql_db.update_document(did, file_path=rel)
    sql_db._execute("UPDATE t_document SET file_sha256 = ? WHERE doc_id = ?", (sha, did))
    return did, sha


def status_of(doc_id):
    d = sql_db.get_document(doc_id)
    return (d["parse_status"], d["extract_status"], d.get("graph_status") or "")


def main() -> int:
    global COURSE, TEACHER
    sql_db.init_tables()
    TEACHER = sql_db.create_user("rs_teacher", hash_password(PASSWORD), role="teacher")
    COURSE = sql_db.create_course("断点续跑课程", TEACHER)

    # 这两步会真的连外部资源，统一替换掉
    document_service.KnowledgeGraphManager.build_graph = staticmethod(lambda *a, **k: None)
    document_service.db.delete_document_graph = lambda *a, **k: (0, 0)

    original_parse = document_service._parse_file_sync
    original_extractor_cls = document_service.KnowledgeExtractor
    original_schedule = DocumentService._schedule_processing

    print("Step 1: 只有解析产物 → 跳过解析，照常抽取与建图")
    did, sha = make_doc("只有解析产物.txt")
    sql_db.update_document(did, parse_status="PARSED", extract_status="EXTRACTING")
    task_state.save_parsed(did, sha, TEXT)

    calls = {"extract": 0}

    class FakeExtractor:
        async def extract(self, _text):
            calls["extract"] += 1
            return {"entities": ENTITIES, "relations": RELATIONS}

    document_service._parse_file_sync = boom          # 解析若被调用就会抛异常
    document_service.KnowledgeExtractor = FakeExtractor
    try:
        res = asyncio.run(DocumentService._process_document(did, resume=True))
    finally:
        document_service._parse_file_sync = original_parse
        document_service.KnowledgeExtractor = original_extractor_cls
    check("任务成功（解析函数虽被换成会炸的，但没有被调用）", res.get("ok") is True,
          str(res)[:120])
    check("抽取被调用 1 次（没有抽取产物时必须真抽取）", calls["extract"] == 1, str(calls))
    check("三阶段全部完成", status_of(did) == ("PARSED", "COMPLETED", "COMPLETED"),
          str(status_of(did)))
    check("成功后中间产物已清理", task_state.available(did, sha) == [],
          str(task_state.available(did, sha)))

    task = sql_db.get_document_task(did)
    check("任务行落成 DONE", bool(task) and task["status"] == "DONE" and task["stage"] == "DONE",
          str(task))
    check("attempts 已记账", bool(task) and task["attempts"] >= 1,
          str(task and task["attempts"]))

    print("\nStep 2: 解析 + 抽取产物都在 → 完全跳过 LLM")
    did2, sha2 = make_doc("两份产物都在.txt")
    sql_db.update_document(did2, parse_status="PARSED", extract_status="EXTRACTING")
    task_state.save_parsed(did2, sha2, TEXT)
    task_state.save_extraction(did2, sha2, ENTITIES, RELATIONS)

    class ExplodingExtractor:
        async def extract(self, _text):
            boom()

    document_service._parse_file_sync = boom
    document_service.KnowledgeExtractor = ExplodingExtractor
    try:
        res2 = asyncio.run(DocumentService._process_document(did2, resume=True))
    finally:
        document_service._parse_file_sync = original_parse
        document_service.KnowledgeExtractor = original_extractor_cls
    check("任务成功（解析与抽取都没跑）", res2.get("ok") is True, str(res2)[:120])
    check("实体数量来自产物", sql_db.get_document(did2)["entity_count"] == len(ENTITIES),
          str(sql_db.get_document(did2)["entity_count"]))
    check("完成后产物清空", task_state.available(did2, sha2) == [])


    print("\nStep 3: retry_document 是「真的重来」——清产物与任务行")
    did3, sha3 = make_doc("重试即重来.txt")
    sql_db.update_document(did3, parse_status="PARSED", extract_status="FAILED")
    task_state.save_parsed(did3, sha3, TEXT)
    sql_db.upsert_document_task(did3, COURSE, stage="EXTRACT", status="FAILED",
                                error_message="旧失败")
    scheduled = {"n": 0}

    def fake_schedule(doc_id, resume=False):
        scheduled["n"] += 1
        scheduled["resume"] = resume
        return True

    DocumentService._schedule_processing = staticmethod(fake_schedule)
    try:
        r = asyncio.run(DocumentService.retry_document(did3, TEACHER))
    finally:
        DocumentService._schedule_processing = staticmethod(original_schedule)
    check("重试被接受", r.get("ok") is True, str(r)[:120])
    check("产物已清空（不复用上次解析结果）", task_state.available(did3, sha3) == [])
    check("任务行已清空", sql_db.get_document_task(did3) is None)
    check("重新入队且不带 resume", scheduled["n"] == 1 and scheduled["resume"] is False,
          str(scheduled))

    print("\nStep 4: 启动恢复——有产物则续跑，不改状态")
    did4, sha4 = make_doc("启动续跑.txt")
    sql_db.update_document(did4, parse_status="PARSED", extract_status="EXTRACTING")
    task_state.save_parsed(did4, sha4, TEXT)
    resumed = []

    def record_schedule(doc_id, resume=False):
        resumed.append((doc_id, resume))
        return True

    DocumentService._schedule_processing = staticmethod(record_schedule)
    try:
        rec = DocumentService.reconcile_interrupted()
    finally:
        DocumentService._schedule_processing = staticmethod(original_schedule)
    check("被识别为续跑", (did4, True) in resumed, str(resumed))
    check("续跑的不改状态（保持 EXTRACTING，交给后台接着跑）",
          status_of(did4)[1] == "EXTRACTING", str(status_of(did4)))
    check("返回 resumed 计数", rec.get("resumed", 0) >= 1, str(rec))

    print("\nStep 5: 调度失败 / 无产物 / 开关关闭 → 仍标 FAILED")
    did5, sha5 = make_doc("调度失败.txt")
    sql_db.update_document(did5, parse_status="PARSED", extract_status="EXTRACTING")
    task_state.save_parsed(did5, sha5, TEXT)
    DocumentService._schedule_processing = staticmethod(lambda doc_id, resume=False: False)
    try:
        DocumentService.reconcile_interrupted()
    finally:
        DocumentService._schedule_processing = staticmethod(original_schedule)
    check("调度失败时标 FAILED", status_of(did5)[1] == "FAILED", str(status_of(did5)))

    did6, _ = make_doc("无产物.txt")
    sql_db.update_document(did6, parse_status="PARSING")
    DocumentService.reconcile_interrupted()
    check("无产物时标 FAILED", status_of(did6)[0] == "FAILED", str(status_of(did6)))

    did7, sha7 = make_doc("开关关闭.txt")
    sql_db.update_document(did7, parse_status="PARSED", extract_status="EXTRACTING")
    task_state.save_parsed(did7, sha7, TEXT)
    original_resume = settings.RESUME_ON_STARTUP
    settings.RESUME_ON_STARTUP = False
    DocumentService._schedule_processing = staticmethod(record_schedule)
    try:
        rec7 = DocumentService.reconcile_interrupted()
    finally:
        settings.RESUME_ON_STARTUP = original_resume
        DocumentService._schedule_processing = staticmethod(original_schedule)
    check("RESUME_ON_STARTUP=0 时不续跑，标 FAILED",
          status_of(did7)[1] == "FAILED", str(status_of(did7)))
    check("开关关闭时 resumed=0", rec7.get("resumed", 0) == 0, str(rec7))

    print("\nStep 6: sha256 不一致的产物不复用")
    did8, sha8 = make_doc("指纹不符.txt")
    sql_db.update_document(did8, parse_status="PARSED", extract_status="EXTRACTING")
    task_state.save_parsed(did8, "deadbeef" * 8, TEXT)   # 假装是另一份文件留下的
    check("产物体检为不可用", task_state.available(did8, sha8) == [],
          str(task_state.available(did8, sha8)))
    check("按 sha 读不到解析文本", task_state.load_parsed(did8, sha8) is None)
    DocumentService.reconcile_interrupted()
    check("指纹不符 → 标 FAILED", status_of(did8)[1] == "FAILED", str(status_of(did8)))

    print(f"\n通过 {total - len(failures)}/{total}" if not failures
          else f"\n失败 {len(failures)}/{total}：{failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)

