"""
测试 Neo4j AuraDB 连接与 Cypher 查询

运行方式（在 backend 目录下执行）：
    python test_neo4j.py
"""
import sys

# Windows 控制台默认 GBK，避免中文/特殊字符输出报错
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.config import settings
from app.core.database import db


def test_connection():
    """测试基本连接与服务器信息"""
    print("=" * 60)
    print("1. 测试连接")
    print(f"   URI : {settings.NEO4J_URI}")
    print(f"   用户: {settings.NEO4J_USER}")
    print(f"   密码: {'*' * len(settings.NEO4J_PASSWORD)}")
    print("=" * 60)

    # 最简单的连通性测试
    result = db.query("RETURN 1 AS ok")
    print(f"   RETURN 1 -> {result[0]['ok']}  （连接成功）")

    # 服务器版本信息
    try:
        info = db.query(
            "CALL dbms.components() YIELD name, versions, edition "
            "RETURN name, versions, edition"
        )
        for row in info:
            print(f"   组件: {row['name']} | 版本: {row['versions']} | 类型: {row['edition']}")
    except Exception as e:
        print(f"   （无法读取组件信息：{e}）")


def test_cypher():
    """测试 Cypher 增删查：创建节点 + 关系 + 查询三元组"""
    print()
    print("=" * 60)
    print("2. 测试 Cypher 查询")
    print("=" * 60)

    # 先清理旧测试数据
    db.query("MATCH (n:TestNode) DETACH DELETE n")

    # 创建节点
    db.query("CREATE (:TestNode {name: '线性表', category: '概念'})")
    db.query("CREATE (:TestNode {name: '链表', category: '概念'})")
    db.query("CREATE (:TestNode {name: '栈', category: '概念'})")

    # 创建关系
    db.query("""
        MATCH (a:TestNode {name: '线性表'}), (b:TestNode {name: '链表'})
        CREATE (a)-[:前置知识]->(b)
    """)
    db.query("""
        MATCH (a:TestNode {name: '链表'}), (b:TestNode {name: '栈'})
        CREATE (a)-[:相关概念]->(b)
    """)

    # 查询所有节点
    nodes = db.query(
        "MATCH (n:TestNode) RETURN n.name AS name, n.category AS category ORDER BY n.name"
    )
    print("\n   [节点]")
    for row in nodes:
        print(f"     - {row['name']}（{row['category']}）")

    # 查询关系（三元组）
    rels = db.query("""
        MATCH (a:TestNode)-[r]->(b:TestNode)
        RETURN a.name AS source, type(r) AS rel, b.name AS target
    """)
    print("\n   [关系三元组] (主体) -[关系]-> (客体)")
    for row in rels:
        print(f"     - ({row['source']}) -[{row['rel']}]-> ({row['target']})")

    # 清理测试数据
    db.query("MATCH (n:TestNode) DETACH DELETE n")
    print("\n   测试数据已清理")


def main():
    if "xxxxxxxx" in settings.NEO4J_URI:
        print("[提示] NEO4J_URI 仍是占位符，请先在 backend/.env 中填入正确的 Neo4j 地址\n")

    try:
        test_connection()
        test_cypher()
        print("\n✓ Neo4j AuraDB 连接与 Cypher 查询测试通过")
    except Exception as e:
        print(f"\n✗ 测试失败：{type(e).__name__}: {e}")
        print("  请检查 .env 里的 NEO4J_URI / NEO4J_PASSWORD 是否正确")
    finally:
        db.close()


if __name__ == "__main__":
    main()
