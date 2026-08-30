"""
金标准校验脚本：检查人工标注的 gold JSON 是否符合规范（不参与评测，只做形式与证据校验）

用法（backend 目录下）：
    python scripts/validate_gold.py eval_data/gold_第7章_树_待标注模板.json eval_data/第7章_树.txt

校验项：
1. JSON 可解析；text_id/source 非空
2. 实体：name 非空且不重复；category ∈ {概念,定理,公式,方法}；description 非空
3. 关系：type ∈ {PRECEDES,CONTAINS,RELATED_TO,APPLIES_TO}；source/target 都在实体中；无自环；无重复三元组
4. evidence 逐字存在于原文中（防编造）；confidence ∈ [0,1]
"""
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

VALID_CATEGORIES = ("概念", "定理", "公式", "方法")
VALID_TYPES = ("PRECEDES", "CONTAINS", "RELATED_TO", "APPLIES_TO")


def validate(gold_path: str, text_path: str) -> int:
    issues = []
    gold = json.loads(Path(gold_path).read_text(encoding="utf-8"))
    text = Path(text_path).read_text(encoding="utf-8").replace("\n", "")

    if not gold.get("text_id") or not gold.get("source"):
        issues.append("text_id/source 为空")

    entities = gold.get("entities", [])
    relations = gold.get("relations", [])

    # ---- 实体 ----
    names = []
    for i, e in enumerate(entities):
        name = (e.get("name") or "").strip()
        if not name:
            issues.append(f"实体[{i}] name 为空")
        names.append(name)
        cat = e.get("category")
        if cat not in VALID_CATEGORIES:
            issues.append(f"实体「{name}」category 非法: {cat}")
        if not (e.get("description") or "").strip():
            issues.append(f"实体「{name}」缺 description")

    dup = {n for n in names if names.count(n) > 1}
    if dup:
        issues.append(f"实体重名: {dup}")
    name_set = set(names)

    # ---- 关系 ----
    seen = set()
    for i, r in enumerate(relations):
        s, t = (r.get("source") or "").strip(), (r.get("target") or "").strip()
        typ = (r.get("type") or "").strip().upper()
        if typ not in VALID_TYPES:
            issues.append(f"关系[{i}] type 非法: {typ}")
            continue
        if s not in name_set or t not in name_set:
            issues.append(f"关系[{i}] 端点不在实体中: {s} -[{typ}]-> {t}")
        if s == t:
            issues.append(f"关系[{i}] 自环: {s}")
        key = (s, typ, t)
        if key in seen:
            issues.append(f"关系[{i}] 重复三元组: {s} -[{typ}]-> {t}")
        seen.add(key)

        ev = (r.get("evidence") or "").strip()
        if not ev:
            issues.append(f"关系[{i}] {s} -[{typ}]-> {t} 缺 evidence")
        elif ev not in text:
            issues.append(f"关系[{i}] evidence 不在原文中（逐字核对失败）: {ev[:40]}…")
        conf = r.get("confidence")
        if not isinstance(conf, (int, float)) or not (0 <= conf <= 1):
            issues.append(f"关系[{i}] confidence 非法: {conf}")

    # ---- 报告 ----
    print(f"实体 {len(entities)} 个 / 关系 {len(relations)} 条")
    if issues:
        print(f"发现 {len(issues)} 个问题：")
        for x in issues:
            print(f"  ✗ {x}")
        return 1
    print("全部校验通过 ✓（形式、端点、去重、evidence 逐字均合法）")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(validate(sys.argv[1], sys.argv[2]))
