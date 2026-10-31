"""阶段 1A 安全收口回归：教师注册邀请码 + 账号态（停用/降级）收敛。

覆盖：
1. 未配置 TEACHER_INVITE_CODE → 教师自助注册关闭（学生注册不受影响）；
2. 配置邀请码后：缺码 / 错码一律 4003，正确码可注册为 teacher；
3. 管理员禁用账号 → 旧 token 立即 401（不用等 24 小时过期）；
4. 管理员把教师降级 → 旧 token 立即失去 require_teacher（403）；
5. 不合规路径：直接改库不通知缓存时，最多等 TTL 收敛（TTL=0 立即生效）；
6. 反向确认缓存真的在工作：TTL 内不 invalidate 时不会每请求查库。

运行：cd backend && python test_auth_guard.py
（纯临时库 + 不生成自动备份，不触碰真实 app.db / Neo4j）
"""
import os
import shutil
import sys
import tempfile

# 必须在导入 app.* 之前改路径：config 在模块导入时读取环境变量
_TMP = tempfile.mkdtemp(prefix="kg_auth_guard_")
os.environ["SQLITE_DB_PATH"] = os.path.join(_TMP, "app.db")
os.environ["UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["AUTO_BACKUP"] = "0"          # 测试不生成 8MB 快照

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient          # noqa: E402

from app.core import user_state                     # noqa: E402
from app.core.config import settings                # noqa: E402
from app.core.sql_database import DEFAULT_ADMIN_USERNAME, sql_db   # noqa: E402
from app.main import app                            # noqa: E402
from app.services.admin_service import AdminService  # noqa: E402

failures = []
total = 0


def check(label, ok, detail=""):
    global total
    total += 1
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    client = TestClient(app)
    with client:
        # ---------- 注册 ----------
        print("Step 1: 未配置邀请码时，教师注册必须关闭")
        settings.TEACHER_INVITE_CODE = ""
        r = client.post("/api/auth/register", json={
            "username": "t_no_code", "password": "pw123456", "role": "teacher"}).json()
        check("教师注册（无邀请码配置）被拒绝 4003", r.get("code") == 4003, str(r))

        r = client.post("/api/auth/register", json={
            "username": "stu_ok", "password": "pw123456", "role": "student"}).json()
        check("学生注册不受影响", r.get("code") == 0, str(r))
        student_id = (r.get("data") or {}).get("user_id")

        print("\nStep 2: 配置邀请码后的三条分支")
        settings.TEACHER_INVITE_CODE = "T-INVITE-2026"
        r = client.post("/api/auth/register", json={
            "username": "t_missing", "password": "pw123456", "role": "teacher"}).json()
        check("教师注册缺邀请码 → 4003", r.get("code") == 4003, str(r))

        r = client.post("/api/auth/register", json={
            "username": "t_wrong", "password": "pw123456", "role": "teacher",
            "teacher_invite_code": "WRONG"}).json()
        check("教师注册错邀请码 → 4003", r.get("code") == 4003, str(r))

        r = client.post("/api/auth/register", json={
            "username": "t_ok", "password": "pw123456", "role": "teacher",
            "teacher_invite_code": "T-INVITE-2026"}).json()
        check("教师注册正确邀请码 → 成功且角色为 teacher",
              r.get("code") == 0 and (r.get("data") or {}).get("role") == "teacher", str(r))
        teacher_id = (r.get("data") or {}).get("user_id")

        # ---------- 登录与基线访问 ----------
        print("\nStep 3: 教师 token 可通过 require_teacher")
        token = client.post("/api/auth/login", json={
            "username": "t_ok", "password": "pw123456"}).json()["data"]["access_token"]
        resp = client.get("/api/v1/teacher/students/progress",
                          params={"course_id": 1},
                          headers={"Authorization": f"Bearer {token}"})
        check("教师访问教学监测：未被 403 拦截", resp.status_code == 200, f"HTTP {resp.status_code}")

        admin_row = sql_db.get_user_by_username(DEFAULT_ADMIN_USERNAME)
        admin_ctx = {"user_id": admin_row["user_id"], "username": admin_row["username"],
                     "role": "admin"}


        # ---------- 降级立即生效 ----------
        print("\nStep 4: 管理员降级 → 旧 token 立即失去教师权限")
        res = AdminService.set_user_role(admin_ctx, teacher_id, "student")
        check("降级操作成功", res.get("ok") is True, str(res))
        resp = client.get("/api/v1/teacher/students/progress",
                          params={"course_id": 1},
                          headers={"Authorization": f"Bearer {token}"})
        check("降级后旧 token 访问教师接口 → HTTP 403", resp.status_code == 403,
              f"HTTP {resp.status_code}")

        # ---------- 禁用立即生效 ----------
        print("\nStep 5: 管理员禁用账号 → 旧 token 立即 401")
        stu_token = client.post("/api/auth/login", json={
            "username": "stu_ok", "password": "pw123456"}).json()["data"]["access_token"]
        resp = client.get("/api/v1/profile", headers={"Authorization": f"Bearer {stu_token}"})
        check("禁用前学生 token 可用", resp.status_code == 200, f"HTTP {resp.status_code}")
        res = AdminService.set_user_active(admin_ctx, student_id, False)
        check("禁用操作成功", res.get("ok") is True, str(res))
        resp = client.get("/api/v1/profile", headers={"Authorization": f"Bearer {stu_token}"})
        check("禁用后旧 token → HTTP 401", resp.status_code == 401, f"HTTP {resp.status_code}")

        # ---------- TTL 收敛（不经过 invalidate 的旁路改动） ----------
        print("\nStep 6: 未经 invalidate 的直接改库，最多等 TTL 收敛")
        r = client.post("/api/auth/register", json={
            "username": "t_ttl", "password": "pw123456", "role": "teacher",
            "teacher_invite_code": "T-INVITE-2026"}).json()
        ttl_teacher_id = (r.get("data") or {}).get("user_id")
        ttl_token = client.post("/api/auth/login", json={
            "username": "t_ttl", "password": "pw123456"}).json()["data"]["access_token"]

        original_ttl = user_state.USER_STATE_TTL
        try:
            # 6a. TTL 内且缓存已填充 → 仍按缓存的教师身份放行（证明缓存真的在工作）
            user_state.USER_STATE_TTL = 60.0
            user_state.invalidate_user_state(ttl_teacher_id)
            first = client.get("/api/v1/teacher/students/progress", params={"course_id": 1},
                               headers={"Authorization": f"Bearer {ttl_token}"})
            sql_db.set_user_role(ttl_teacher_id, "student")     # 故意不 invalidate
            second = client.get("/api/v1/teacher/students/progress", params={"course_id": 1},
                                headers={"Authorization": f"Bearer {ttl_token}"})
            check("TTL 内未 invalidate → 仍按缓存身份放行（缓存生效）",
                  first.status_code == 200 and second.status_code == 200,
                  f"first={first.status_code} second={second.status_code}")

            # 6b. TTL 置 0 → 下次请求即从库读取，降级立即可见
            user_state.USER_STATE_TTL = 0.0
            third = client.get("/api/v1/teacher/students/progress", params={"course_id": 1},
                               headers={"Authorization": f"Bearer {ttl_token}"})
            check("TTL=0 时旁路改库立即生效 → 403", third.status_code == 403,
                  f"HTTP {third.status_code}")
        finally:
            user_state.USER_STATE_TTL = original_ttl
            user_state.invalidate_user_state()

    print(f"\n通过 {total - len(failures)}/{total}" if not failures
          else f"\n失败 {len(failures)}/{total}：{failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
