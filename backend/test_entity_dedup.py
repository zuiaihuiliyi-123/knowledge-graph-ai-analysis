"""抽取阶段同名实体合并：不得静默丢弃后续描述与类别差异。

背景：`KnowledgeExtractor._merge_results` 按**规范化名**去重，只保留首个实体的
`description` / `category`。同一文档不同分块对同名实体的解释（甚至不同含义）会被
静默吞掉，而下游无法恢复——图谱 MERGE 键就是 `name`，融合模块只做映射、不拆分
（见 `docs/文档级知识融合与实体消歧实施报告.md` §10「同名不同义…不可恢复」）。

本脚本守住：
1. 首条描述仍是主 `description`（评测/展示口径不变）；
2. 其余不同描述被保留到 `descriptions`（首个之外的部分另给 `alt_descriptions`），不再丢失；
3. 类别冲突被记录到 `category_variants`，不静默吞掉；
4. 完全重复的描述不重复累积；
5. 统计字段 `entity_duplicates` / `duplicate_names` 可观测（能看出发生了多少次同名合并）；
6. 不同名实体与关系合并行为不受影响。

运行：cd backend && python test_entity_dedup.py
（纯内存计算，不调用 LLM、不读写数据库）
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.services.knowledge_extractor import KnowledgeExtractor  # noqa: E402

failures = []
total = 0


def check(label, ok, detail=""):
    global total
    total += 1
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def merge(*results):
    # 直接用类型调用：_merge_results 不依赖实例状态（构造实例会创建 LLM 客户端）
    return KnowledgeExtractor._merge_results(list(results))


def main() -> int:
    print("Step 1: 同名 · 不同描述 → 两条描述都要留下")
    merged = merge(
        {"entities": [{"name": "栈", "category": "概念", "description": "后进先出的线性表"}],
         "relations": []},
        {"entities": [{"name": "栈 ", "category": "概念",
                       "description": "函数调用时保存返回地址的内存区域"}],
         "relations": []},
    )
    ents = merged["entities"]
    check("同名实体只保留一个节点", len(ents) == 1, str(len(ents)))
    e = ents[0]
    check("主描述仍是首条（口径不变）", e.get("description") == "后进先出的线性表",
          str(e.get("description")))
    check("第二条描述不再丢失", "函数调用时保存返回地址的内存区域" in (e.get("descriptions") or []),
          str(e.get("descriptions")))
    check("alt_descriptions 只含非主描述",
          e.get("alt_descriptions") == ["函数调用时保存返回地址的内存区域"],
          str(e.get("alt_descriptions")))

    print("\nStep 2: 同名 · 类别冲突 → 记录而不吞掉")
    merged2 = merge(
        {"entities": [{"name": "快速排序", "category": "方法", "description": "分治排序"}],
         "relations": []},
        {"entities": [{"name": "快速排序", "category": "概念", "description": "考试常考算法"}],
         "relations": []},
    )
    e2 = merged2["entities"][0]
    check("主类别仍为首条", e2.get("category") == "方法", str(e2.get("category")))
    check("冲突类别被记录", e2.get("category_variants") == ["概念"], str(e2.get("category_variants")))

    print("\nStep 3: 完全重复的描述不重复累积")
    merged3 = merge(
        {"entities": [{"name": "图", "category": "概念", "description": "由顶点和边组成"}],
         "relations": []},
        {"entities": [{"name": "图", "category": "概念", "description": "由顶点和边组成"}],
         "relations": []},
    )
    e3 = merged3["entities"][0]
    check("descriptions 只有一条", e3.get("descriptions") == ["由顶点和边组成"],
          str(e3.get("descriptions")))
    check("alt_descriptions 为空", not e3.get("alt_descriptions"), str(e3.get("alt_descriptions")))

    print("\nStep 4: 统计可观测")
    check("entity_duplicates 计数正确（Step1+2+3 各 1 次）",
          merged.get("entity_duplicates") == 1 and merged2.get("entity_duplicates") == 1
          and merged3.get("entity_duplicates") == 1,
          f"{merged.get('entity_duplicates')}/{merged2.get('entity_duplicates')}/"
          f"{merged3.get('entity_duplicates')}")
    names = merged.get("duplicate_names") or []
    check("duplicate_names 记录了被合并的规范名", names == ["栈"], str(names))

    print("\nStep 5: 不同名实体与关系不受影响")
    merged4 = merge(
        {"entities": [{"name": "数组", "category": "概念", "description": "连续存储"},
                      {"name": "链表", "category": "概念", "description": "节点串联"}],
         "relations": [{"source": "数组", "target": "链表", "type": "RELATED_TO",
                        "confidence": 0.9}]},
    )
    check("两个不同名实体都保留", len(merged4["entities"]) == 2, str(len(merged4["entities"])))
    check("关系正常保留", len(merged4["relations"]) == 1, str(len(merged4["relations"])))
    check("无同名合并时计数为 0", merged4.get("entity_duplicates") == 0,
          str(merged4.get("entity_duplicates")))

    print("\nStep 6: 落图验证（Neo4j 可用时执行，否则跳过）")
    try:
        from app.core.database import db
        from app.services.kg_manager import KnowledgeGraphManager

        db.query("RETURN 1 AS ok")
        neo_ok = True
    except Exception as exc:  # noqa: BLE001
        neo_ok = False
        print(f"  [SKIP] Neo4j 不可用，跳过落库验证：{type(exc).__name__}")

    if neo_ok:
        TEST_CID, TEST_DID = 990071, 990072
        try:
            merged5 = merge(
                {"entities": [{"name": "栈", "category": "概念",
                               "description": "后进先出的线性表"}], "relations": []},
                {"entities": [{"name": "栈", "category": "方法",
                               "description": "函数调用栈"}], "relations": []},
            )
            KnowledgeGraphManager.build_graph(
                TEST_CID, TEST_DID, merged5["entities"], merged5["relations"])
            graph = KnowledgeGraphManager.get_raw_graph_v1(TEST_CID, TEST_DID)
            node = next((n for n in graph.get("nodes") or [] if n.get("label") == "栈"), None)
            props = (node or {}).get("properties") or {}
            check("图谱节点保留 alt_descriptions",
                  props.get("alt_descriptions") == ["函数调用栈"],
                  str(props.get("alt_descriptions")))
            check("图谱节点保留 category_variants",
                  props.get("category_variants") == ["方法"],
                  str(props.get("category_variants")))
            check("主描述/主类别未被覆盖",
                  (node or {}).get("description") == "后进先出的线性表"
                  and props.get("category") == "概念",
                  f"{node.get('description') if node else None}/{props.get('category')}")
        finally:
            try:
                db.delete_course_graph(TEST_CID)
            except Exception:  # noqa: BLE001
                pass

    print(f"\n通过 {total - len(failures)}/{total}" if not failures
          else f"\n失败 {len(failures)}/{total}：{failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
