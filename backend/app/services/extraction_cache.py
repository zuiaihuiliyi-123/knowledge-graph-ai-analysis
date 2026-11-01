"""抽取结果缓存（阶段 5'）：同一段文本 + 同一套 prompt/模型/温度，只调用一次 LLM。

为什么值得做：知识抽取是整条链路里**最贵**的一步（每分块一次 LLM 调用）。而它会被
反复触发在同一份文本上——

- 教师点「重试」（抽取成功、建图失败的情况：修复后重跑不该再花一遍 LLM 费用）；
- 同一份文件被重复上传（批量路径有 sha256 去重，但跨文档/换课程仍会重抽）；
- 开发期反复跑 `test_extraction.py` / 评测脚本。

缓存键把「会影响输出的输入」全部包含进去：**prompt 指纹 + 模型名 + 温度 + 文本**。
因此改 Prompt 或换模型时会自动失效，不会拿旧结果冒充新 Prompt 的效果。

边界（刻意保守）：
- **只缓存成功结果**：失败（限流/截断/结构非法）不写缓存，否则一次抖动会被固化；
- 键里带温度：temperature=0 的评测与 0.15 的日常抽取互不污染；
- 命中只返回结构化结果，不重复做 JSON 修复与重试；
- 目录可用 `EXTRACT_CACHE=0` 整体关闭（评测对照/排障时使用）。
"""
import hashlib
import json
import logging
import os
import tempfile

from ..core.config import settings

_logger = logging.getLogger(__name__)

# 负缓存不做：失败不写盘。命中统计只在进程内累计，供脚本/排障观察。
_stats = {"hit": 0, "miss": 0, "store": 0}


def enabled() -> bool:
    return bool(getattr(settings, "EXTRACT_CACHE", True))


def _cache_dir() -> str:
    return str(getattr(settings, "EXTRACT_CACHE_DIR", "") or "")


def build_key(text: str, model: str, temperature, prompt_fingerprint: str) -> str:
    """缓存键：prompt 指纹 + 模型 + 温度 + 文本，任何一项变化都会换键。"""
    h = hashlib.sha256()
    h.update((prompt_fingerprint or "").encode("utf-8"))
    h.update(b"\x00" + (model or "").encode("utf-8"))
    h.update(b"\x00" + str(temperature).encode("utf-8"))
    h.update(b"\x00" + (text or "").encode("utf-8"))
    return h.hexdigest()


def load(key: str):
    """读缓存；未命中/不可用/内容损坏一律返回 None（并计入 miss）。"""
    if not enabled() or not key:
        return None
    path = os.path.join(_cache_dir(), f"{key}.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict) or "entities" not in payload:
            return None
        _stats["hit"] += 1
        return payload
    except FileNotFoundError:
        _stats["miss"] += 1
        return None
    except Exception as exc:  # noqa: BLE001 - 缓存损坏不能让抽取失败
        _logger.warning("抽取缓存读取失败（按未命中处理）%s: %s", path, exc)
        _stats["miss"] += 1
        return None


def store(key: str, payload: dict) -> None:
    """写缓存（原子写：先写临时文件再替换，避免并发读到半截 JSON）。"""
    if not enabled() or not key or not isinstance(payload, dict):
        return
    if payload.get("error"):
        return                      # 只缓存成功结果
    directory = _cache_dir()
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, os.path.join(directory, f"{key}.json"))
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        _stats["store"] += 1
    except Exception as exc:  # noqa: BLE001 - 写缓存失败不影响抽取本身
        _logger.warning("抽取缓存写入失败: %s", exc)


def stats() -> dict:
    """进程内命中统计 + 磁盘条目数（供脚本/排障使用）。"""
    entries = 0
    directory = _cache_dir()
    try:
        entries = len([n for n in os.listdir(directory) if n.endswith(".json")])
    except OSError:
        entries = 0
    return {**_stats, "entries": entries, "dir": directory}


def clear() -> int:
    """清空缓存目录，返回删除的文件数（评测/排障用）。"""
    directory = _cache_dir()
    removed = 0
    try:
        for name in os.listdir(directory):
            if not name.endswith(".json"):
                continue
            try:
                os.remove(os.path.join(directory, name))
                removed += 1
            except OSError:
                pass
    except OSError:
        return 0
    return removed
