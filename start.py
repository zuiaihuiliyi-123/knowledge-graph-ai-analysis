#!/usr/bin/env python3
"""仓库根目录兼容入口：实际启动逻辑在 scripts/start.py。"""
import runpy
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "scripts" / "start.py"
runpy.run_path(str(SCRIPT), run_name="__main__")
