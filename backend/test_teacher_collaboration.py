"""多教师协同管理课程：端到端验证脚本

运行方式（在 backend 目录下执行）：
    python test_teacher_collaboration.py

覆盖验收主线：
    教师 A 创建课程 -> 教师 B 通过加课码申请、A 审核通过 -> B 成为协作教师
    -> B 可查看学生 / 上传文档 / 编辑图谱 / 管理题库
    -> B 不能删除课程、不能管理其他教师成员、不能操作其他课程
    -> B 被移除后权限立即失效
    -> 学生调用教师接口被拒绝，原创建者权限不受影响

安全约定：
- 只操作 data/app.db 的副本（脚本开头把 SQLITE_DB_PATH 指向临时副本），
  上传文件也写到临时 UPLOAD_DIR，结束时校验线上库 MD5 未变。
- 文档上传用 background=1 并把 _schedule_processing 打桩，避免触发真实 LLM 抽取。
- 图谱用例依赖 Neo4j：不可用时只验证权限拒绝/放行边界（4003 vs 3000），并显式跳过写入断言。
"""
import hashlib
import os
import shutil
import sys
import tempfile

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_DB = os.path.join(BACKEND_DIR, "data", "app.db")

# ---- 必须在导入 app 之前改掉数据库与上传目录 ----
TMP_DIR = tempfile.mkdtemp(prefix="kgu_collab_")
TMP_DB = os.path.join(TMP_DIR, "app_copy.db")
TMP_UPLOAD = os.path.join(TMP_DIR, "uploads")
os.makedirs(TMP_UPLOAD, exist_ok=True)
shutil.copy2(LIVE_DB, TMP_DB)
os.environ["SQLITE_DB_PATH"] = TMP_DB
os.environ["UPLOAD_DIR"] = TMP_UPLOAD
os.environ["TEACHER_INVITE_CODE"] = "T-COLLAB-E2E"

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient          # noqa: E402
from app.main import app                            # noqa: E402
from app.core.database import db                    # noqa: E402
from app.core.sql_database import sql_db            # noqa: E402
import app.services.document_service as doc_service  # noqa: E402

