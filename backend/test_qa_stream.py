"""阶段 5'：SSE 流式问答（事件序列、上下文一致、错误路径、HTTP 端到端）。

为什么要流式：`/ask` 要等整段答案生成完（几秒到十几秒）才返回，前端只能转圈。
`/ask/stream` 先给证据链、再逐块给增量，首字延迟降到「首个 token」。

本脚本守住：
1. 事件顺序固定：sources → delta… → done；
2. **sources 就是喂给模型的上下文**（同一次检索，不重跑）；
3. 增量拼接结果 == done.answer；
4. LLM 调用带 `stream=True`；
5. 失败时给 `error` 事件、**不伪造答案**、不再发 done；
6. HTTP `/ask/stream` 返回 `text/event-stream` 且带 `X-Accel-Buffering: no`；
7. 原 `/ask` 同步端点行为不变（回归）。

运行：cd backend && python test_qa_stream.py
（临时库 + 假客户端，不调外部 API、不连 Neo4j）
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace

_TMP = tempfile.mkdtemp(prefix="kg_qa_stream_")
os.environ["SQLITE_DB_PATH"] = os.path.join(_TMP, "app.db")
os.environ["AUTO_BACKUP"] = "0"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient                       # noqa: E402

from app.api import qa as qa_api                                # noqa: E402
from app.core.dependencies import get_current_user              # noqa: E402
from app.core.security import hash_password                     # noqa: E402
from app.core.sql_database import sql_db                        # noqa: E402
from app.main import app                                        # noqa: E402
from app.services.qa_service import QAService                   # noqa: E402

SOURCES = [{"kp_id": "kp1", "name": "栈", "category": "概念",
            "description": "后进先出的线性表"}]
CHUNKS = ["栈是", "一种", "后进先出的", "线性表。"]
failures = []
total = 0


def check(label, ok, detail=""):
    global total
    total += 1
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


class FakeStream:
    """模拟 openai 的流式返回：可迭代出若干 chunk。"""

    def __init__(self, chunks):
        self._chunks = [SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(content=c))]) for c in chunks]

    def __iter__(self):
        return iter(self._chunks)


class FakeCompletions:
    def __init__(self, chunks=None, raise_exc=None):
        self.chunks = chunks or []
        self.raise_exc = raise_exc
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.raise_exc:
            raise self.raise_exc
        return FakeStream(self.chunks)


class FakeClient:
    def __init__(self, chunks=None, raise_exc=None):
        self.completions = FakeCompletions(chunks, raise_exc)
        self.chat = SimpleNamespace(completions=self.completions)


def make_service(chunks=None, raise_exc=None):
    svc = QAService()
    svc.client = FakeClient(chunks, raise_exc)
    svc.search_related_nodes = lambda *a, **k: list(SOURCES)
    return svc


def collect(svc):
    async def _run():
        return [e async for e in svc.stream_answer("栈是什么？")]
    return asyncio.run(_run())


def main() -> int:
    sql_db.init_tables()

    print("Step 1: 正常流式 —— 事件顺序与内容")
    svc = make_service(CHUNKS)
    events = collect(svc)
    kinds = [e["event"] for e in events]
    check("首事件是 sources", kinds and kinds[0] == "sources", str(kinds))
    check("末事件是 done", kinds and kinds[-1] == "done", str(kinds))
    check("中间都是 delta", all(k == "delta" for k in kinds[1:-1]), str(kinds))
    check("sources 数量正确", len(events[0]["data"]["sources"]) == 1,
          str(events[0]["data"]["sources"]))
    joined = "".join(e["data"]["text"] for e in events if e["event"] == "delta")
    check("增量拼接正确", joined == "".join(CHUNKS), joined)
    done = events[-1]["data"]
    check("done.answer == 拼接结果", done["answer"] == joined, done["answer"])
    check("done 同时带 sources", done["sources"] == SOURCES, str(done["sources"])[:60])

    print("\nStep 2: sources 就是喂给模型的上下文，且请求带 stream=True")
    kwargs = svc.client.completions.kwargs
    check("请求带 stream=True", kwargs.get("stream") is True, str(kwargs.get("stream")))
    system_msg = kwargs["messages"][0]["content"]
    check("system 提示里含检索到的知识点", "后进先出的线性表" in system_msg, system_msg[:80])
    check("真实问题作为 user 消息", kwargs["messages"][1]["content"] == "栈是什么？",
          kwargs["messages"][1]["content"])

    print("\nStep 3: 失败路径 —— 给 error 事件、不伪造答案")
    bad = make_service(raise_exc=RuntimeError("连接超时"))
    bad_events = collect(bad)
    kinds_bad = [e["event"] for e in bad_events]
    check("先给 sources", kinds_bad[0] == "sources", str(kinds_bad))
    check("随后是 error", kinds_bad[-1] == "error", str(kinds_bad))
    check("不出现 done（不能假装成功）", "done" not in kinds_bad, str(kinds_bad))
    check("错误信息明确指出不可用", "暂时不可用" in bad_events[-1]["data"]["message"],
          bad_events[-1]["data"]["message"])

    print("\nStep 4: HTTP /ask/stream 端到端")
    user_id = sql_db.create_user("qa_streamer", hash_password("pw123456"), role="student")
    fake_events = [{"event": "sources", "data": {"sources": SOURCES}},
                   {"event": "delta", "data": {"text": "栈是"}},
                   {"event": "delta", "data": {"text": "线性表"}},
                   {"event": "done", "data": {"answer": "栈是线性表", "sources": SOURCES}}]

    class FakeService:
        async def stream_answer(self, question, course_id=None, document_id=None,
                                allowed_ids=None):
            for e in fake_events:
                yield e

        async def ask_with_sources(self, question, course_id=None, document_id=None,
                                   allowed_ids=None):
            return {"answer": "栈是线性表", "sources": SOURCES}

    original_service = qa_api.qa_service
    qa_api.qa_service = FakeService()
    try:
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": user_id, "username": "qa_streamer", "role": "student"}
        client = TestClient(app)
        with client:
            resp = client.post("/api/v1/qa/ask/stream", json={"question": "栈是什么？"})
            check("HTTP 200", resp.status_code == 200, f"HTTP {resp.status_code}")
            check("Content-Type 是 event-stream",
                  "text/event-stream" in resp.headers.get("content-type", ""),
                  resp.headers.get("content-type"))
            check("禁用反向代理缓冲",
                  resp.headers.get("x-accel-buffering") == "no",
                  str(resp.headers.get("x-accel-buffering")))
            body = resp.text
            check("包含 sources 事件", "event: sources" in body, body[:80])
            check("包含 delta 事件", "event: delta" in body, body[:80])
            check("包含 done 事件", "event: done" in body, body[:80])

            frames = [f for f in body.split("\n\n") if f.strip()]
            parsed = []
            for frame in frames:
                lines = dict(ln.split(": ", 1) for ln in frame.splitlines() if ": " in ln)
                parsed.append((lines.get("event"), json.loads(lines.get("data", "{}"))))
            check("事件可解析且顺序正确",
                  [p[0] for p in parsed] == ["sources", "delta", "delta", "done"],
                  str([p[0] for p in parsed]))
            check("done 里答案完整", parsed[-1][1].get("answer") == "栈是线性表",
                  str(parsed[-1][1])[:80])

            print("\nStep 5: 原同步端点行为不变（回归）")
            resp2 = client.post("/api/v1/qa/ask", json={"question": "栈是什么？"})
            body2 = resp2.json()
            check("同步端点 200 且 code=0",
                  resp2.status_code == 200 and body2.get("code") == 0, str(body2)[:100])
            check("同步端点返回 answer 与 sources",
                  (body2.get("data") or {}).get("answer") == "栈是线性表"
                  and len((body2.get("data") or {}).get("sources") or []) == 1,
                  str(body2.get("data"))[:100])
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        qa_api.qa_service = original_service

    print(f"\n通过 {total - len(failures)}/{total}" if not failures
          else f"\n失败 {len(failures)}/{total}：{failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
