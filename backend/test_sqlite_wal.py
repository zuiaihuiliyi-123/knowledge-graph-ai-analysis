"""阶段 G：SQLite 并发可用性验收（WAL + busy_timeout）——含**决定性 A/B 反证**

背景：本项目部署形态是「一个 uvicorn 进程 + 若干 CLI 脚本/评测」共同写同一个 `app.db`。
rollback journal 模式下**长读事务会挡住写者的提交**（写者拿不到 EXCLUSIVE 锁 →
`database is locked`），而 L2 引入的批量写入又让写事务变长，两头一夹很容易撞上。

本测试验证（全部在**临时副本**上操作，不碰线上库）：

  1. 线上库的 `journal_mode` 确实是 WAL（`pragma_status()` + `PRAGMA` 实测）；
  2. 经 `_connect()` 打开的连接都带 `busy_timeout`；
  3. `backup_to()` 生成的备份**逐表行数与线上一致**（WAL 下裸拷会静默少数据，
     故这条同时验证了备份方法本身）；
  4. **决定性 A/B**：一个线程持有长读事务，另一个线程尝试写入 ——
       · rollback journal + 短 busy_timeout → **失败**（database is locked）
       · WAL + 生产 busy_timeout → **成功**
     若 B 也失败说明 WAL 没起作用；若 A 也成功说明本测试测不出问题（无意义）。
  5. 生产配置下的并发读写压力：locked 错误必须为 0。

用法（backend 目录下）：python test_sqlite_wal.py
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import threading

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.sql_database import BUSY_TIMEOUT_MS, JOURNAL_MODE, sql_db

HOLD_SEC = 1.2          # 长读事务持有时长
FAST_BUSY_MS = 300      # 反证用的短等待：让失败快速暴露


def _read_journal_mode(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()


def _ab_long_reader_read_vs_write(copy_path, journal_mode, busy_ms):
    """A/B：线程 A 持有长读事务，线程 B 尝试写入。返回写入结果事实。

    为什么这个场景能区分 WAL 与 rollback journal：
    rollback journal 下读者的 SHARED 锁会阻止写者取 EXCLUSIVE 完成提交
    （写者只能等，busy_timeout 用尽就报 `database is locked`）；
    WAL 下读者读的是快照，写者追加 WAL 即可提交，二者互不阻塞。
    这与本项目真实场景同构：一边是慢的大批量读（导出 5000 行向量），
    一边是学生的作答写入。
    """
    setup = sqlite3.connect(copy_path)
    try:
        setup.execute(f"PRAGMA journal_mode = {journal_mode}")
        setup.execute("CREATE TABLE IF NOT EXISTS _wal_probe (x INTEGER)")
        setup.commit()
    finally:
        setup.close()

    result = {"write_ok": None, "error": None}
    reader_started = threading.Event()
    release = threading.Event()

    def reader():
        # ⚠️ 连接必须**在本线程内创建**：sqlite3 连接默认 check_same_thread=True，
        #    跨线程使用会抛 ProgrammingError（本测试初版就踩了这个坑）。
        conn_r = sqlite3.connect(copy_path)
        try:
            conn_r.execute("BEGIN")
            conn_r.execute("SELECT count(*) FROM t_question").fetchall()   # 取 SHARED 读锁
            reader_started.set()
            release.wait(HOLD_SEC + 3.0)          # 持有读事务，模拟慢查询
        finally:
            try:
                conn_r.rollback()
            except Exception:
                pass
            conn_r.close()

    def writer():
        reader_started.wait(3.0)
        conn_w = sqlite3.connect(copy_path, timeout=max(busy_ms / 1000.0, 0.01))
        try:
            conn_w.execute(f"PRAGMA busy_timeout = {busy_ms}")
            conn_w.execute("INSERT INTO _wal_probe VALUES (1)")
            conn_w.commit()
            result["write_ok"] = True
        except Exception as e:
            result["write_ok"] = False
            result["error"] = f"{type(e).__name__}: {e}"
        finally:
            conn_w.close()

    t_r = threading.Thread(target=reader)
    t_w = threading.Thread(target=writer)
    t_r.start()
    t_w.start()
    t_w.join()
    release.set()
    t_r.join()
    return result


def _hammer(copy_path, n_threads=4, n_ops=20):
    """多线程同时读+写（生产配置）：统计 locked 错误与成功次数。"""
    stats = {"write": 0, "read": 0, "locked": 0, "errors": []}
    lock = threading.Lock()

    def worker(idx):
        conn = sqlite3.connect(copy_path)
        try:
            conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            for i in range(n_ops):
                try:
                    if (idx + i) % 2 == 0:
                        conn.execute("SELECT count(*) FROM t_question").fetchone()
                        kind = "read"
                    else:
                        conn.execute("BEGIN IMMEDIATE")
                        conn.execute("INSERT INTO _wal_probe VALUES (?)", (i,))
                        conn.commit()
                        kind = "write"
                    with lock:
                        stats[kind] += 1
                except sqlite3.OperationalError as e:
                    with lock:
                        if "locked" in str(e).lower() or "busy" in str(e).lower():
                            stats["locked"] += 1
                        else:
                            stats["errors"].append(str(e))
                except Exception as e:
                    with lock:
                        stats["errors"].append(f"{type(e).__name__}: {e}")
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return stats


def main():
    checks = []

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    tmpdir = tempfile.mkdtemp(prefix="wal_verify_")
    try:
        # ---------- 1. 线上库的 journal_mode ----------
        status = sql_db.pragma_status()
        actual = _read_journal_mode(sql_db.db_path)
        print(f"线上库：requested={status.get('requested')} actual={actual} "
              f"(切换事实 {status.get('actual')}，错误 {status.get('error')})")
        check("线上库 journal_mode = wal", actual == "wal", f"实际 {actual}")
        if JOURNAL_MODE == "WAL":
            check("journal_mode 切换无错误", status.get("error") in (None, ""),
                  str(status.get("error")))

        # ---------- 2. busy_timeout 已随连接生效 ----------
        conn = sql_db._connect()
        try:
            bt = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        finally:
            conn.close()
        check(f"_connect() 连接带 busy_timeout（当前 {BUSY_TIMEOUT_MS} ms）",
              bt == BUSY_TIMEOUT_MS, f"{bt}")

        # ---------- 3. backup_to 逐表完整性 ----------
        dst = os.path.join(tmpdir, "snap.db")
        fact = sql_db.backup_to(dst)
        live = {}
        conn = sql_db._connect()
        try:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall()]
            for n in names:
                live[n] = conn.execute(f"SELECT count(*) FROM {n}").fetchone()[0]
        finally:
            conn.close()
        diff = {t: (live.get(t), fact["tables"].get(t))
                for t in set(live) | set(fact["tables"])
                if live.get(t) != fact["tables"].get(t)}
        print(f"备份事实：{fact['bytes']} bytes，{len(fact['tables'])} 张表 / "
              f"{fact['rows']} 行，源 journal={fact['source_journal_mode']}，"
              f"自包含={fact['journal_reset']}")
        check("backup_to 覆盖全部表", set(live) == set(fact["tables"]),
              f"缺 {set(live) - set(fact['tables'])}")
        check("backup_to 逐表行数与线上一致（WAL 下裸拷会静默丢数据）",
              not diff, str(diff))
        check("备份文件自包含（journal 已复位 delete，无 -wal 依赖）",
              bool(fact["journal_reset"]) and _read_journal_mode(dst) == "delete")
        snap_conn = sqlite3.connect(dst)
        try:
            snap_rows = snap_conn.execute("SELECT count(*) FROM t_question").fetchone()[0]
        finally:
            snap_conn.close()
        check("备份文件可独立打开并读到数据", snap_rows == live.get("t_question"))

        # ---------- 4. 决定性 A/B ----------
        copy_rollback = os.path.join(tmpdir, "rollback.db")
        copy_wal = os.path.join(tmpdir, "wal.db")
        shutil.copy2(dst, copy_rollback)     # 从「自包含快照」派生两个副本，内容必然一致
        shutil.copy2(dst, copy_wal)

        rb = _ab_long_reader_read_vs_write(copy_rollback, "delete", FAST_BUSY_MS)
        wa = _ab_long_reader_read_vs_write(copy_wal, "wal", BUSY_TIMEOUT_MS)
        print(f"〔A 反证〕rollback journal + busy_timeout={FAST_BUSY_MS}ms → "
              f"写入{'成功' if rb['write_ok'] else '失败'}：{rb['error']}")
        print(f"〔B 生产〕WAL + busy_timeout={BUSY_TIMEOUT_MS}ms → "
              f"写入{'成功' if wa['write_ok'] else '失败'}：{wa['error']}")

        check("反证：rollback journal 下长读事务挡住写提交（报 locked）",
              rb["write_ok"] is False and bool(rb["error"])
              and "lock" in str(rb["error"]).lower(), str(rb))
        check("**WAL 下同一场景写入成功（读不阻塞写）**", wa["write_ok"] is True, str(wa))
        check("A/B 结果相反 → 本测试确实能区分两种 journal 模式",
              bool(rb["write_ok"]) != bool(wa["write_ok"]))

        # ---------- 5. 生产配置下的并发压力 ----------
        stats = _hammer(copy_wal)
        print(f"并发压力（4 线程 × 20 次读写，WAL + {BUSY_TIMEOUT_MS}ms）："
              f"读 {stats['read']} / 写 {stats['write']} / locked {stats['locked']} / "
              f"其他错误 {len(stats['errors'])}")
        check("并发读写无 locked 错误", stats["locked"] == 0, str(stats["locked"]))
        check("并发读写无其他错误", not stats["errors"], str(stats["errors"][:2]))
        check("读写都真的执行了（不是被整体跳过）",
              stats["read"] > 0 and stats["write"] > 0, str(stats))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

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

