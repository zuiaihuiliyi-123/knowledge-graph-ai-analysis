"""课程中心改造：权限矩阵测试（表驱动，正/负两类用例）

运行方式（在 backend 目录下执行）：
    python test_permission_matrix.py

安全约定（重要）：
- 全程只操作 data/app.db 的【副本】：脚本一开始就把 SQLITE_DB_PATH 指向临时副本，
  结束再校验线上库 MD5 未变。既有测试脚本（如 test_courses_crud.py）会清空业务数据，
  不能拿来跑线上库，本脚本刻意不碰任何既有数据。
- 对 Neo4j 只读：不写入任何节点/关系，正向用例复用已有课程的真实图谱数据。
- 不调用上传接口（会触发 LLM 抽取），文档行直接写 SQLite 副本 + 一个真实临时文件。

关于期望值：本项目有两类拒绝，
- 4003 业务码（HTTP 200，信封内 code=4003）：接口用 get_current_user + Permissions 判定；
- HTTP 403（FastAPI 的 HTTPException，来自 require_teacher）：学生访问教师专属接口。
两者都是「正确拒绝」，测试按接口实际使用的依赖分别断言。
"""
import hashlib
import os
import shutil
import sys
import tempfile

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_DB = os.path.join(BACKEND_DIR, "data", "app.db")

