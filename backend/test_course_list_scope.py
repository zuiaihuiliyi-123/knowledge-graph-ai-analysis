"""课程中心改造：课程列表可见范围测试

验证「列表接口不再泄漏别人课程」以及「客户端传 teacher_id 不能扩大结果集」。

运行方式（在 backend 目录下执行）：
    python test_course_list_scope.py

同样只操作 data/app.db 副本，结束校验线上库 MD5 未变。
"""
import hashlib
import os
import shutil
import sys
import tempfile

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_DB = os.path.join(BACKEND_DIR, "data", "app.db")

TMP_DIR = tempfile.mkdtemp(prefix="kgu_scope_")
TMP_DB = os.path.join(TMP_DIR, "app_copy.db")
shutil.copy2(LIVE_DB, TMP_DB)
os.environ["SQLITE_DB_PATH"] = TMP_DB

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient          # noqa: E402
from app.main import app                            # noqa: E402
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

    def data_of(resp):
        return resp.json().get("data") if resp.status_code == 200 else None

    def token_of(username, role):
        call("post", "/api/auth/register",
             json={"username": username, "password": "pw123456", "role": role})
        r = call("post", "/api/auth/login", json={"username": username, "password": "pw123456"})
        assert r.json().get("code") == 0, r.text
        return r.json()["data"]["access_token"]

    suffix = "sc"
    t1 = token_of(f"t1_{suffix}", "teacher")
    t2 = token_of(f"t2_{suffix}", "teacher")
    s1 = token_of(f"s1_{suffix}", "student")
    s2 = token_of(f"s2_{suffix}", "student")

    def make(token, name, join_mode="approval"):
        r = call("post", "/api/v1/courses", token,
                 json={"course_name": name, "join_mode": join_mode})
        assert r.json().get("code") == 0, r.text
        return r.json()["data"]

    A = make(t1, f"范围测试A_{suffix}")
    B = make(t1, f"范围测试B_{suffix}", "auto")
    D = make(t2, f"范围测试D_{suffix}", "auto")
    cA, cB, cD = A["course_id"], B["course_id"], D["course_id"]

    r = call("post", "/api/v1/courses/join-by-code", s1, json={"join_code": A["join_code"]})
    assert r.json().get("code") == 0, r.text
    s1_id = sql_db.get_user_by_username(f"s1_{suffix}")["user_id"]
    call("post", f"/api/v1/courses/{cA}/members/{s1_id}/approve", t1)

    print("=" * 70)
    print("Step 1: 教师只能看到自己的课程")
    print("=" * 70)
    items = data_of(call("get", "/api/v1/courses?page_size=100", t1))["items"]
    ids = {i["course_id"] for i in items}
    check("t1 的课程列表含自己创建的 A、B", {cA, cB} <= ids, f"{sorted(ids)}")
    check("t1 的课程列表不含 t2 的 D", cD not in ids, f"{sorted(ids)}")
    check("t1 的课程列表不含既有课程 5（属于 admin）", 5 not in ids, f"{sorted(ids)}")
    check("t1 课程总数 = 2（自己两门）", data_of(call("get", "/api/v1/courses?page_size=100", t1))["total"] == 2,
          str(data_of(call("get", "/api/v1/courses?page_size=100", t1))["total"]))

    print("\n" + "=" * 70)
    print("Step 2: 学生只能看到已加入的课程")
    print("=" * 70)
    items = data_of(call("get", "/api/v1/courses?page_size=100", s1))["items"]
    ids = {i["course_id"] for i in items}
    check("s1 的列表含已加入的 A", cA in ids, f"{sorted(ids)}")
    check("s1 的列表不含未加入的 B", cB not in ids, f"{sorted(ids)}")
    check("s1 的列表不含未加入的 D", cD not in ids, f"{sorted(ids)}")
    check("s1 的列表不含既有课程 5", 5 not in ids, f"{sorted(ids)}")

    items = data_of(call("get", "/api/v1/courses?page_size=100", s2))["items"]
    check("s2（无任何课程）列表为空", items == [], str(items))

    print("\n" + "=" * 70)
    print("Step 3: 客户端传 teacher_id 不能扩大结果集")
    print("=" * 70)
    r = call("get", f"/api/v1/courses?teacher_id={sql_db.get_user_by_username(f't2_{suffix}')['user_id']}"
                    f"&page_size=100", s1)
    ids = {i["course_id"] for i in data_of(r)["items"]}
    check("s1 传别的教师的 teacher_id → 仍只看得到自己的可见范围", cD not in ids and 5 not in ids,
          f"{sorted(ids)}")
    r = call("get", f"/api/v1/courses?teacher_id={s1_id}&page_size=100", s1)
    ids = {i["course_id"] for i in data_of(r)["items"]}
    check("s1 传自己的 user_id 作为 teacher_id → 仍受成员范围限制",
          cD not in ids and 5 not in ids, f"{sorted(ids)}")

    print("\n" + "=" * 70)
    print("Step 4: 我的课程 / 申请中")
    print("=" * 70)
    r = call("get", "/api/v1/courses/my?page_size=100", s1)
    d = data_of(r)
    check("s1 /courses/my → 只含 A", {i["course_id"] for i in d["items"]} == {cA}, str(d["items"]))
    check("s1 的 my_role=student / my_status=approved",
          d["items"][0]["my_role"] == "student" and d["items"][0]["my_status"] == "approved",
          str(d["items"][0])[:140])

    r = call("get", "/api/v1/courses/my?page_size=100", t1)
    d = data_of(r)
    check("t1 /courses/my → A、B 且 my_role=teacher",
          {i["course_id"] for i in d["items"]} == {cA, cB}
          and all(i["my_role"] == "teacher" for i in d["items"]),
          str([i["my_role"] for i in d["items"]]))

    # s2 申请 A（A 是 approval）→ 进待审核，理由入库
    s2_id = sql_db.get_user_by_username(f"s2_{suffix}")["user_id"]
    r = call("post", f"/api/v1/courses/{cA}/apply", s2, json={"reason": "想学"})
    check("s2 申请「审核后加入」的公开课 A → pending",
          r.json().get("code") == 0 and r.json()["data"]["status"] == "pending", r.text[:140])
    d = data_of(call("get", "/api/v1/courses/my?status=pending&page_size=100", s2))
    check("s2 的「申请中」列表含 A 与申请理由",
          [i["course_id"] for i in d["items"]] == [cA]
          and d["items"][0]["applied_reason"] == "想学", str(d["items"])[:160])

    # 拒绝后仍能在「申请中」看到审核意见
    r = call("post", f"/api/v1/courses/{cA}/members/{s2_id}/reject", t1, json={"comment": "名额已满"})
    check("t1 拒绝 s2 的申请并给理由", r.json().get("code") == 0, r.text[:140])
    d = data_of(call("get", "/api/v1/courses/my?status=pending&page_size=100", s2))
    check("s2 仍能看到被拒记录与审核意见",
          d["items"][0]["review_comment"] == "名额已满", str(d["items"])[:160])

    # 回归：B 是「直接加入」，学生加入就该直接成为成员，不许进待审核
    r = call("post", f"/api/v1/courses/{cB}/apply", s2, json={"reason": "想学"})
    check("s2 加入「直接加入」的公开课 B → 直接 approved（无需审核）",
          r.json().get("code") == 0 and r.json()["data"]["status"] == "approved", r.text[:140])
    check("B 上没有留下待审核记录",
          sql_db.get_membership(cB, s2_id)["status"] == "approved",
          str(sql_db.get_membership(cB, s2_id))[:140])

    print("\n" + "=" * 70)
    print("Step 5: 既有课程未丢失、教师能按关键词/分类过滤")
    print("=" * 70)
    r = call("get", "/api/v1/courses?keyword=范围测试&page_size=100", t1)
    check("关键词过滤生效", data_of(r)["total"] == 2, str(data_of(r)["total"]))
    r = call("get", "/api/v1/courses?keyword=不存在的课程名xyz&page_size=100", t1)
    check("无匹配关键词返回空", data_of(r)["total"] == 0, str(data_of(r)["total"]))

    all_ids_in_copy = {c["course_id"] for c in sql_db.list_courses()}
    check("副本库里既有 7 门课程仍在（未被测试清掉）",
          {5, 9, 10, 11, 12, 30, 31} <= all_ids_in_copy, f"{sorted(all_ids_in_copy)}")

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
