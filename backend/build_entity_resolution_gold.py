"""生成 `eval_data/entity_resolution_gold.json`（**一次性数据构建脚本**，不属于测试）。

数据来源与构造方式（全部来自项目内的**真实文档与真实图谱**，无任何人工编造的实体）：

正样本（label=SAME）
  在真实文档正文里搜索「实体名（另一种写法）」形态，且**括号里的写法在本 doc 的
  实体表中不存在**。这类对正是实体消融模块存在的意义：模型若把两种写法都抽成节点，
  就应该把它们合并。每一条都保留**逐字原文片段**作为依据。
  构建后**逐条人工复核**：只保留"括号里确实是同一概念的另一名称"的
  （中文 ↔ 英文名 / 缩写 / 学名）。括号里写的是**定义**而不是名称的（例如
  「摩尔（论了物质的宏观质量…）」）一律剔除 —— 这类占了原始命中的一多半，
  是这种收割方式的固有噪声。

负样本（label=DIFFERENT）—— 三类**真实数据里就存在的**危险对：
  1. substring：同一文档内一个是另一个的真子串（「原子」/「原子核」、「金属」/「类金属」）。
     两者在标注里本就是**两个不同的知识点**，标签以标注为准。
  2. relation：同一文档内存在 CONTAINS / PRECEDES / APPLIES_TO 关系的实体对
     （上位/下位、依赖），提取器自己就认为它们是两个概念。
  3. symbol_clash：共享英文缩写前缀的对（CFS / FCFS），名称很像但不是一回事。

运行（在 backend 目录下）：
    python build_entity_resolution_gold.py
"""
import asyncio
import io
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.core.sql_database import sql_db
from app.core.storage import resolve_document_path
from app.services.document_parser import DocumentParser
from app.services.fusion.entity_normalizer import normalize
from app.services.kg_manager import KnowledgeGraphManager

OUT = os.path.join("eval_data", "entity_resolution_gold.json")

_GLOSS = re.compile(
    r"([^\s，,。；;：:、（）()\[\]【】]{1,20})\s*[（(]\s*([^（()）]{1,40}?)\s*[)）]")

# 人工复核后的正样本白名单：键为 (doc_id, 规范化的两个名字)，值为判定为同一实体的理由。
# **只保留"括号里是同一概念的另一名称"的情形**；定义式括注一律不收。
CURATED_SAME = {
    (7, "FCFS", "先来先服务"): "缩写 ↔ 中文全称",
    (7, "SJF", "短作业优先"): "缩写 ↔ 中文全称",
    (7, "CFS", "COMPLETELY FAIR SCHEDULER"): "缩写 ↔ 英文全称",
    (94, "哈夫曼树", "最优二叉树"): "同一概念的两种中文名",
    (94, "WPL", "带权路径长度"): "缩写 ↔ 中文全称",
    (94, "平衡二叉树", "AVL 树"): "讲义正文直接把两者等同",
    (105, "公制单位", "国际单位制"): "同一概念的中文名与学名",
    (107, "运动学", "KINEMATICS"): "中文名 ↔ 英文名",
    (107, "位移", "DISPLACEMENT"): "中文名 ↔ 英文名",
    (107, "加速度", "ACCELERATION"): "中文名 ↔ 英文名",
    (107, "落体", "FALLING OBJECTS"): "中文名 ↔ 英文名",
    (107, "路程", "DISTANCE TRAVELED"): "中文名 ↔ 英文名",
    (107, "速度", "SPEED"): "中文名 ↔ 英文名",
    (107, "平均速度", "AVERAGE VELOCITY"): "中文名 ↔ 英文名",
    (107, "平均加速度", "AVERAGE ACCELERATION"): "中文名 ↔ 英文名",
    (129, "元素周期表", "THE PERIODIC TABLE"): "中文名 ↔ 英文名",
    (129, "化学命名法", "CHEMICAL NOMENCLATURE"): "中文名 ↔ 英文名",
    (129, "化学符号", "CHEMICAL SYMBOL"): "中文名 ↔ 英文名",
    (129, "分子式", "MOLECULAR FORMULA"): "中文名 ↔ 英文名",
    (129, "过渡金属", "TRANSITION METALS"): "中文名 ↔ 英文名",
    (129, "含氧酸根阴离子", "OXYANIONS"): "中文名 ↔ 英文名",
    (129, "命名法", "NOMENCLATURE"): "中文名 ↔ 英文名",
    (129, "卤素", "HALOGENS"): "中文名 ↔ 英文名",
    (129, "原子序数", "Z"): "物理量 ↔ 其标准符号",
    (129, "质量数", "A"): "物理量 ↔ 其标准符号",
    (130, "百万分率", "PPM"): "中文名 ↔ 缩写",
    (130, "摩尔浓度", "M"): "物理量 ↔ 其标准符号",
}

_STRIP_PREFIX = re.compile(r"^(以|添加|也称为|添加单词)")


