"""SQLite 连接、pragma、备份与基础查询。"""

import array
import logging
import os
import sqlite3
from datetime import datetime
from ..core.config import settings

_logger = logging.getLogger(__name__)

# ---------- 阶段 G：SQLite 并发 pragma ----------
# `journal_mode=WAL`：读不阻塞写、写不阻塞读（只有写-写串行）。
#   为什么需要：本项目部署形态是「一个 uvicorn 进程 + 若干 CLI 脚本/评测」共同写
#   同一个 `app.db`（`_uvicorn.log` 长期有进程在写就是证据）。rollback journal 模式下
#   写者持**全库排它锁**，两进程同时写极易撞上 `database is locked`。
#   WAL 是**持久属性**（写进库文件头），故只需在 init_tables 末端设置一次。
# `busy_timeout`：写者互斥时**等待**而非立刻报错。`sqlite3.connect` 的默认 timeout=5s
#   不够——L2 引入的批量写入（121 条向量 / 全量替换关联行 / ≤5000 行 BLOB 回填）
#   单次持锁就会超过它。该属性**每连接**生效，故在 `_connect()` 里设置。
# 回退开关：`SQLITE_JOURNAL_MODE=delete` 回到旧行为；`SQLITE_BUSY_TIMEOUT=0` 关闭等待。
JOURNAL_MODE = (os.getenv("SQLITE_JOURNAL_MODE", "WAL") or "").strip().upper()
BUSY_TIMEOUT_MS = int(os.getenv("SQLITE_BUSY_TIMEOUT", "15000"))

def _vec_to_blob(vec) -> bytes:
    """向量 → **float32 小端 BLOB**（与 numpy `np.frombuffer(blob, "<f4")` 对齐）。

    用标准库 `array` 而不是 numpy：core 层不引入重依赖（与 embedding.py 的既有取舍一致）。
    """
    return array.array("f", [float(x) for x in vec]).tobytes()


def _blob_to_vec(blob) -> list:
    """BLOB → list[float]（float32 精度）。用于无 numpy 环境的回落路径与自检。"""
    a = array.array("f")
    a.frombytes(bytes(blob))
    return list(a)

