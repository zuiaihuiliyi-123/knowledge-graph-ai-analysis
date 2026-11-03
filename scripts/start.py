#!/usr/bin/env python3
"""跨平台一键启动：Neo4j（可选）+ FastAPI 后端 + Vite 前端。

设计目标：
- 不写死任何本机路径。Neo4j 优先用 NEO4J_HOME / PATH / 常见安装目录发现；
  找不到或连接失败时**不阻塞**前后端启动（SQLite 会由后端自动建库）。
- Windows / macOS / Linux 共用这一份脚本；根目录 start.bat、start.sh 只是入口包装。
- 首次运行可在缺少依赖时自动安装（--no-install 可关闭）。
- Ctrl+C 会结束由本脚本启动的进程（不会动用户原本已开着的服务）。

用法：
    python start.py                 # 自动检查环境并启动全部
    python start.py --check         # 只做环境检查，不启动、不安装
    python start.py --backend-only  # 只启动后端
    python start.py --frontend-only # 只启动前端
    python start.py --no-db         # 跳过 Neo4j 检查/启动
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

# 兼容两种放置方式：仓库根目录的 start.py，或 scripts/start.py。
# 主逻辑放在 scripts/ 时，项目根目录是它的上一级，否则 backend/、frontend-vue/
# 会被错误解析成 scripts/backend、scripts/frontend-vue。
_SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SCRIPT_DIR.parent if _SCRIPT_DIR.name == "scripts" else _SCRIPT_DIR
BACKEND_DIR = PROJECT_ROOT / "backend"
FRONTEND_DIR = PROJECT_ROOT / "frontend-vue"
ENV_PATH = BACKEND_DIR / ".env"
ENV_EXAMPLE = BACKEND_DIR / ".env.example"
REQUIREMENTS = BACKEND_DIR / "requirements.txt"
NODE_MODULES = FRONTEND_DIR / "node_modules"

BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8000
FRONTEND_HOST = "127.0.0.1"
FRONTEND_PORT = 5173
NEO4J_DEFAULT_PORT = 7687
IS_WINDOWS = os.name == "nt"

# 运行时真正会 import 的第三方包（matplotlib 仅评测脚本用，不参与启动检查）。
BACKEND_IMPORTS = (
    "fastapi", "uvicorn", "neo4j", "openai", "jwt", "dotenv", "numpy",
    "pydantic", "multipart", "docx", "pdfplumber", "PyPDF2", "jieba",
    "tiktoken", "aiofiles",
)

_print_lock = threading.Lock()


def _configure_stdio() -> None:
    """Git Bash / 重定向到管道时，Windows 默认 cp936 会把中文打成乱码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def info(message: str = "") -> None:
    with _print_lock:
        print(message, flush=True)


def warn(message: str) -> None:
    info(f"[警告] {message}")


def step(message: str) -> None:
    info(f"\n==> {message}")


