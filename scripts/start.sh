#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then
  # Windows Git Bash：venv 的解释器在 Scripts/ 下
  PYTHON_BIN=".venv/Scripts/python.exe"
elif python3 -c "import sys" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif python -c "import sys" >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "[start] 未找到 Python，请先安装 Python 3.12+" >&2
  exit 1
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/start.py" "$@"
