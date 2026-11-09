"""抽取诊断模块单元测试（纯离线，不访问网络、不写数据库）。

覆盖 v1.4 的 12 项诊断能力：
证据四分类、原文缺失时的降级、实体名称记录、direction_basis 三态、
unmatched_mentions 清洗/去重/限量/代码符号过滤、跨分块合并、诊断不修改输入。

运行：cd backend && python test_extraction_diagnostics.py
"""

import copy
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.services import extraction_diagnostics as diag

failures = []


def check(label, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def test_evidence_categories() -> None:
    source = "二叉树是一种树结构。完全二叉树是二叉树的一种。时间复杂度 O(n)。"

    check("1. 逐字证据判 exact_match",
          diag.classify_evidence("完全二叉树是二叉树的一种", source) == diag.EVIDENCE_EXACT,
          diag.classify_evidence("完全二叉树是二叉树的一种", source))

    check("2. 全角/半角差异判 normalized_match",
          diag.classify_evidence("时间复杂度 O(n)", "时间复杂度　Ｏ（ｎ）。") == diag.EVIDENCE_NORMALIZED,
          diag.classify_evidence("时间复杂度 O(n)", "时间复杂度　Ｏ（ｎ）。"))

    check("3. 多余空白差异判 normalized_match",
          diag.classify_evidence("二叉树 是 一种 树结构", "二叉树  是 一种 树结构。")
          == diag.EVIDENCE_NORMALIZED,
          diag.classify_evidence("二叉树 是 一种 树结构", "二叉树  是 一种 树结构。"))

    check("4. 改写句判 paraphrase",
          diag.classify_evidence("二叉树包括完全二叉树", source) == diag.EVIDENCE_PARAPHRASE,
          diag.classify_evidence("二叉树包括完全二叉树", source))

    check("5. 空/非字符串证据判 missing",
          all(diag.classify_evidence(value, source) == diag.EVIDENCE_MISSING
              for value in ("", "   ", None, 123)),
          "空串/空白/None/数字")

    check("6. 无原文时非空证据记为 paraphrase 且标记不可核对",
          diag.classify_evidence("任意证据", "") == diag.EVIDENCE_PARAPHRASE,
          diag.classify_evidence("任意证据", ""))


def test_build_diagnostics() -> None:
    source = "二叉树是一种树结构。完全二叉树是二叉树的一种。要遍历必须先掌握递归。"
    payload = {
        "entities": [
            {"name": "二叉树", "category": "概念", "description": "树结构", "evidence": "二叉树是一种树结构"},
            {"name": "完全二叉树", "category": "概念", "description": "特殊二叉树",
             "evidence": "完全二叉树包括二叉树"},  # paraphrase
            {"name": "ＣＮＮ", "category": "概念", "description": "卷积网络"},  # normalized name
            {"name": "递归", "category": "方法", "description": "方法", "evidence": 123},  # 类型异常→missing
        ],
        "relations": [
            {"source": "二叉树", "target": "完全二叉树", "type": "CONTAINS",
             "evidence": "完全二叉树是二叉树的一种", "direction_basis": "上位概念指向下位概念"},
            {"source": "递归", "target": "二叉树", "type": "PRECEDES",
             "evidence": "要遍历必须先掌握递归", "direction_basis": ""},
            {"source": "完全二叉树", "target": "二叉树", "type": "RELATED_TO", "evidence": ""},
        ],
        "unmatched_mentions": [
            {"name": "红黑树", "evidence": "红黑树是一种二叉搜索树", "reason": "未纳入实体"},
            {"name": "红黑树", "evidence": "红黑树是一种二叉搜索树", "reason": "重复"},
        ],
    }
    before = copy.deepcopy(payload)
    result = diag.build_diagnostics(payload, source, normalize_name=None)
    summary = result["summary"]

    check("7. 实体证据四分类计数正确",
          summary["entities"]["evidence"] == {
              diag.EVIDENCE_EXACT: 1,
              diag.EVIDENCE_NORMALIZED: 0,
              diag.EVIDENCE_PARAPHRASE: 1,
              diag.EVIDENCE_MISSING: 2,
          },
          str(summary["entities"]["evidence"]))

    names = {item["name"]: item for item in result["entities"]}
    check("8. 记录原始名/规范名/是否变名/是否在原文出现",
          names["ＣＮＮ"]["normalized_name"] == "CNN"
          and names["ＣＮＮ"]["name_changed"] is True
          and names["ＣＮＮ"]["name_in_source"] is False
          and names["二叉树"]["name_in_source"] is True,
          str(names["ＣＮＮ"]))

    direction = summary["relations"]["direction_basis"]
    check("9. direction_basis 三态计数正确（有内容/空值/缺失）",
          direction == {"present": 2, "empty": 1, "missing": 1},
          str(direction))

    check("10. unmatched_mentions 去重后只留一条",
          len(result["unmatched_mentions"]) == 1
          and result["unmatched_mentions"][0]["name"] == "红黑树",
          str(result["unmatched_mentions"]))

    check("11. 诊断不修改传入的抽取结果",
          payload == before, "payload 应与调用前完全一致")


def test_unmatched_sanitize() -> None:
    raw = [
        {"name": "红黑树", "evidence": "原文", "reason": "原因"},
        {"name": "红黑树", "evidence": "原文", "reason": "重复"},
        "链表",
        "==",
        "BUILD_TREE()",
        {"name": "", "evidence": "空名"},
        {"name": 123, "evidence": "非字符串名"},
        {"name": "  完全二叉树  ", "evidence": "原文"},
        None,
    ]
    cleaned = diag.sanitize_unmatched_mentions(raw, max_items=10)
    names = [item["name"] for item in cleaned]

    check("12. 清洗去重并过滤空名/非字典/明显代码符号",
          names == ["红黑树", "链表", "完全二叉树"],
          str(names))

    limited = diag.sanitize_unmatched_mentions(
        [{"name": f"知识点{i}"} for i in range(5)], max_items=2)
    check("13. 超过上限时截断而非报错",
          len(limited) == 2, str(len(limited)))

    check("14. 非 list 输入降级为空列表",
          diag.sanitize_unmatched_mentions({"name": "红黑树"}) == [],
          str(diag.sanitize_unmatched_mentions({"name": "红黑树"})))

    merged = diag.merge_unmatched_mentions([
        {"name": "红黑树", "evidence": "A"},
        {"name": "红黑树", "evidence": "A"},
        {"name": "红黑树", "evidence": "B"},
    ])
    check("15. 跨分块合并按 name+evidence 去重",
          len(merged) == 2, str(merged))


def test_detail_cap_and_empty_source() -> None:
    payload = {"entities": [{"name": f"实体{i}"} for i in range(3)], "relations": []}
    result = diag.build_diagnostics(payload, "实体0 实体1 实体2", max_items=1)
    check("16. 明细条数可截断但计数仍为精确总数",
          len(result["entities"]) == 1 and result["summary"]["entities"]["total"] == 3,
          f"明细={len(result['entities'])} 总数={result['summary']['entities']['total']}")

    empty = diag.build_diagnostics(payload, "")
    check("17. 无原文时 summary.source_available=False 且不抛异常",
          empty["summary"]["source_available"] is False,
          str(empty["summary"]["source_available"]))


def main() -> None:
    print("抽取诊断模块单元测试")
    test_evidence_categories()
    test_build_diagnostics()
    test_unmatched_sanitize()
    test_detail_cap_and_empty_source()
    total = 17
    print(f"\n通过 {total - len(failures)}/{total}")
    if failures:
        print("失败项：" + "；".join(failures))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