failures = []
passed = 0
HTTP_403 = -403


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
    with client:                                      # 触发 lifespan -> 在副本上建表/迁移
        pass

    def call(method, path, token=None, **kw):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return getattr(client, method)(path, headers=headers, **kw)

    def code_of(resp):
        try:
            body = resp.json()
        except Exception:
            return -resp.status_code
        if isinstance(body, dict) and "code" in body:
            return body["code"]
        return -resp.status_code

    def data_of(resp):
        try:
            return resp.json().get("data")
        except Exception:
            return None

    def token_of(username, role="teacher"):
        payload = {"username": username, "password": "pw123456", "role": role}
        if role == "teacher":
            payload["teacher_invite_code"] = os.environ["TEACHER_INVITE_CODE"]
        r = call("post", "/api/auth/register", json=payload)
        if code_of(r) not in (0, 2006):
            raise SystemExit(f"注册 {username} 失败: {r.text}")
        r = call("post", "/api/auth/login",
                 json={"username": username, "password": "pw123456"})
        if code_of(r) != 0:
            raise SystemExit(f"登录 {username} 失败: {r.text}")
        return r.json()["data"]["access_token"]

    suffix = f"collab{os.getpid()}"
    t_a = token_of(f"ta_{suffix}")
    t_b = token_of(f"tb_{suffix}")
    t_c = token_of(f"tc_{suffix}")
    s_1 = token_of(f"s1_{suffix}", "student")
    s_2 = token_of(f"s2_{suffix}", "student")
    ids = {name: sql_db.get_user_by_username(f"{name}_{suffix}")["user_id"]
           for name in ("ta", "tb", "tc", "s1", "s2")}
    id_a, id_b, id_c = ids["ta"], ids["tb"], ids["tc"]
    id_s2 = ids["s2"]

    print("=" * 72)
    print("Step 1: 课程创建 + 教师 B 用加课码申请，创建者审核")
    print("=" * 72)
    r = call("post", "/api/v1/courses", t_a,
             json={"course_name": f"协作课程A_{suffix}", "join_mode": "approval",
                   "is_public": 1, "description": "协作教师端到端验证"})
    assert code_of(r) == 0, r.text
    course_a = data_of(r)
    c_a, code_a = course_a["course_id"], course_a["join_code"]

    r = call("post", "/api/v1/courses", t_c,
             json={"course_name": f"隔离课程D_{suffix}", "join_mode": "auto", "is_public": 1})
    assert code_of(r) == 0, r.text
    c_d = data_of(r)["course_id"]

    r = call("post", "/api/v1/courses/join-by-code", t_b, json={"join_code": code_a})
    check("教师 B 用加课码加入 → pending（教师入课必须审核）",
          code_of(r) == 0 and data_of(r)["status"] == "pending", r.text[:140])
    m = sql_db.get_membership(c_a, id_b)
    check("成员行 role=teacher / status=pending", m["role"] == "teacher" and m["status"] == "pending",
          str(dict(m))[:160])

    r = call("get", f"/api/v1/courses/{c_a}/members", t_b)
    check("待审核的教师 B 不能查看学生列表 → 4003", code_of(r) == 4003, f"实际 {code_of(r)}")
    r = call("get", f"/api/v1/courses/{c_a}", t_b)
    check("待审核的教师 B 可看课程元数据（申请进度）", code_of(r) == 0, r.text[:120])
    r = call("get", "/api/v1/courses/my?status=pending&page_size=100", t_b)
    pending_ids = [i["course_id"] for i in data_of(r)["items"]]
    check("申请中的课程出现在「申请中」列表", c_a in pending_ids, str(pending_ids))

    r = call("post", f"/api/v1/courses/{c_a}/members/{id_b}/approve", t_a)
    check("创建者 A 审核通过教师 B", code_of(r) == 0, r.text[:120])
    m = sql_db.get_membership(c_a, id_b)
    check("B 成为 approved 协作教师", m["role"] == "teacher" and m["status"] == "approved",
          str(dict(m))[:160])

    r = call("post", "/api/v1/courses/join-by-code", t_b, json={"join_code": code_a})
    check("B 重复加入同一课程 → 4004，不产生重复关系", code_of(r) == 4004, f"实际 {code_of(r)}")
    cnt = sql_db._query_one(
        "SELECT count(*) AS c FROM t_course_member WHERE course_id=? AND user_id=?", (c_a, id_b))["c"]
    check("成员关系唯一（UNIQUE 约束）", cnt == 1, f"行数 {cnt}")

    r = call("get", "/api/v1/courses/my?status=approved&page_size=100", t_b)
    items = data_of(r)["items"]
    mine = next((i for i in items if i["course_id"] == c_a), None)
    check("协作课程出现在 B 的「我的课程」", mine is not None)
    check("B 对课程 A：is_owner=false / my_role=teacher",
          bool(mine) and mine["is_owner"] is False and mine["my_role"] == "teacher",
          str(mine)[:160])
    check("协作教师能在列表中看到加课码（可管理）", bool(mine) and bool(mine.get("join_code")))

    r = call("get", f"/api/v1/courses/{c_a}", t_b)
    d = data_of(r)
    check("课程详情 can_manage=true 且返回加课码",
          code_of(r) == 0 and d["can_manage"] is True and bool(d.get("join_code")), str(d)[:160])

    print("\n" + "=" * 72)
    print("Step 2: 协作教师的教学管理权限（学生 / 文档 / 题库）")
    print("=" * 72)
    r = call("post", "/api/v1/courses/join-by-code", s_2, json={"join_code": code_a})
    assert code_of(r) == 0, r.text
    r = call("post", f"/api/v1/courses/{c_a}/members/{id_s2}/approve", t_b)
    check("协作教师 B 可审核学生加入申请", code_of(r) == 0, r.text[:120])

    r = call("get", f"/api/v1/courses/{c_a}/members", t_b)
    members = data_of(r)["items"] if code_of(r) == 0 else []
    check("协作教师可查看学生名单（含学习进度字段）",
          code_of(r) == 0 and any(x["user_id"] == id_s2 for x in members)
          and all("progress" in x for x in members), r.text[:140])
    r = call("get", f"/api/v1/courses/{c_a}/members/stats", t_b)
    check("协作教师可查看成员统计", code_of(r) == 0, r.text[:120])
    r = call("get", f"/api/v1/teacher/students/progress?course_id={c_a}", t_b)
    check("协作教师可查看教学监测（学生学习情况）", code_of(r) == 0, r.text[:120])

    # 文档上传：打桩后台调度，只验证权限、落库与 uploader_id，不触发 LLM
    original_schedule = doc_service.DocumentService._schedule_processing
    doc_service.DocumentService._schedule_processing = staticmethod(lambda doc_id: True)
    try:
        r = call("post", "/api/v1/documents/upload?background=1", t_b,
                 files={"file": ("collab_doc.txt", b"collaboration document content",
                                 "text/plain")},
                 data={"course_id": str(c_a)})
    finally:
        doc_service.DocumentService._schedule_processing = original_schedule
    check("协作教师可上传课程文档", code_of(r) == 0, r.text[:160])
    doc_id = data_of(r)["document_id"] if code_of(r) == 0 else None
    doc_row = sql_db.get_document(doc_id) if doc_id else None
    check("文档归属正确，uploader_id 记录实际操作教师 B",
          bool(doc_row) and doc_row["course_id"] == c_a and doc_row["uploader_id"] == id_b,
          str(dict(doc_row))[:180] if doc_row else "无文档行")
    r = call("get", f"/api/v1/documents?course_id={c_a}", t_b)
    check("协作教师可查看课程文档列表", code_of(r) == 0, r.text[:120])
    r = call("get", f"/api/v1/documents/{doc_id}", t_b)
    check("协作教师可查看文档详情", code_of(r) == 0, r.text[:120])
    r = call("get", f"/api/v1/documents/{doc_id}/content", t_b)
    check("协作教师可在线阅读文档内容",
          r.status_code == 200 and r.content == b"collaboration document content",
          f"状态 {r.status_code}")

    # 删除权限：另建一份文档，避免影响后续图谱用例
    doc_service.DocumentService._schedule_processing = staticmethod(lambda _doc_id: True)
    try:
        r = call("post", "/api/v1/documents/upload?background=1", t_b,
                 files={"file": ("collab_delete.txt", b"to be deleted", "text/plain")},
                 data={"course_id": str(c_a)})
    finally:
        doc_service.DocumentService._schedule_processing = original_schedule
    doc_del = data_of(r)["document_id"] if code_of(r) == 0 else None
    try:
        db.query("RETURN 1 AS x")
        neo4j_available_for_delete = True
    except Exception:                             # noqa: BLE001
        neo4j_available_for_delete = False
    if doc_del and neo4j_available_for_delete:
        r = call("delete", f"/api/v1/documents/{doc_del}", t_b)
        check("协作教师可删除课程文档", code_of(r) == 0, r.text[:160])
        check("文档行已删除", sql_db.get_document(doc_del) is None)
    elif doc_del:
        r = call("delete", f"/api/v1/documents/{doc_del}", t_c)
        check("外部教师删除本课程文档 → 4003", code_of(r) == 4003, f"实际 {code_of(r)}")

    q_payload = {
        "course_id": str(c_a), "q_type": "SINGLE",
        "stem": f"协作教师创建的题目_{suffix}",
        "options": ["选项一", "选项二"], "answer": "A", "difficulty": 1,
    }
    r = call("post", "/api/v1/questions", t_b, json=q_payload)
    check("协作教师可创建题目", code_of(r) == 0, r.text[:160])
    q_id = data_of(r)["question_id"] if code_of(r) == 0 else None
    r = call("put", f"/api/v1/questions/{q_id}", t_b, json={"stem": "协作教师修改后的题干"})
    check("协作教师可编辑题目", code_of(r) == 0, r.text[:120])
    r = call("get", f"/api/v1/questions?course_id={c_a}&page_size=100", t_b)
    stems = [q["stem"] for q in data_of(r)["items"]] if code_of(r) == 0 else []
    check("协作教师可查看课程题库", code_of(r) == 0 and "协作教师修改后的题干" in stems)
    r = call("delete", f"/api/v1/questions/{q_id}", t_b)
    check("协作教师可删除无作答记录的题目", code_of(r) == 0, r.text[:120])

    print("\n" + "=" * 72)
    print("Step 3: 协作教师的知识图谱编辑权限")
    print("=" * 72)
    try:
        db.query("RETURN 1 AS x")
        neo4j_up = True
    except Exception as e:                        # noqa: BLE001
        neo4j_up = False
        print(f"  [WARN] Neo4j 不可用，写入断言将跳过：{type(e).__name__}")

    # 跨课程的 document_id 必须被拒绝（无论 Neo4j 是否可用，都在权限/归属校验处返回）
    doc_d = sql_db.create_document(course_id=c_d, uploader_id=id_c,
                                   file_name="isolation_doc.txt",
                                   file_type="TXT", file_size=3)
    r = call("get", f"/api/v1/graph/{c_a}?document_id={doc_d}", t_b)
    check("用本课程权限 + 其他课程 document_id 读图 → 4003（文档归属校验）",
          code_of(r) == 4003, f"实际 {code_of(r)}")
    r = call("post", f"/api/v1/graph/{c_a}/nodes?document_id={doc_d}", t_b,
             json={"name": "越界节点"})
    check("用本课程权限 + 其他课程 document_id 建节点 → 4003",
          code_of(r) == 4003, f"实际 {code_of(r)}")

    r = call("post", f"/api/v1/graph/{c_a}/nodes?document_id={doc_d}", t_c,
             json={"name": "外部教师越权节点"})
    check("未加入课程的教师 C 建节点 → 4003", code_of(r) == 4003, f"实际 {code_of(r)}")

    if neo4j_up and doc_id:
        r = call("post", f"/api/v1/graph/{c_a}/nodes?document_id={doc_id}", t_b,
                 json={"name": f"协作节点1_{suffix}", "category": "概念",
                       "description": "协作教师创建"})
        check("协作教师可新增知识节点", code_of(r) == 0, r.text[:160])
        kp_1 = data_of(r)["kp_id"] if code_of(r) == 0 else None
        r = call("post", f"/api/v1/graph/{c_a}/nodes?document_id={doc_id}", t_b,
                 json={"name": f"协作节点2_{suffix}", "category": "概念"})
        kp_2 = data_of(r)["kp_id"] if code_of(r) == 0 else None
        r = call("put", f"/api/v1/graph/{c_a}/nodes/{kp_1}?document_id={doc_id}", t_b,
                 json={"description": "协作教师修改"})
        check("协作教师可修改知识节点", code_of(r) == 0, r.text[:160])
        r = call("post", f"/api/v1/graph/{c_a}/edges?document_id={doc_id}", t_b,
                 json={"source": kp_1, "target": kp_2, "type": "RELATED_TO"})
        check("协作教师可新增知识关系", code_of(r) == 0, r.text[:160])
        edge_id = data_of(r)["edge_id"] if code_of(r) == 0 else None
        if edge_id:
            r = call("delete", f"/api/v1/graph/{c_a}/edges/{edge_id}?document_id={doc_id}", t_b)
            check("协作教师可删除知识关系", code_of(r) == 0, r.text[:120])
        for kp in (kp_1, kp_2):
            call("delete", f"/api/v1/graph/{c_a}/nodes/{kp}?document_id={doc_id}", t_b)
    else:
        r = call("post", f"/api/v1/graph/{c_a}/nodes?document_id={doc_id}", t_b,
                 json={"name": f"权限探测_{suffix}"})
        check("Neo4j 不可用：协作教师已通过权限闸门（返回 3000 而非 4003）",
              code_of(r) == 3000, f"实际 {code_of(r)}")

    print("\n" + "=" * 72)
    print("Step 4: 治理操作保持创建者专属；B 不能越权")
    print("=" * 72)
    r = call("delete", f"/api/v1/courses/{c_a}?confirm=true", t_b)
    check("协作教师删除课程 → 4003（仅创建者）", code_of(r) == 4003, f"实际 {code_of(r)}")
    r = call("post", f"/api/v1/courses/{c_a}/invites", t_b, json={"role": "teacher"})
    check("协作教师生成「教师邀请」 → 4003（仅创建者）", code_of(r) == 4003, f"实际 {code_of(r)}")
    r = call("post", f"/api/v1/courses/{c_a}/invites", t_b, json={"role": "student"})
    check("协作教师仍可生成「学生邀请」", code_of(r) == 0, r.text[:120])

    r = call("post", f"/api/v1/courses/{c_a}/invites", t_a, json={"role": "teacher"})
    teacher_invite = data_of(r) if code_of(r) == 0 else {}
    check("创建者可生成「教师邀请」", code_of(r) == 0, r.text[:120])
    if teacher_invite.get("invite_id"):
        r = call("delete",
                 f"/api/v1/courses/{c_a}/invites/{teacher_invite['invite_id']}", t_b)
        check("协作教师撤销「教师邀请」 → 4003", code_of(r) == 4003, f"实际 {code_of(r)}")
        r = call("delete",
                 f"/api/v1/courses/{c_a}/invites/{teacher_invite['invite_id']}", t_a)
        check("创建者可撤销「教师邀请」", code_of(r) == 0, r.text[:120])

    r = call("delete", f"/api/v1/courses/{c_a}/members/{id_a}?confirm=true", t_b)
    check("协作教师移除课程创建者 → 4003", code_of(r) == 4003, f"实际 {code_of(r)}")

    # 教师 C 通过创建者的教师邀请加入，用于验证「协作教师不能管理其他教师」
    r = call("post", f"/api/v1/courses/{c_a}/invites", t_a,
             json={"role": "teacher", "expires_in_days": 7})
    token_teacher = data_of(r)["token"]
    r = call("post", "/api/v1/invites/accept", t_c, json={"token": token_teacher})
    check("教师 C 通过教师邀请加入 → approved", code_of(r) == 0, r.text[:120])
    r = call("delete", f"/api/v1/courses/{c_a}/members/{id_c}?confirm=true", t_b)
    check("协作教师移除其他协作教师 → 4003（治理操作仅创建者）",
          code_of(r) == 4003, f"实际 {code_of(r)}")
    r = call("delete", f"/api/v1/courses/{c_a}/members/{id_c}?confirm=true", t_a)
    check("创建者可移除协作教师 C", code_of(r) == 0, r.text[:120])

    r = call("delete", f"/api/v1/courses/{c_a}/members/{id_s2}?confirm=true", t_b)
    check("协作教师可移除学生（日常教学管理）", code_of(r) == 0, r.text[:120])

    print("\n" + "=" * 72)
    print("Step 5: 课程间隔离与角色闸门")
    print("=" * 72)
    for label, fn in [
        ("查看成员", lambda: call("get", f"/api/v1/courses/{c_d}/members", t_b)),
        ("查看文档", lambda: call("get", f"/api/v1/documents?course_id={c_d}", t_b)),
        ("查看题库", lambda: call("get", f"/api/v1/questions?course_id={c_d}", t_b)),
        ("创建题目", lambda: call("post", "/api/v1/questions", t_b,
                                 json={**q_payload, "course_id": str(c_d)})),
        ("读图谱", lambda: call("get", f"/api/v1/graph/{c_d}?document_id={doc_d}", t_b)),
        ("改课程", lambda: call("put", f"/api/v1/courses/{c_d}", t_b,
                               json={"course_name": "越权改名"})),
    ]:
        got = code_of(fn())
        check(f"协作教师对未加入课程执行「{label}」 → 4003", got == 4003, f"实际 {got}")

    r = call("get", f"/api/v1/courses/{c_d}", t_c)
    check("课程 D 的创建者权限不受影响", code_of(r) == 0, r.text[:120])

    for label, fn in [
        ("查看成员", lambda: call("get", f"/api/v1/courses/{c_a}/members", s_1)),
        ("生成邀请", lambda: call("post", f"/api/v1/courses/{c_a}/invites", s_1, json={})),
    ]:
        got = code_of(fn())
        check(f"学生调用教师接口「{label}」 → 4003", got == 4003, f"实际 {got}")
    got = code_of(call("post", "/api/v1/questions", s_1, json=q_payload))
    check(f"学生创建题目 → HTTP 403，实际 {got}", got == HTTP_403)
    got = code_of(call("post", f"/api/v1/graph/{c_a}/nodes?document_id={doc_id}", s_1,
                       json={"name": "学生越权节点"}))
    check(f"学生编辑图谱 → HTTP 403，实际 {got}", got == HTTP_403)
    got = code_of(call("get", f"/api/v1/courses/{c_a}/join-code", s_1))
    check(f"学生读取加课码 → HTTP 403，实际 {got}", got == HTTP_403)

    resp = call("get", f"/api/v1/courses/{c_a}/members")
    check("未登录访问成员列表 → HTTP 401", resp.status_code == 401, f"实际 {resp.status_code}")

    r = call("post", "/api/v1/courses/join-by-code", t_c, json={"join_code": "ZZZZZZZZ"})
    check("无效加课码 → 4005", code_of(r) == 4005, f"实际 {code_of(r)}")
    r = call("post", "/api/v1/invites/accept", t_c, json={"token": "invalid-token-xxxxxxxxxxxxxxxx"})
    check("无效邀请令牌 → 4010", code_of(r) == 4010, f"实际 {code_of(r)}")

    # 过期邀请：创建后把 expires_at 改到过去
    r = call("post", f"/api/v1/courses/{c_a}/invites", t_a, json={"role": "teacher"})
    expired_token = data_of(r)["token"]
    with sql_db._connect() as conn:
        conn.execute("UPDATE t_course_invite SET expires_at='2000-01-01 00:00:00' WHERE token=?",
                     (expired_token,))
        conn.commit()
    r = call("post", "/api/v1/invites/accept", t_c, json={"token": expired_token})
    check("过期邀请不能接受 → 4010", code_of(r) == 4010, f"实际 {code_of(r)}")

    print("\n" + "=" * 72)
    print("Step 6: 移除协作教师后权限立即失效")
    print("=" * 72)
    r = call("delete", f"/api/v1/courses/{c_a}/members/{id_b}?confirm=true", t_a)
    check("创建者移除协作教师 B", code_of(r) == 0, r.text[:120])
    m = sql_db.get_membership(c_a, id_b)
    check("B 的成员状态变为 removed", m["status"] == "removed", str(dict(m))[:140])

    for label, fn in [
        ("查看成员", lambda: call("get", f"/api/v1/courses/{c_a}/members", t_b)),
        ("上传文档权限", lambda: call("post", "/api/v1/documents/upload", t_b,
                                    files={"file": ("x.txt", b"x", "text/plain")},
                                    data={"course_id": str(c_a)})),
        ("创建题目", lambda: call("post", "/api/v1/questions", t_b, json=q_payload)),
        ("编辑图谱", lambda: call("post", f"/api/v1/graph/{c_a}/nodes?document_id={doc_id}", t_b,
                                 json={"name": "移除后越权节点"})),
    ]:
        got = code_of(fn())
        check(f"B 被移除后「{label}」 → 4003", got == 4003, f"实际 {got}")

    r = call("get", "/api/v1/courses/my?status=approved&page_size=100", t_b)
    ids_after = [i["course_id"] for i in data_of(r)["items"]]
    check("被移除后课程 A 不再出现在 B 的我的课程", c_a not in ids_after, str(ids_after))

    r = call("get", f"/api/v1/courses/{c_a}", t_b)
    d = data_of(r)
    check("被移除后课程详情不再下发加课码",
          code_of(r) == 0 and not d.get("join_code"), str(d)[:140])
    r = call("get", f"/api/v1/courses?page_size=100&teacher_id={id_b}", t_b)
    items = data_of(r)["items"] if code_of(r) == 0 else []
    item_a = next((i for i in items if i["course_id"] == c_a), None)
    check("被移除后课程列表中不再下发加课码",
          item_a is None or not item_a.get("join_code"), str(item_a)[:160])

    print("\n" + "=" * 72)
    print("Step 7: 创建者权限与数据隔离最终校验")
    print("=" * 72)
    for label, fn in [
        ("查看详情", lambda: call("get", f"/api/v1/courses/{c_a}", t_a)),
        ("查看成员", lambda: call("get", f"/api/v1/courses/{c_a}/members", t_a)),
        ("查看加课码", lambda: call("get", f"/api/v1/courses/{c_a}/join-code", t_a)),
        ("查看文档", lambda: call("get", f"/api/v1/documents?course_id={c_a}", t_a)),
        ("查看题库", lambda: call("get", f"/api/v1/questions?course_id={c_a}", t_a)),
        ("修改课程", lambda: call("put", f"/api/v1/courses/{c_a}", t_a,
                                 json={"description": "创建者仍然可管理"})),
    ]:
        got = code_of(fn())
        check(f"创建者 A「{label}」 → 0", got == 0, f"实际 {got}")

    check("课程 A 与 D 相互独立（均存在）",
          sql_db.get_course(c_a) is not None and sql_db.get_course(c_d) is not None)
    r = call("get", f"/api/v1/courses/{c_d}/members", t_c)
    check("课程 D 创建者仍可管理自己的成员", code_of(r) == 0, r.text[:120])

    check("线上 app.db MD5 未变", live_before == md5(LIVE_DB),
          f"{live_before[:12]}… -> {md5(LIVE_DB)[:12]}…")

    shutil.rmtree(TMP_DIR, ignore_errors=True)

    print("\n" + "=" * 72)
    if failures:
        print(f"结果: 通过 {passed} 项，失败 {len(failures)} 项")
        for f in failures:
            print("   -", f)
        sys.exit(1)
    print(f"结果: 全部通过（{passed} 项）")
    print("=" * 72)


if __name__ == "__main__":
    main()
