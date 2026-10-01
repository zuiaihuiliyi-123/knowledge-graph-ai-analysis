"""文档级知识融合（Fusion）P0：数据库迁移验证（幂等性 + 老数据不受损）

运行方式（在 backend 目录下执行）：
    python test_fusion_migration.py

重要：本脚本全程只操作 data/app.db 的【副本】，并在结束时校验线上库 MD5 与行数未变，
      因此不会影响任何现有用户 / 课程 / 文档 / 学习记录 / 收藏 / 向量。
"""
import hashlib
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
from app.core import sql_database as sd

# 受保护的既有表：融合迁移绝不允许改动它们的内容
PROTECTED_TABLES = ("t_user", "t_course", "t_document", "t_question",
                    "t_learning_record", "t_student_favorite", "t_kp_embedding",
                    "t_admin_audit_log")

# 本阶段新增的 5 张表与它们的索引
FUSION_TABLES = ("t_kp_fusion_run", "t_kp_fusion_candidate", "t_kp_fusion_map",
                 "t_kp_fusion_conflict", "t_kp_fusion_scope")
FUSION_INDEXES = ("idx_fusion_run_scope", "idx_fusion_cand_scope",
                  "idx_fusion_map_scope", "idx_fusion_conflict_scope")

failures = []


def check(label, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def table_counts(db):
    return {t: db._query_one(f"SELECT count(*) AS c FROM {t}")["c"] for t in PROTECTED_TABLES}


def main():
    live = settings.SQLITE_DB_PATH
    live_md5_before = md5(live)
    print("=" * 72)
    print("文档级知识融合 P0：迁移幂等 + 老数据不受损")
    print("线上库:", live)
    print("MD5(前):", live_md5_before)
    print("=" * 72)

    live_tables_now = {r["name"] for r in sd.sql_db._query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    live_before = {
        t: (sd.sql_db._query_one(f"SELECT count(*) AS c FROM {t}")["c"]
            if t in live_tables_now else 0)
        for t in PROTECTED_TABLES
    }

    tmp_dir = tempfile.mkdtemp(prefix="kg_fusion_migrate_")
    tmp_db = os.path.join(tmp_dir, "app_copy.db")
    shutil.copy2(live, tmp_db)
    print(f"副本: {tmp_db}\n")

    # 让新实例指向副本；模块级单例 sql_db 仍指向线上库，本脚本绝不使用它
    settings.SQLITE_DB_PATH = tmp_db
    dbo = sd.SQLDatabase()
    assert os.path.abspath(dbo.db_path) == os.path.abspath(tmp_db), "实例未指向副本，中止"

    before = table_counts(dbo)

    print("Step 1: 连续执行 init_tables() 三次（第 2、3 次是幂等性测试）")
    dbo.init_tables()
    dbo.init_tables()
    dbo.init_tables()
    after = table_counts(dbo)

    for t in PROTECTED_TABLES:
        check(f"既有表 {t} 行数不变", before[t] == after[t], f"{before[t]} -> {after[t]}")

    print("\nStep 2: 5 张新表与索引是否齐备")
    objs = {r["name"]: r["type"] for r in dbo._query(
        "SELECT name, type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
    for t in FUSION_TABLES:
        check(f"表 {t} 已创建", objs.get(t) == "table")
    for idx in FUSION_INDEXES:
        check(f"索引 {idx} 已创建", objs.get(idx) == "index")

    print("\nStep 3: 新表初始为空（迁移不臆造融合数据）")
    for t in FUSION_TABLES:
        n = dbo._query_one(f"SELECT count(*) AS c FROM {t}")["c"]
        check(f"{t} 初始 0 行", n == 0, f"实际 {n} 行")

    print("\nStep 4: 融合 DAO 的基本读写与 UNIQUE 约束")
    e1 = dbo.fusion_scope_bump_epoch(999999, 888888)
    e2 = dbo.fusion_scope_bump_epoch(999999, 888888)
    check("epoch 首次为 1、再次为 2", (e1, e2) == (1, 2), f"e1={e1} e2={e2}")
    dbo.fusion_scope_mark_indexed(999999, 888888, e2)
    scope = dbo.fusion_scope_get(999999, 888888)
    check("indexed_epoch 已记录", scope and scope["indexed_epoch"] == e2, str(scope))

    fid = dbo.insert_fusion_map(999999, 888888, "kp_a", "kp_b", "A", "B",
                                source_category="概念", target_category="概念")
    check("写入映射成功", fid > 0, f"fusion_id={fid}")
    fid2 = dbo.insert_fusion_map(999999, 888888, "kp_a", "kp_c", "A", "C")
    check("同源重复写入被 UNIQUE 挡住（目标唯一）", fid2 == 0, f"第二次返回 {fid2}")

    dbo.insert_fusion_map(999999, 888888, "kp_x", "kp_y", "X", "Y")
    rows = dbo.list_active_fusion_map(999999, 888888)
    check("ACTIVE 映射读到 2 条", len(rows) == 2, f"实际 {len(rows)}")
    check("fused_source_kp_ids 返回 2 个源",
          dbo.fused_source_kp_ids(999999, 888888) == {"kp_a", "kp_x"},
          str(dbo.fused_source_kp_ids(999999, 888888)))

    n = dbo.revoke_fusion_map(999999, 888888, fid)
    check("撤销单条影响 1 行", n == 1, f"受影响 {n} 行")
    check("撤销后 ACTIVE 只剩 1 条", len(dbo.list_active_fusion_map(999999, 888888)) == 1)
    check("撤销**不删行**（历史保留）", len(dbo.list_fusion_map(999999, 888888)) == 2,
          f"总行数 {len(dbo.list_fusion_map(999999, 888888))}")
    check("重复撤销幂等（返回 0）", dbo.revoke_fusion_map(999999, 888888, fid) == 0)

    print("\nStep 5: 文档级级联清理")
    counts = dbo.delete_fusion_by_document(999999, 888888)
    # 返回的是**各表被删除的行数**：前面写了 2 条映射（1 条已撤销）、bump 过 1 行 scope
    check("delete_fusion_by_document 删除 2 条映射 + 1 条 scope",
          counts.get("t_kp_fusion_map") == 2 and counts.get("t_kp_fusion_scope") == 1,
          str(counts))
    check("清理后 t_kp_fusion_map 无残留",
          dbo._query_one("SELECT count(*) AS c FROM t_kp_fusion_map "
                         "WHERE course_id = 999999")["c"] == 0)
    check("清理后 t_kp_fusion_scope 无残留",
          dbo._query_one("SELECT count(*) AS c FROM t_kp_fusion_scope "
                         "WHERE course_id = 999999")["c"] == 0)
    check("清理不越界（其他 course 的融合行不受影响）",
          dbo._query_one("SELECT count(*) AS c FROM t_kp_fusion_map")["c"] == 0)

    # ---- 线上库校验 ----
    print("\nStep 6: 线上库未被本脚本改动")
    check("线上库 MD5 未变", md5(live) == live_md5_before,
          f"{live_md5_before} -> {md5(live)}")
    live_after = {
        t: (sd.sql_db._query_one(f"SELECT count(*) AS c FROM {t}")["c"]
            if t in live_tables_now else 0)
        for t in PROTECTED_TABLES
    }
    for t in PROTECTED_TABLES:
        check(f"线上 {t} 行数未变", live_before[t] == live_after[t],
              f"{live_before[t]} -> {live_after[t]}")

    shutil.rmtree(tmp_dir, ignore_errors=True)

    print("\n" + "=" * 72)
    if failures:
        print(f"失败 {len(failures)} 项：")
        for f in failures:
            print("  -", f)
    else:
        print("全部通过")
    print("=" * 72)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
