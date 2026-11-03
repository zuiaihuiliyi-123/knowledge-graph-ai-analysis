"""注销账号用户名复用回归。

覆盖：
1. 注销（软停用）后，同名可以直接重新注册；
2. 旧账号行仍保留（is_active=0），用户名被归档为内部前缀，历史数据不丢；
3. 新账号是全新的 user_id，登录/鉴权正常，不会继承旧账号身份；
4. 仍在启用的同名账号依旧提示占用；
5. 注销后旧 token 立即失效（账号态缓存失效，不等 30 秒 TTL）；
6. 内部归档前缀不允许被真实用户注册。

运行：cd backend && python test_auth_register_reclaim.py
（纯临时库 + 不生成自动备份，不触碰真实 app.db / Neo4j）
"""
import os
import shutil
import sys
import tempfile

# 必须在导入 app.* 之前改路径：config 在模块导入时读取环境变量
_TMP = tempfile.mkdtemp(prefix="kg_auth_reclaim_")
os.environ["SQLITE_DB_PATH"] = os.path.join(_TMP, "app.db")
os.environ["UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["AUTO_BACKUP"] = "0"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient          # noqa: E402

from app.core.sql_database import (  # noqa: E402
    ARCHIVED_USERNAME_PREFIX,
    DEFAULT_ADMIN_USERNAME,
    DEFAULT_TEACHER_USERNAME,
    sql_db,
)
from app.main import app                            # noqa: E402

failures = []
total = 0


def check(label, ok, detail=""):
    global total
    total += 1
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    username = "reclaim_me"
    old_password = "old-pass-123"
    new_password = "new-pass-456"

    client = TestClient(app)
    with client:
        print("Step 1: 注册并登录旧账号")
        r = client.post("/api/auth/register", json={
            "username": username, "password": old_password, "role": "student"}).json()
        check("旧账号注册成功", r.get("code") == 0, str(r))
        old_id = (r.get("data") or {}).get("user_id")

        r = client.post("/api/auth/login", json={
            "username": username, "password": old_password}).json()
        check("旧账号登录成功", r.get("code") == 0, str(r))
        old_token = (r.get("data") or {}).get("access_token")
        old_headers = {"Authorization": f"Bearer {old_token}"}

        # 先访问一次受保护接口，把账号态写进 user_state 缓存（验证注销后立即失效）
        resp = client.get("/api/v1/profile", headers=old_headers)
        check("注销前旧 token 可用", resp.status_code == 200, f"HTTP {resp.status_code}")

        print("\nStep 2: 注销账号")
        r = client.post("/api/auth/deactivate",
                        json={"password": old_password}, headers=old_headers).json()
        check("注销成功", r.get("code") == 0, str(r))

        resp = client.get("/api/v1/profile", headers=old_headers)
        check("注销后旧 token 立即 401（缓存已失效）", resp.status_code == 401,
              f"HTTP {resp.status_code}")

        r = client.post("/api/auth/login", json={
            "username": username, "password": old_password}).json()
        check("注销后原用户名仍不能登录（提示已注销）",
              r.get("code") == 2004, str(r))

        print("\nStep 3: 同名重新注册")
        r = client.post("/api/auth/register", json={
            "username": username, "password": new_password, "role": "student"}).json()
        check("注销后同名注册成功", r.get("code") == 0, str(r))
        new_id = (r.get("data") or {}).get("user_id")
        check("新账号拿到新的 user_id", new_id and new_id != old_id,
              f"old={old_id} new={new_id}")

        old_row = sql_db.get_user_by_id(old_id)
        check("旧账号行保留且仍为停用状态",
              old_row is not None and int(old_row.get("is_active", 1)) == 0,
              str(old_row))
        check("旧账号用户名已归档到内部命名空间",
              bool(old_row) and old_row["username"].startswith(ARCHIVED_USERNAME_PREFIX),
              str(old_row and old_row["username"]))

        print("\nStep 4: 新账号可正常登录，不继承旧身份")
        r = client.post("/api/auth/login", json={
            "username": username, "password": new_password}).json()
        check("新账号登录成功", r.get("code") == 0, str(r))
        got_id = ((r.get("data") or {}).get("user") or {}).get("user_id")
        check("登录返回的是新 user_id", got_id == new_id, f"got={got_id} new={new_id}")

        r = client.post("/api/auth/login", json={
            "username": username, "password": old_password}).json()
        check("旧密码不能登录同名账号", r.get("code") == 2002, str(r))

        print("\nStep 5: 启用中的同名账号仍视为占用")
        r = client.post("/api/auth/register", json={
            "username": username, "password": "another-pass", "role": "student"}).json()
        check("重复注册启用中的用户名 → 2006", r.get("code") == 2006, str(r))

        print("\nStep 6: 内部归档前缀不可被真实用户占用")
        r = client.post("/api/auth/register", json={
            "username": f"{ARCHIVED_USERNAME_PREFIX}999__x",
            "password": "whatever-123", "role": "student"}).json()
        check("注册归档前缀用户名 → 1001", r.get("code") == 1001, str(r))

        print("\nStep 7: 管理员用户名即使停用也不允许被复用")
        admin_row = sql_db.get_user_by_username(DEFAULT_ADMIN_USERNAME)
        sql_db.deactivate_user(admin_row["user_id"])
        admin_after = sql_db.get_user_by_id(admin_row["user_id"])
        check("管理员已停用", int(admin_after["is_active"]) == 0, str(admin_after))
        r = client.post("/api/auth/register", json={
            "username": DEFAULT_ADMIN_USERNAME, "password": "whatever-123",
            "role": "student"}).json()
        check("停用管理员同名注册 → 2006", r.get("code") == 2006, str(r))
        admin_after = sql_db.get_user_by_id(admin_row["user_id"])
        check("管理员行用户名未被归档", admin_after["username"] == DEFAULT_ADMIN_USERNAME,
              str(admin_after))

        teacher_row = sql_db.get_user_by_username(DEFAULT_TEACHER_USERNAME)
        sql_db.deactivate_user(teacher_row["user_id"])
        teacher_after = sql_db.get_user_by_id(teacher_row["user_id"])
        check("引导教师已停用", int(teacher_after["is_active"]) == 0, str(teacher_after))
        r = client.post("/api/auth/register", json={
            "username": DEFAULT_TEACHER_USERNAME, "password": "whatever-123",
            "role": "student"}).json()
        check("停用引导教师同名注册 → 2006", r.get("code") == 2006, str(r))
        teacher_after = sql_db.get_user_by_id(teacher_row["user_id"])
        check("引导教师用户名未被归档",
              teacher_after["username"] == DEFAULT_TEACHER_USERNAME, str(teacher_after))

    print(f"\n通过 {total - len(failures)}/{total}" if not failures
          else f"\n失败 {len(failures)}/{total}：{failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
