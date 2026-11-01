"""LLM 抽取可靠性回归测试（假客户端，不访问网络）。

覆盖：JSON mode 请求参数、供应商不支持时回退、结构校验、可恢复错误有限重试、
不可恢复错误不重试、截断/空响应分类、非法条目逐条丢弃。

注意：本脚本必须 **关闭抽取结果缓存**（`EXTRACT_CACHE=0`）——它反复用「同一段文本
+ 不同假响应」验证错误路径与新行为，缓存命中会让第二次之后的用例直接拿到第一次的结果。

运行：cd backend && python test_extraction_reliability.py
"""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

# 必须在导入 app.* 之前设置：config 在模块导入时读取环境变量
os.environ["EXTRACT_CACHE"] = "0"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.config import settings
from app.services.knowledge_extractor import KnowledgeExtractor

failures = []

VALID = json.dumps({
    "entities": [{"name": "二叉树", "category": "概念", "description": "一种树结构"}],
    "relations": [],
}, ensure_ascii=False)


def check(label, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


class FakeAuthError(Exception):
    status_code = 401


class FakeBadRequest(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


class FakeClient:
    """OpenAI 兼容客户端替身：按脚本返回 (content, finish_reason) 或抛异常。"""

    def __init__(self, script):
        self.records = []
        self.script = list(script)
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.records.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        content, finish_reason = item
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=content),
            finish_reason=finish_reason,
        )])


def run(script, json_mode=True):
    old_mode = settings.LLM_JSON_MODE
    settings.LLM_JSON_MODE = json_mode
    try:
        extractor = KnowledgeExtractor.__new__(KnowledgeExtractor)
        extractor.client = FakeClient(script)
        extractor.temperature = 0
        result = asyncio.run(extractor.extract("课程文本"))
        return result, extractor.client
    finally:
        settings.LLM_JSON_MODE = old_mode


def test_json_mode_and_valid_payload() -> None:
    result, client = run([(VALID, "stop")])
    check("JSON mode 请求参数生效",
          client.records[0].get("response_format") == {"type": "json_object"},
          str(client.records[0].get("response_format")))
    check("合法结果正常返回",
          not result.get("error") and len(result["entities"]) == 1,
          str(result))


def test_invalid_json_retries_once() -> None:
    result, client = run([("这不是 JSON", "stop"), (VALID, "stop")])
    check("非法 JSON 重试一次", len(client.records) == 2, str(len(client.records)))
    check("重试后成功", not result.get("error") and len(result["entities"]) == 1, str(result))


def test_schema_invalid_retries_and_classifies() -> None:
    bad = json.dumps({"entities": "oops", "relations": []})
    result, client = run([(bad, "stop"), (bad, "stop")])
    check("结构非法重试一次", len(client.records) == 2, str(len(client.records)))
    check("结构非法被归类",
          result.get("error_types") == ["schema_invalid"], str(result.get("error_types")))
    check("结构非法最终透传 error", bool(result.get("error")), str(result))


def test_auth_error_not_retried() -> None:
    result, client = run([FakeAuthError("invalid api key")])
    check("鉴权失败不重试", len(client.records) == 1, str(len(client.records)))
    check("鉴权失败被归类",
          result.get("error_types") == ["auth"], str(result.get("error_types")))


def test_json_mode_fallback() -> None:
    result, client = run([
        FakeBadRequest("response_format is not supported"),
        (VALID, "stop"),
    ])
    check("不支持 JSON mode 时回退", len(client.records) == 2, str(len(client.records)))
    check("回退请求不再携带 response_format",
          "response_format" not in client.records[1], str(client.records[1].keys()))
    check("回退后成功", not result.get("error"), str(result))


def test_bad_request_without_json_hint_not_retried() -> None:
    result, client = run([FakeBadRequest("prompt too long")])
    check("无关 400 不重试", len(client.records) == 1, str(len(client.records)))
    check("无关 400 归类为 bad_request",
          result.get("error_types") == ["bad_request"], str(result.get("error_types")))


def test_truncated_not_retried() -> None:
    result, client = run([(VALID, "length")])
    check("输出截断不重试", len(client.records) == 1, str(len(client.records)))
    check("输出截断被归类",
          result.get("error_types") == ["truncated"], str(result.get("error_types")))


def test_empty_response_retries() -> None:
    result, client = run([("", "stop"), (VALID, "stop")])
    check("空响应重试一次", len(client.records) == 2, str(len(client.records)))
    check("空响应重试后成功", not result.get("error") and len(result["entities"]) == 1, str(result))


def test_malformed_items_dropped_individually() -> None:
    payload = json.dumps({
        "entities": [
            {"name": "A", "category": "概念", "description": "a"},
            {"name": 123},
            "not-a-dict",
            {"name": "B"},
        ],
        "relations": [
            {"source": "A", "target": "B", "type": "PRECEDES"},
            {"source": 1, "target": "B", "type": "PRECEDES"},
        ],
    })
    result, _ = run([(payload, "stop")])
    names = sorted(e["name"] for e in result["entities"])
    check("合法实体保留、非法条目丢弃", names == ["A", "B"], str(names))
    check("合法关系保留、非法关系丢弃",
          len(result["relations"]) == 1 and result["relations"][0]["source"] == "A",
          str(result["relations"]))
    check("逐条丢弃不算整块失败", not result.get("error"), str(result))


def main() -> int:
    tests = [
        ("JSON mode + 合法结果", test_json_mode_and_valid_payload),
        ("非法 JSON 有限重试", test_invalid_json_retries_once),
        ("结构非法分类与重试", test_schema_invalid_retries_and_classifies),
        ("鉴权失败不重试", test_auth_error_not_retried),
        ("JSON mode 兼容回退", test_json_mode_fallback),
        ("无关 400 不重试", test_bad_request_without_json_hint_not_retried),
        ("输出截断不重试", test_truncated_not_retried),
        ("空响应可重试", test_empty_response_retries),
        ("非法条目逐条丢弃", test_malformed_items_dropped_individually),
    ]
    passed = 0
    for name, fn in tests:
        before = len(failures)
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - 测试脚本：异常转为失败项继续跑
            print(f"  [FAIL] {name}: {type(e).__name__}: {e}")
            failures.append(name)
        if len(failures) == before:
            passed += 1
    print(f"通过 {passed}/{len(tests)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
