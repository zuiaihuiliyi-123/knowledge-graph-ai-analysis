"""读图折叠：按融合映射把「已融合的源节点」折进它的「目标节点」。

这是融合结果**唯一**的生效点 —— Neo4j 里什么都没变，变的只是读出来的这一份数据。
因此它必须是**纯函数**：不查库、不写库、不改入参，给定同样的
`(nodes, edges, mapping)` 永远得到同样的输出。可测、可复现、可逐字节比对。

不变量（由 `upsert`/`apply` 侧保证，本模块只做防御性处理）：
- 每个源最多一条出边（`UNIQUE(course_id, document_id, source_kp_id)`）→ 映射是一片有向森林
- 无环（写时防环），这里仍做一次环检测，发现即就地切断并记入 report
"""


def resolve_root(kp_id: str, mapping: dict, on_cycle=None) -> str:
    """沿出边走到底，返回所属连通分量的 root。

    链式映射（A→B、B→C）在这里归一到 C —— 映射**刻意不做写时扁平化**，
    这样撤销任意子集都自洽：撤掉 B→C 后 A 仍折进 B，撤掉 A→B 后 B 折进 C。
    """
    seen = set()
    cur = kp_id
    while cur in mapping:
        if cur in seen:
            # 正常情况下写时防环已经拦住；真出现环就就地切断（把当前点当作 root），
            # 既不静默反转也不无限循环。
            if on_cycle is not None:
                on_cycle(cur)
            return cur
        seen.add(cur)
        cur = mapping[cur]
    return cur


def _edge_rank(edge: dict):
    """重复边去重的排序依据：confidence 降序 → is_manual 降序 → id 升序（确定性）"""
    props = edge.get("properties") or {}
    try:
        conf = float(props.get("confidence"))
    except (TypeError, ValueError):
        conf = -1.0
    return conf, 1 if props.get("is_manual") else 0


def _pick_edge(a: dict, b: dict) -> dict:
    ra, rb = _edge_rank(a), _edge_rank(b)
    if ra != rb:
        return a if ra > rb else b
    return a if str(a.get("id") or "") <= str(b.get("id") or "") else b


def fold_graph(nodes: list, edges: list, mapping: dict, report: dict = None):
    """按映射折叠图，返回 `(nodes, edges)`。

    mapping: {source_kp_id: target_kp_id}，来自 `fusion_map.load_active_mapping`。

    **无映射时是纯 no-op**：直接返回入参本身（同一个对象），
    所以「没有融合记录的文档」读图结果与引入本模块之前逐字节相同。

    report（可选，出参）：会被写入四个列表，供调用方记日志/写冲突表 ——
      stale      端点已不在图里的映射（文档被重新抽取后 kp_id 整体更换所致）
      cycles     检测到的环（已就地切断）
      self_loops 折叠后变成自环、被丢弃的边
      deduped    折叠后重复、被去重的边
    """
    if not mapping:
        return nodes, edges

    if report is not None:
        report.setdefault("stale", [])
        report.setdefault("cycles", [])
        report.setdefault("self_loops", [])
        report.setdefault("deduped", [])

    by_id = {n["id"]: n for n in nodes}

    # 陈旧守卫：端点不在本次图里的映射自动失效。
    # kp_id 会在文档重新抽取后整体更换，旧映射必然指向不存在的节点 ——
    # 此时**静默失效**（而不是报错、也不是乱折）才是正确的失败方向。
    effective = {}
    for src, tgt in mapping.items():
        if src in by_id and tgt in by_id and src != tgt:
            effective[src] = tgt
        elif report is not None:
            report["stale"].append({"source_kp_id": src, "target_kp_id": tgt})

    if not effective:
        return nodes, edges

    def _on_cycle(kp_id):
        if report is not None:
            report["cycles"].append(kp_id)

    root_of = {nid: resolve_root(nid, effective, _on_cycle) for nid in by_id}

    # ---- 节点折叠 ----
    # root 占据「该分量首个出现的节点」的位置，被折掉的源不再单独出现。
    out_nodes = []
    placed = {}
    for n in nodes:
        nid = n["id"]
        root = root_of[nid]
        root_node = by_id.get(root)
        if root_node is None:
            continue
        if root not in placed:
            host = dict(root_node)
            # properties 也要拷贝：下面会往里追加 fused_from，不能污染入参
            host["properties"] = dict(root_node.get("properties") or {})
            placed[root] = host
            out_nodes.append(host)
        if nid != root:
            fused = placed[root]["properties"].setdefault("fused_from", [])
            fused.append({
                "kp_id": nid,
                "name": n.get("label") or "",
                "category": (n.get("properties") or {}).get("category") or "",
                "description": n.get("description") or "",
            })

    for host in placed.values():
        fused = host["properties"].get("fused_from")
        if fused:
            host["properties"]["fused_count"] = len(fused)

    # ---- 边改写 + 自环丢弃 + 重复边去重 ----
    out_edges = []
    seen = {}          # (source_root, target_root, type) -> (在 out_edges 中的下标, 边)
    for e in edges:
        s = root_of.get(e.get("source"), e.get("source"))
        t = root_of.get(e.get("target"), e.get("target"))
        if s == t:
            # 折叠后自环：与 build_graph 的 source==target 跳过、
            # 与 _merge_results 的 synonym_split 丢弃是同一语义
            if report is not None:
                report["self_loops"].append({"id": e.get("id"), "type": e.get("type")})
            continue

        rewritten = dict(e)
        rewritten["source"] = s
        rewritten["target"] = t

        key = (s, t, e.get("type"))
        slot = seen.get(key)
        if slot is None:
            seen[key] = (len(out_edges), rewritten)
            out_edges.append(rewritten)
            continue

        idx, prev = slot
        keep = _pick_edge(prev, rewritten)
        if keep is rewritten:
            # 新边更优：**原位**替换，输出顺序保持稳定（不把边挪到末尾）
            out_edges[idx] = rewritten
            seen[key] = (idx, rewritten)
        if report is not None:
            report["deduped"].append({
                "type": e.get("type"),
                "kept_id": keep.get("id"),
                "dropped_id": (prev if keep is rewritten else rewritten).get("id"),
            })

    return out_nodes, out_edges
