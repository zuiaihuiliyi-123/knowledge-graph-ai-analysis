"""
验证路径推荐模块是否正常工作（对齐新图模型 KnowledgePoint + PRECEDES）

运行方式（在 backend 目录下执行）：
    python test_path_recommender.py

说明：本脚本先探测 SQLite 中的课程与 Neo4j 中的实际知识点，
再针对真实数据调用 PathRecommender 的四个方法，验证能出结果。
"""
import sys

# Windows 控制台默认 GBK，避免中文/特殊字符输出报错
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.database import db
from app.core.sql_database import sql_db
from app.services.path_recommender import PathRecommender


def main():
    # 1. SQLite 现有课程
    courses = sql_db.list_courses()
    print("=" * 64)
    print("[1] SQLite 现有课程")
    for c in courses:
        print(f"    course_id={c['course_id']}  {c['course_name']}")
    if not courses:
        print("    （无课程，请先上传文档构建图谱）")
        return

    # 2. Neo4j 知识点分布
    print("=" * 64)
    print("[2] Neo4j 各课程知识点数量")
    try:
        node_stats = db.query(
            "MATCH (n:KnowledgePoint) "
            "RETURN n.course_id AS cid, count(n) AS cnt ORDER BY cnt DESC"
        )
    except Exception as e:
        print(f"    ✗ 连接 Neo4j 失败：{type(e).__name__}: {e}")
        print("    请确认 Neo4j 已启动，且 .env 中 NEO4J_URI/PASSWORD 正确")
        return

    for r in node_stats:
        print(f"    course_id={r['cid']}  知识点数={r['cnt']}")
    if not node_stats:
        print("    （无 KnowledgePoint 节点，请先上传文档构建图谱）")
        return

    # 3. 取节点最多的课程做验证
    cid = node_stats[0]["cid"]
    nodes = db.query(
        "MATCH (n:KnowledgePoint {course_id: $cid}) "
        "RETURN n.name AS name, n.category AS category ORDER BY n.name",
        {"cid": cid},
    )
    names = [r["name"] for r in nodes]
    print("=" * 64)
    print(f"[3] 选定课程 course_id={cid}，共 {len(names)} 个知识点")
    print("    知识点：", ", ".join(names[:30]))

    # 4. 关系类型分布（判断是否有 PRECEDES 前置关系）
    rel_dist = db.query(
        "MATCH (:KnowledgePoint {course_id: $cid})-[r]->(:KnowledgePoint {course_id: $cid}) "
        "RETURN type(r) AS t, count(r) AS cnt ORDER BY cnt DESC",
        {"cid": cid},
    )
    print("=" * 64)
    print("[4] 关系类型分布")
    for r in rel_dist:
        print(f"    {r['t']}: {r['cnt']} 条")
    if not any(r["t"] == "PRECEDES" for r in rel_dist):
        print("    ⚠ 该课程没有任何 PRECEDES（前置知识）关系，")
        print("      路径推荐/前置查询会退化为空或全部视为入门节点（数据问题，非代码问题）")

    # 5. recommend_next：无已掌握 -> 推荐入门节点
    print("=" * 64)
    print("[5] recommend_next(无已掌握) -> 入门节点")
    recs = PathRecommender.recommend_next([], course_id=cid)
    for i, r in enumerate(recs, 1):
        print(f"    {i}. {r['name']}（{r['category']}）: {r['reason']}")

    # 6. recommend_next：有已掌握 -> 推荐后继
    if names:
        learned = names[:2]
        print("=" * 64)
        print(f"[6] recommend_next(已掌握 {learned}) -> 后继")
        recs2 = PathRecommender.recommend_next(learned, course_id=cid)
        for i, r in enumerate(recs2, 1):
            print(f"    {i}. {r['name']}（{r['category']}）priority={r['priority']}: {r['reason']}")
        if not recs2:
            print("    （无满足前置条件的后继节点）")

    # 7. get_prerequisites：某节点前置知识
    if names:
        target = names[-1]
        print("=" * 64)
        print(f"[7] get_prerequisites({target})")
        prereqs = PathRecommender.get_prerequisites(target, course_id=cid)
        for p in prereqs:
            print(f"    - {p['name']}（{p['category']}）depth={p['depth']}")
        if not prereqs:
            print("    （该节点无前置知识）")

    # 8. get_learning_path：到目标节点的学习路径
    if names:
        target = names[-1]
        print("=" * 64)
        print(f"[8] get_learning_path(到 {target})")
        paths = PathRecommender.get_learning_path(target, course_id=cid)
        for i, steps in enumerate(paths, 1):
            chain = " -> ".join(s["name"] for s in steps)
            print(f"    路径{i}（{len(steps)} 步）: {chain}")
        if not paths:
            print("    （无可达路径，目标可能无前置链或已是根节点）")

    print("=" * 64)
    print("验证结束")
    db.close()


if __name__ == "__main__":
    main()
