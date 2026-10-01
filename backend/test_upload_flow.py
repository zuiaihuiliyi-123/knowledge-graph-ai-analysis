"""
端到端上传流程验证：真实 PDF -> 建课程(整数ID) -> 建文档 -> 解析 -> 抽取 -> 入图
并校验 SQLite / Neo4j 两端 course_id 统一为整数

⚠️ 本脚本已过时，**当前跑不起来，请勿当作回归测试执行** ⚠️
    它写于「上传会自动建课」的年代，调用的是 `process_upload(course_name=...)`；
    而现在的契约是 `process_upload(content, filename, course_id, teacher_id)`
    （Phase 5 起必须指定已存在课程，禁止自动建课）。
    重写它需要一并补齐「先建课程 → 再上传」的前置步骤。

⚠️ 历史事故记录（2026-10-01，请勿重蹈）⚠️
    本文件原先在 Step 1 执行 `db.query("MATCH (n) DETACH DELETE n")` ——
    一句**不分课程、不分文档**的全库清空。由于它在 `process_upload` 报错**之前**执行，
    跑一次这个早已失效的脚本，就会把本机 Neo4j 里**全部**知识点节点清空
    （实测 626 个节点归零），而脚本自己只会抛出一个 TypeError，
    看起来像是"这个测试本来就跑不通"。
    现已改为只清理本脚本自建的那个测试课程；即便如此，
    在跑任何 `test_*.py` 之前也请先确认它不会影响真实数据。

运行方式（在 backend 目录下执行）：
    python test_upload_flow.py
"""
import asyncio
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.database import db
from app.core.sql_database import sql_db
from app.services.document_service import DocumentService
from app.services.kg_manager import KnowledgeGraphManager

PDF_PATH = os.path.join("data", "sample_docs", "数据结构", "数据结构第一章.pdf")
COURSE_NAME = "数据结构（整数ID验证）"

# 本脚本专属的测试课程号，与真实数据不相干。
# 原先这里是一句 `MATCH (n) DETACH DELETE n`（全库清空），在任何断言之前执行，
# 导致「跑一个本就跑不通的过时脚本」会顺手清空整台机器的知识图谱。
TEST_COURSE_ID = 900101


async def main():
    print("=" * 60)
    print("Step 1: 初始化 SQLite + 清理**本测试课程**的图谱")
    print("=" * 60)
    sql_db.init_tables()
    # 只清本脚本自建的测试课程：**绝不**再写成不带 course_id 的全库删除
    db.query("MATCH (n:KnowledgePoint {course_id: $cid}) DETACH DELETE n",
             {"cid": TEST_COURSE_ID})

    print("\n" + "=" * 60)
    print("Step 2: 读取真实 PDF 并走完整上传流程")
    print("=" * 60)
    if not os.path.exists(PDF_PATH):
        print(f"  [错误] 样例 PDF 不存在: {PDF_PATH}")
        return
    with open(PDF_PATH, "rb") as f:
        content = f.read()
    print(f"  PDF 大小: {len(content)} 字节")

    result = await DocumentService.process_upload(
        content=content, filename="数据结构第一章.pdf", course_name=COURSE_NAME,
    )
    if not result["ok"]:
        print(f"  [失败] code={result['code']} {result['message']}")
        return
    data = result["data"]
    print(f"  返回: {data}")

    course_id = data["course_id"]
    doc_id = data["document_id"]
    assert isinstance(course_id, int), "course_id 必须是整数"
    assert isinstance(doc_id, int), "doc_id 必须是整数"

    print("\n" + "=" * 60)
    print("Step 3: SQLite 侧校验（用户 -> 课程 -> 文档 链路）")
    print("=" * 60)
    course = sql_db.get_course(course_id)
    doc = sql_db.get_document(doc_id)
    teacher = sql_db.get_user_by_id(course["teacher_id"])
    print(f"  课程: course_id={course['course_id']} name={course['course_name']} "
          f"teacher={teacher['username']}({course['teacher_id']})")
    print(f"  文档: doc_id={doc['doc_id']} course_id={doc['course_id']} "
          f"file={doc['file_name']} parse={doc['parse_status']} "
          f"extract={doc['extract_status']} 实体={doc['entity_count']} 关系={doc['relation_count']}")
    print(f"  文件路径: {doc['file_path']}")

    print("\n" + "=" * 60)
    print("Step 4: Neo4j 侧校验（course_id 应为整数）")
    print("=" * 60)
    graph = KnowledgeGraphManager.get_graph_v1(course_id, doc_id)
    node_cnt = len(graph["nodes"])
    edge_cnt = len(graph["edges"])
    print(f"  节点数: {node_cnt} / 关系数: {edge_cnt}")
    sample = db.query(
        "MATCH (n:KnowledgePoint) RETURN n.course_id AS cid, n.kp_id AS kp, n.name AS name LIMIT 3",
    )
    for r in sample:
        print(f"    course_id={r['cid']!r}({type(r['cid']).__name__}) "
              f"kp_id={r['kp']} name={r['name']}")
    rel_sample = db.query(
        "MATCH (:KnowledgePoint)-[r]->(:KnowledgePoint) RETURN r.course_id AS cid, type(r) AS t, r.relation_type AS label LIMIT 3",
    )
    for r in rel_sample:
        print(f"    关系 course_id={r['cid']!r} type={r['t']} label={r['label']}")

    print()
    # 校验结论
    cids = [r["cid"] for r in sample]
    ok = (
        isinstance(course_id, int)
        and doc["course_id"] == course_id
        and node_cnt > 0
        and all(isinstance(c, int) for c in cids)
    )
    if ok:
        print("✓ 端到端通过：course_id 在 SQLite / Neo4j 统一为整数，全链路跑通")
    else:
        print("⚠ 校验未通过，请检查上方输出")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        db.close()
