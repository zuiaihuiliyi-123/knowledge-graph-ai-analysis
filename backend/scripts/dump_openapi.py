"""输出 OpenAPI 规范化 JSON 与 SHA-256，用于重构前后契约比对。

用法（backend 目录下）：
    python scripts/dump_openapi.py                 # 只打印哈希
    python scripts/dump_openapi.py --out openapi.json
"""
import argparse
import hashlib
import json
import pathlib
import sys

BACKEND_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.main import app  # noqa: E402


def canonical() -> str:
    return json.dumps(app.openapi(), ensure_ascii=False, sort_keys=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", help="可选：把规范化 JSON 写到指定文件")
    args = parser.parse_args()

    text = canonical()
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if args.out:
        pathlib.Path(args.out).write_text(
            json.dumps(json.loads(text), ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8")
    print(f"openapi_sha256={digest}")
    print(f"paths={len(app.openapi().get('paths', {}))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
