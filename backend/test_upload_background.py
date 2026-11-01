"""阶段 4：单文件上传支持「后台处理」模式（默认仍同步，不破坏既有语义）。

背景：单文件上传长期是同步的——几十页 PDF 的解析 + 抽取 + 入图要几十秒到几分钟，
请求会一直挂着，前端只能干等（浏览器/网关超时还会直接断掉）。批量上传早就是
「落库即返回 + 后台任务」，单文件却仍走同步路径。

本脚本守住：
1. `background=True`：落库后立即返回 `status=processing`，处理交给后台任务；
2. 后台任务**确实被执行**（用打桩的 `_process_document` 观察，不调 LLM / 不连 Neo4j）；
3. `background=False`（默认）：保持同步语义，返回前处理已完成；
4. 校验失败（空文件）不落库；
5. HTTP 路由 `?background=1` 正确透传。

运行：cd backend && python test_upload_background.py
"""
import asyncio
import os
import shutil
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="kg_bgup_")
os.environ["SQLITE_DB_PATH"] = os.path.join(_TMP, "app.db")
os.environ["UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["AUTO_BACKUP"] = "0"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient                       # noqa: E402

from app.core.security import hash_password                     # noqa: E402
from app.core.sql_database import sql_db                        # noqa: E402
from app.main import app                                        # noqa: E402
from app.services.document_service import DocumentService       # noqa: E402

PASSWORD = "pw123456"
failures = []
total = 0
calls = []


def check(label, ok, detail=""):
    global total
    total += 1
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


async def fake_process(doc_id: int, resume: bool = False) -> dict:
    """打桩的处理函数。

    `resume` 是阶段 4 新增的入参（启动续跑专用），这里必须一起接住：
    后台任务入口会显式传 `resume=resume`，签名不匹配会直接把任务打成异常。
    """
    """打桩：记录调用并返回「已完成」，不碰 LLM / Neo4j。"""
    calls.append(int(doc_id))
    doc = sql_db.get_document(int(doc_id))
    return {"ok": True, "code": 0, "message": "success", "data": {
        "document_id": int(doc_id), "course_id": doc["course_id"],
        "filename": doc["file_name"], "status": "completed",
    }}


async def scenario(teacher_id, course_id):
    print("Step 1: background=True → 立即返回 processing，处理交给后台")
    calls.clear()
    res = await DocumentService.process_upload(
        content=b"hello world", filename="bg.txt", course_id=course_id,
        teacher_id=teacher_id, background=True)
    check("调用成功", res.get("ok") is True, str(res)[:160])
    data = res.get("data") or {}
    check("返回 status=processing", data.get("status") == "processing", str(data.get("status")))
    check("标记 background=True", data.get("background") is True, str(data.get("background")))
    check("计数为 0（尚未处理）", data.get("entity_count") == 0, str(data.get("entity_count")))
    check("返回时后台任务尚未执行完", calls == [], str(calls))
    bg_doc = data.get("document_id")

    await asyncio.sleep(0.1)          # 让出事件循环，后台任务执行
    check("后台任务随后被执行", bg_doc in calls, str(calls))

    print("\nStep 2: 默认同步模式保持原语义")
    calls.clear()
    res2 = await DocumentService.process_upload(
        content=b"hello sync", filename="sync.txt", course_id=course_id,
        teacher_id=teacher_id)
    check("同步调用成功", res2.get("ok") is True, str(res2)[:160])
    check("返回时处理已完成（打桩被同步调用）", len(calls) == 1, str(calls))
    check("status=completed", (res2.get("data") or {}).get("status") == "completed",
          str((res2.get("data") or {}).get("status")))

    print("\nStep 3: 校验失败不落库")
    before = sql_db._query_one(
        "SELECT count(*) AS c FROM t_document WHERE course_id = ?", (course_id,))["c"]
    res3 = await DocumentService.process_upload(
        content=b"", filename="empty.txt", course_id=course_id,
        teacher_id=teacher_id, background=True)
    check("空文件被拒 1003", res3.get("code") == 1003, str(res3)[:120])
    after = sql_db._query_one(
        "SELECT count(*) AS c FROM t_document WHERE course_id = ?", (course_id,))["c"]
    check("未创建文档记录", after == before, f"{before} -> {after}")


def main() -> int:
    client = TestClient(app)
    with client:
        teacher_id = sql_db.create_user("bg_teacher", hash_password(PASSWORD), role="teacher")
        course_id = sql_db.create_course("异步上传回归课程", teacher_id)

        original = DocumentService._process_document
        DocumentService._process_document = staticmethod(fake_process)
        try:
            asyncio.run(scenario(teacher_id, course_id))

            print("\nStep 4: HTTP 路由透传 background")
            calls.clear()
            token = client.post("/api/auth/login", json={
                "username": "bg_teacher", "password": PASSWORD}).json()["data"]["access_token"]
            resp = client.post(
                "/api/v1/documents/upload",
                params={"background": "true"},
                data={"course_id": str(course_id)},
                files={"file": ("http.txt", b"http async", "text/plain")},
                headers={"Authorization": f"Bearer {token}"},
            )
            body = resp.json()
            check("HTTP 200", resp.status_code == 200, f"HTTP {resp.status_code}")
            check("响应 status=processing",
                  (body.get("data") or {}).get("status") == "processing", str(body)[:160])
        finally:
            DocumentService._process_document = original

    print(f"\n通过 {total - len(failures)}/{total}" if not failures
          else f"\n失败 {len(failures)}/{total}：{failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
