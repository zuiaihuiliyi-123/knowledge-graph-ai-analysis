"""
测试 LLM 知识提取：使用 DeepSeek 从测试文本中抽取 JSON 三元组

运行方式（在 backend 目录下执行）：
    python test_extraction.py
"""
import asyncio
import json
import sys

# Windows 控制台默认 GBK，避免中文/特殊字符输出报错
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.services.knowledge_extractor import KnowledgeExtractor

# 测试文本（示例：数据结构课程片段）
TEST_TEXT = """
线性表是数据结构中最基本、最常见的一种结构，它由 n 个数据元素组成的有限序列。
线性表有两种存储结构：顺序存储结构（顺序表）和链式存储结构（链表）。
顺序表用一组地址连续的存储单元依次存储数据元素，支持随机访问。
链表通过指针将各个结点链接起来，插入和删除操作不需要移动元素。
栈是一种操作受限的线性表，只能在一端（栈顶）进行插入和删除，遵循后进先出（LIFO）原则。
队列也是一种操作受限的线性表，只允许在一端插入、另一端删除，遵循先进先出（FIFO）原则。
"""


def print_result(result: dict) -> None:
    """美化打印提取结果"""
    if result.get("error"):
        print(f"[错误] {result['error']}")
        return

    entities = result.get("entities", [])
    relations = result.get("relations", [])

    print("=" * 60)
    print(f"提取到 {len(entities)} 个实体、{len(relations)} 条关系")
    print("=" * 60)

    print("\n[实体]")
    for e in entities:
        print(f"  - {e.get('name')}（{e.get('category')}）：{e.get('description')}")

    print("\n[关系三元组] (主体) -[关系]-> (客体)")
    for r in relations:
        print(f"  - ({r.get('source')}) -[{r.get('type')}]-> ({r.get('target')})")

    print("\n[完整 JSON]")
    print(json.dumps(result, ensure_ascii=False, indent=2))


async def main():
    extractor = KnowledgeExtractor()
    result = await extractor.extract(TEST_TEXT)
    print_result(result)


if __name__ == "__main__":
    asyncio.run(main())
