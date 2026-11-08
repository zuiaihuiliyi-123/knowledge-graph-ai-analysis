"""P1 修复回归（临时库，不触碰线上 app.db / Neo4j、不调外部 API）

覆盖本批 P1 修复里**不需要 Neo4j / LLM / embedding 服务**的判定逻辑：
1. `_is_code_symbol` 不再误杀数学/中文括号知识点；
2. `_merge_results` 暴露分块成功/失败计数；
3. 账号被降级后旧 teacher 成员行不再授予管理权；
4. 学生不能接受教师协作邀请，且拒绝不消费令牌；
5. 不能注销最后一个启用管理员；
6. `join_code` 只对创建者/已通过协作教师可见；
7. 向量表 `model` 列迁移与按模型过滤；
8. 上传复用排除失败/丢文件文档；
9. 删除文档级联清理题目导入批次；
10. 向量维度不一致返回空而非抛异常。

运行：cd backend && python test_p1_fixes.py
"""
import gc
import os
import shutil
import sqlite3
import sys
import tempfile
import time

# 与其他 backend/test_*.py 一致：tempfile 默认目录不可用时落在 backend/ 下，
# 结束时会清理；本目录仅用于测试，不读也不写线上 app.db。
_TMP = tempfile.mkdtemp(prefix="kg_p1_fixes_")
os.environ["SQLITE_DB_PATH"] = os.path.join(_TMP, "app.db")
os.environ["AUTO_BACKUP"] = "0"
os.environ["UPLOAD_DIR"] = os.path.join(_TMP, "uploads")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

from app.core.config import settings                       # noqa: E402
from app.core.permissions import Permissions               # noqa: E402
from app.core.security import hash_password                # noqa: E402
from app.core.sql_database import sql_db                   # noqa: E402
from app.services import course_service as course_service_module  # noqa: E402
from app.services.auth_service import AuthService          # noqa: E402
from app.services.course_service import CourseService      # noqa: E402
from app.services.document_service import DocumentService  # noqa: E402
from app.services.knowledge_extractor import KnowledgeExtractor, _is_code_symbol  # noqa: E402
from app.services.member_service import MemberService      # noqa: E402
from app.services.vector_index import vector_index         # noqa: E402

total = 0
failures = []


