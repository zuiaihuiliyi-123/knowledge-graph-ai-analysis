"""文档级知识融合（Fusion）P2：LLM 语义消歧与降级契约

运行方式（在 backend 目录下执行）：
    python test_fusion_resolver.py

**不真调外部 API**：用 `FakeDisambiguator` 覆盖正常路径，用假客户端覆盖异常路径。
本脚本要守住的核心契约是：

    **一切异常路径都必须落 UNCERTAIN，永远不得落 SAME。**

（超时 / 非法 JSON / 缺字段 / 服务不可用 / 未配置 key —— 一条都不能例外。
 把"模型没说清"当成"是同一个实体"是本模块最危险的失败模式：它会永久污染图谱。）
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.services.fusion import entity_resolver as R
from app.services.fusion.entity_resolver import LlmDisambiguator, parse_strict_json, validate_verdict

failures = []


def check(label, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


class FakeClient:
    """OpenAI 兼容客户端的替身：按预设序列返回内容或抛异常"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.chat = self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.calls += 1
        item = self.script.pop(0) if self.script else self.script_last
        self.script_last = item
        if isinstance(item, Exception):
            raise item
        return type("R", (), {"choices": [
            type("C", (), {"message": type("M", (), {"content": item})()})()]})()


def pair(a_name="BFS", b_name="广度优先搜索"):
    return {
        "document_id": 1,
        "a": {"kp_id": "kp_a", "name": a_name, "category": "概念", "description": "缩写",
              "aliases": [], "reconstructed_context": {
                  "reconstruction_ok": True, "hit_terms": [a_name],
                  "chunk_indices": [1], "snippets": ["片段"]}},
        "b": {"kp_id": "kp_b", "name": b_name, "category": "概念", "description": "全称",
              "aliases": [], "reconstructed_context": {
                  "reconstruction_ok": True, "hit_terms": [b_name],
                  "chunk_indices": [1], "snippets": ["片段"]}},
    }