def _now() -> str:
    """返回当前本地时间字符串（MySQL DATETIME 兼容格式）"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

class ConnectionMixin:
    """连接、pragma、备份与基础查询（原 SQLDatabase 的基础设施部分）。"""


    def __init__(self, db_path: str = None):
        # 默认仍取 settings.SQLITE_DB_PATH；显式传参便于测试/脚本注入临时库。
        self.db_path = db_path or settings.SQLITE_DB_PATH
        # 确保数据库文件所在目录存在
        parent = os.path.dirname(os.path.abspath(self.db_path))
        os.makedirs(parent, exist_ok=True)


    def _connect(self) -> sqlite3.Connection:
        """新建连接：外键约束 + 行字典工厂 + 阶段 G 的 `busy_timeout`"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if BUSY_TIMEOUT_MS > 0:
            # 写者互斥时等待而不是立刻抛 "database is locked"。该 pragma 是**连接级**的，
            # 每连接都要设（journal_mode 则是库级持久属性，见 _apply_journal_mode）。
            conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        return conn


    def _apply_journal_mode(self) -> dict:
        """设置库级持久 pragma `journal_mode`（阶段 G），返回**事实**供启动自查/验收。

        设计要点：
        - **只在 init_tables 末端调用一次**：WAL 是持久属性，无需每连接设置；
          且它要求「当前没有其他写事务」，放在建表/迁移之后最容易一次成功。
        - **失败不抛异常**：某些文件系统（网络盘）不支持 WAL，或并发下正有写事务。
          退回 rollback journal 只是并发能力差一些，**不影响正确性**，
          故记录状态后继续 —— 但状态会被保留（`pragma_status()`），不静默丢弃。
        """
        state = {"requested": JOURNAL_MODE, "actual": None, "error": None}
        if not JOURNAL_MODE:
            return state
        conn = self._connect()
        try:
            row = conn.execute(f"PRAGMA journal_mode = {JOURNAL_MODE}").fetchone()
            state["actual"] = row[0] if row else None
        except Exception as e:
            state["error"] = f"{type(e).__name__}: {e}"
        finally:
            conn.close()
        return state


    def pragma_status(self) -> dict:
        """当前连接 pragma 事实（阶段 G 自查用）：journal_mode 的请求值/实际值 + busy_timeout。"""
        state = dict(getattr(self, "journal_state", {}) or {})
        state.setdefault("requested", JOURNAL_MODE)
        state["busy_timeout_ms"] = BUSY_TIMEOUT_MS
        return state


    def backup_to(self, dst_path) -> dict:
        """**WAL 安全的**在线快照备份（用 SQLite 官方 backup API），返回备份事实。

        为什么不能用 `shutil.copy2(app.db)`（阶段 G 之前 `scripts/backup_migration.py`
        就是这么做的，本方法替换它）——**这是启用 WAL 的前置条件**：

        - WAL 模式下**最近提交可能还留在 `app.db-wal`** 里，只拷主文件会得到
          「**静默少数据**」的备份：不报错、事后才发现；
        - 若拷贝恰逢 checkpoint 进行中，还可能拿到**内部不一致**的快照。

        而文档承诺的回滚方式正是「恢复 `backend/backup/*.bak`」——
        备份静默失效等于**回滚路径静默失效**，所以这件事必须与 WAL 同时落地。

        `sqlite3.Connection.backup()` 由 SQLite 保证「在写事务边界上取一致快照」，
        且**不阻塞写者**（无需停服）。备份文件会把 journal_mode 复位为 DELETE，
        使其**单文件自包含**（不必连 `-wal` 一起拷）。

        返回 `{path, bytes, tables, rows, source_journal_mode, journal_reset}`，
        `tables` 是逐表行数 —— 便于把备份与线上逐表核对（本次改动就据此核对）。
        """
        dst_path = str(dst_path)
        os.makedirs(os.path.dirname(os.path.abspath(dst_path)), exist_ok=True)
        tables, journal_reset = {}, False
        src = sqlite3.connect(self.db_path)
        try:
            dst = sqlite3.connect(dst_path)
            try:
                src.backup(dst)                      # 一致快照；WAL 内容一并落进目标文件
                names = [r[0] for r in dst.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
                ).fetchall()]
                for name in names:
                    tables[name] = dst.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                try:
                    # 复位为 rollback journal → 备份文件自包含（不留 -wal 边车）
                    dst.execute("PRAGMA journal_mode = DELETE")
                    dst.commit()
                    journal_reset = True
                except Exception:
                    dst.rollback()
            finally:
                dst.close()
        finally:
            src.close()
        conn = sqlite3.connect(self.db_path)          # 源库当前 journal 模式（写入事实便于核对）
        try:
            row = conn.execute("PRAGMA journal_mode").fetchone()
            source_mode = row[0] if row else None
        finally:
            conn.close()
        return {"path": dst_path, "bytes": os.path.getsize(dst_path), "tables": tables,
                "rows": sum(tables.values()), "source_journal_mode": source_mode,
                "journal_reset": journal_reset}


    # ---------- 启动前自动备份 ----------

    _AUTO_BACKUP_SUBDIR = "auto"


    def auto_backup(self, keep: int | None = None) -> dict | None:
        """启动时自动留一份库快照（轮转保留最近 N 份）；跳过或失败返回 None。

        为什么放在 `init_tables()` **之前**：启动会跑一串幂等迁移（补列 / 回填 /
        重建表），迁移本身是写操作；再加上本项目的开发库曾被 `git checkout`
        静默覆盖过一次，「最近一次启动前的库」是唯一既新鲜又廉价可得的回退点。

        三条刻意的取舍：
        1. 库不存在或 0 字节 → 直接跳过，不制造一堆空备份；
        2. 备份失败只 warning 不抛异常 —— 备份是旁路，不能成为启动的新故障点
           （与 `main.py` 里请求指标采集的取舍一致）；
        3. 复用 `backup_to()` 的官方 backup API，不裸拷 `.db`：WAL 下裸拷会静默少数据。

        返回 `backup_to()` 的事实字典，便于启动日志/自检核对（bytes/tables/rows）。
        """
        if not getattr(settings, "AUTO_BACKUP", True):
            return None
        keep = settings.AUTO_BACKUP_KEEP if keep is None else keep
        try:
            if not os.path.exists(self.db_path) or os.path.getsize(self.db_path) == 0:
                return None
            out_dir = os.path.join(settings.BACKUP_DIR, self._AUTO_BACKUP_SUBDIR)
            os.makedirs(out_dir, exist_ok=True)
            # 带毫秒，避免同一秒内连续启动（脚本/测试）互相覆盖
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            dst = os.path.join(out_dir, f"app.db.{stamp}.bak")
            fact = self.backup_to(dst)
            self._rotate_auto_backups(out_dir, keep)
            _logger.info("启动前自动备份完成：%s（%s bytes，%s 张表）",
                         dst, fact["bytes"], len(fact["tables"]))
            return fact
        except Exception as exc:  # noqa: BLE001 - 备份失败不得阻塞启动
            _logger.warning("启动前自动备份失败（不影响启动）：%s", exc)
            return None


    @staticmethod
    def _rotate_auto_backups(out_dir: str, keep: int) -> None:
        """只保留最近 `keep` 份自动备份（按修改时间倒序）；keep <= 0 不做轮转。"""
        if keep <= 0:
            return
        try:
            files = [os.path.join(out_dir, n) for n in os.listdir(out_dir) if n.endswith(".bak")]
        except OSError as exc:
            _logger.warning("扫描自动备份目录失败：%s", exc)
            return
        files.sort(key=os.path.getmtime, reverse=True)
        for old in files[keep:]:
            try:
                os.remove(old)
            except OSError as exc:
                _logger.warning("清理旧自动备份失败：%s (%s)", old, exc)


    def _execute(self, sql: str, params: tuple = ()) -> int:
        """执行写操作，返回 lastrowid（INSERT 时的自增主键）"""
        with self._connect() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid


    def _query(self, sql: str, params: tuple = ()) -> list:
        """执行查询，返回字典列表"""
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]


    def _query_one(self, sql: str, params: tuple = ()) -> dict:
        """执行查询，返回单行字典；无结果返回 None"""
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None