def check(label, ok, detail=""):
    global total
    total += 1
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def main():
    sql_db.init_tables()
    print(f"临时库：{sql_db.db_path}\n")

    # ---------- 1. 代码符号过滤 ----------
    print("Step 1: _is_code_symbol 不再误杀数学/中文括号知识点")
    check("时间复杂度 O(n) 保留", _is_code_symbol("时间复杂度 O(n)") is False)
    check("f(x) 的单调性 保留", _is_code_symbol("f(x) 的单调性") is False)
    check("category=公式 的 f(x) 保留", _is_code_symbol("f(x)", "公式") is False)
    check("BUILD_TREE() 判为代码符号", _is_code_symbol("BUILD_TREE()") is True)
    check("equals() 判为代码符号", _is_code_symbol("equals()") is True)
    check("UPDATE_HEIGHT 判为代码符号", _is_code_symbol("UPDATE_HEIGHT") is True)
    check("纯运算符 == 判为代码符号", _is_code_symbol("==") is True)

    # ---------- 2. 分块成功/失败计数 ----------
    print("\nStep 2: _merge_results 暴露分块计数")
    merged = KnowledgeExtractor._merge_results([
        {"error": "chunk timeout"},
        {"entities": [], "relations": []},
    ])
    check("chunk_count == 2", merged.get("chunk_count") == 2, str(merged.get("chunk_count")))
    check("failed_chunk_count == 1", merged.get("failed_chunk_count") == 1,
          str(merged.get("failed_chunk_count")))
    check("succeeded_chunk_count == 1", merged.get("succeeded_chunk_count") == 1,
          str(merged.get("succeeded_chunk_count")))
    check("error 仍表示「至少一个分块失败」", bool(merged.get("error")),
          str(merged.get("error"))[:60])

    teacher_id = sql_db.create_user("v_teacher", hash_password("pw123456"), role="teacher")
    student_id = sql_db.create_user("v_student", hash_password("pw123456"), role="student")
    teacher2_id = sql_db.create_user("v_teacher2", hash_password("pw123456"), role="teacher")

    # ---------- 3. 账号角色兜底管理权 ----------
    print("\nStep 3: 被降级账号的旧 teacher 成员行不再授予管理权")
    cid = sql_db.create_course("验证课程", teacher_id, join_code="ABC123")
    sql_db.upsert_membership(cid, student_id, "teacher", "approved", "invite")
    r_student = Permissions.resolve(cid, {"user_id": student_id, "role": "student"})
    check("关系仍识别为 TEACHER_MEMBER",
          r_student["data"]["relation"] == "TEACHER_MEMBER", r_student["data"]["relation"])
    check("student 账号 can_manage == False", r_student["data"]["can_manage"] is False)
    check("内容仍可读", r_student["data"]["can_read_content"] is True)
    r_teacher = Permissions.resolve(cid, {"user_id": student_id, "role": "teacher"})
    check("teacher 账号 can_manage == True", r_teacher["data"]["can_manage"] is True)
    r_owner_student = Permissions.require_course_owner(
        cid, {"user_id": teacher_id, "role": "student"})
    check("被降级的课程创建者不能删课", r_owner_student["ok"] is False,
          r_owner_student.get("message", ""))

    # ---------- 4. 教师邀请必须由教师接受 ----------
    print("\nStep 4: 学生不能接受教师协作邀请")
    cid2 = sql_db.create_course("邀请验证课程", teacher_id)
    token = "tok_teacher_invite_1"
    sql_db.create_invite(cid2, token, teacher_id, role="teacher")
    bad = MemberService.accept_invite(student_id, token)
    check("学生接受被拒绝", bad["ok"] is False and bad["code"] == 4003, str(bad)[:80])
    inv = sql_db.get_invite_by_token(token)
    check("拒绝后邀请未被消费", not inv.get("used_by"), str(inv.get("used_by")))
    good = MemberService.accept_invite(teacher2_id, token)
    check("教师接受成功", good["ok"] is True, str(good)[:80])
    check("教师成员行已写入", sql_db.get_membership(cid2, teacher2_id)["status"] == "approved")

    # ---------- 5. 最后一个管理员保护 ----------
    print("\nStep 5: 不能注销最后一个启用管理员")
    admin_id = sql_db.create_user("v_admin", hash_password("pw123456"), role="admin")
    with sql_db._connect() as conn:
        conn.execute("UPDATE t_user SET is_active = 0 WHERE role = 'admin' AND user_id != ?",
                     (admin_id,))
        conn.commit()
    last = AuthService.deactivate(admin_id, "pw123456")
    check("注销最后一个管理员被拒绝", last["ok"] is False and last["code"] == 4003,
          str(last)[:80])
    check("账号仍启用", sql_db.get_user_by_id(admin_id)["is_active"] == 1)
    sql_db.create_user("v_admin2", hash_password("pw123456"), role="admin")
    ok_deact = AuthService.deactivate(admin_id, "pw123456")
    check("有第二个管理员后可注销", ok_deact["ok"] is True, str(ok_deact)[:80])
    check("账号已停用", sql_db.get_user_by_id(admin_id)["is_active"] == 0)

    # ---------- 6. join_code 可见性 ----------
    print("\nStep 6: join_code 只对创建者/已通过协作教师可见")
    pending_teacher = sql_db.create_user("v_teacher_pending", hash_password("pw123456"),
                                         role="teacher")
    sql_db.upsert_membership(cid, pending_teacher, "teacher", "pending", "invite")
    original_query = course_service_module.db.query
    course_service_module.db.query = lambda *a, **k: [{"cnt": 0}]
    try:
        d_pending = CourseService.get_course_detail(
            cid, viewer={"user_id": pending_teacher, "role": "teacher"})
        check("待审核教师看不到 join_code", not (d_pending.get("data") or {}).get("join_code"),
              str((d_pending.get("data") or {}).get("join_code")))
        sql_db.upsert_membership(cid, pending_teacher, "teacher", "approved", "invite")
        d_approved = CourseService.get_course_detail(
            cid, viewer={"user_id": pending_teacher, "role": "teacher"})
        check("已通过协作教师可见 join_code",
              (d_approved.get("data") or {}).get("join_code") == "ABC123",
              str((d_approved.get("data") or {}).get("join_code")))
        d_owner = CourseService.get_course_detail(
            cid, viewer={"user_id": teacher_id, "role": "teacher"})
        check("创建者可见 join_code",
              (d_owner.get("data") or {}).get("join_code") == "ABC123")
        d_student = CourseService.get_course_detail(
            cid, viewer={"user_id": student_id, "role": "student"})
        check("学生看不到 join_code",
              not (d_student.get("data") or {}).get("join_code"))
    finally:
        course_service_module.db.query = original_query

    # ---------- 7. embedding model 迁移与读写 ----------
    print("\nStep 7: 向量表 model 列迁移与按模型过滤")
    for table in ("t_kp_embedding", "t_question_embedding"):
        cols = {r["name"] for r in sql_db._query(f"PRAGMA table_info({table})")}
        check(f"{table} 有 model 列", "model" in cols, str(sorted(cols)))
    sql_db.upsert_kp_embedding(cid, 1, "kp1", [0.6, 0.8],
                               model=settings.EMBEDDING_MODEL)
    check("get_embedding_models 返回当前模型",
          sql_db.get_embedding_models(cid, 1) == {settings.EMBEDDING_MODEL},
          str(sql_db.get_embedding_models(cid, 1)))
    check("当前模型能读到向量",
          len(sql_db.get_kp_embedding_blobs(cid, 1,
                                            model=settings.EMBEDDING_MODEL)) == 1)
    check("其他模型读不到当前向量",
          sql_db.get_kp_embedding_blobs(cid, 1, model="other-model") == [])

    # ---------- 8. 上传去重排除失败/丢文件文档 ----------
    print("\nStep 8: 上传复用只接受可用且文件存在的文档")
    cid3 = sql_db.create_course("去重验证课程", teacher_id)
    real_file = os.path.join(_TMP, "dup.txt")
    with open(real_file, "wb") as f:
        f.write(b"abc")
    doc_a = sql_db.create_document(cid3, teacher_id, "dup.txt", "TXT", 3,
                                   "sha_dup", real_file)
    found = DocumentService._find_reusable_duplicate(cid3, "sha_dup")
    check("可用文档可复用", bool(found) and found["doc_id"] == doc_a,
          str(found and found["doc_id"]))
    sql_db.update_document(doc_a, graph_status="FAILED")
    check("FAILED 文档不复用",
          DocumentService._find_reusable_duplicate(cid3, "sha_dup") is None)
    sql_db.create_document(cid3, teacher_id, "dup_missing.txt", "TXT", 3,
                           "sha_dup", os.path.join(_TMP, "not_exists.txt"))
    check("文件缺失的文档不复用",
          DocumentService._find_reusable_duplicate(cid3, "sha_dup") is None)
    doc_c = sql_db.create_document(cid3, teacher_id, "dup_ok.txt", "TXT", 3,
                                   "sha_dup", real_file)
    found_c = DocumentService._find_reusable_duplicate(cid3, "sha_dup")
    check("新的可用文档可复用", bool(found_c) and found_c["doc_id"] == doc_c,
          str(found_c and found_c["doc_id"]))

    # ---------- 9. 删除文档级联清理导入批次 ----------
    print("\nStep 9: 删除文档同步清理题目导入批次")
    cid4 = sql_db.create_course("批次验证课程", teacher_id)
    with sql_db._connect() as conn:
        conn.executemany(
            "INSERT INTO t_question_import_batch (batch_id, course_id, document_id) "
            "VALUES (?, ?, ?)",
            [("b_doc10", cid4, 10), ("b_doc11", cid4, 11)],
        )
        conn.commit()
    removed = sql_db.delete_import_batches_by_document(cid4, 10)
    check("删除目标文档批次 1 条", removed == 1, str(removed))
    left = {r["batch_id"] for r in sql_db._query(
        "SELECT batch_id FROM t_question_import_batch WHERE course_id = ?", (cid4,))}
    check("其他文档批次保留", left == {"b_doc11"}, str(left))

    # ---------- 10. 向量维度不一致不抛异常 ----------
    print("\nStep 10: 向量检索维度不一致返回空而非 500")
    vector_index.invalidate()
    hits = vector_index.kp_search(cid, 1, [0.6, 0.8], top_k=5)
    check("同维检索命中 kp1",
          len(hits) == 1 and hits[0][0] == "kp1" and hits[0][1] > 0.99, str(hits))
    check("查询向量多一维时返回空",
          vector_index.kp_search(cid, 1, [0.6, 0.8, 0.0], top_k=5) == [])
    sql_db.upsert_question_embedding(101, cid, 1, "hash101", [1.0, 0.0],
                                     model=settings.EMBEDDING_MODEL)
    vector_index.invalidate()
    sims = vector_index.question_max_similarity(cid, 1, [[1.0, 0.0]])
    check("题目同维相似度可算", sims.get(101) is not None, str(sims))
    check("题目维度不一致返回空",
          vector_index.question_max_similarity(cid, 1, [[1.0, 0.0, 0.0]]) == {})

    print(f"\n通过 {total - len(failures)}/{total}" if not failures
          else f"\n失败 {len(failures)}/{total}：{failures}")
    return 0 if not failures else 1


def _cleanup(path):
    """Windows 下未显式 close 的 sqlite3 连接会锁住库文件。

    本项目 `with self._connect() as conn` 只提交/回滚、不关闭连接（sqlite3 的
    上下文管理器语义），残留连接可能一直活到进程退出。测试收尾时统一关闭后再删目录。
    """
    for _ in range(20):
        gc.collect()
        for obj in gc.get_objects():
            # 用 type() 而不是 isinstance()：某些库的代理对象会在 __class__ 上
            # 触发延迟导入（openai 的 pandas 代理就会在这里抛 MissingDependencyError）。
            if type(obj) is sqlite3.Connection:
                try:
                    obj.close()
                except Exception:
                    pass
        try:
            shutil.rmtree(path)
            return
        except OSError:
            time.sleep(0.2)


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        _cleanup(_TMP)