class StartupError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 子进程管理
# ---------------------------------------------------------------------------
class ProcessManager:
    """统一管理子进程，保证 Ctrl+C / 异常退出时不留孤儿进程。"""

    def __init__(self):
        self.items: list[tuple[str, subprocess.Popen]] = []
        self._stopping = False

    def spawn(self, label: str, cmd: list[str], cwd: Path,
              extra_env: dict[str, str] | None = None) -> subprocess.Popen:
        env = os.environ.copy()
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUNBUFFERED", "1")
        if extra_env:
            env.update(extra_env)

        kwargs: dict = {}
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True

        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **kwargs,
        )
        self.items.append((label, proc))
        threading.Thread(target=self._pump, args=(label, proc), daemon=True).start()
        return proc

    @staticmethod
    def _pump(label: str, proc: subprocess.Popen) -> None:
        if proc.stdout is None:
            return
        for line in iter(proc.stdout.readline, ""):
            with _print_lock:
                print(f"[{label}] {line.rstrip()}", flush=True)

    def first_exited(self) -> tuple[str, int] | None:
        for label, proc in self.items:
            code = proc.poll()
            if code is not None:
                return label, code
        return None

    def stop(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        for label, proc in reversed(self.items):
            if proc.poll() is not None:
                continue
            info(f"[停止] {label} (PID {proc.pid})")
            self._terminate_tree(proc)
        deadline = time.time() + 8
        for _label, proc in self.items:
            while proc.poll() is None and time.time() < deadline:
                time.sleep(0.1)
            if proc.poll() is None:
                self._kill_tree(proc)

    @staticmethod
    def _terminate_tree(proc: subprocess.Popen) -> None:
        try:
            if IS_WINDOWS:
                subprocess.run(
                    ["taskkill", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            proc.terminate()

    @staticmethod
    def _kill_tree(proc: subprocess.Popen) -> None:
        try:
            if IS_WINDOWS:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            proc.kill()


# ---------------------------------------------------------------------------
# 环境检查
# ---------------------------------------------------------------------------
def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 60,
         capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, timeout=timeout,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        text=True, encoding="utf-8", errors="replace",
    )


def _venv_python(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def _as_python(path: str | Path) -> Path | None:
    raw = str(path)
    p = Path(raw)
    if not p.exists() and os.path.sep not in raw and "/" not in raw:
        found = shutil.which(raw)
        if not found:
            return None
        p = Path(found)
    if p.is_dir():
        p = _venv_python(p)
    return p if p.exists() else None


def probe_python(py: Path) -> tuple[tuple[int, ...] | None, list[str]]:
    """返回 (版本号, 缺失模块)。版本号读不到时为 None。"""
    code = (
        "import sys\n"
        f"mods = {BACKEND_IMPORTS!r}\n"
        "missing = []\n"
        "for m in mods:\n"
        "    try:\n"
        "        __import__(m)\n"
        "    except Exception:\n"
        "        missing.append(m)\n"
        "print('KG_PROBE|' + '.'.join(map(str, sys.version_info[:3])) + '|' "
        "+ ','.join(missing))\n"
    )
    try:
        r = _run([str(py), "-c", code], timeout=60)
    except Exception:
        return None, list(BACKEND_IMPORTS)
    marker = next(
        (line for line in reversed((r.stdout or "").splitlines())
         if line.startswith("KG_PROBE|")),
        "",
    )
    if r.returncode != 0 or not marker:
        return None, list(BACKEND_IMPORTS)
    _, version_text, missing_text = (marker.split("|", 2) + ["", ""])[:3]
    try:
        version = tuple(int(x) for x in version_text.split("."))
    except ValueError:
        version = None
    missing = [m for m in missing_text.split(",") if m]
    return version, missing


def resolve_python(args: argparse.Namespace, allow_install: bool) -> Path:
    candidates: list[Path] = []

    def add(path: str | Path | None) -> None:
        if not path:
            return
        p = _as_python(path)
        if p and p not in candidates:
            candidates.append(p)

    add(args.python)
    add(os.environ.get("VIRTUAL_ENV"))
    add(sys.executable)
    add(PROJECT_ROOT / ".venv")
    add(BACKEND_DIR / ".venv")

    for py in candidates:
        version, missing = probe_python(py)
        if version and version < (3, 10):
            warn(f"{py} 版本为 {'.'.join(map(str, version))}，建议使用 Python 3.12+")
            continue
        if not missing:
            info(f"Python: {py}")
            return py

    if not allow_install:
        detail = "、".join(BACKEND_IMPORTS)
        raise StartupError(
            "找不到安装完整后端依赖的 Python 解释器。\n"
            f"  需要的模块：{detail}\n"
            "  首次运行请去掉 --no-install / --check，让脚本自动安装；"
            "或先在 backend/ 下执行 pip install -r requirements.txt。"
        )

    target = None
    if args.python:
        target = _as_python(args.python)
    elif os.environ.get("VIRTUAL_ENV"):
        target = _as_python(os.environ["VIRTUAL_ENV"])
    elif (PROJECT_ROOT / ".venv").exists():
        target = _as_python(PROJECT_ROOT / ".venv")

    if target is None:
        step("创建项目虚拟环境 .venv")
        venv_dir = PROJECT_ROOT / ".venv"
        try:
            _run([sys.executable, "-m", "venv", str(venv_dir)], cwd=PROJECT_ROOT,
                 timeout=180, capture=False)
        except Exception as exc:
            warn(f"创建虚拟环境失败（{exc}），将直接使用当前 Python 安装依赖")
        target = _as_python(venv_dir) or (candidates[0] if candidates else None)
        if target is None:
            raise StartupError("找不到可用的 Python 解释器")

    step(f"安装后端依赖（{target}）")
    info(f"    {REQUIREMENTS}")
    r = _run([str(target), "-m", "pip", "install",
              "--disable-pip-version-check", "-r", str(REQUIREMENTS)],
             cwd=BACKEND_DIR, timeout=1800, capture=False)
    if r.returncode != 0:
        raise StartupError("后端依赖安装失败，请检查网络/代理后重试")

    _version, missing = probe_python(target)
    if missing:
        raise StartupError(f"后端依赖仍缺失：{', '.join(missing)}")
    info(f"Python: {target}")
    return target


def resolve_npm(allow_install: bool) -> str:
    npm = shutil.which("npm")
    node = shutil.which("node")
    if not npm or not node:
        raise StartupError("未找到 Node.js / npm，请先安装 Node.js（Vite 7 需要 20.19+ 或 22.12+）")

    try:
        r = _run([node, "-p", "process.versions.node"], timeout=20)
        version = r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        version = ""
    if version:
        parts = [int(x) for x in version.split(".")[:2]]
        major, minor = parts[0], parts[1] if len(parts) > 1 else 0
        if major < 20 or (major == 20 and minor < 19) or (major == 22 and minor < 12):
            warn(f"当前 Node.js {version}，Vite 7 建议 20.19+ 或 22.12+")
        elif major == 21:
            warn(f"当前 Node.js {version} 为非 LTS 版本，Vite 7 未正式支持，建议升级到 22+")
    info(f"Node.js: {version or '未知'}    npm: {npm}")

    has_vite = (NODE_MODULES / ".bin").exists()
    if not has_vite:
        if not allow_install:
            raise StartupError(
                "frontend-vue/node_modules 不存在。\n"
                "  首次运行请去掉 --no-install / --check，让脚本自动执行 npm install。"
            )
        step("安装前端依赖（npm install）")
        r = _run([npm, "install", "--no-audit", "--no-fund"],
                 cwd=FRONTEND_DIR, timeout=1800, capture=False)
        if r.returncode != 0:
            raise StartupError("npm install 失败，请检查网络/镜像后重试")
    return npm


# ---------------------------------------------------------------------------
# .env / Neo4j
# ---------------------------------------------------------------------------
def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def ensure_env(check_only: bool) -> dict[str, str]:
    if not ENV_PATH.exists():
        if check_only:
            warn(f"缺少 {ENV_PATH}；将使用默认配置（LLM/Embedding/Neo4j 可能不可用）")
        else:
            if not ENV_EXAMPLE.exists():
                raise StartupError("backend/.env 与 backend/.env.example 都不存在")
            shutil.copyfile(ENV_EXAMPLE, ENV_PATH)
            warn(f"已生成 {ENV_PATH}")
            warn("请填写 NEO4J_PASSWORD、LLM_API_KEY、EMBEDDING_API_KEY 后重新启动；"
                 "本次仍会尝试启动前后端")
    values = parse_env_file(ENV_PATH if ENV_PATH.exists() else ENV_EXAMPLE)

    def get(key: str, default: str = "") -> str:
        return os.environ.get(key, values.get(key, default)).strip()

    password = get("NEO4J_PASSWORD")
    if not password or password == "your-password":
        warn("NEO4J_PASSWORD 未配置：图谱接口会连接失败并降级")
    if get("LLM_API_KEY") in ("", "your-api-key-here"):
        warn("LLM_API_KEY 未配置：知识抽取与问答生成不可用")
    if get("EMBEDDING_API_KEY") in ("", "your-siliconflow-api-key"):
        warn("EMBEDDING_API_KEY 未配置：RAG 会退回关键词检索")
    return {
        "uri": get("NEO4J_URI", "bolt://localhost:7687"),
        "user": get("NEO4J_USER", "neo4j"),
        "password": password,
    }


def _neo4j_target(neo4j_conf: dict[str, str]) -> tuple[str, int, bool]:
    uri = neo4j_conf["uri"]
    parsed = urlparse(uri)
    host = parsed.hostname or "localhost"
    port = parsed.port or NEO4J_DEFAULT_PORT
    local = host.lower() in {"localhost", "127.0.0.1", "::1"}
    return host, port, local


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_ok(url: str, timeout: float = 1.0) -> bool:
    try:
        with urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except Exception:
        return False


def _http_text(url: str, timeout: float = 1.5) -> str:
    try:
        with urlopen(url, timeout=timeout) as resp:
            if not (200 <= resp.status < 400):
                return ""
            return resp.read(200_000).decode("utf-8", "ignore")
    except Exception:
        return ""


def _is_our_backend() -> bool:
    """端口被占用时确认是本项目的 FastAPI，而不是其他程序。"""
    return (
        _http_ok(f"http://{BACKEND_HOST}:{BACKEND_PORT}/health")
        and "课程知识图谱" in _http_text(f"http://{BACKEND_HOST}:{BACKEND_PORT}/")
    )


def _is_our_frontend() -> bool:
    return "课程知识图谱智能构建与学习系统" in _http_text(
        f"http://{FRONTEND_HOST}:{FRONTEND_PORT}/"
    )


def find_neo4j(args: argparse.Namespace) -> tuple[str, Path] | None:
    """返回 ('home', Neo4j 根目录) 或 ('exe', neo4j 可执行文件)。"""
    home_candidates: list[Path] = []
    if args.neo4j_home:
        home_candidates.append(Path(args.neo4j_home).expanduser())
    if os.environ.get("NEO4J_HOME"):
        home_candidates.append(Path(os.environ["NEO4J_HOME"]).expanduser())

    for home in home_candidates:
        exe = home / "bin" / ("neo4j.bat" if IS_WINDOWS else "neo4j")
        if exe.exists():
            return "home", home

    for name in (("neo4j.bat", "neo4j") if IS_WINDOWS else ("neo4j",)):
        exe = shutil.which(name)
        if exe:
            return "exe", Path(exe)

    # 常见的解压式安装位置（不写死用户名；只做有限层扫描）
    patterns = [
        "neo4j-community-*", "neo4j-community-*/neo4j-community-*",
        "Downloads/neo4j-community-*", "Downloads/neo4j-community-*/neo4j-community-*",
        "neo4j-*", "Downloads/neo4j-*",
    ]
    roots = [Path.home(), Path.home() / "Desktop"]
    if IS_WINDOWS:
        roots += [Path("C:/Program Files"), Path("C:/Program Files (x86)")]
    else:
        roots += [Path("/opt"), Path("/usr/local")]
    for root in roots:
        for pattern in patterns:
            try:
                matches = sorted(root.glob(pattern), reverse=True)
            except OSError:
                continue
            for candidate in matches:
                exe = candidate / "bin" / ("neo4j.bat" if IS_WINDOWS else "neo4j")
                if candidate.is_dir() and exe.exists():
                    return "home", candidate
    return None


def _neo4j_auth_ok(py: Path, conf: dict[str, str]) -> bool:
    password = conf["password"]
    if not password or password == "your-password":
        return False
    # 直接读后端 settings，避免把口令拼进命令行（ps / 进程审计可见）。
    code = (
        "from app.core.config import settings\n"
        "from neo4j import GraphDatabase\n"
        "d = GraphDatabase.driver(settings.NEO4J_URI, "
        "auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD), connection_timeout=3)\n"
        "d.verify_connectivity()\n"
        "d.close()\n"
    )
    try:
        r = _run([str(py), "-c", code], cwd=BACKEND_DIR, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def prepare_neo4j(py: Path | None, args: argparse.Namespace,
                  manager: ProcessManager, check_only: bool) -> tuple[bool, str]:
    """返回 (数据库是否可用, 说明文案)。"""
    if args.no_db:
        return False, "已通过 --no-db 跳过 Neo4j"
    conf = ensure_env(check_only)
    host, port, local = _neo4j_target(conf)
    if not local:
        reachable = _port_open(host, port, timeout=2)
        if reachable:
            return True, f"使用远程 Neo4j：{conf['uri']}"
        return False, f"远程 Neo4j 不可达：{conf['uri']}（前后端仍会启动，图谱功能降级）"

    if _port_open(host, port):
        if py and not _neo4j_auth_ok(py, conf):
            return False, (f"Neo4j 端口 {port} 已监听，但认证/连接失败；"
                           "请检查 .env 的 NEO4J_USER / NEO4J_PASSWORD")
        return True, f"Neo4j 已在运行：{conf['uri']}"

    found = find_neo4j(args)
    if not found:
        return False, ("未找到本地 Neo4j，NEO4J_URI 指向本地但端口未监听。"
                       "可设置 NEO4J_HOME 后重试；本次继续启动前后端（图谱功能降级）")
    mode, path = found
    if check_only:
        return True, f"检测到 Neo4j，可启动：{path}"

    if mode == "home":
        exe = path / "bin" / ("neo4j.bat" if IS_WINDOWS else "neo4j")
        cwd = path
    else:
        exe = path
        cwd = path.parent.parent
    cmd = [str(exe), "console"]
    if IS_WINDOWS and exe.suffix.lower() in {".bat", ".cmd"}:
        cmd = ["cmd", "/c", *cmd]

    step(f"启动 Neo4j（{path}）")
    proc = manager.spawn("Neo4j", cmd, cwd)
    deadline = time.time() + 120
    while time.time() < deadline:
        if proc.poll() is not None:
            return False, (f"Neo4j 启动后退出（退出码 {proc.returncode}）；"
                           "请检查安装目录/Java/日志。本次继续启动前后端")
        if _port_open(host, port):
            # 端口监听后稍等服务就绪；认证不通过只警告，不阻塞。
            time.sleep(1.0)
            if py and not _neo4j_auth_ok(py, conf):
                warn("Neo4j 已启动，但认证失败；请检查 NEO4J_USER / NEO4J_PASSWORD")
            return True, f"已启动 Neo4j：{conf['uri']}"
        time.sleep(1.0)
    return False, "Neo4j 启动超时（120s），本次继续启动前后端（图谱功能可能降级）"


# ---------------------------------------------------------------------------
# 启动流程
# ---------------------------------------------------------------------------
def _check_port_or_raise(label: str, host: str, port: int, url: str) -> None:
    if _port_open(host, port):
        raise StartupError(
            f"{label}端口 {port} 已被占用。\n"
            f"  如果服务已在运行，可直接访问 {url}；\n"
            "  需要由本脚本重新启动时，请先关闭占用该端口的进程；\n"
            "  只启动另一端可用：python start.py --frontend-only / --backend-only。"
        )


def _wait_http(url: str, proc: subprocess.Popen, label: str, timeout: int = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise StartupError(f"{label}进程已退出（退出码 {proc.returncode}），请查看上方日志")
        if _http_ok(url):
            return
        time.sleep(0.5)
    raise StartupError(f"{label}启动超时（{timeout}s）：{url}")


def _child_env(frontend: bool = False) -> dict[str, str]:
    env = {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    if frontend:
        # Vite 的代理目标是 localhost:8000；避免 Node 把 localhost 解析成 ::1
        # 而后端只监听 IPv4 时出现「前后端各自启动成功、代理却 502」。
        existing = os.environ.get("NODE_OPTIONS", "")
        if "dns-result-order" not in existing:
            env["NODE_OPTIONS"] = (existing + " --dns-result-order=ipv4first").strip()
    return env


def check_mode(py: Path | None, npm: str | None, args: argparse.Namespace,
               manager: ProcessManager, backend_reuse: bool,
               frontend_reuse: bool) -> int:
    issues = 0
    backend_url = f"http://{BACKEND_HOST}:{BACKEND_PORT}"
    frontend_url = f"http://{FRONTEND_HOST}:{FRONTEND_PORT}"

    if not args.frontend_only:
        if backend_reuse:
            info(f"后端端口 {BACKEND_PORT}：已在运行，将复用")
        elif _port_open(BACKEND_HOST, BACKEND_PORT):
            info(f"后端端口 {BACKEND_PORT}：被其他程序占用（启动前需关闭）")
            issues += 1
        else:
            info(f"后端端口 {BACKEND_PORT}：可用")
    if not args.backend_only:
        if frontend_reuse:
            info(f"前端端口 {FRONTEND_PORT}：已在运行，将复用")
        elif _port_open(FRONTEND_HOST, FRONTEND_PORT):
            info(f"前端端口 {FRONTEND_PORT}：被其他程序占用（启动前需关闭）")
            issues += 1
        else:
            info(f"前端端口 {FRONTEND_PORT}：可用")

    if not args.frontend_only and not backend_reuse:
        db_ok, db_message = prepare_neo4j(py, args, manager, check_only=True)
        info(f"Neo4j：{'可用' if db_ok else '不可用'} — {db_message}")
        if not db_ok and not args.no_db:
            warn("数据库不可用不会阻止前后端启动，但图谱/路径/问答会降级")
    elif backend_reuse:
        info("Neo4j：后端已在运行，本次跳过数据库检查")

    if py:
        info(f"后端 Python：{py}")
    if npm:
        info(f"前端 npm：{npm}")
    info(f"后端地址：{backend_url}")
    info(f"前端地址：{frontend_url}")
    if issues:
        info("\n检查完成：发现端口冲突，请先处理后再启动。")
        return 1
    info("\n检查完成：未发现阻塞项。")
    return 0


def run(args: argparse.Namespace) -> int:
    check_only = args.check
    allow_install = not (args.no_install or check_only)
    need_backend = not args.frontend_only
    need_frontend = not args.backend_only

    info("=" * 72)
    info("  课程知识图谱智能构建与学习系统 — 一键启动")
    info("=" * 72)

    manager = ProcessManager()
    try:
        backend_occupied = need_backend and _port_open(BACKEND_HOST, BACKEND_PORT)
        frontend_occupied = need_frontend and _port_open(FRONTEND_HOST, FRONTEND_PORT)
        backend_reuse = backend_occupied and _is_our_backend()
        frontend_reuse = frontend_occupied and _is_our_frontend()

        if backend_occupied and not backend_reuse:
            _check_port_or_raise("后端", BACKEND_HOST, BACKEND_PORT,
                                 f"http://{BACKEND_HOST}:{BACKEND_PORT}/health")
        if frontend_occupied and not frontend_reuse:
            _check_port_or_raise("前端", FRONTEND_HOST, FRONTEND_PORT,
                                 f"http://{FRONTEND_HOST}:{FRONTEND_PORT}")

        if backend_reuse:
            info(f"检测到本项目后端已在运行，将复用：http://{BACKEND_HOST}:{BACKEND_PORT}")
        if frontend_reuse:
            info(f"检测到本项目前端已在运行，将复用：http://{FRONTEND_HOST}:{FRONTEND_PORT}")

        # 已复用的服务不需要再检查/安装依赖；只有真正要启动的一端才做预检。
        py = (resolve_python(args, allow_install)
              if need_backend and not backend_reuse else None)
        npm = resolve_npm(allow_install) if need_frontend and not frontend_reuse else None

        if check_only:
            return check_mode(py, npm, args, manager, backend_reuse, frontend_reuse)

        if need_backend and not backend_reuse:
            db_ok, db_message = prepare_neo4j(py, args, manager, check_only=False)
            info(f"Neo4j：{'可用' if db_ok else '不可用'} — {db_message}")
            if not db_ok and not args.no_db:
                warn("继续启动前后端；图谱/路径/问答相关功能可能降级")

        backend_proc = None
        if need_backend and not backend_reuse:
            cmd = [str(py), "-m", "uvicorn", "app.main:app",
                   "--host", BACKEND_HOST, "--port", str(BACKEND_PORT)]
            if args.reload:
                cmd.append("--reload")
            step("启动后端 FastAPI")
            backend_proc = manager.spawn("后端", cmd, BACKEND_DIR, _child_env())
            _wait_http(f"http://{BACKEND_HOST}:{BACKEND_PORT}/health", backend_proc, "后端")
            info(f"后端就绪：http://{BACKEND_HOST}:{BACKEND_PORT}  "
                 f"（接口文档 /docs）")

        frontend_proc = None
        if need_frontend and not frontend_reuse:
            cmd = [npm, "run", "dev", "--", "--host", FRONTEND_HOST,
                   "--port", str(FRONTEND_PORT), "--strictPort"]
            step("启动前端 Vite")
            frontend_proc = manager.spawn("前端", cmd, FRONTEND_DIR,
                                          _child_env(frontend=True))
            _wait_http(f"http://{FRONTEND_HOST}:{FRONTEND_PORT}/", frontend_proc, "前端")
            info(f"前端就绪：http://{FRONTEND_HOST}:{FRONTEND_PORT}")

        if need_frontend and not args.no_browser:
            webbrowser.open(f"http://{FRONTEND_HOST}:{FRONTEND_PORT}")
            info("已在浏览器中打开前端页面")

        if backend_proc is None and frontend_proc is None:
            info("\n所需服务均已在运行，无需重复启动。")
            return 0

        info("\n启动完成。按 Ctrl+C 停止由本脚本启动的进程。")
        if backend_reuse:
            info("[提示] 后端是复用的已有进程，Ctrl+C 不会关闭它。")
        if frontend_reuse:
            info("[提示] 前端是复用的已有进程，Ctrl+C 不会关闭它。")

        while True:
            exited = manager.first_exited()
            if exited:
                label, code = exited
                info(f"\n[{label}] 进程已退出（退出码 {code}），正在停止其余进程…")
                return 1 if code else 0
            time.sleep(0.5)

    except KeyboardInterrupt:
        info("\n收到停止信号，正在关闭…")
        return 0
    except StartupError as exc:
        info(f"\n[启动失败] {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - 单文件启动器，异常要变成可读提示
        info(f"\n[启动失败] {type(exc).__name__}: {exc}")
        return 1
    finally:
        manager.stop()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="跨平台一键启动 Neo4j（可选）+ FastAPI 后端 + Vue 前端",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--backend-only", action="store_true", help="只启动后端")
    mode.add_argument("--frontend-only", action="store_true", help="只启动前端")
    parser.add_argument("--check", action="store_true",
                        help="只检查环境，不启动、不安装依赖")
    parser.add_argument("--no-install", action="store_true",
                        help="缺少依赖时直接报错，不自动安装")
    parser.add_argument("--no-db", action="store_true",
                        help="跳过 Neo4j 检查与启动")
    parser.add_argument("--no-browser", action="store_true",
                        help="启动完成后不自动打开浏览器")
    parser.add_argument("--reload", action="store_true",
                        help="后端启用 uvicorn --reload（改代码自动重载）")
    parser.add_argument("--neo4j-home", metavar="DIR",
                        help="Neo4j 安装目录（也可用环境变量 NEO4J_HOME）")
    parser.add_argument("--python", metavar="PYTHON",
                        help="指定后端 Python 解释器（默认自动查找含依赖的环境）")
    return parser.parse_args(argv)


def _raise_keyboard_interrupt(_signum, _frame) -> None:
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    args = parse_args(argv or sys.argv[1:])
    # 让 SIGTERM（Docker/CI/超时命令）也走统一的清理路径。
    for sig_name in ("SIGTERM", "SIGBREAK"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _raise_keyboard_interrupt)
        except Exception:
            pass
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
