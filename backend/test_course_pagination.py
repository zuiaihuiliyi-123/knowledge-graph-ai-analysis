"""阶段 2 正确性：课程列表分页（total / 边界 / 跨页不重不漏 / 稳定排序）。

课程列表被三个入口复用（我的课程、发现课程、管理员课程），分页算错会直接表现为
「某门课翻不到」或「同一门课出现两次」。本脚本守住：
1. total 与实际条数一致；
2. 逐页拉取恰好覆盖全部课程，不重复不遗漏；
3. 越界页返回空列表且 total 不变；
4. page_size 被夹到 [1, 100]，非法 page 归 1；
5. 同样的查询两次返回顺序一致（分页必须稳定，否则翻页会串行）；
6. 「我的课程」不包含别人的课程。

运行：cd backend && python test_course_pagination.py
（临时库 + AUTO_BACKUP=0，不连 Neo4j / 不调外部 API）
"""
import os
import shutil
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="kg_paging_")
os.environ["SQLITE_DB_PATH"] = os.path.join(_TMP, "app.db")
os.environ["UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["AUTO_BACKUP"] = "0"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient                 # noqa: E402

from app.core.security import hash_password               # noqa: E402
from app.core.sql_database import sql_db                  # noqa: E402
from app.main import app                                  # noqa: E402
from app.services.course_service import CourseService     # noqa: E402

PASSWORD = "pw123456"
failures = []
total_checks = 0


def check(label, ok, detail=""):
    global total_checks
    total_checks += 1
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    client = TestClient(app)
    with client:
        teacher_id = sql_db.create_user("pg_teacher", hash_password(PASSWORD), role="teacher")
        other_id = sql_db.create_user("pg_other", hash_password(PASSWORD), role="teacher")
        mine = [sql_db.create_course(f"分页课程{i:02d}", teacher_id) for i in range(7)]
        foreign = sql_db.create_course("别人的课程", other_id)

        viewer = {"user_id": teacher_id, "username": "pg_teacher", "role": "teacher"}

        print("Step 1: total 与首页条数")
        r = CourseService.list_courses(page=1, page_size=3, teacher_id=teacher_id, viewer=viewer)
        data = r["data"]
        check("查询成功", r.get("ok") is True, str(r)[:120])
        check("total = 7", data["total"] == 7, str(data["total"]))
        check("首页 3 条", len(data["items"]) == 3, str(len(data["items"])))

        print("\nStep 2: 逐页覆盖全部课程（不重不漏）")
        seen = []
        for page in (1, 2, 3):
            rows = CourseService.list_courses(page=page, page_size=3, teacher_id=teacher_id,
                                              viewer=viewer)["data"]["items"]
            seen.extend(c["course_id"] for c in rows)
        check("三页合计 7 条", len(seen) == 7, str(len(seen)))
        check("无重复", len(set(seen)) == len(seen), str(seen))
        check("与创建的课程集合一致", set(seen) == set(mine), f"{sorted(seen)} vs {sorted(mine)}")

        print("\nStep 3: 越界页 / 边界参数")
        beyond = CourseService.list_courses(page=99, page_size=3, teacher_id=teacher_id,
                                            viewer=viewer)["data"]
        check("越界页返回空列表", beyond["items"] == [], str(beyond["items"]))
        check("越界页 total 不变", beyond["total"] == 7, str(beyond["total"]))

        clamped = CourseService.list_courses(page=0, page_size=1000, teacher_id=teacher_id,
                                             viewer=viewer)["data"]
        check("page=0 归一为第 1 页", clamped["page"] == 1, str(clamped["page"]))
        check("page_size 上限夹到 100", clamped["page_size"] == 100, str(clamped["page_size"]))
        check("一次取回全部 7 条", len(clamped["items"]) == 7, str(len(clamped["items"])))

        print("\nStep 4: 分页顺序稳定")
        a = [c["course_id"] for c in CourseService.list_courses(
            page=2, page_size=2, teacher_id=teacher_id, viewer=viewer)["data"]["items"]]
        b = [c["course_id"] for c in CourseService.list_courses(
            page=2, page_size=2, teacher_id=teacher_id, viewer=viewer)["data"]["items"]]
        check("两次相同查询顺序一致", a == b, f"{a} vs {b}")

        print("\nStep 5: 可见范围")
        ids = {c["course_id"] for c in CourseService.list_courses(
            page=1, page_size=100, teacher_id=teacher_id, viewer=viewer)["data"]["items"]}
        check("不包含别人的课程", foreign not in ids, str(sorted(ids)))

    print(f"\n通过 {total_checks - len(failures)}/{total_checks}" if not failures
          else f"\n失败 {len(failures)}/{total_checks}：{failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
