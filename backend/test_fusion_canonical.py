"""文档级知识融合（Fusion）P1：规范节点选择与多候选冲突

运行方式（在 backend 目录下执行）：
    python test_fusion_canonical.py

**完全离线**（纯规则函数，不碰数据库 / 不调 LLM）。
断言的核心是「谁是规范节点由确定性规则梯子决定，不由名称长度或相似度决定」。
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.services.fusion import entity_resolver as R

failures = []


def check(label, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def prof(kp_id, name, category="概念", description="", confidence=0.9, degree=0,
         is_manual=False, created_at=None):
    return {"kp_id": kp_id, "name": name, "category": category, "description": description,
            "confidence": confidence, "degree": degree, "is_manual": is_manual,
            "created_at": created_at}


def main():
    print("=" * 72)
    print("文档级知识融合 P1：规范节点选择 choose_canonical")
    print("=" * 72)

    # ---------- 1. is_manual 最高优先 ----------
    print("\nStep 1: 人工确认过的节点优先作为规范节点")
    a = prof("kp_1", "BFS", is_manual=False)
    b = prof("kp_2", "广度优先搜索", is_manual=True)
    src, tgt, why = R.choose_canonical(a, b)
    check("manual 者胜出（与参数顺序无关，两种入参都一样）",
          tgt["kp_id"] == "kp_2" and src["kp_id"] == "kp_1", f"{src['kp_id']}->{tgt['kp_id']}")
    src2, tgt2, _ = R.choose_canonical(b, a)
    check("交换入参后结论不变（确定性）",
          tgt2["kp_id"] == "kp_2" and src2["kp_id"] == "kp_1")
    check("给出了可读的选择理由", "人工" in why, why)

    # ---------- 2. confidence ----------
    print("\nStep 2: is_manual 相同时，confidence 高者胜")
    a = prof("kp_1", "甲", confidence=0.6)
    b = prof("kp_2", "乙", confidence=0.95)
    src, tgt, why = R.choose_canonical(a, b)
    check("confidence 高者当规范节点", tgt["kp_id"] == "kp_2", why)

    # ---------- 3. 定义非空（**只比有无，不比长度**） ----------
    print("\nStep 3: 定义非空优先，且**不比较长度**")
    short = prof("kp_1", "甲", description="短定义")
    long_ = prof("kp_2", "乙", description="这是一个非常非常非常长的定义，长到足以让人误以为它更权威" * 3)
    empty = prof("kp_3", "丙", description="")
    src, tgt, why = R.choose_canonical(short, empty)
    check("有定义者胜出", tgt["kp_id"] == "kp_1", why)
    check("理由是「只比较有无，不比较长度」", "不比较长度" in why, why)

    # 两个都有定义时，**不得**因为更长就赢 —— 应落到下一档（度数）
    a = prof("kp_1", "甲", description="短", confidence=0.9, degree=5)
    b = prof("kp_2", "乙", description="很长" * 100, confidence=0.9, degree=1)
    src, tgt, why = R.choose_canonical(a, b)
    check("两者都有定义时不比长度，改比度数", tgt["kp_id"] == "kp_1", why)
    check("理由里出现的是度数而非长度", "度数" in why, why)

    # ---------- 4. 度数 ----------
    print("\nStep 4: 关系度数更高者当规范节点（改写影响面更小）")
    a = prof("kp_1", "甲", degree=2)
    b = prof("kp_2", "乙", degree=9)
    src, tgt, why = R.choose_canonical(a, b)
    check("度数高者胜", tgt["kp_id"] == "kp_2", why)

    # ---------- 5. 创建时间 ----------
    print("\nStep 5: 创建时间更早者当规范节点")
    a = prof("kp_1", "甲", created_at="2026-05-01T00:00:00Z")
    b = prof("kp_2", "乙", created_at="2026-01-01T00:00:00Z")
    src, tgt, why = R.choose_canonical(a, b)
    check("更早者胜", tgt["kp_id"] == "kp_2", why)

    # 缺时间时不做猜测，直接落到最后一条
    a = prof("kp_1", "甲", created_at=None)
    b = prof("kp_2", "乙", created_at="2026-01-01T00:00:00Z")
    src, tgt, why = R.choose_canonical(a, b)
    check("一方时间缺失时不据此判定（落到 kp_id 兜底）", "kp_id 字典序" in why, why)

    # ---------- 6. kp_id 兜底 ----------
    print("\nStep 6: 前五条全平局时按 kp_id 字典序（**只为确定性**）")
    a = prof("kp_b", "甲")
    b = prof("kp_a", "乙")
    src, tgt, why = R.choose_canonical(a, b)
    check("kp_id 小者当规范节点", tgt["kp_id"] == "kp_a", why)
    check("理由写明这只是确定性兜底，不是判断依据",
          "确定性" in why and "字典序" in why, why)

    # ---------- 7. 名称长度 / 相似度不参与 ----------
    print("\nStep 7: 名称长度与相似度**不参与**规范节点选择")
    a = prof("kp_1", "树")
    b = prof("kp_2", "一种非常重要的数据组织方式叫做树结构")
    # 两者其余属性完全一致 → 只能落到 kp_id 兜底，绝不会因为名字长而当选
    src, tgt, why = R.choose_canonical(a, b)
    check("名称更长的一方没有因此获胜", tgt["kp_id"] == "kp_1", f"{why}")
    check("理由里不含任何「名称」相关判据", "名称" not in why, why)

    # ---------- 8. 多候选冲突 ----------
    print("\nStep 8: 同一源命中多个 SAME 目标 —— 只允许一个（目标唯一）")
    src_p = prof("kp_s", "BFS")
    t1 = prof("kp_t1", "广度优先搜索", confidence=0.9)
    t2 = prof("kp_t2", "广度优先遍历", confidence=0.95)
    chosen, superseded = R.resolve_multi_target([
        {"target": t1, "score": 0.90},
        {"target": t2, "score": 0.90},
    ])
    check("分数并列时按规则梯子选出 t2（confidence 更高）",
          chosen["target"]["kp_id"] == "kp_t2", str(chosen["target"]["kp_id"]))
    check("其余进入 superseded", [e["target"]["kp_id"] for e in superseded] == ["kp_t1"])

    chosen, superseded = R.resolve_multi_target([
        {"target": t1, "score": 0.95},
        {"target": t2, "score": 0.90},
    ])
    check("分数不同时高分胜出", chosen["target"]["kp_id"] == "kp_t1")

    # 平局也要确定性：交换入参顺序结果不变
    entries = [{"target": t1, "score": 0.9}, {"target": t2, "score": 0.9}]
    c1, _ = R.resolve_multi_target(entries)
    c2, _ = R.resolve_multi_target(list(reversed(entries)))
    check("多候选结论与入参顺序无关（确定性）",
          c1["target"]["kp_id"] == c2["target"]["kp_id"],
          f"{c1['target']['kp_id']} vs {c2['target']['kp_id']}")

    check("空列表不炸", R.resolve_multi_target([]) == (None, []))

    # ---------- 9. 分档边界 ----------
    print("\nStep 9: 分档边界与「LLM 不可用时绝不落 SAME」")
    from app.services.fusion import fusion_config as C

    d, band = R.decide_by_rules(C.AUTO_SAME_THRESHOLD)
    check("达到 AUTO_SAME 阈值 → SAME", d == "SAME", f"{d}/{band}")
    d, band = R.decide_by_rules(C.AUTO_SAME_THRESHOLD - 1e-6)
    check("差一点点 → 不判 SAME", d is None and band == R.BAND_REVIEW, f"{d}/{band}")
    d, band = R.decide_by_rules(C.AUTO_SAME_THRESHOLD - 1e-6, allow_review=False)
    check("LLM 关闭时 REVIEW 档落 UNCERTAIN（**不是 SAME**）",
          d == "UNCERTAIN", f"{d}/{band}")
    d, band = R.decide_by_rules(C.RECALL_THRESHOLD - 1e-6)
    check("低于召回下限 → 不建候选", d is None and band == R.BAND_REJECT, f"{d}/{band}")

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
