"""
六文档答辩版评测 —— 批量真实抽取 + 评分。

对 eval_data/report6/manifest.json 中的 6 篇文档逐篇执行：
    eval_accuracy.py --live --runs 3 --temperature 0.3

每篇产出：
    pred/<name>.json            预测（3 轮原始结果）
    eval_report/<name>.json     完整评分
    report/<name>.md            Markdown 报告
    logs/<name>.log             控制台日志

最后汇总为 eval_data/report6/summary.json。

注意：EXTRACT_CACHE=0，确保每一轮都是真实 LLM 调用，不读旧缓存。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BACKEND = Path(__file__).resolve().parent.parent
BASE = BACKEND / "eval_data" / "report6"


def main() -> None:
    ap = argparse.ArgumentParser(description="六文档批量抽取与评分")
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out-subdir", default="", help="输出子目录，如 t0；空表示 report6 根目录")
    ap.add_argument("--prompt-version", default="v1.9.1")
    args = ap.parse_args()

    OUT = BASE / args.out_subdir if args.out_subdir else BASE
    manifest = json.loads((BASE / "manifest.json").read_text(encoding="utf-8"))
    for sub in ("pred", "eval_report", "report", "logs"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["EXTRACT_CACHE"] = "0"
    env["PYTHONIOENCODING"] = "utf-8"

    rows = []
    for item in manifest:
        name = item["name"]
        gold = BASE / "gold" / f"{name}.json"
        text = BASE / "texts" / f"{name}.txt"
        pred = OUT / "pred" / f"{name}.json"
        report = OUT / "eval_report" / f"{name}.json"
        markdown = OUT / "report" / f"{name}.md"
        log = OUT / "logs" / f"{name}.log"

        cmd = [
            sys.executable, "eval_accuracy.py", "--live",
            "--gold", str(gold.relative_to(BACKEND)),
            "--text", str(text.relative_to(BACKEND)),
            "--runs", str(args.runs), "--temperature", str(args.temperature),
            "--pred", str(pred.relative_to(BACKEND)),
            "--output", str(report.relative_to(BACKEND)),
            "--markdown", str(markdown.relative_to(BACKEND)),
        ]
        print(f"\n===== {name} =====", flush=True)
        print(" ".join(cmd), flush=True)
        started = datetime.now()
        proc = subprocess.run(
            cmd, cwd=BACKEND, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        elapsed = (datetime.now() - started).total_seconds()
        log.write_text(proc.stdout + "\n\n[STDERR]\n" + proc.stderr, encoding="utf-8")
        print(proc.stdout[-3000:], flush=True)
        if proc.returncode != 0:
            print(f"!! {name} 失败，返回码 {proc.returncode}，见 {log}", flush=True)
            rows.append({"name": name, "error": proc.returncode, "elapsed_s": elapsed})
            continue

        data = json.loads(report.read_text(encoding="utf-8"))
        s = data["summary"]
        st = data["stability"]
        rows.append(
            {
                "name": name,
                "course": item["course"],
                "characters": item["characters"],
                "gold_entities": item["entities"],
                "gold_relations": item["relations"],
                "elapsed_s": round(elapsed, 1),
                "entity_f1_mean": st["entity_f1"]["mean"],
                "entity_f1_std": st["entity_f1"]["std"],
                "entity_category_accuracy": st["entity_category_accuracy"]["mean"],
                "relation_primary_f1_mean": st["relation_primary_f1"]["mean"],
                "relation_primary_f1_std": st["relation_primary_f1"]["std"],
                "relation_primary_precision": st["relation_primary_precision"]["mean"],
                "relation_primary_recall": st["relation_primary_recall"]["mean"],
                "relation_core_f1": st["relation_core_f1"]["mean"],
                "relation_full_f1": st["relation_full_f1"]["mean"],
                "relation_macro_f1": st["relation_macro_f1_active"]["mean"],
                "direction_error_rate": st["direction_error_rate"]["mean"],
                "pred_total": s["relation_primary_micro"]["pred_total"],
                "hit_full": s["relation_primary_micro"]["hit_full"],
                "core_total": s["relation_primary_micro"]["core_total"],
                "hit_core": s["relation_primary_micro"]["hit_core"],
                "per_round_f1": st["relation_primary_f1"]["values"],
                "relation_by_type": s["relation_by_type"],
                "error_taxonomy": s["error_taxonomy_totals"],
            }
        )

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "prompt_version": args.prompt_version,
        "model": "deepseek-chat",
        "temperature": args.temperature,
        "runs": args.runs,
        "extract_cache": False,
        "documents": rows,
    }
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n汇总已写入 {OUT / 'summary.json'}", flush=True)


if __name__ == "__main__":
    main()