async def main():
    docs = sql_db._query(
        "SELECT doc_id, course_id, file_name, file_path FROM t_document "
        "WHERE extract_status = 'COMPLETED' ORDER BY doc_id")

    pairs = []
    same_hits = 0
    per_doc = {}

    for d in docs:
        try:
            text = await DocumentParser.parse(resolve_document_path(d))
            graph = KnowledgeGraphManager.get_raw_graph_v1(d["course_id"], d["doc_id"], limit=2000)
        except Exception as e:  # noqa: BLE001
            print(f"  跳过 doc {d['doc_id']}（{e}）")
            continue
        nodes = graph["nodes"]
        edges = graph["edges"]
        by_norm, by_kp = {}, {}
        for n in nodes:
            by_norm.setdefault(normalize(n["label"]), n)
            by_kp[n["id"]] = n
        names = set(by_norm)

        # ---------- 正样本：正文里的显式等价式 + 人工白名单 ----------
        for m in _GLOSS.finditer(text):
            a, b, raw = m.group(1).strip(), m.group(2).strip(), m.group(0)
            if normalize(b) in names and normalize(a) not in names:
                a, b = b, a
            elif not (normalize(a) in names and normalize(b) not in names):
                continue
            # 括号里可能是「halogens，第17族」这种复合写法，取第一段
            b = re.split(r"[，,、]", b)[0].strip()
            if _STRIP_PREFIX.match(b):
                continue
            key = (d["doc_id"], normalize(a), normalize(b))
            if key in CURATED_SAME:
                same_hits += 1
                pairs.append({
                    "document_id": d["doc_id"], "course_id": d["course_id"],
                    "entity_a": a, "entity_b": b, "label": "SAME",
                    "kind": "explicit_alias", "evidence": raw[:120],
                    "note": CURATED_SAME[key], "file_name": d["file_name"],
                })
            per_doc.setdefault(d["doc_id"], (set(), set()))
            per_doc[d["doc_id"]][0].add(normalize(a))

        # ---------- 负样本 1：同文档内的真子串对 ----------
        # 每文档**最多 3 条**，否则子串对会把整个样本集淹没（它们是数量最多的一类）。
        labels = sorted({n["label"] for n in nodes})
        sub_added = 0
        for x in labels:
            if sub_added >= 3:
                break
            for y in labels:
                if x == y or x not in y:
                    continue
                if len(x) < 2 or len(x) / len(y) >= 0.85:
                    continue
                sub_added += 1
                pairs.append({
                    "document_id": d["doc_id"], "course_id": d["course_id"],
                    "entity_a": x, "entity_b": y, "label": "DIFFERENT",
                    "kind": "substring",
                    "evidence": f"标注中「{x}」与「{y}」是同一文档里的两个不同知识点",
                    "note": "一个是另一个的真子串，但属于上下位而非同义",
                    "file_name": d["file_name"],
                })
                break

        # ---------- 负样本 2：存在上下位/依赖关系的对 ----------
        # 边的 source/target 是 **kp_id**（不是名字），必须经 by_kp 映射回节点
        rel_added, seen_rel = 0, set()
        for e in edges:
            if rel_added >= 3:
                break
            if e["type"] not in ("CONTAINS", "PRECEDES", "APPLIES_TO"):
                continue
            a, b = by_kp.get(e["source"]), by_kp.get(e["target"])
            if not a or not b:
                continue
            key = frozenset((a["label"], b["label"]))
            if key in seen_rel:
                continue
            seen_rel.add(key)
            rel_added += 1
            pairs.append({
                "document_id": d["doc_id"], "course_id": d["course_id"],
                "entity_a": a["label"], "entity_b": b["label"], "label": "DIFFERENT",
                "kind": "relation",
                "evidence": f"图谱中存在 {e['type']} 关系（{a['label']} → {b['label']}）",
                "note": "有上下位/依赖关系的两个知识点，不是同义",
                "file_name": d["file_name"],
            })

    # ---------- 负样本 3：共享英文缩写前缀（真实数据里的 CFS / FCFS）----------
    clash = [("CFS", "FCFS")]
    doc7 = next((d for d in docs if d["doc_id"] == 7), None)
    if doc7:
        for a, b in clash:
            pairs.append({
                "document_id": 7, "course_id": doc7["course_id"],
                "entity_a": a, "entity_b": b, "label": "DIFFERENT",
                "kind": "symbol_clash",
                "evidence": "两个调度算法的缩写，名称高度相似但含义不同",
                "note": "首字母相同的缩写极易被误判为同一实体",
                "file_name": doc7["file_name"],
            })

    n_same = sum(1 for p in pairs if p["label"] == "SAME")
    n_diff = len(pairs) - n_same
    out = {
        "meta": {
            "purpose": "文档级实体消歧与融合的评测集（与 V1.1 抽取准确率评测相互独立）",
            "built_by": "build_entity_resolution_gold.py",
            "provenance": "全部来自项目内真实文档正文与真实知识图谱；正样本经人工逐条复核",
            "caveats": [
                "正样本来自正文里写出的等价式（中文↔英文名/缩写/学名），**规模有限**："
                "抽取 Prompt 要求同义写法只保留一个规范名，人工标注也遵循同一约定，"
                "因此既有 gold 里几乎没有「同义不同名」的节点对。这是方法层面的召回上限。",
                "括号里写的是定义而非名称的情形占原始命中一多半，已全部剔除；"
                "这说明「显式等价式」信号本身有噪声，不能无条件采信。",
                "负样本以**标注**为准：同一文档里被标成两个知识点的实体就是不同的两个。",
                "⚠️ 本集被用于**改进过**模块（第一版只召回 1/20，据此补上了「正文等价式」"
                "这条路径）。因此在这批样本上得到的指标偏乐观，不代表泛化表现。",
            ],
            "counts": {"total": len(pairs), "same": n_same, "different": n_diff,
                       "documents": len({p["document_id"] for p in pairs})},
        },
        "pairs": pairs,
    }
    io.open(OUT, "w", encoding="utf-8").write(
        json.dumps(out, ensure_ascii=False, indent=2))
    print(f"写入 {OUT}")
    print(f"  共 {len(pairs)} 对：SAME {n_same} / DIFFERENT {n_diff}，"
          f"覆盖 {out['meta']['counts']['documents']} 个文档")
    print(f"  正样本白名单命中 {same_hits} 条")


if __name__ == "__main__":
    asyncio.run(main())
