"""文档级知识融合（Fusion）P2：管理员接口的权限与归属校验

运行方式（在 backend 目录下执行）：
    python test_fusion_api_guard.py

两条互补的验证：

  1. **静态内省**（不需要起服务）：遍历 `admin_fusion.router.routes`，断言**每一个**
     路由的 dependencies 里都挂着 `require_admin`。这比逐条点接口更可靠 ——
     新增端点时漏挂权限，会在这里立刻失败，而不是等到被人越权访问才发现。
  2. **运行时归属校验**（副本库 + 合成图）：跨课程 / 跨文档 / 状态不允许的请求
     一律被拒，且拒绝发生在写任何数据之前。
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.api import admin_fusion
from app.core.dependencies import require_admin
from app.core.sql_database import sql_db
from app.services.fusion import fusion_pipeline as FP
from fusion_testkit import Harness, make_edge, make_node

failures = []


def check(label, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def main():
    print("=" * 72)
    print("文档级知识融合 P2：管理员接口权限与归属校验")
    print("=" * 72)

    # ---------- 1. 静态内省：每个路由都必须挂 require_admin ----------
    print("\nStep 1: 逐个路由检查是否显式挂载 require_admin")
    routes = [r for r in admin_fusion.router.routes if hasattr(r, "dependant")]
    check("路由数量符合预期（>= 12）", len(routes) >= 12, f"{len(routes)} 条")

    unguarded = []
    for r in routes:
        deps = [d.call for d in r.dependant.dependencies]
        # 依赖可能被包一层（例如 Depends(require_admin) 直接挂载）
        flat = []
        for d in deps:
            flat.append(d)
            flat.extend(getattr(d, "dependencies", []) and
                        [x.call for x in d.dependencies] or [])
        if require_admin not in flat:
            unguarded.append(f"{sorted(r.methods)} {r.path}")

    check("所有路由都挂了 require_admin（漏挂即失败）", not unguarded, str(unguarded))
    print("      已覆盖路由：")
    for r in routes:
        print(f"        {','.join(sorted(r.methods)):6s} {r.path}")

    # ---------- 2. 前缀与命名规范 ----------
    print("\nStep 2: 路由前缀与管理员端规范一致")
    check("前缀为 /api/v1/admin/fusion",
          admin_fusion.router.prefix == "/api/v1/admin/fusion",
          admin_fusion.router.prefix)
    check("所有路径都在管理员前缀下",
          all(r.path.startswith("/api/v1/admin/fusion") for r in routes))

    # ---------- 3. 运行时归属校验 ----------
    print("\nStep 3: 运行时归属校验（副本库 + 合成图）")
    with Harness() as h:
        cid, did = h.course_id, h.document_id
        h.set_graph(nodes=[make_node("kp_a", "BFS"), make_node("kp_b", "广度优先搜索")],
                    edges=[make_edge("e1", "kp_a", "kp_b")])

        # 正常作用域可用
        r = FP.apply(cid, did, dry_run=True)
        check("本文档作用域可用", r["ok"], str(r.get("message")))

        # 用别人的 course_id 访问同一 document
        r = FP.apply(cid + 1, did, dry_run=True)
        check("换个 course_id 访问同一文档 → 拒绝（4003）",
              not r["ok"] and r["code"] == 4003, str(r.get("message")))

        # 不存在的文档
        r = FP.apply(cid, did + 777, dry_run=True)
        check("不存在的文档 → 拒绝（2001）", not r["ok"] and r["code"] == 2001,
              str(r.get("message")))

        # 候选归属：造一条属于别的 course 的候选，用本作用域去操作它
        other_course = cid + 5
        sql_db._execute(
            "INSERT INTO t_course (course_id, course_code, course_name, teacher_id, status, "
            "join_code, join_mode) VALUES (?, 'FUSION_TEST2', '另一个课程', "
            "(SELECT user_id FROM t_user ORDER BY user_id LIMIT 1), 1, 'FUSIONCODE2', 'approval')",
            (other_course,))
        sql_db.upsert_fusion_candidates(other_course, did, [{
            "source_kp_id": "kp_a", "target_kp_id": "kp_b", "source_name": "A",
            "target_name": "B", "source_category": "概念", "target_category": "概念",
            "score": 0.99, "decision": "SAME", "decision_source": "RULE"}])
        _, other_cands = sql_db.list_fusion_candidates(other_course, did)
        foreign_id = other_cands[0]["candidate_id"]

        r = FP.apply(cid, did, candidate_ids=[foreign_id], dry_run=False)
        check("用本作用域去应用别的课程下的候选 → 拒绝（4003）",
              not r["ok"] and r["code"] == 4003, str(r.get("message")))
        check("被拒绝后没有写入任何映射",
              sql_db.count_fusion_map(cid, did) == 0 and
              sql_db.count_fusion_map(other_course, did) == 0)

        # 撤销：用本作用域去撤销别的课程的融合记录
        sql_db.insert_fusion_map(other_course, did, "kp_a", "kp_b", "A", "B")
        foreign_map = sql_db.list_fusion_map(other_course, did)[0]["fusion_id"]
        r = FP.revoke(cid, did, foreign_map)
        check("用本作用域去撤销别的课程的融合记录 → 拒绝（2001）",
              not r["ok"] and r["code"] == 2001, str(r.get("message")))
        check("别的课程的融合记录仍是 ACTIVE",
              sql_db.count_fusion_map(other_course, did, status="ACTIVE") == 1)

        # 批次归属
        r = FP.undo_run(cid, did, "不存在的run")
        check("不存在的批次 → 拒绝（2001）", not r["ok"] and r["code"] == 2001,
              str(r.get("message")))

        sql_db.delete_fusion_by_document(other_course, did)
        sql_db._execute("DELETE FROM t_course WHERE course_id = ?", (other_course,))

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
