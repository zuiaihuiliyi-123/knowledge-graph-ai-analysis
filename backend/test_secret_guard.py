"""阶段 1B 密钥管理回归：不再内置口令、SECRET_KEY 可持久化回落、生产强制校验。

覆盖：
1. 未配置 SECRET_KEY → 使用本地随机密钥文件（长度达标、文件被创建、重启后一致）；
2. 配置过短 / 历史默认值的 SECRET_KEY → 视为不合规，改用本地密钥（绝不复用弱值）；
3. 配置合规 SECRET_KEY → 直接使用环境变量值；
4. NEO4J_PASSWORD 没有内置兜底（显式置空即得到空串）；
5. APP_ENV=production 且 SECRET_KEY 未来自环境变量 → 启动失败（导入即抛错）；
6. 启动自检 `_audit_secrets()` 能列出缺失项，且返回文案不含密钥明文；
7. 仓库源码里不再残留真实图库口令。

运行：cd backend && python test_secret_guard.py
（纯子进程 + 临时密钥文件，不改动真实 .env / 数据库）
"""
import json
import os
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
OUT = None
failures = []
total = 0


def check(label, ok, detail=""):
    global total
    total += 1
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def run_child(code: str, env_extra: dict, timeout=60):
    """在干净环境变量的子进程里跑一段代码，返回 (returncode, stdout, stderr)。"""
    env = dict(os.environ)
    # 清掉可能继承进来的相关变量，保证「未配置」是真的未配置
    for key in ("SECRET_KEY", "SECRET_KEY_FILE", "APP_ENV", "NEO4J_PASSWORD", "AUTO_BACKUP"):
        env.pop(key, None)
    env.update(env_extra)
    p = subprocess.run([sys.executable, "-X", "utf8", "-c", code], cwd=BACKEND_DIR, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    return p.returncode, p.stdout or "", p.stderr or ""


def main() -> int:
    tmpdir = tempfile.mkdtemp(prefix="kg_secret_guard_")
    secret_file = os.path.join(tmpdir, ".secret_key")

    probe = (
        "import sys, json; sys.path.insert(0, r'%s');"
        "from app.core.config import settings;"
        "print(json.dumps({'source': settings.SECRET_KEY_SOURCE,"
        " 'len': len(settings.SECRET_KEY), 'key': settings.SECRET_KEY,"
        " 'neo4j_pw': settings.NEO4J_PASSWORD}))" % BACKEND_DIR
    )

    print("Step 1: 未配置 SECRET_KEY → 本地随机密钥文件")
    # 注意：传 "" 而不是「删掉变量」——python-dotenv 只在环境变量**不存在**时补值，
    # 显式空串等价于「确实没配置」，否则 backend/.env 里的真值会被重新注入。
    rc, out, err = run_child(probe, {"SECRET_KEY": "", "SECRET_KEY_FILE": secret_file})
    info = {}
    try:
        info = json.loads(out.strip().splitlines()[-1])
    except Exception:
        info = {}
    check("子进程正常返回", rc == 0, err[-200:])
    check("来源为 local-file", info.get("source") == "local-file", str(info.get("source")))
    check("密钥长度 >= 32", (info.get("len") or 0) >= 32, str(info.get("len")))
    check("本地密钥文件已生成", os.path.exists(secret_file))

    first_key = info.get("key")
    rc, out, err = run_child(probe, {"SECRET_KEY": "", "SECRET_KEY_FILE": secret_file})
    second = json.loads(out.strip().splitlines()[-1]) if rc == 0 else {}
    check("重启后密钥保持一致（多 worker / 重启不失效）",
          bool(first_key) and second.get("key") == first_key)

    print("\nStep 2: 过短 / 历史默认值视为不合规")
    weak_file = os.path.join(tmpdir, ".secret_key_weak")
    rc, out, _ = run_child(probe, {"SECRET_KEY": "123", "SECRET_KEY_FILE": weak_file})
    weak = json.loads(out.strip().splitlines()[-1]) if rc == 0 else {}
    check("短密钥被替换为本地随机密钥",
          weak.get("source") == "local-file" and weak.get("key") != "123", str(weak.get("source")))

    legacy_file = os.path.join(tmpdir, ".secret_key_legacy")
    rc, out, _ = run_child(probe, {
        "SECRET_KEY": "please-set-a-random-secret-key-of-at-least-32-bytes",
        "SECRET_KEY_FILE": legacy_file})
    legacy = json.loads(out.strip().splitlines()[-1]) if rc == 0 else {}
    check("历史默认密钥不再被接受",
          legacy.get("key") != "please-set-a-random-secret-key-of-at-least-32-bytes",
          str(legacy.get("source")))

    print("\nStep 3: 合规 SECRET_KEY 直接采用环境变量")
    strong = "S" * 48
    rc, out, _ = run_child(probe, {"SECRET_KEY": strong, "SECRET_KEY_FILE": secret_file})
    env_info = json.loads(out.strip().splitlines()[-1]) if rc == 0 else {}
    check("来源为 env 且值一致",
          env_info.get("source") == "env" and env_info.get("key") == strong,
          str(env_info.get("source")))

    print("\nStep 4: NEO4J_PASSWORD 没有内置兜底")
    rc, out, _ = run_child(probe, {"SECRET_KEY": strong, "NEO4J_PASSWORD": ""})
    neo = json.loads(out.strip().splitlines()[-1]) if rc == 0 else {}
    check("显式置空后即为空串（无硬编码口令）", neo.get("neo4j_pw") == "", repr(neo.get("neo4j_pw")))

    print("\nStep 5: 生产环境必须显式配置 SECRET_KEY")
    rc, _, err = run_child(probe, {"APP_ENV": "production", "SECRET_KEY": "",
                                   "SECRET_KEY_FILE": secret_file})
    check("APP_ENV=production 且未来自环境变量 → 导入失败", rc != 0, f"rc={rc}")
    check("失败原因指向 SECRET_KEY",
          "SECRET_KEY" in err and "必须" in err, err.strip().splitlines()[-1] if err.strip() else "(空)")

    strength_ok = run_child(probe, {"APP_ENV": "production", "SECRET_KEY": strong})
    check("生产环境配置合规密钥 → 可正常启动", strength_ok[0] == 0,
          f"rc={strength_ok[0]}")

    print("\nStep 6: 启动自检列出缺失项且不泄漏密钥本体")
    audit_code = (
        "import sys, json; sys.path.insert(0, r'%s');"
        "from app.core.config import settings;"
        "from app.main import _audit_secrets;"
        "issues = _audit_secrets();"
        "print(json.dumps({'issues': issues, 'leak': [i for i in issues if settings.SECRET_KEY in i],"
        " 'has_secret': any('SECRET_KEY' in i for i in issues)}))" % BACKEND_DIR
    )
    rc, out, err = run_child(audit_code, {
        "SECRET_KEY": "", "SECRET_KEY_FILE": secret_file, "NEO4J_PASSWORD": "",
        "LLM_API_KEY": "", "EMBEDDING_API_KEY": ""})
    audit = {}
    try:
        audit = json.loads(out.strip().splitlines()[-1])
    except Exception:
        audit = {}
    check("自检可执行", rc == 0, err[-200:])
    check("缺失项被列出（含 NEO4J / LLM / EMBEDDING）",
          any("NEO4J_PASSWORD" in i for i in audit.get("issues", []))
          and any("LLM_API_KEY" in i for i in audit.get("issues", []))
          and any("EMBEDDING_API_KEY" in i for i in audit.get("issues", [])),
          str(audit.get("issues"))[:200])
    check("自检文案不含密钥明文", audit.get("leak") == [], str(audit.get("leak")))

    print("\nStep 7: 源码不再残留真实图库口令")
    with open(os.path.join(BACKEND_DIR, "app", "core", "config.py"), encoding="utf-8") as f:
        source = f.read()
    # 拆开拼接：这句断言本身不该成为「把口令写回仓库」的新落点
    legacy_password = "Dream@" + "161616"
    check("config.py 不含历史真实口令", legacy_password not in source)
    check("NEO4J_PASSWORD 默认值为空串",
          'NEO4J_PASSWORD: str = os.getenv("NEO4J_PASSWORD", "")' in source)

    print(f"\n通过 {total - len(failures)}/{total}" if not failures
          else f"\n失败 {len(failures)}/{total}：{failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
