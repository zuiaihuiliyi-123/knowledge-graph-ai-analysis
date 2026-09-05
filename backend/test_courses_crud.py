"""
课程管理 CRUD 验证（对齐规划文档 6.6.6~6.6.10）
运行方式：python test_courses_crud.py
"""
import os
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.database import db
from app.core.sql_database import sql_db
from app.services.course_service import CourseService


def main():
    print("=" * 60)
    print("Step 1: 初始化（清空业务数据，保留默认教师）")
    print("=" * 60)
    sql_db.init_tables()
    db.query("MATCH (n) DETACH DELETE n")
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
    try:
        main()
    finally:
        db.close()
