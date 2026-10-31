"""阶段 1C 授权覆盖回归：学生端练习接口的跨课程访问必须被拦住。

背景：`/api/v1/practice/*` 原先只要登录就能传任意 `course_id`——出题/推荐返回题干与选项，
`/submit` 还会下发标准答案，等于把别人课程的题库连同答案整套导出去。
本脚本守住三条：
1. 非成员 → 一律 4003（出题 / 推荐 / 错题本 / 统计 / 收藏 / 提交作答）；
2. 已加入的学生 → 正常可用（不能因为收紧权限把正常流程砍掉）；
3. 课程创建者 → 正常可用。

运行：cd backend && python test_practice_scope.py
（临时库 + 不生成自动备份，不触碰真实 app.db / Neo4j）
"""
import os
import shutil
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="kg_practice_scope_")
os.environ["SQLITE_DB_PATH"] = os.path.join(_TMP, "app.db")
os.environ["UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["AUTO_BACKUP"] = "0"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient              # noqa: E402

from app.core.security import hash_password            # noqa: E402
from app.core.sql_database import sql_db               # noqa: E402
from app.main import app                               # noqa: E402
from app.services.question_service import QuestionService  # noqa: E402

PASSWORD = "pw123456"
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
        teacher_id = sql_db.create_user("ps_teacher", hash_password(PASSWORD), role="teacher")
        student_id = sql_db.create_user("ps_outsider", hash_password(PASSWORD), role="student")
        member_id = sql_db.create_user("ps_member", hash_password(PASSWORD), role="student")

        course_id = sql_db.create_course("练习越权回归课程", teacher_id)
        doc_id = sql_db.create_document(course_id, teacher_id, "ps.txt", "TXT", 10)
        created = QuestionService.create_question(teacher_id, course_id, {
            "document_id": doc_id, "kp_id": None, "q_type": "SINGLE",
            "stem": "练习越权回归：以下哪项正确？",
            "options": [{"key": "A", "text": "选项 A"}, {"key": "B", "text": "选项 B"}],
            "answer": "A", "analysis": "回归用解析", "difficulty": 1,
        })
        question_id = (created.get("data") or {}).get("question_id") if created.get("ok") else None
        check("准备：题目创建成功", bool(question_id), str(created)[:200])

        # 已通过审核的学生成员
        sql_db.upsert_membership(course_id, member_id, role="student",
                                 status="approved", join_source="code")

        def login(username: str) -> str:
            body = client.post("/api/auth/login",
                               json={"username": username, "password": PASSWORD}).json()
            return (body.get("data") or {}).get("access_token") or ""

        def call(method, path, token, **kw):
            return getattr(client, method)(path, headers={"Authorization": f"Bearer {token}"}, **kw)

        outsider = login("ps_outsider")
        member = login("ps_member")
        teacher = login("ps_teacher")
        check("准备：三个身份都能登录", all([outsider, member, teacher]))

        def code_of(resp):
            try:
                return resp.json().get("code")
            except Exception:
                return f"HTTP {resp.status_code}"

        print("\nStep 1: 非成员跨课程访问一律 4003")
        r = call("get", "/api/v1/practice/questions", outsider, params={"course_id": course_id})
        check("出题被拒", code_of(r) == 4003, str(code_of(r)))
        r = call("post", "/api/v1/practice/recommend", outsider, json={"course_id": str(course_id)})
        check("推荐出题被拒", code_of(r) == 4003, str(code_of(r)))
        r = call("get", "/api/v1/practice/wrong-book", outsider, params={"course_id": course_id})
        check("错题本被拒", code_of(r) == 4003, str(code_of(r)))
        r = call("get", "/api/v1/practice/stats", outsider, params={"course_id": course_id})
        check("统计被拒", code_of(r) == 4003, str(code_of(r)))
        r = call("get", "/api/v1/practice/favorites", outsider, params={"course_id": course_id})
        check("收藏列表被拒", code_of(r) == 4003, str(code_of(r)))
        r = call("post", "/api/v1/practice/favorites", outsider,
                 json={"course_id": str(course_id), "question_id": question_id})
        check("收藏题目被拒", code_of(r) == 4003, str(code_of(r)))
        r = call("post", "/api/v1/practice/submit", outsider, json={"question_id": question_id})
        check("提交作答被拒（否则可套出标准答案）", code_of(r) == 4003, str(code_of(r)))

        print("\nStep 2: 课程成员与创建者不受影响")
        r = call("get", "/api/v1/practice/questions", member, params={"course_id": course_id})
        check("已加入学生可出题", code_of(r) == 0, str(code_of(r)))
        r = call("get", "/api/v1/practice/stats", member, params={"course_id": course_id})
        check("已加入学生可看统计", code_of(r) == 0, str(code_of(r)))
        r = call("post", "/api/v1/practice/submit", member, json={"question_id": question_id})
        check("已加入学生可提交作答", code_of(r) == 0, str(code_of(r)))
        r = call("get", "/api/v1/practice/questions", teacher, params={"course_id": course_id})
        check("课程创建者可出题", code_of(r) == 0, str(code_of(r)))

        print("\nStep 3: 不存在的课程仍返回 2001")
        r = call("get", "/api/v1/practice/questions", outsider, params={"course_id": 999999})
        check("课程不存在 → 2001", code_of(r) == 2001, str(code_of(r)))

    print(f"\n通过 {total - len(failures)}/{total}" if not failures
          else f"\n失败 {len(failures)}/{total}：{failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
