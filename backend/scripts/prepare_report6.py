"""
为《知识抽取准确率测试报告（六文档答辩版）》准备评测集。

做三件事：
1. 把 6 篇真实章节的原文复制到 eval_data/report6/texts/（路径固定，便于复现）
2. 修正旧 gold 中两类**形式问题**（不改任何实体/关系/方向/core 判定）：
   - 缺 confidence：补 0.9
   - evidence 含省略号或改写、无法逐字命中：替换为原文中包含 source/target 的逐字句
3. 写出 manifest.json（源文件、修复条数、SHA256）

只改形式、不改语义。修复前/后的差异逐条记录，报告里会披露。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "eval_data" / "report6"

# (输出名, gold, 原文, 课程, 备注)
DOCS = [
    (
        "d1_数据结构_第7章_树",
        ROOT / "eval_data/gold_第7章_树.json",
        ROOT / "eval_data/第7章_树.txt",
        "数据结构",
        "《Hello 算法》第 7 章；原始 gold 已完整通过 validate_gold",
    ),
    (
        "d2_数据结构_第2章_复杂度分析",
        ROOT / "eval_data/gold_数据结构_第2章_复杂度分析_人工版.json",
        ROOT / "eval_data/第2章_复杂度分析.txt",
        "数据结构",
        "《Hello 算法》第 2 章；原始 gold 已完整通过 validate_gold",
    ),
    (
        "d3_物理_第2章_运动学",
        ROOT / "eval_data/gold_物理_第2章_运动学_人工版.json",
        ROOT / "eval_data/第2章_运动学_Kinematics_中文版.txt",
        "物理",
        "OpenStax College Physics 2e 中译；原始 gold 已完整通过 validate_gold",
    ),
    (
        "d4_化学_第2章_原子分子与离子",
        ROOT / "eval_data/gold_化学_第2章_原子分子与离子_人工版.json",
        ROOT / "eval_data/第2章_原子分子与离子_中文版.txt",
        "化学",
        "OpenStax Chemistry 2e 中译；原始 gold 已完整通过 validate_gold",
    ),
    (
        "d5_化学_第4章_化学反应计量",
        ROOT / "eval_data/gold_第4章_化学反应计量_中文版_人工版.json",
        ROOT / "data/sample_docs/openstax_化学/第4章_化学反应计量_中文版.txt",
        "化学",
        "OpenStax Chemistry 2e 中译；原 gold 缺 confidence，实体/关系/core 未改动",
    ),
    (
        "d6_化学_第1章_化学基本概念",
        ROOT / "eval_data/gold_第1章_化学基本概念_中文版_人工版.json",
        ROOT / "data/sample_docs/openstax_化学/第1章_化学基本概念_中文版.txt",
        "化学",
        "OpenStax Chemistry 2e 中译；原 gold 缺 confidence 且 2 条 evidence 无法逐字命中",
    ),
]


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fix_evidence(ev: str, text_no_nl: str, source: str, target: str) -> tuple[str, str]:
    """把无法逐字命中的 evidence 换成原文中的逐字片段。返回 (新证据, 修复方式)。"""
    ev = (ev or "").strip()
    if ev and norm(ev) in norm(text_no_nl):
        return ev, ""

    # 1) 省略号前的部分往往就是原文
    for sep in ("……", "…", "..."):
        if sep in ev:
            prefix = ev.split(sep, 1)[0].strip()
            if prefix and norm(prefix) in norm(text_no_nl):
                return prefix, "截断省略号"

    # 2) 在原文中找包含 source/target 的完整句子
    sentences = re.split(r"(?<=[。！？；])", text_no_nl)
    for name in (source, target):
        if not name:
            continue
        for sent in sentences:
            sent = sent.strip()
            if name in sent and 8 <= len(sent) <= 120:
                return sent, f"替换为含「{name}」的原文句"

    # 3) 退路：取包含名字的前后窗口（仍保证是原文连续子串）
    for name in (source, target):
        idx = text_no_nl.find(name)
        if idx >= 0:
            start = max(0, idx - 20)
            end = min(len(text_no_nl), idx + len(name) + 40)
            return text_no_nl[start:end], f"原文窗口「{name}」"

    return ev, "无法修复"


def main() -> None:
    (OUT / "texts").mkdir(parents=True, exist_ok=True)
    (OUT / "gold").mkdir(parents=True, exist_ok=True)

    manifest = []
    for name, gold_path, text_path, course, note in DOCS:
        text = text_path.read_text(encoding="utf-8")
        text_no_nl = text.replace("\n", "")
        dst_text = OUT / "texts" / f"{name}.txt"
        shutil.copyfile(text_path, dst_text)

        data = json.loads(gold_path.read_text(encoding="utf-8"))
        docs = data if isinstance(data, list) else [data]
        patched_conf = 0
        patched_ev = 0
        evidence_changes = []

        for doc in docs:
            for rel in doc.get("relations", []):
                conf = rel.get("confidence")
                if not isinstance(conf, (int, float)) or isinstance(conf, bool) or not (0 <= conf <= 1):
                    rel["confidence"] = 0.9
                    patched_conf += 1

                old_ev = (rel.get("evidence") or "").strip()
                new_ev, how = fix_evidence(
                    old_ev, text_no_nl, rel.get("source", ""), rel.get("target", "")
                )
                if how:
                    rel["evidence"] = new_ev
                    patched_ev += 1
                    evidence_changes.append(
                        {
                            "triple": f"{rel.get('source')} -[{rel.get('type')}]-> {rel.get('target')}",
                            "old": old_ev,
                            "new": new_ev,
                            "how": how,
                        }
                    )

        dst_gold = OUT / "gold" / f"{name}.json"
        dst_gold.write_text(
            json.dumps(data if isinstance(data, list) else docs[0], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        entities = sum(len(d.get("entities", [])) for d in docs)
        relations = sum(len(d.get("relations", [])) for d in docs)
        relation_types = sorted(
            {r.get("type") for d in docs for r in d.get("relations", []) if r.get("type")}
        )

        manifest.append(
            {
                "name": name,
                "course": course,
                "note": note,
                "source_gold": str(gold_path.relative_to(ROOT)),
                "source_text": str(text_path.relative_to(ROOT)),
                "characters": len(text),
                "entities": entities,
                "relations": relations,
                "relation_types": relation_types,
                "patched_confidence": patched_conf,
                "patched_evidence": patched_ev,
                "evidence_changes": evidence_changes,
                "gold_sha256": sha256(dst_gold),
                "text_sha256": sha256(dst_text),
            }
        )
        print(
            f"{name}: {len(text)} 字符, {entities} 实体, {relations} 关系, "
            f"类型={relation_types}, 修复 confidence={patched_conf}, evidence={patched_ev}"
        )

    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nmanifest: {OUT / 'manifest.json'}")


if __name__ == "__main__":
    main()
