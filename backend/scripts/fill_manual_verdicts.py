"""
把人工判定结果写回抽样判定表。

判定结果用一个 JSON 文件提供：
    {
      "relations": {"R1": {"verdict": "正确", "note": "..."}, ...},
      "entities":  {"E1": {"verdict": "正确", "note": "..."}, ...}
    }

用法（backend 目录下）：
    python scripts/fill_manual_verdicts.py eval_data/report6/manual_verdicts.json \
        --dir eval_data/report6/manual_check
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def fill(csv_path: Path, prefix: str, verdicts: dict) -> tuple[int, int]:
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig", newline="")))
    if not rows:
        raise SystemExit(f"空表：{csv_path}")
    verdict_key = next(k for k in rows[0] if k and k.strip().startswith("判定"))
    note_key = next((k for k in rows[0] if k and k.strip() == "备注"), None)
    filled = 0
    for row in rows:
        key = f"{prefix}{row['序号']}"
        item = verdicts.get(key)
        if not item:
            continue
        row[verdict_key] = item.get("verdict", "")
        if note_key is not None:
            row[note_key] = item.get("note", "")
        filled += 1
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return filled, len(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("verdicts", help="判定 JSON")
    ap.add_argument("--dir", default="eval_data/report6/manual_check")
    args = ap.parse_args()

    data = json.loads(Path(args.verdicts).read_text(encoding="utf-8"))
    d = Path(args.dir)
    r_filled, r_total = fill(d / "抽样判定表_关系.csv", "R", data.get("relations", {}))
    e_filled, e_total = fill(d / "抽样判定表_实体.csv", "E", data.get("entities", {}))
    print(f"关系：已填 {r_filled}/{r_total}")
    print(f"实体：已填 {e_filled}/{e_total}")


if __name__ == "__main__":
    main()
