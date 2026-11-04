"""「直接加入」(join_mode='auto') 的课程不该出现待审核 —— 加入规则回归测试

背景：auto 课程的学生用加课码加入后，仍可能落在「申请中 / 等待教师审核」。
根因是「曾被移除 / 历史待审核」的旧成员记录被无条件降级为 pending，
与「直接加入」的课程设置自相矛盾。此脚本逐条锁定修复后的行为。

运行方式（在 backend 目录下）：
    python test_join_auto_no_review.py

只操作 data/app.db 副本，结束校验线上库 MD5 未变。
"""
import hashlib
import os
import shutil
import sys
import tempfile

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_DB = os.path.join(BACKEND_DIR, "data", "app.db")

TMP_DIR = tempfile.mkdtemp(prefix="kgu_autojoin_")
TMP_DB = os.path.join(TMP_DIR, "app_copy.db")
# 线上库是 WAL 模式，连 -wal/-shm 一起复制，避免读到旧快照
shutil.copy2(LIVE_DB, TMP_DB)
for tail in ("-wal", "-shm"):
    src = LIVE_DB + tail
    if os.path.exists(src):
        shutil.copy2(src, TMP_DB + tail)
os.environ["SQLITE_DB_PATH"] = TMP_DB
os.environ["AUTO_BACKUP"] = "0"          # 测试不生成 8MB 快照

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient          # noqa: E402
from app.main import app                            # noqa: E402
from app.core.security import hash_password         # noqa: E402
from app.core.sql_database import sql_db            # noqa: E402

failures = []
passed = 0


