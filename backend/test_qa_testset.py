"""
20 题智能问答测试集：基于 course 5（第6章 面向对象程序设计）真实知识图谱

对每道题：
  1. search_related_knowledge(question, course_id=5, top_k=3) —— 记录向量检索 top3 上下文
  2. await qa_service.ask(question, course_id=5) —— 记录 RAG 生成回答

运行方式（backend 目录下）：python test_qa_testset.py
输出：控制台进度 + test_qa_testset.json（完整结果）
"""
import asyncio
import json
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.services.qa_service import QAService

COURSE_ID = "5"

# 20 道测试题：覆盖概念定义 / 方法用法 / 对比辨析 / 关系前置 / 特殊方法与运算符 / 边界诚实性
# expected：该题应命中的核心知识点（用于判定检索与回答是否切题）
QUESTIONS = [
    {"q": "什么是封装？", "expected": ["封装"], "type": "概念定义"},
    {"q": "什么是继承？", "expected": ["继承"], "type": "概念定义"},
    {"q": "什么是多态？", "expected": ["多态"], "type": "概念定义"},
    {"q": "什么是运算符重载？", "expected": ["运算符重载", "特殊方法"], "type": "概念定义"},
    {"q": "什么是抽象类？它和抽象方法是什么关系？", "expected": ["抽象类", "抽象方法"], "type": "概念定义"},

    {"q": "super() 函数有什么作用？", "expected": ["super()"], "type": "方法用法"},
    {"q": "property 装饰器有什么作用？", "expected": ["property", "只读属性"], "type": "方法用法"},
    {"q": "isinstance() 函数的作用是什么？", "expected": ["isinstance()"], "type": "方法用法"},
    {"q": "静态方法和类方法有什么区别？", "expected": ["静态方法", "类方法"], "type": "方法用法"},
    {"q": "如何用 types 模块给对象动态绑定方法？", "expected": ["types模块", "MethodType"], "type": "方法用法"},

    {"q": "基类和派生类有什么区别？", "expected": ["基类", "派生类"], "type": "对比辨析"},
    {"q": "类属性和实例属性有什么区别？", "expected": ["类属性", "实例属性"], "type": "对比辨析"},
    {"q": "私有成员和受保护成员有什么区别？", "expected": ["私有成员", "受保护成员"], "type": "对比辨析"},
    {"q": "私有方法和公有方法有什么区别？", "expected": ["私有方法", "公有方法"], "type": "对比辨析"},

    {"q": "学习运算符重载之前，需要先掌握什么？", "expected": ["特殊方法"], "type": "关系前置"},
    {"q": "类和对象是什么关系？", "expected": ["类", "对象"], "type": "关系前置"},
    {"q": "学习多态之前，需要先掌握什么？", "expected": ["继承"], "type": "关系前置"},

    {"q": "Python 中的比较运算符有哪些？", "expected": ["比较运算符"], "type": "运算符"},
    {"q": "什么是位运算符？", "expected": ["位运算符"], "type": "运算符"},
    {"q": "迭代器和可迭代对象有什么区别？", "expected": ["迭代器", "可迭代对象"], "type": "运算符"},

    {"q": "什么是快速排序？它的时间复杂度是多少？", "expected": [], "type": "边界诚实性"},
]


async def main():
    qa = QAService()
    results = []

    for i, item in enumerate(QUESTIONS, 1):
        question = item["q"]
        print(f"\n[{i:2d}/{len(QUESTIONS)}] {question}")

        # 1. 向量检索 top3
        contexts = qa.search_related_knowledge(question, COURSE_ID, top_k=3)
        print("    检索 top3:")
        for c in contexts:
            print(f"      - {c}")

        # 2. RAG 回答
        answer = await qa.ask(question, COURSE_ID)
        print(f"    回答: {answer[:120]}{'…' if len(answer) > 120 else ''}")

        results.append({
            "id": i,
            "type": item["type"],
            "question": question,
            "expected_kp": item["expected"],
            "retrieved_top3": contexts,
            "answer": answer,
        })

    with open("test_qa_testset.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n{'=' * 60}")
    print(f"测试完成，共 {len(results)} 题，结果已写入 test_qa_testset.json")


if __name__ == "__main__":
    asyncio.run(main())