# ---- 必须在导入 app 之前改掉数据库路径 ----
TMP_DIR = tempfile.mkdtemp(prefix="kgu_perm_")
TMP_DB = os.path.join(TMP_DIR, "app_copy.db")
shutil.copy2(LIVE_DB, TMP_DB)
os.environ["SQLITE_DB_PATH"] = TMP_DB
# 阶段 1A 起教师注册需要邀请码；本脚本自建测试教师，显式给一个仅测试用的值。
os.environ["TEACHER_INVITE_CODE"] = "T-PERM-MATRIX"

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
HTTP_403 = -403                                       # code_of 对非信封响应的表示


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
    with client:                                      # 触发 lifespan → 在副本上建表/迁移
        pass

    def call(method, path, token=None, **kw):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return getattr(client, method)(path, headers=headers, **kw)

    def code_of(resp):
        """业务码；非信封响应（HTTPException / 二进制）返回 -HTTP状态码以便区分"""
        try:
            body = resp.json()
        except Exception:
            return -resp.status_code
        if isinstance(body, dict) and "code" in body:
            return body["code"]
        return -resp.status_code

    def token_of(username, password="pw123456", role=None):
        if role:
            payload = {"username": username, "password": password, "role": role}
            if role == "teacher":
                payload["teacher_invite_code"] = os.environ["TEACHER_INVITE_CODE"]
            r = call("post", "/api/auth/register", json=payload)
            if code_of(r) not in (0, 2006):           # 2006 = 已存在（重复运行时）
                raise SystemExit(f"注册 {username} 失败: {r.text}")
        r = call("post", "/api/auth/login", json={"username": username, "password": password})
        if code_of(r) != 0:
            raise SystemExit(f"登录 {username} 失败: {r.text}")
        return r.json()["data"]["access_token"]

    print("=" * 70)
    print("Step 1: 准备账号（注册到副本库，不影响线上数据）")
    print("=" * 70)
    suffix = "pm"
    t1 = token_of(f"t1_{suffix}", role="teacher")
    t2 = token_of(f"t2_{suffix}", role="teacher")
    s1 = token_of(f"s1_{suffix}", role="student")
    s2 = token_of(f"s2_{suffix}", role="student")
    print("  t1/t2（教师）、s1/s2（学生）就绪")

    admin_token = None
    r = call("post", "/api/auth/login", json={"username": "admin", "password": "admin123"})
    if code_of(r) == 0:
        admin_token = r.json()["data"]["access_token"]
        print("  admin（既有教师，持有课程 5/10/11/12/30/31 与真实图谱）登录成功")
    else:
        print("  [WARN] admin 登录失败，跳过「既有真实图谱」用例")

    print("\n" + "=" * 70)
    print("Step 2: 建课（t1：A=审核制 / B=自动加入 / C=关闭加入；t2：D）")
    print("=" * 70)

    def create_course(token, name, join_mode="approval", is_public=1):
        r = call("post", "/api/v1/courses", token,
                 json={"course_name": name, "join_mode": join_mode, "is_public": is_public})
        assert code_of(r) == 0, r.text
        return r.json()["data"]

    A = create_course(t1, f"权限测试A_{suffix}", "approval")
    B = create_course(t1, f"权限测试B_{suffix}", "auto")
    C = create_course(t1, f"权限测试C_{suffix}", "closed")
    D = create_course(t2, f"权限测试D_{suffix}", "auto")
    cA, cB, cC, cD = A["course_id"], B["course_id"], C["course_id"], D["course_id"]
    print(f"  A={cA} code={A['join_code']}  B={cB} code={B['join_code']}  C={cC}  D={cD}")
    check("创建课程即生成 8 位加课码", bool(A.get("join_code")) and len(A["join_code"]) == 8)
    check("加课码不等于课程主键", A["join_code"] != str(cA))
    check("创建者自动成为 approved 教师成员",
          sql_db.get_membership(cA, sql_db.get_user_by_username(f"t1_{suffix}")["user_id"])
          ["status"] == "approved")

    doc_file = os.path.join(TMP_DIR, "perm_doc.txt")
    with open(doc_file, "w", encoding="utf-8") as f:
        f.write("权限测试文档内容")
    doc_a = sql_db.create_document(course_id=cA, uploader_id=1, file_name="perm_doc.txt",
                                   file_type="TXT", file_size=os.path.getsize(doc_file))
    sql_db.update_document(doc_a, file_path=doc_file)
    doc_d = sql_db.create_document(course_id=cD, uploader_id=1, file_name="perm_doc_d.txt",
                                   file_type="TXT", file_size=10)
    print(f"  课程A文档 doc_id={doc_a}，课程D文档 doc_id={doc_d}")

    print("\n" + "=" * 70)
    print("Step 3: 成员关系（s1 加入 A 并审核通过；s2 自动加入 B）")
    print("=" * 70)
    s1_id = sql_db.get_user_by_username(f"s1_{suffix}")["user_id"]
    s2_id = sql_db.get_user_by_username(f"s2_{suffix}")["user_id"]

    r = call("post", "/api/v1/courses/join-by-code", s1, json={"join_code": A["join_code"]})
    check("s1 用 A 的加课码加入 → 待审核（approval）",
          code_of(r) == 0 and r.json()["data"]["status"] == "pending", r.text[:120])

    r = call("get", f"/api/v1/courses/{cA}/members?status=pending", t1)
    check("t1 能看到 1 条待审核", code_of(r) == 0 and r.json()["data"]["total"] == 1, r.text[:120])

    r = call("post", f"/api/v1/courses/{cA}/members/{s1_id}/approve", t1)
    check("t1 同意 s1 的申请", code_of(r) == 0, r.text[:120])

    r = call("post", "/api/v1/courses/join-by-code", s2, json={"join_code": B["join_code"]})
    check("s2 用 B 的加课码（auto）→ 直接通过",
          code_of(r) == 0 and r.json()["data"]["status"] == "approved", r.text[:120])

    r = call("post", "/api/v1/courses/join-by-code", s2, json={"join_code": B["join_code"]})
    check("s2 重复加入 B → 4004", code_of(r) == 4004, r.text[:120])

    r = call("post", "/api/v1/courses/join-by-code", s1, json={"join_code": C["join_code"]})
    check("s1 用关闭加入的 C 的加课码 → 4006", code_of(r) == 4006, r.text[:120])

    r = call("post", "/api/v1/courses/join-by-code", s1, json={"join_code": "ZZZZZZZZ"})
    check("无效加课码 → 4005", code_of(r) == 4005, r.text[:120])

    print("\n" + "=" * 70)
    print("Step 4: 负向权限用例（s2 是学生且不是 A 的成员）")
    print("=" * 70)
    s2_mark = {"course_id": str(cA), "document_id": str(doc_a),
               "kp_id": "kp_nonexistent_perm", "status": "MASTERED", "mastery_level": 100}
    counts_before = {t: sql_db._query_one(f"SELECT count(*) AS c FROM {t}")["c"]
                     for t in ("t_course_member", "t_course_invite", "t_learning_record",
                               "t_student_favorite", "t_document", "t_course")}

    student_negative = [
        ("GET  文档列表",       (lambda: call("get", f"/api/v1/documents?course_id={cA}", s2)), 4003),
        ("GET  文档详情",       (lambda: call("get", f"/api/v1/documents/{doc_a}", s2)), 4003),
        ("GET  图谱",           (lambda: call("get", f"/api/v1/graph/{cA}?document_id={doc_a}", s2)), 4003),
        ("GET  收藏列表",       (lambda: call("get", f"/api/v1/favorites?course_id={cA}", s2)), 4003),
        ("POST 新增收藏",       (lambda: call("post", "/api/v1/favorites", s2,
                                             json={"course_id": str(cA), "document_id": str(doc_a),
                                                   "kp_id": "kp_x"})), 4003),
        ("POST 标记掌握",       (lambda: call("post", "/api/v1/learning/mark", s2, json=s2_mark)), 4003),
        ("GET  学习进度",       (lambda: call("get", f"/api/v1/learning/progress"
                                             f"?course_id={cA}&document_id={doc_a}", s2)), 4003),
        ("POST 问答",           (lambda: call("post", "/api/v1/qa/ask", s2,
                                             json={"question": "测试", "course_id": str(cA)})), 4003),
        ("POST 学习路径推荐",   (lambda: call("post", "/api/v1/learning-path/recommend", s2,
                                             json={"mastered": [], "course_id": str(cA)})), 4003),
        ("POST 目标路径",       (lambda: call("post", "/api/v1/learning-path/path-to-target", s2,
                                             json={"target": "x", "course_id": str(cA)})), 4003),
        ("GET  前置知识",       (lambda: call("get", f"/api/v1/learning-path/prerequisites/x"
                                             f"?course_id={cA}", s2)), 4003),
        ("GET  成员列表",       (lambda: call("get", f"/api/v1/courses/{cA}/members", s2)), 4003),
        ("GET  成员统计",       (lambda: call("get", f"/api/v1/courses/{cA}/members/stats", s2)), 4003),
        ("POST 生成邀请",       (lambda: call("post", f"/api/v1/courses/{cA}/invites", s2, json={})), 4003),
        # 以下接口用 require_teacher 做角色闸门 → 学生得到 HTTP 403
        ("POST 图谱·新增节点",  (lambda: call("post", f"/api/v1/graph/{cA}/nodes"
                                             f"?document_id={doc_a}", s2,
                                             json={"name": "越权节点"})), HTTP_403),
        ("POST 图谱·新增关系",  (lambda: call("post", f"/api/v1/graph/{cA}/edges"
                                             f"?document_id={doc_a}", s2,
                                             json={"source": "a", "target": "b"})), HTTP_403),
        ("GET  加课码",         (lambda: call("get", f"/api/v1/courses/{cA}/join-code", s2)), HTTP_403),
        ("POST 刷新加课码",     (lambda: call("post", f"/api/v1/courses/{cA}/join-code/refresh", s2)), HTTP_403),
        ("PUT  改课程",         (lambda: call("put", f"/api/v1/courses/{cA}", s2,
                                             json={"course_name": "篡改"})), HTTP_403),
        ("DELETE 删课程",       (lambda: call("delete", f"/api/v1/courses/{cA}?confirm=true", s2)), HTTP_403),
        ("GET  教学监测",       (lambda: call("get", f"/api/v1/teacher/students/progress"
                                             f"?course_id={cA}", s2)), HTTP_403),
    ]
    for label, fn, expected in student_negative:
        got = code_of(fn())
        check(f"{label} → {expected}", got == expected, f"实际 {got}")

    resp = call("get", f"/api/v1/documents/{doc_a}/content", s2)
    check("GET 文档内容 → HTTP 403（阅读器靠它显示「无权限」）",
          resp.status_code == 403, f"实际 {resp.status_code}")

    counts_after = {t: sql_db._query_one(f"SELECT count(*) AS c FROM {t}")["c"] for t in counts_before}
    check("被拒绝的写操作没有落库（行数不变）", counts_before == counts_after,
          f"{counts_before} vs {counts_after}")

    print("\n" + "=" * 70)
    print("Step 5: 公开课「可见元数据但读不到内容」；私有课连元数据都不给")
    print("=" * 70)
    E = create_course(t1, f"权限测试E_私有_{suffix}", "approval", is_public=0)
    cE = E["course_id"]
    r = call("get", f"/api/v1/courses/{cD}", s1)
    check("① 学生读未加入的公开课 D 详情 → 允许（申请加入前必须能看课程信息）",
          code_of(r) == 0, r.text[:120])
    r = call("get", f"/api/v1/documents?course_id={cD}", s1)
    check("② 学生读未加入课程 D 的文档列表 → 4003（元数据可读 ≠ 内容可读）",
          code_of(r) == 4003, r.text[:120])
    r = call("get", f"/api/v1/graph/{cD}?document_id={doc_d}", s1)
    check("③ 学生读未加入课程 D 的图谱 → 4003", code_of(r) == 4003, r.text[:120])
    r = call("get", f"/api/v1/courses/{cE}", s1)
    check("④ 学生读未加入的【私有】课 E 详情 → 4003", code_of(r) == 4003, r.text[:120])
    r = call("get", f"/api/v1/courses/{cE}", t2)
    check("⑤ 别的教师读私有课 E 详情 → 4003", code_of(r) == 4003, r.text[:120])
    r = call("get", "/api/v1/courses/discover?page_size=100", s1)
    ids = [i["course_id"] for i in r.json()["data"]["items"]] if code_of(r) == 0 else []
    check("⑥ 发现课程列表含公开课 D", cD in ids, f"列表 {ids}")
    check("⑦ 发现课程列表不含私有课 E", cE not in ids, f"列表 {ids}")
    check("⑧ 发现课程列表不含已加入的 A", cA not in ids, f"列表 {ids}")

    print("\n" + "=" * 70)
    print("Step 6: 正向用例（s1 是 A 的已通过成员）")
    print("=" * 70)
    for label, fn, expected in [
        ("GET  课程详情", (lambda: call("get", f"/api/v1/courses/{cA}", s1)), (0,)),
        ("GET  文档列表", (lambda: call("get", f"/api/v1/documents?course_id={cA}", s1)), (0,)),
        ("GET  文档详情", (lambda: call("get", f"/api/v1/documents/{doc_a}", s1)), (0,)),
        ("GET  图谱",     (lambda: call("get", f"/api/v1/graph/{cA}?document_id={doc_a}", s1)), (0, 3000)),
        ("GET  收藏列表", (lambda: call("get", f"/api/v1/favorites?course_id={cA}", s1)), (0,)),
        ("GET  学习进度", (lambda: call("get", f"/api/v1/learning/progress"
                                       f"?course_id={cA}&document_id={doc_a}", s1)), (0,)),
        ("POST 问答",     (lambda: call("post", "/api/v1/qa/ask", s1,
                                       json={"question": "测试", "course_id": str(cA)})), (0,)),
    ]:
        got = code_of(fn())
        check(f"{label} → {expected}", got in expected, f"实际 {got}")

    resp = call("get", f"/api/v1/documents/{doc_a}/content", s1)
    check("GET 文档内容 → HTTP 200 + 字节正确",
          resp.status_code == 200 and resp.content.decode("utf-8") == "权限测试文档内容",
          f"状态 {resp.status_code}")

    r = call("get", f"/api/v1/courses/{cA}/join-code", s1)
    check("成员学生拿不到加课码 → 403（教师专属接口）", code_of(r) == HTTP_403, r.text[:120])

    print("\n" + "=" * 70)
    print("Step 7: 跨教师（t2 是教师，但不是 A 的教师）")
    print("=" * 70)
    for label, fn in [
        ("PUT  改课程",        lambda: call("put", f"/api/v1/courses/{cA}", t2, json={"course_name": "越权改名"})),
        ("DELETE 删课程",      lambda: call("delete", f"/api/v1/courses/{cA}?confirm=true", t2)),
        ("GET  图谱",          lambda: call("get", f"/api/v1/graph/{cA}?document_id={doc_a}", t2)),
        ("GET  文档列表",      lambda: call("get", f"/api/v1/documents?course_id={cA}", t2)),
        ("GET  文档内容",      lambda: call("get", f"/api/v1/documents/{doc_a}/content", t2)),
        ("GET  成员列表",      lambda: call("get", f"/api/v1/courses/{cA}/members", t2)),
        ("POST 生成邀请",      lambda: call("post", f"/api/v1/courses/{cA}/invites", t2, json={})),
        ("GET  教学监测",      lambda: call("get", f"/api/v1/teacher/students/progress?course_id={cA}", t2)),
        ("POST 图谱·新增节点", lambda: call("post", f"/api/v1/graph/{cA}/nodes?document_id={doc_a}", t2,
                                            json={"name": "越权"})),
        ("GET  加课码",        lambda: call("get", f"/api/v1/courses/{cA}/join-code", t2)),
    ]:
        got = code_of(fn())
        check(f"t2 {label} → 4003", got == 4003, f"实际 {got}")

    r = call("get", f"/api/v1/courses/{cD}/join-code", t2)
    check("t2 读自己课程 D 的加课码 → 允许", code_of(r) == 0, r.text[:120])

    print("\n" + "=" * 70)
    print("Step 8: 课程创建者 t1 全部放行")
    print("=" * 70)
    for label, fn, expected in [
        ("GET  详情",     (lambda: call("get", f"/api/v1/courses/{cA}", t1)), (0,)),
        ("GET  加课码",   (lambda: call("get", f"/api/v1/courses/{cA}/join-code", t1)), (0,)),
        ("GET  成员列表", (lambda: call("get", f"/api/v1/courses/{cA}/members", t1)), (0,)),
        ("GET  成员统计", (lambda: call("get", f"/api/v1/courses/{cA}/members/stats", t1)), (0,)),
        ("GET  教学监测", (lambda: call("get", f"/api/v1/teacher/students/progress?course_id={cA}", t1)), (0,)),
        ("GET  图谱",     (lambda: call("get", f"/api/v1/graph/{cA}?document_id={doc_a}", t1)), (0, 3000)),
        ("PUT  改课程",   (lambda: call("put", f"/api/v1/courses/{cA}", t1,
                                       json={"description": "改简介"})), (0,)),
    ]:
        got = code_of(fn())
        check(f"t1 {label} → {expected}", got in expected, f"实际 {got}")

    print("\n" + "=" * 70)
    print("Step 9: 未登录 / 路由顺序 / 既有真实图谱")
    print("=" * 70)
    for label, path in [("课程详情", f"/api/v1/courses/{cA}"),
                        ("文档列表", f"/api/v1/documents?course_id={cA}"),
                        ("发现课程", "/api/v1/courses/discover"),
                        ("我的课程", "/api/v1/courses/my"),
                        ("个人资料", "/api/v1/profile"),
                        ("成员列表", f"/api/v1/courses/{cA}/members")]:
        resp = call("get", path)
        check(f"未登录 GET {label} → 401", resp.status_code == 401, f"实际 {resp.status_code}")

    for label, path in [("discover", "/api/v1/courses/discover"), ("my", "/api/v1/courses/my")]:
        resp = call("get", path, s1)
        check(f"GET /courses/{label} → HTTP 200（静态路径未被 /{{course_id}} 抢先匹配）",
              resp.status_code == 200 and code_of(resp) == 0, f"状态 {resp.status_code}")

    if admin_token:
        r = call("get", "/api/v1/graph/5?document_id=4", admin_token)
        got = code_of(r)
        nodes = r.json()["data"]["nodes"] if got == 0 else []
        check("既有课程 5：课程创建者可读真实图谱", got == 0, f"实际 {got}")
        check("既有课程 5：图谱确有节点（正向路径未被破坏）", len(nodes) > 0, f"节点数 {len(nodes)}")
        r = call("get", "/api/v1/graph/5?document_id=4", s1)
        check("既有课程 5：非成员学生 → 4003", code_of(r) == 4003, f"实际 {code_of(r)}")
        r = call("get", "/api/v1/courses/5", t1)
        check("既有课程 5：别的教师读详情 → 4003（改造前可读）", code_of(r) == 4003,
              f"实际 {code_of(r)}")
        r = call("get", "/api/v1/courses/list_check_placeholder", t1)
        check("不存在的课程 id 非整数 → 404/422（不误判为 4003）", r.status_code in (404, 422),
              f"实际 {r.status_code}")

    print("\n" + "=" * 70)
    print("Step 10: 移除后不能用加课码自动复活（安全关键）")
    print("=" * 70)
    r = call("delete", f"/api/v1/courses/{cB}/members/{s2_id}?confirm=true", t1)
    check("t1 移除 B 中的学生 s2", code_of(r) == 0, r.text[:120])
    r = call("post", "/api/v1/courses/join-by-code", s2, json={"join_code": B["join_code"]})
    check("s2 再用 B 的加课码（auto）→ 只能 pending，不能自动通过",
          code_of(r) == 0 and r.json()["data"]["status"] == "pending", r.text[:120])
    m = sql_db.get_membership(cB, s2_id)
    check("s2 在 B 的状态 = pending 且 join_source = apply",
          m["status"] == "pending" and m["join_source"] == "apply", str(dict(m))[:160])
    r = call("get", f"/api/v1/courses/{cB}/members?status=removed&role=student", t1)
    check("移除后成员记录保留为 removed（软移除）",
          code_of(r) == 0 and r.json()["data"]["total"] >= 0, r.text[:120])

    print("\n" + "=" * 70)
    print("Step 11: 线上库必须原封不动")
    print("=" * 70)
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
