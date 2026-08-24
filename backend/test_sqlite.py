"""
SQLite 建表与 CRUD 验证脚本（对齐规划文档 4.2 四张表）

运行方式（在 backend 目录下执行）：
    python test_sqlite.py
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.sql_database import sql_db

DB_FILE = sql_db.db_path


def main():
    print("=" * 60)
    print("Step 1: 初始化表结构（幂等）")
    print("=" * 60)
    sql_db.init_tables()
    print(f"  数据库文件: {os.path.abspath(DB_FILE)}")

    # 校验四张表是否存在
    tables = [r["name"] for r in sql_db._query(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 't_%' ORDER BY name"
    )]
    print(f"  已建表: {tables}")

    # 校验索引
    indexes = [r["name"] for r in sql_db._query(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%' ORDER BY name"
    )]
    print(f"  已建索引: {indexes}")

    print("\n" + "=" * 60)
    print("Step 2: 插入链路 user -> course -> document -> learning_record")
    print("=" * 60)
    teacher_id = sql_db.create_user(
        username="teacher_demo", password_hash="<hash>",
        role="teacher", display_name="演示教师",
    )
    print(f"  教师 user_id={teacher_id}")

    course_id = sql_db.create_course(
        course_name="数据结构", teacher_id=teacher_id, course_code="CS101",
        description="数据结构课程示例",
    )
    print(f"  课程 course_id={course_id}")

    doc_id = sql_db.create_document(
        course_id=course_id, uploader_id=teacher_id,
        file_name="数据结构第一章.pdf", file_type="PDF", file_size=2048,
    )
    print(f"  文档 doc_id={doc_id}")

    print("\n" + "=" * 60)
    print("Step 3: 文档状态更新（parse_status + entity_count）")
    print("=" * 60)
    sql_db.update_document(doc_id, parse_status="PARSED", extract_status="COMPLETED",
                           entity_count=9, relation_count=9)
    doc = sql_db.get_document(doc_id)
    print(f"  parse_status={doc['parse_status']}, extract_status={doc['extract_status']}, "
          f"entity_count={doc['entity_count']}, updated_at={doc['updated_at']}")

    print("\n" + "=" * 60)
    print("Step 4: 学习记录 upsert（唯一约束校验）")
    print("=" * 60)
    kp_id = "kp_course_demo_0001"
    sql_db.upsert_learning_record(user_id=teacher_id, course_id=course_id, kp_id=kp_id,
                                  status="LEARNING", mastery_level=60)
    # 第二次写入同一 (user, course, kp)，应更新而非新增
    sql_db.upsert_learning_record(user_id=teacher_id, course_id=course_id, kp_id=kp_id,
                                  status="MASTERED", mastery_level=100)
    records = sql_db.list_records_by_user_course(teacher_id, course_id)
    print(f"  记录数（应=1）: {len(records)}")
    for r in records:
        print(f"    kp_id={r['kp_id']} status={r['status']} mastery={r['mastery_level']}")

    print("\n" + "=" * 60)
    print("Step 5: 查询回读验证")
    print("=" * 60)
    course = sql_db.get_course(course_id)
    print(f"  课程: {course['course_code']} {course['course_name']} (teacher_id={course['teacher_id']})")
    docs = sql_db.list_documents_by_course(course_id)
    print(f"  课程下文档数: {len(docs)}")
    users = sql_db.list_users()
    print(f"  用户数: {len(users)}")

    print()
    if (len(tables) == 4 and len(records) == 1
            and doc["parse_status"] == "PARSED"
            and records[0]["status"] == "MASTERED"):
        print("✓ SQLite 建表 + CRUD + 唯一约束 全部验证通过")
    else:
        print("⚠ 部分校验未通过，请检查输出")


if __name__ == "__main__":
    main()
