"""进程内运行指标（管理员端「系统监控」的数据来源）

设计约束（对应管理员端要求「不要虚构 token 消耗、价格等不存在的数据」）：
本模块只统计**真实发生过的事实**——HTTP 请求与 LLM 调用的次数、成败与耗时。
不估算 token、不估算费用：项目里没有任何地方记录这两项，编一个数字出来
比不展示更糟。

为什么是内存计数而不是新表：
- 这些指标的价值在「最近这段时间服务怎么样」，重启归零是可接受的语义
  （响应里显式带 `since`，前端照实写「自本次服务启动以来」）；
- 写库会给每个请求加一次磁盘写，把监控变成被监控对象的负担。

线程安全：uvicorn 的同步端点跑在线程池里（项目大量使用同步 OpenAI / Neo4j 客户端），
故计数用 threading.Lock 保护；asyncio 单线程下加锁的开销也可忽略。

已知边界：多 worker 启动时（uvicorn --workers N）每个进程各记一份，
本模块不做跨进程聚合——本项目始终单进程运行，若将来改多 worker，
应在响应里标注这一点而不是悄悄求和。
"""
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime

# 只统计这些「AI 能力」的 LLM 调用（其余调用点未接入，见下）
LLM_SCOPES = {
    "extraction": "知识抽取",
    "qa": "智能问答",
}

# 明确声明「未接入统计」的调用点：管理员端据此显示「未统计」，
# 而不是把它们当作 0 次调用（0 会被误读为「没调用过」）。
UNTRACKED_LLM_SCOPES = (
    "试题文档导入（question_extractor）",
    "知识点关系补全（relation_completion）",
    "Git 历史分析（git_analyzer）",
    "向量化（embedding，走 Embedding 接口而非 LLM）",
)

_lock = threading.Lock()
_started_at = time.time()

_requests = {
    "total": 0,
    "errors": 0,          # HTTP 状态码 >= 400
    "server_errors": 0,   # HTTP 状态码 >= 500
    "total_ms": 0.0,
}
_by_path = defaultdict(lambda: {"count": 0, "errors": 0, "total_ms": 0.0})
_llm = defaultdict(lambda: {"calls": 0, "success": 0, "failed": 0, "total_ms": 0.0})


def record_request(path: str, status_code: int, duration_ms: float) -> None:
    """记录一次 HTTP 请求（由 main.py 的中间件调用）"""
    failed = status_code >= 400
    with _lock:
        _requests["total"] += 1
        _requests["total_ms"] += duration_ms
        if failed:
            _requests["errors"] += 1
        if status_code >= 500:
            _requests["server_errors"] += 1
        bucket = _by_path[path]
        bucket["count"] += 1
        bucket["total_ms"] += duration_ms
        if failed:
            bucket["errors"] += 1


def record_llm_call(scope: str, ok: bool, duration_ms: float) -> None:
    """记录一次 LLM 调用（成功/失败/耗时）"""
    with _lock:
        bucket = _llm[scope]
        bucket["calls"] += 1
        bucket["total_ms"] += duration_ms
        if ok:
            bucket["success"] += 1
        else:
            bucket["failed"] += 1


@contextmanager
def track_llm_call(scope: str):
    """包住一次 LLM 调用：无论成功、抛异常还是被取消，都会如实记账并放行异常。

    只应包住「发起请求」这一行，不要包住后续的 JSON 解析——
    解析失败说明模型返回不合规，但这一次 LLM 调用本身是成功的，
    记成 failed 会让「LLM 成功率」这项指标失去意义。
    """
    started = time.perf_counter()
    ok = True
    try:
        yield
    except BaseException:
        ok = False
        raise
    finally:
        record_llm_call(scope, ok, (time.perf_counter() - started) * 1000)


def _avg(total_ms: float, count: int) -> float:
    """平均耗时（毫秒，保留 1 位小数）；无样本返回 0.0"""
    return round(total_ms / count, 1) if count else 0.0


def snapshot(top_paths: int = 8) -> dict:
    """当前指标快照（管理员端 /api/v1/admin/system/status 用）"""
    with _lock:
        requests_total = dict(_requests)
        llm_raw = {k: dict(v) for k, v in _llm.items()}
        paths = sorted(_by_path.items(), key=lambda kv: kv[1]["count"], reverse=True)

    requests_summary = {
        "total": requests_total["total"],
        "errors": requests_total["errors"],
        "server_errors": requests_total["server_errors"],
        "avg_ms": _avg(requests_total["total_ms"], requests_total["total"]),
    }

    llm_summary = {"total_calls": 0, "success": 0, "failed": 0, "avg_ms": 0.0, "scopes": []}
    all_ms = 0.0
    for scope, name in LLM_SCOPES.items():
        bucket = llm_raw.get(scope) or {"calls": 0, "success": 0, "failed": 0, "total_ms": 0.0}
        llm_summary["total_calls"] += bucket["calls"]
        llm_summary["success"] += bucket["success"]
        llm_summary["failed"] += bucket["failed"]
        all_ms += bucket["total_ms"]
        llm_summary["scopes"].append({
            "scope": scope,
            "label": name,
            "calls": bucket["calls"],
            "success": bucket["success"],
            "failed": bucket["failed"],
            "avg_ms": _avg(bucket["total_ms"], bucket["calls"]),
        })
    llm_summary["avg_ms"] = _avg(all_ms, llm_summary["total_calls"])
    llm_summary["untracked"] = list(UNTRACKED_LLM_SCOPES)

    return {
        "started_at": datetime.fromtimestamp(_started_at).astimezone().isoformat(
            timespec="seconds"),
        "uptime_seconds": int(time.time() - _started_at),
        "requests": requests_summary,
        "top_paths": [
            {
                "path": p,
                "count": b["count"],
                "errors": b["errors"],
                "avg_ms": _avg(b["total_ms"], b["count"]),
            }
            for p, b in paths[:top_paths]
        ],
        "llm": llm_summary,
        # 计数是多进程不安全的：显式声明，避免前端把它当成全局精确值
        "scope_note": "指标为当前进程内存计数，自本次服务启动起算；多 worker 部署时不聚合。",
    }
