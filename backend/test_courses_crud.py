"""
课程管理 CRUD 验证（对齐规划文档 6.6.6~6.6.10）
运行方式：python test_courses_crud.py

⚠️ 历史事故记录（2026-10-01，已修复，请勿重蹈）⚠️
    本脚本原先在 Step 1 同时做两件**破坏性**的事，且都发生在任何断言之前、不可回退：
      1. `db.query("MATCH (n) DETACH DELETE n")` —— Neo4j **全库**清空；
      2. 对**线上** `app.db` 直接 `DELETE FROM t_learning_record / t_document / t_course`。
    也就是说，照惯例跑一遍 `python test_*.py` 就会清掉本机全部课程、文档与知识图谱。
    现在：SQLite 的清理改在 app.db 的**临时副本**上执行；Neo4j 那道全局删除已移除
    （课程 CRUD 断言用的是新建课程自己的统计，本来就不需要清空图库）。
"""
import os
import shutil
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.config import settings
from app.core.database import db
from app.core.sql_database import sql_db
from app.services.course_service import CourseService


def main():
    print("=" * 60)
    print("Step 1: 初始化（在 app.db 的**副本**上清空业务数据，保留默认教师）")
    print("=" * 60)
    # 全程只动副本：本脚本会 DELETE 课程 / 文档 / 学习记录，指向线上库即是灾难
    tmp_dir = tempfile.mkdtemp(prefix="kg_courses_crud_")
    live_db = settings.SQLITE_DB_PATH
    tmp_db = os.path.join(tmp_dir, "app_copy.db")
    shutil.copy2(live_db, tmp_db)
    sql_db.db_path = tmp_db
    print(f"  副本: {tmp_db}")

    sql_db.init_tables()
    # 注意：**不再**清空 Neo4j。课程 CRUD 只关心新建课程自己的节点统计，
    # 清空整库对断言没有帮助，却会让别人的知识图谱消失。
    with sql_db._connect() as conn:
        for t in ("t_learning_record", "t_document", "t_course"):
            conn.execute(f"DELETE FROM {t}")
        conn.commit()

    print("\n" + "=" * 60)
    print("Step 2: 创建课程")
    print("=" * 60)
    r = CourseService.create_course("数据结构", course_code="CS101", description="数据结构课程")
    print(f"  创建1: code={r['code']} {r['data']}")
    aid = r["data"]["course_id"]
    assert isinstance(aid, int), "course_id 必须是整数"

    r2 = CourseService.create_course("数据结构")
    print(f"  重名创建: code={r2['code']} message={r2['message']}")

    rb = CourseService.create_course("操作系统", course_code="OS101")
    bid = rb["data"]["course_id"]
    print(f"  创建2: code={rb['code']} course_id={bid}")

    print("\n" + "=" * 60)
    print("Step 3: 课程列表（分页 / 关键词）")
    print("=" * 60)
    d = CourseService.list_courses(page=1, page_size=10)["data"]
    print(f"  全量: total={d['total']} names={[i['course_name'] for i in d['items']]}")
    d = CourseService.list_courses(keyword="数据")["data"]
    print(f"  keyword='数据': total={d['total']} -> {[i['course_name'] for i in d['items']]}")

    print("\n" + "=" * 60)
    print("Step 4: 课程详情（含文档/节点/关系统计）")
    print("=" * 60)
    # 手动造 1 条文档记录 + 2 节点 1 关系，验证统计
    teacher = sql_db.ensure_default_teacher()
    doc_id = sql_db.create_document(aid, teacher, "第一章.pdf", "PDF", 100)
    tmp = os.path.join(tempfile.gettempdir(), f"del_test_{doc_id}.pdf")
    with open(tmp, "w") as f:
        f.write("x")
    sql_db.update_document(doc_id, file_path=tmp)
    db.create_knowledge_node(aid, doc_id, "线性表", "概念", "测试节点")
    db.create_knowledge_node(aid, doc_id, "顺序表", "概念", "测试节点")
    db.create_relationship(aid, doc_id, "线性表", "顺序表", "CONTAINS")

    d = CourseService.get_course_detail(aid)["data"]
    print(f"  name={d['course_name']} teacher={d['teacher_name']} "
          f"doc={d['document_count']} node={d['node_count']} edge={d['edge_count']}")

    print("\n" + "=" * 60)
    print("Step 5: 更新课程")
    print("=" * 60)
    r = CourseService.update_course(aid, course_name="数据结构（2026版）")
    print(f"  改名: code={r['code']} -> {r['data']['course']['course_name']}")
    r = CourseService.update_course(aid, course_name="操作系统")
    print(f"  改成已存在名: code={r['code']} message={r['message']}")

    print("\n" + "=" * 60)
    print("Step 6: 删除课程（需 confirm）")
    print("=" * 60)
    r = CourseService.delete_course(aid, confirm=False)
    print(f"  confirm=false: code={r['code']} message={r['message']}")
    r = CourseService.delete_course(aid, confirm=True)
    print(f"  confirm=true: {r['data']}")
    print(f"  本地文件已删除: {not os.path.exists(tmp)}")
    r = CourseService.get_course_detail(aid)
    print(f"  再查详情: code={r['code']} message={r['message']}")

    CourseService.delete_course(bid, confirm=True)

    print()
    ok = (
        r2["code"] == 2003
        and d["document_count"] == 1
        and d["node_count"] == 2
        and d["edge_count"] == 1
        and not os.path.exists(tmp)
        and r["code"] == 2001
    )
    if ok:
        print("✓ 课程 CRUD 全链路验证通过（创建/重名/列表/详情统计/更新/删除+confirm）")
    else:
        print("⚠ 部分校验未通过，请检查输出")


if __name__ == "__main__":
    _live = settings.SQLITE_DB_PATH
    try:
        main()
    finally:
        # 恢复单例指向线上库并清掉副本目录：脚本异常退出时也不能把单例留在副本上，
        # 否则同进程内后续代码会继续写测试副本（更糟：以为写的是线上库）
        sql_db.db_path = _live
        db.close()