def check(label, ok, detail=""):
    global passed
    if ok:
        passed += 1
    else:
        failures.append(f"{label} — {detail}")
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    live_before = md5(LIVE_DB)
    client = TestClient(app)
    with client:
        pass

    def call(method, path, token=None, **kw):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return getattr(client, method)(path, headers=headers, **kw)

    def code_of(resp):
        try:
            body = resp.json()
        except Exception:
            return -resp.status_code
        return body.get("code", -resp.status_code) if isinstance(body, dict) else -resp.status_code

    def data_of(resp):
        return resp.json().get("data")

    def mk_user(username, role):
        """直接建账号再登录：教师自助注册默认关闭（需要 TEACHER_INVITE_CODE）"""
        user_id = sql_db.create_user(username, hash_password("pw123456"), role)
        r = call("post", "/api/auth/login",
                 json={"username": username, "password": "pw123456"})
        assert code_of(r) == 0, r.text
        return data_of(r)["access_token"], user_id

    suffix = "aj"
    t, t_id = mk_user(f"t_{suffix}", "teacher")
    t2, t2_id = mk_user(f"t2_{suffix}", "teacher")
    s1, s1_id = mk_user(f"s1_{suffix}", "student")
    s2, s2_id = mk_user(f"s2_{suffix}", "student")
    s3, s3_id = mk_user(f"s3_{suffix}", "student")

    def mk_course(name, mode):
        r = call("post", "/api/v1/courses", t, json={"course_name": name, "join_mode": mode})
        assert code_of(r) == 0, r.text
        return data_of(r)

    AUTO = mk_course(f"直接加入_{suffix}", "auto")
    APPROVAL = mk_course(f"审核后加入_{suffix}", "approval")
    CLOSED = mk_course(f"关闭加入_{suffix}", "closed")
    cA, cA_code = AUTO["course_id"], AUTO["join_code"]
    cP, cP_code = APPROVAL["course_id"], APPROVAL["join_code"]
    cC, cC_code = CLOSED["course_id"], CLOSED["join_code"]

    print("\n" + "=" * 70)
    print("Step 1: 直接加入的课程，学生用加课码 → 直接成为成员")
    print("=" * 70)
    r = call("post", "/api/v1/courses/join-by-code", s1, json={"join_code": cA_code})
    check("auto + 加课码 → approved（不是 pending）",
          code_of(r) == 0 and data_of(r)["status"] == "approved", r.text[:140])
    m = sql_db.get_membership(cA, s1_id)
    check("成员行 status=approved / join_source=code / joined_at 有值",
          m["status"] == "approved" and m["join_source"] == "code" and m["joined_at"],
          str(dict(m))[:140])
    r = call("post", "/api/v1/courses/join-by-code", s1, json={"join_code": cA_code})
    check("已是成员再提交 → 4004", code_of(r) == 4004, f"实际 {code_of(r)}")

    print("\n" + "=" * 70)
    print("Step 2: 核心回归 —— 被移除过的学生再用加课码，仍应直接进（原 bug）")
    print("=" * 70)
    r = call("delete", f"/api/v1/courses/{cA}/members/{s1_id}?confirm=true", t)
    check("教师移除 s1", code_of(r) == 0 and sql_db.get_membership(cA, s1_id)["status"] == "removed",
          r.text[:140])
    r = call("post", "/api/v1/courses/join-by-code", s1, json={"join_code": cA_code})
    check("曾被移除 + auto + 加课码 → 直接 approved（不再挂「等待教师审核」）",
          code_of(r) == 0 and data_of(r)["status"] == "approved", r.text[:140])
    check("成员行 join_source 回到 code（不再是 apply）",
          sql_db.get_membership(cA, s1_id)["join_source"] == "code",
          str(dict(sql_db.get_membership(cA, s1_id)))[:140])

    print("\n" + "=" * 70)
    print("Step 3: 历史遗留的待审核记录会被就地纠正")
    print("=" * 70)
    sql_db.upsert_membership(cA, s1_id, "student", "pending", "apply")
    check("先造一条 pending 旧记录", sql_db.get_membership(cA, s1_id)["status"] == "pending")
    r = call("post", "/api/v1/courses/join-by-code", s1, json={"join_code": cA_code})
    check("pending 旧记录 + auto + 加课码 → 升为 approved（不再回 4004「审核中」）",
          code_of(r) == 0 and data_of(r)["status"] == "approved", r.text[:140])

    print("\n" + "=" * 70)
    print("Step 4: 安全规则不变 —— 教师账号经加课码仍要审核")
    print("=" * 70)
    r = call("post", "/api/v1/courses/join-by-code", t2, json={"join_code": cA_code})
    check("教师 + auto + 加课码 → pending（协作教师有管理权，不能凭码直接得权）",
          code_of(r) == 0 and data_of(r)["status"] == "pending", r.text[:140])
    check("教师成员行 role=teacher",
          sql_db.get_membership(cA, t2_id)["role"] == "teacher",
          str(dict(sql_db.get_membership(cA, t2_id)))[:140])

    print("\n" + "=" * 70)
    print("Step 5: 安全规则不变 —— 审核后加入的课程，被移除者仍需重新审核")
    print("=" * 70)
    r = call("post", "/api/v1/courses/join-by-code", s2, json={"join_code": cP_code})
    check("approval + 加课码 → pending", code_of(r) == 0 and data_of(r)["status"] == "pending",
          r.text[:140])
    call("post", f"/api/v1/courses/{cP}/members/{s2_id}/approve", t)
    check("教师通过后 s2 成为成员", sql_db.get_membership(cP, s2_id)["status"] == "approved")
    call("delete", f"/api/v1/courses/{cP}/members/{s2_id}?confirm=true", t)
    r = call("post", "/api/v1/courses/join-by-code", s2, json={"join_code": cP_code})
    check("曾被移除 + approval + 加课码 → 仍 pending（防加课码外泄复活）",
          code_of(r) == 0 and data_of(r)["status"] == "pending", r.text[:140])

    print("\n" + "=" * 70)
    print("Step 6: 关闭加入的课程直接拒绝")
    print("=" * 70)
    r = call("post", "/api/v1/courses/join-by-code", s2, json={"join_code": cC_code})
    check("closed + 加课码 → 4006", code_of(r) == 4006, f"实际 {code_of(r)}")

    print("\n" + "=" * 70)
    print("Step 7: 「发现课程」加入也要遵守同一套规则")
    print("=" * 70)
    r = call("post", f"/api/v1/courses/{cA}/apply", s2, json={"reason": "想学"})
    check("auto + 发现课程加入 → 直接 approved（不是申请）",
          code_of(r) == 0 and data_of(r)["status"] == "approved", r.text[:140])
    d = data_of(call("get", "/api/v1/courses/my?status=pending&page_size=100", s2))
    check("「申请中」列表里没有 auto 课程",
          all(i["course_id"] != cA for i in d["items"]), str(d["items"])[:160])
    r = call("post", f"/api/v1/courses/{cP}/apply", s3, json={"reason": "想学"})
    check("approval + 发现课程加入 → pending",
          code_of(r) == 0 and data_of(r)["status"] == "pending", r.text[:140])

    print("\n" + "=" * 70)
    print("Step 8: 清理与线上库校验")
    print("=" * 70)
    for cid in (cA, cP, cC):
        call("delete", f"/api/v1/courses/{cid}?confirm=true", t)
    check("测试课程已删除", all(sql_db.get_course(c) is None for c in (cA, cP, cC)))
    check("线上 app.db MD5 未变", live_before == md5(LIVE_DB),
          f"{live_before[:12]}… -> {md5(LIVE_DB)[:12]}…")

    shutil.rmtree(TMP_DIR, ignore_errors=True)

    print("\n" + "=" * 70)
    if failures:
        print(f"结果: 通过 {passed} 项，失败 {len(failures)} 项")
        for f in failures:
            print("   -", f)
        sys.exit(1)
    print(f"结果: 全部通过（{passed} 项）")
    print("=" * 70)


if __name__ == "__main__":
    main()
