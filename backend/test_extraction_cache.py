"""阶段 5'：抽取结果缓存（同一文本 + 同一 Prompt/模型/温度只调用一次 LLM）。

背景：知识抽取是整条链路最贵的一步。教师点「重试」（抽取成功但建图失败）、
重复上传同一份文件、开发期反复跑评测，都会对**同一段文本**重复调用 LLM。

本脚本守住：
1. 首次抽取写缓存；
2. **再次抽取（新实例，模拟重启/重试）命中缓存，LLM 调用 0 次**，结果一致；
3. 温度变化 → 键变化 → 重新调用（避免评测与日常抽取互相污染）；
4. `EXTRACT_CACHE=0` → 完全不读写缓存；
5. **失败结果不缓存**（否则一次限流/截断会被永久固化）；
6. `clear()` 后重新变回未命中。

运行：cd backend && python test_extraction_cache.py
（临时缓存目录 + 假客户端，不调外部 API、不连数据库/图库）
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace

_TMP = tempfile.mkdtemp(prefix="kg_extract_cache_")
os.environ["EXTRACT_CACHE_DIR"] = os.path.join(_TMP, "cache")
os.environ["EXTRACT_CACHE"] = "1"
os.environ["AUTO_BACKUP"] = "0"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

from app.core.config import settings                              # noqa: E402
from app.services import extraction_cache                        # noqa: E402
from app.services.knowledge_extractor import KnowledgeExtractor   # noqa: E402

VALID = json.dumps({
    "entities": [{"name": "栈", "category": "概念", "description": "后进先出的线性表"}],
    "relations": [],
}, ensure_ascii=False)
TEXT = "栈是后进先出的线性表，支持入栈与出栈两种基本操作。"
failures = []
total = 0


def check(label, ok, detail=""):
    global total
    total += 1
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


class FakeClient:
    """按脚本返回 (content, finish_reason)，并统计调用次数。"""

    def __init__(self, script=None):
        self.calls = 0
        self.script = list(script or [])
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.calls += 1
        content, finish = self.script.pop(0) if self.script else (VALID, "stop")
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=content), finish_reason=finish)])


def run_extract(temperature=0.15, script=None):
    ext = KnowledgeExtractor(temperature=temperature)
    client = FakeClient(script)
    ext.client = client
    result = asyncio.run(ext.extract(TEXT))
    return result, client


def names(result):
    return [e.get("name") for e in result.get("entities") or []]


def main() -> int:
    extraction_cache.clear()
    settings.EXTRACT_CACHE = True

    print("Step 1: 首次抽取 → 调用 LLM 并写缓存")
    first, client1 = run_extract()
    check("调用 LLM 1 次", client1.calls == 1, str(client1.calls))
    check("抽取到实体", names(first) == ["栈"], str(names(first)))
    check("缓存目录有 1 个条目", extraction_cache.stats()["entries"] == 1,
          str(extraction_cache.stats()))

    print("\nStep 2: 再次抽取（新实例，模拟重启/重试）→ 命中缓存")
    second, client2 = run_extract()
    check("未调用 LLM", client2.calls == 0, str(client2.calls))
    check("结果与首次一致", names(second) == names(first), str(names(second)))
    check("命中计数增加", extraction_cache.stats()["hit"] >= 1,
          str(extraction_cache.stats()["hit"]))

    print("\nStep 3: 温度变化 → 键变化 → 重新调用")
    third, client3 = run_extract(temperature=0)
    check("温度 0 未命中缓存", client3.calls == 1, str(client3.calls))
    check("缓存条目增至 2", extraction_cache.stats()["entries"] == 2,
          str(extraction_cache.stats()["entries"]))

    print("\nStep 4: EXTRACT_CACHE=0 → 不读写缓存")
    settings.EXTRACT_CACHE = False
    try:
        off1, c_off1 = run_extract()
        off2, c_off2 = run_extract()
        check("关闭后每次都调用 LLM", c_off1.calls == 1 and c_off2.calls == 1,
              f"{c_off1.calls}/{c_off2.calls}")
    finally:
        settings.EXTRACT_CACHE = True

    print("\nStep 5: 失败结果不缓存")
    extraction_cache.clear()
    bad_text = TEXT + "（失败样本）"
    ext = KnowledgeExtractor(temperature=0.15)
    # 抽取器对「可恢复错误」会重试一次（LLM_RETRY=1），所以脚本要连续给两次坏响应
    bad_client = FakeClient([("这不是 JSON", "stop"), ("这不是 JSON", "stop")])
    ext.client = bad_client
    bad = asyncio.run(ext.extract(bad_text))
    check("返回错误而不是伪造结果", bool(bad.get("error")), str(bad.get("error"))[:60])
    check("失败结果未写缓存", extraction_cache.stats()["entries"] == 0,
          str(extraction_cache.stats()["entries"]))
    ext2 = KnowledgeExtractor(temperature=0.15)
    good_client = FakeClient()
    ext2.client = good_client
    good = asyncio.run(ext2.extract(bad_text))
    check("同样文本再跑会真正调用 LLM", good_client.calls == 1, str(good_client.calls))
    check("这次成功并写入缓存", extraction_cache.stats()["entries"] == 1,
          str(extraction_cache.stats()["entries"]))
    check("成功结果含实体", names(good) == ["栈"], str(names(good)))

    print("\nStep 6: clear() 后重新未命中")
    removed = extraction_cache.clear()
    check("清空返回删除条数", removed == 1, str(removed))
    after, client_after = run_extract()
    check("清空后再次调用 LLM", client_after.calls == 1, str(client_after.calls))

    print(f"\n通过 {total - len(failures)}/{total}" if not failures
          else f"\n失败 {len(failures)}/{total}：{failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
