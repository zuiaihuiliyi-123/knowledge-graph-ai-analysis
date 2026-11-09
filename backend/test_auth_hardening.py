"""认证加固回归：强制改密 / token 吊销 / 登录限流 / CORS / 健康依赖检查。

覆盖：
1. 用户名长度与密码最小长度（后端校验，不依赖前端）；
2. must_change_password=1 时，除改密/个人资料等白名单外一律 403；
3. 改密后旧 token 立即 401，响应里带回新 token；
4. 登出后同一 token 立即 401（token_version 递增）；
5. 同一用户名 + IP 连续失败 8 次后返回 429；成功登录会清零计数；
6. CORS 只放行 CORS_ORIGINS 中的来源；
7. /health/deps 返回 SQLite / Neo4j 的真实状态（不连图库时降级而不是 500）。

运行：cd backend && python test_auth_hardening.py
（纯临时库 + 不生成自动备份，不触碰真实 app.db / Neo4j）
"""
import os
import sys
import tempfile

# 必须在导入 app.* 之前改路径：config 在模块导入时读取环境变量
_TMP = tempfile.mkdtemp(prefix="kg_auth_hardening_")
os.environ["SQLITE_DB_PATH"] = os.path.join(_TMP, "app.db")
os.environ["UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["AUTO_BACKUP"] = "0"
os.environ["TEACHER_INVITE_CODE"] = ""

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient  # noqa: E402

from app.core import user_state  # noqa: E402
from app.core.sql_database import sql_db  # noqa: E402
from app.main import app  # noqa: E402

failures = []
total = 0


def check(label, ok, detail=""):
    global total
    total += 1
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def register(client, username, password="pw123456"):
    return client.post("/api/auth/register", json={
        "username": username, "password": password, "role": "student"}).json()


def login(client, username, password="pw123456"):
    return client.post("/api/auth/login", json={
        "username": username, "password": password})


def main() -> int:
    client = TestClient(app)
    with client:
        print("Step 1: 注册参数校验")
        r = register(client, "ab")
        check("2 位用户名被拒绝 1001", r.get("code") == 1001, str(r))
        r = register(client, "stu_hard", "12345")
        check("5 位密码被拒绝 1001", r.get("code") == 1001, str(r))
        r = register(client, "stu_hard")
        check("合法学生注册成功", r.get("code") == 0, str(r))
        user_id = (r.get("data") or {}).get("user_id")

        print("\nStep 2: 首次登录强制改密（后端拦截，不只靠前端）")
        r = login(client, "stu_hard")
        body = r.json()
        token = (body.get("data") or {}).get("access_token")
        check("登录成功并拿到 token", bool(token), str(body)[:120])
        sql_db.set_must_change_password(user_id, True)
        user_state.invalidate_user_state(user_id)
        r = client.get("/api/v1/graph/1", params={"document_id": 1}, headers=auth(token))
        check("未改密访问业务接口 → HTTP 403", r.status_code == 403, f"HTTP {r.status_code}")
        check("403 文案提示先改密", "先修改初始密码" in (r.text or ""), r.text[:120])

        print("\nStep 3: 改密后旧 token 立即失效，新 token 可用")
        r = client.post("/api/auth/change-password", headers=auth(token), json={
            "old_password": "pw123456", "new_password": "pw654321"})
        body = r.json()
        new_token = (body.get("data") or {}).get("access_token")
        check("改密成功并返回新 token", body.get("code") == 0 and bool(new_token), str(body)[:160])
        r = client.get("/api/v1/graph/1", params={"document_id": 1}, headers=auth(token))
        check("旧 token → HTTP 401", r.status_code == 401, f"HTTP {r.status_code}")
        r = client.get("/api/v1/graph/1", params={"document_id": 1}, headers=auth(new_token))
        check("新 token 通过鉴权（不再 401/403 改密拦截）", r.status_code == 200, f"HTTP {r.status_code}")

        print("\nStep 4: 登出吊销全部已签发 token")
        r = client.post("/api/auth/logout", headers=auth(new_token))
        check("登出成功", r.json().get("code") == 0, r.text[:120])
        r = client.get("/api/v1/profile", headers=auth(new_token))
        check("登出后同一 token → HTTP 401", r.status_code == 401, f"HTTP {r.status_code}")

        print("\nStep 5: 登录失败限流（同一用户名 + IP）")
        register(client, "stu_limit")
        for i in range(8):
            r = login(client, "stu_limit", "wrong-password")
            if i < 7:
                check(f"第 {i + 1} 次失败仍返回业务错误 2002",
                      r.status_code == 200 and r.json().get("code") == 2002, r.text[:100])
        r = login(client, "stu_limit", "wrong-password")
        check("第 9 次失败 → HTTP 429", r.status_code == 429, f"HTTP {r.status_code}")
        check("429 业务码 4290", r.json().get("code") == 4290, r.text[:120])
        r = login(client, "stu_limit")
        check("锁定期间即使密码正确也 429", r.status_code == 429, f"HTTP {r.status_code}")

        print("\nStep 6: 成功登录清零失败计数")
        register(client, "stu_clear")
        for _ in range(3):
            login(client, "stu_clear", "wrong-password")
        r = login(client, "stu_clear")
        check("3 次失败后正确登录成功", r.status_code == 200 and r.json().get("code") == 0, r.text[:120])
        for _ in range(7):
            login(client, "stu_clear", "wrong-password")
        r = login(client, "stu_clear", "wrong-password")
        check("清零后重新计数，第 8 次仍未被锁", r.status_code == 200, f"HTTP {r.status_code}")

        print("\nStep 7: CORS 白名单")
        r = client.options("/health", headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        })
        check("白名单来源放行", r.headers.get("access-control-allow-origin") == "http://localhost:5173",
              str(dict(r.headers))[:160])
        r = client.options("/health", headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "GET",
        })
        check("非白名单来源不放行", r.headers.get("access-control-allow-origin") is None,
              str(dict(r.headers))[:160])

        print("\nStep 8: 健康检查拆分")
        r = client.get("/health")
        check("liveness 不查依赖，始终 ok", r.status_code == 200 and r.json().get("status") == "ok", r.text[:120])
        r = client.get("/health/deps")
        body = r.json()
        check("readiness 返回 sqlite/neo4j 状态", r.status_code == 200 and "dependencies" in body, r.text[:180])
        check("SQLite 连通", body.get("dependencies", {}).get("sqlite") == "ok", str(body)[:180])

    print(f"\n通过 {total - len(failures)}/{total}")
    if failures:
        print("失败项：")
        for f in failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
