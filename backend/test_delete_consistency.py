"""阶段 1 一致性（1-5）：两个「取消」端点的参数校验/幂等口径必须与对应「设置」端点一致。

背景：`POST /learning/mark` 与 `POST /favorites` 都会校验 `kp_id` 非空，
但对应的两个 DELETE 把空串一路带进 SQL（`WHERE kp_id = ''`）：删 0 行、返回 success，
调用方（前端「取消掌握/取消收藏」按钮）完全看不出什么都没发生。

本脚本守住：
1. 空白 kp_id → 4001（两个端点都一样），不再「假成功」；
2. 合法 kp_id 但记录不存在 → 仍然 success + `deleted=0`（幂等，重复点击不弹错）；
3. 记录存在 → `deleted=1` 且确实被删；
4. 响应里回显 course_id/document_id/kp_id（把空串 strip 后的规范值），便于前端排查；
5. 两个 DELETE 都不校验课程成员身份（退出课程后仍能清理自己的数据）。

运行：cd backend && python test_delete_consistency.py
（临时库；DELETE 不碰 Neo4j，因此无需图库）
"""
import asyncio
import os
import shutil
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="kg_delcons_")
os.environ["SQLITE_DB_PATH"] = os.path.join(_TMP, "app.db")
os.environ["AUTO_BACKUP"] = "0"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

from app.api.favorites import remove_favorite                 # noqa: E402
from app.api.learning import unmark_knowledge                 # noqa: E402
from app.core.security import hash_password                   # noqa: E402
from app.core.sql_database import sql_db                      # noqa: E402

failures = []
total = 0


def check(label, ok, detail=""):
    global total
    total += 1
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    sql_db.init_tables()
    uid = sql_db.create_user("dc_student", hash_password("pw123456"), role="student")
    teacher = sql_db.create_user("dc_teacher", hash_password("pw123456"), role="teacher")
    course = sql_db.create_course("一致性课程", teacher)
    doc = sql_db.create_document(course, teacher, "一致性.txt", "TXT", 10)
    user = {"user_id": uid, "role": "student"}

    print("Step 1: 空白 kp_id 一律 4001（不是假成功）")
    for label, value in [("空串", ""), ("纯空白", "   "), ("None", None)]:
        res = asyncio.run(unmark_knowledge(course, doc, value, user))
        check(f"learning/mark {label} → 4001", res.get("code") == 4001, str(res))
        res2 = asyncio.run(remove_favorite(value, course, doc, user))
        check(f"favorites {label} → 4001", res2.get("code") == 4001, str(res2))

    print("\nStep 2: 非法 course_id / document_id 仍是 4001")
    res = asyncio.run(unmark_knowledge("abc", doc, "kp1", user))
    check("learning/mark 非数字 course_id → 4001", res.get("code") == 4001, str(res))
    res = asyncio.run(remove_favorite("kp1", "abc", doc, user))
    check("favorites 非数字 course_id → 4001", res.get("code") == 4001, str(res))
    res = asyncio.run(remove_favorite("kp1", course, "abc", user))
    check("favorites 非数字 document_id → 4001", res.get("code") == 4001, str(res))

    print("\nStep 3: 记录不存在 → 幂等成功 + deleted=0")
    res = asyncio.run(unmark_knowledge(course, doc, "kp_absent", user))
    check("learning/mark code=0", res.get("code") == 0, str(res))
    check("learning/mark deleted=0", res["data"]["deleted"] == 0, str(res.get("data")))
    res = asyncio.run(remove_favorite("kp_absent", course, doc, user))
    check("favorites code=0", res.get("code") == 0, str(res))
    check("favorites deleted=0", res["data"]["deleted"] == 0, str(res.get("data")))

    print("\nStep 4: 记录存在 → deleted=1 且真的删掉")
    sql_db.upsert_learning_record(user_id=uid, course_id=course, document_id=doc,
                                  kp_id="kp_real", status="MASTERED", mastery_level=100,
                                  source="MANUAL", last_learned_at="2026-11-01 10:00:00")
    sql_db.add_favorite(uid, course, doc, "kp_real")
    res = asyncio.run(unmark_knowledge(course, doc, "  kp_real  ", user))
    check("learning/mark deleted=1", res["data"]["deleted"] == 1, str(res.get("data")))
    check("回显 strip 后的 kp_id", res["data"]["kp_id"] == "kp_real", str(res.get("data")))
    left = sql_db.list_records_by_user_course(uid, course, doc)
    check("学习记录确实已删", not [r for r in left if r["kp_id"] == "kp_real"],
          str([r["kp_id"] for r in left]))

    res = asyncio.run(remove_favorite("kp_real", course, doc, user))
    check("favorites deleted=1", res["data"]["deleted"] == 1, str(res.get("data")))
    favs = sql_db.list_favorites_by_user_course(uid, course, doc)
    check("收藏确实已删", not [f for f in favs if f["kp_id"] == "kp_real"],
          str([f["kp_id"] for f in favs]))

    print("\nStep 5: 不校验成员身份（退出课程后仍能清理）")
    outsider = {"user_id": uid, "role": "student"}   # 本就没加过课程成员
    sql_db.add_favorite(uid, course, doc, "kp_outsider")
    res = asyncio.run(remove_favorite("kp_outsider", course, doc, outsider))
    check("非成员也能删除自己的收藏", res.get("code") == 0 and res["data"]["deleted"] == 1,
          str(res)[:120])

    print(f"\n通过 {total - len(failures)}/{total}" if not failures
          else f"\n失败 {len(failures)}/{total}：{failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