def main():
    print("=" * 72)
    print("文档级知识融合 P2：LLM 语义消歧（降级契约）")
    print("=" * 72)

    # ---------- 1. 严格 JSON 解析 ----------
    print("\nStep 1: 严格 JSON 解析")
    check("裸 JSON", parse_strict_json('{"same_entity": true}')["same_entity"] is True)
    check("markdown 代码块包裹",
          parse_strict_json('```json\n{"same_entity": false}\n```')["same_entity"] is False)
    check("夹杂说明文字时裁出对象",
          parse_strict_json('好的：{"same_entity": true, "confidence": 0.9} 以上')["same_entity"] is True)
    for bad in ("", "  ", "not json at all", "```json\n{坏掉的}\n```"):
        try:
            parse_strict_json(bad)
            check(f"非法输入应抛错: {bad[:16]!r}", False, "没有抛错")
        except ValueError:
            check(f"非法输入抛 ValueError: {bad[:16]!r}", True)

    # ---------- 2. 字段校验 ----------
    print("\nStep 2: 返回字段的校验")
    v = validate_verdict({"same_entity": True, "confidence": 0.93, "reason": "定义一致"})
    check("合法返回解析正确",
          v["decision"] == "SAME" and v["confidence"] == 0.93, str(v))
    check("false 映射为 DIFFERENT",
          validate_verdict({"same_entity": False})["decision"] == "DIFFERENT")

    bad_cases = [
        ({"confidence": 0.9}, "缺 same_entity"),
        ({"same_entity": "true"}, "same_entity 不是布尔"),
        ({"same_entity": True, "confidence": "高"}, "confidence 非数字"),
        ({"same_entity": True, "confidence": 1.5}, "confidence 越界"),
        ("不是对象", "返回不是 dict"),
    ]
    for raw, label in bad_cases:
        try:
            validate_verdict(raw)
            check(f"非法返回应被拒: {label}", False, "没有抛错")
        except ValueError:
            check(f"非法返回被拒: {label}", True)

    # ---------- 3. 正常路径 ----------
    print("\nStep 3: 正常路径（假客户端，不发真实请求）")
    d = LlmDisambiguator(client=FakeClient(['{"same_entity": true, "confidence": 0.94, "reason": "缩写与全称"}']))
    r = d.judge(pair())
    check("合法 JSON → SAME", r["decision"] == "SAME" and r["confidence"] == 0.94, str(r))

    d = LlmDisambiguator(client=FakeClient(['{"same_entity": false, "confidence": 0.9, "reason": "上下位"}']))
    check("返回 false → DIFFERENT", d.judge(pair())["decision"] == "DIFFERENT")

    # ---------- 4. 降级契约 ----------
    print("\nStep 4: 降级契约 —— 每条异常路径都必须是 UNCERTAIN")

    d = LlmDisambiguator(client=FakeClient(['这不是 JSON', '这也不是']))
    r = d.judge(pair())
    check("非法 JSON（重试后仍失败）→ UNCERTAIN", r["decision"] == "UNCERTAIN", str(r))
    from app.services.fusion import fusion_config as _C
    check(f"非法 JSON 按配置重试（共 {_C.LLM_RETRY + 1} 次调用）",
          d.client.calls == _C.LLM_RETRY + 1, f"实际调用 {d.client.calls} 次")

    d = LlmDisambiguator(client=FakeClient([TimeoutError("请求超时")]))
    r = d.judge(pair())
    check("超时 → UNCERTAIN（**不是 SAME**）", r["decision"] == "UNCERTAIN", str(r))
    check("超时原因被记录", "TimeoutError" in (r["error"] or ""), str(r["error"]))

    d = LlmDisambiguator(client=FakeClient([ConnectionError("服务不可用")]))
    r = d.judge(pair())
    check("服务不可用 → UNCERTAIN", r["decision"] == "UNCERTAIN", str(r))

    d = LlmDisambiguator(client=FakeClient(['{"same_entity": "或许吧"}']))
    r = d.judge(pair())
    check("字段类型不合规 → UNCERTAIN", r["decision"] == "UNCERTAIN", str(r))

    d = LlmDisambiguator(client=FakeClient(['{"confidence": 0.9}']))
    check("缺 same_entity → UNCERTAIN", d.judge(pair())["decision"] == "UNCERTAIN")

    # 未配置 key（client=None）
    d = LlmDisambiguator(client=None)
    d.client = None
    r = d.judge(pair())
    check("未配置 LLM_API_KEY → UNCERTAIN，且不发请求",
          r["decision"] == "UNCERTAIN" and r["error"] == "llm_unavailable", str(r))

    # 先失败后成功（真实调用里偶发限流的常见形态）
    d = LlmDisambiguator(client=FakeClient([TimeoutError("首包超时"),
                                            '{"same_entity": true, "confidence": 0.9}']))
    r = d.judge(pair())
    check("首次失败、重试成功 → 采纳重试结果", r["decision"] == "SAME", str(r))

    # ---------- 5. 全量兜底：任何异常都不得产生 SAME ----------
    print("\nStep 5: 穷举异常类型，断言绝不出现 SAME")
    exceptions = [TimeoutError("t"), ConnectionError("c"), ValueError("v"),
                  RuntimeError("r"), KeyError("k"), Exception("e")]
    leaked = []
    for exc in exceptions:
        d = LlmDisambiguator(client=FakeClient([exc]))
        if d.judge(pair())["decision"] == "SAME":
            leaked.append(type(exc).__name__)
    check("六类异常均未泄漏成 SAME", not leaked, str(leaked))

    # ---------- 6. 提示词里必须写明边界 ----------
    print("\nStep 6: 提示词必须向模型交代清楚边界")
    p = R.DISAMBIGUATION_PROMPT
    check("要求结合上下文、不得只看名称",
          "名称相似不等于同一个概念" in p, "")
    check("列出上下位/整体部分/属性对象/依赖四类不算同义",
          all(k in p for k in ("上位/下位", "整体与部分", "属性与对象", "依赖关系")), "")
    check("明确要求证据不足返回 uncertain", "证据不足时必须返回 uncertain" in p, "")
    check("声明上下文是重建的、不是原始抽取证据",
          "不是原始抽取证据" in p, "")
    rendered = LlmDisambiguator(client=None)._render(pair())
    check("渲染后的提示词含双方名称与类别",
          "BFS" in rendered and "广度优先搜索" in rendered, "")

    # 上下文不可用时必须显式说明
    p2 = pair()
    p2["a"]["reconstructed_context"] = {"reconstruction_ok": False, "reason": "文档解析失败"}
    rendered2 = LlmDisambiguator(client=None)._render(p2)
    check("上下文不可用时在提示词里显式标注",
          "不可用" in rendered2 and "文档解析失败" in rendered2, rendered2[:0])

    print("\n" + "=" * 72)
    if failures:
        print(f"失败 {len(failures)} 项：")
        for f in failures:
            print("  -", f)
    else:
        print("全部通过")
    print("=" * 72)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
