"""
复现 v1.8 Prompt 在 D5/D6 上的 1 轮基线（仅用于报告中的迭代对照）。

做法：从 git HEAD 读取旧版 `knowledge_extractor.py`，用 AST 取出其中的
`EXTRACTION_PROMPT` 字符串，在运行时替换当前模块的同名全局变量，再走
`eval_accuracy.run_live_extraction` 真实调用 LLM。**不修改工作区文件**。

用法（backend 目录下）：
    EXTRACT_CACHE=0 python scripts/run_v18_baseline.py
"""
from __future__ import annotations

import ast
import asyncio
import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
OUT = BACKEND / "eval_data" / "report6" / "v18_baseline"

sys.path.insert(0, str(BACKEND))


def load_v18_prompt() -> str:
    src = subprocess.check_output(
        ["git", "show", "HEAD:backend/app/services/knowledge_extractor.py"],
        cwd=REPO, text=True, encoding="utf-8",
    )
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "EXTRACTION_PROMPT":
                    return ast.literal_eval(node.value)
    raise RuntimeError("HEAD 中找不到 EXTRACTION_PROMPT")


def main() -> None:
    old_prompt = load_v18_prompt()

    import app.services.knowledge_extractor as ke
    ke.EXTRACTION_PROMPT = old_prompt
    print(f"已切换到 HEAD 版 Prompt（{len(old_prompt)} 字符）")

    from eval_accuracy import run_live_extraction, save_json

    for sub in ("pred", "eval_report", "report", "logs"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)

    docs = [
        ("d5_化学_第4章_化学反应计量", "d5"),
        ("d6_化学_第1章_化学基本概念", "d6"),
    ]
    for name, tag in docs:
        gold = BACKEND / "eval_data/report6/gold" / f"{name}.json"
        text_path = BACKEND / "eval_data/report6/texts" / f"{name}.txt"
        text = text_path.read_text(encoding="utf-8")
        pred = OUT / "pred" / f"{name}.json"
        report = OUT / "eval_report" / f"{name}.json"
        markdown = OUT / "report" / f"{name}.md"

        gold_data = json.loads(gold.read_text(encoding="utf-8"))
        gold_doc = gold_data[0] if isinstance(gold_data, list) else gold_data
        text_id = gold_doc["text_id"]

        print(f"\n[{tag}] 调用 v1.8 Prompt 抽取 1 轮…", flush=True)
        runs = asyncio.run(
            run_live_extraction(text=text, runs=1, temperature=0.3)
        )
        save_json(pred, [{"text_id": text_id, "runs": runs}])
        print(f"[{tag}] 预测已落盘：{pred}（text_id={text_id}）")

        cmd = [
            sys.executable, "eval_accuracy.py",
            "--gold", str(gold.relative_to(BACKEND)),
            "--pred", str(pred.relative_to(BACKEND)),
            "--output", str(report.relative_to(BACKEND)),
            "--markdown", str(markdown.relative_to(BACKEND)),
        ]
        subprocess.run(cmd, cwd=BACKEND, check=True)

    print("\n完成。v1.8 基线产物在 eval_data/report6/v18_baseline/")


if __name__ == "__main__":
    main()
