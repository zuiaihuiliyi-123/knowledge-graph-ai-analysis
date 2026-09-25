"""阶段 G：`qa_service.ask()` 不再阻塞事件循环 —— 并发验证

背景（`CLAUDE.md`「已知问题」第 1 条）：本项目检索与 LLM 调用都是**同步阻塞**的
（openai 同步客户端 / Neo4j 同步驱动 / SQLite）。若直接在 `async def` 里执行，
一个请求会占住整个事件循环，同进程内其他请求全部排队。

本测试**不联网、不调 LLM**：把真正耗时的 `_ask_blocking` 换成 `time.sleep`
（一个货真价实的阻塞调用），然后验证：

  1. `ask()` 仍是协程函数（调用方仍可 await）—— 接口未被破坏；
  2. `_ask_blocking()` 是同步函数且体内无 `await` —— 它**才**能被丢进线程池；
  3. 两个并发 `ask()` 的**总耗时 ≈ 1 个**（并行）而非 2 个（串行）；
  4. **核心断言**：一个「快协程」能在慢请求完成**之前**跑完 → 事件循环没被占住；
  5. **反证**：把同一段阻塞体直接写进协程里（等价于修复前的写法）时，
     第 3、4 条都会**失败** —— 这证明本测试确实能测出问题；
     否则「通过」毫无意义（一个恒真的测试等于没有测试）。
  6. 端点侧：`api/qa.py` 的两处同步调用都改成了 `await asyncio.to_thread(...)`
     （**源码断言** —— 该端点需要鉴权与完整 app，起 HTTP 端到端成本过高，
     而这一处的风险恰恰是「被人改回同步调用」，源码断言能拦住这个回归）。

用法（backend 目录下）：python test_qa_async.py
"""
import asyncio
import inspect
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.qa_service import QAService

BLOCK = 0.6      # 模拟一次「慢 LLM 调用」的阻塞时长
FAST = 0.03      # 快协程的时长（远小于 BLOCK）
N_CONCURRENT = 2


def _stub_blocking(self, *args, **kwargs):
    """替换 `_ask_blocking`：用 time.sleep 模拟耗时同步调用（真阻塞，非 async sleep）。"""
    time.sleep(BLOCK)
    return "stub-answer"


async def _blocked_ask(self, question, *args, **kwargs):
    """反证用：把阻塞体**直接写在协程里**（等价于修复前：同步调用占住事件循环）。"""
    time.sleep(BLOCK)
    return "blocked-answer"


async def _scenario(ask_callable, svc, n=N_CONCURRENT):
    """并发跑 n 个 ask + 1 个快协程，返回 (总耗时秒, 完成顺序列表)。"""
    order = []
    t0 = time.perf_counter()

    async def one(i):
        r = await ask_callable(svc, f"q{i}")
        order.append(f"ask{i}")
        return r

    async def fast():
        await asyncio.sleep(FAST)
        order.append("fast")

    await asyncio.gather(fast(), *[one(i) for i in range(n)])
    return time.perf_counter() - t0, order


def main():
    checks = []

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    # ---------- 1/2. 结构：接口未破坏，且同步体真的同步 ----------
    check("ask() 仍是协程函数（调用方可 await）", inspect.iscoroutinefunction(QAService.ask))
    check("_ask_blocking() 是同步函数", not inspect.iscoroutinefunction(QAService._ask_blocking))
    body = inspect.getsource(QAService._ask_blocking)
    check("_ask_blocking() 体内无 await（否则丢线程池会报错）",
          "await" not in body, f"出现 {body.count('await')} 次")
    check("_ask_blocking() 原样保留了异常处理",
          "except Exception" in body)

    # ---------- 3/4. 修复后：并行 + 不阻塞事件循环 ----------
    orig = QAService._ask_blocking
    QAService._ask_blocking = _stub_blocking
    try:
        t_thread, order_thread = asyncio.run(_scenario(QAService.ask, QAService()))
    finally:
        QAService._ask_blocking = orig
    print(f"〔修复后〕{N_CONCURRENT} 个并发 ask：总耗时 {t_thread:.3f}s（单次阻塞 {BLOCK}s），"
          f"完成顺序 {order_thread}")

    check(f"并发总耗时 ≈ 1 个（< {BLOCK * 1.5:.2f}s）→ 确实并行执行",
          t_thread < BLOCK * 1.5, f"{t_thread:.3f}s")
    check("**快协程在慢请求之前完成 → 事件循环未被阻塞**",
          order_thread[0] == "fast", f"完成顺序 {order_thread}")

    # ---------- 5. 反证：修复前的写法会暴露问题 ----------
    t_blocked, order_blocked = asyncio.run(_scenario(_blocked_ask, None))
    print(f"〔反证/修复前〕{N_CONCURRENT} 个并发 ask：总耗时 {t_blocked:.3f}s，"
          f"完成顺序 {order_blocked}")
    check(f"反证：串行执行 → 总耗时 ≈ {N_CONCURRENT} 个（≥ {BLOCK * 1.9:.2f}s）",
          t_blocked >= BLOCK * 1.9, f"{t_blocked:.3f}s")
    check("反证：快协程被排在最后 → 事件循环确实被占住",
          order_blocked[-1] == "fast", f"完成顺序 {order_blocked}")
    check("修复后总耗时显著优于修复前（测试可区分两种实现）",
          t_thread < t_blocked - BLOCK * 0.5, f"{t_thread:.3f}s vs {t_blocked:.3f}s")

    # ---------- 6. 端点侧源码断言 ----------
    qa_api = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "app", "api", "qa.py")
    src = open(qa_api, encoding="utf-8").read()
    check("api/qa.py：qa_service.ask 仍被 await", "await qa_service.ask(" in src)
    check("api/qa.py：search_related_nodes 已改走 asyncio.to_thread",
          "await asyncio.to_thread(qa_service.search_related_nodes" in src)
    check("api/qa.py：已 import asyncio", "import asyncio" in src)

    # ---------- 汇总 ----------
    print("\n" + "=" * 70)
    print("校验结果")
    print("=" * 70)
    failed = 0
    for name, ok, detail in checks:
        if not ok:
            failed += 1
        line = f"{'✓' if ok else '✗'} {name}"
        if detail and not ok:
            line += f"    → {detail}"
        print(line)
    total = len(checks)
    print("-" * 70)
    print(f"通过 {total - failed}/{total}" + (f"，失败 {failed}" if failed else "，全部通过"))
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
