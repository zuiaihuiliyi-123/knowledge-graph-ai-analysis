"""文档处理任务的中间产物存储（阶段 4：断点续跑）。

为什么需要：解析（PDF 提取）与抽取（LLM 调用）是两个昂贵阶段，而任务只活在进程内。
进程一旦重启，旧实现只能把文档标成 FAILED，教师重试就要**从零再解析、再花钱调 LLM**。
这里把两段产物落到 `TASK_STATE_DIR/<doc_id>/`：

| 文件 | 内容 | 复用后省掉 |
|---|---|---|
| `parsed.txt` | 解析出的全文 | 重新解析 PDF/DOCX |
| `extracted.json` | `{entities, relations}` | 重新调用 LLM（真金白银） |

一致性：`meta.json` 记录 `sha256`（文档内容指纹）与产物格式版本。文件被替换、
或产物由旧版本代码写出时，一律视为**不可复用**——宁可重跑，也不要拿错文本建图。

清理：整个流水线成功后清目录；`retry_document`（教师显式重试）也会先清，
保证「重试 = 真的重来」，而「启动恢复」才是复用产物接着跑。
"""
import hashlib
import json
import logging
import os
import shutil

from ..core.config import settings

_logger = logging.getLogger(__name__)

# 产物格式版本：改动解析/抽取产物结构时 +1，旧产物自动失效
ARTIFACT_VERSION = 1


def _doc_dir(doc_id) -> str:
    return os.path.join(settings.TASK_STATE_DIR, str(int(doc_id)))


def _path(doc_id, name: str) -> str:
    return os.path.join(_doc_dir(doc_id), name)


def _sha8(sha256: str | None) -> str:
    return (sha256 or "")[:8]


def _write_meta(doc_id, sha256: str | None, artifacts: list[str]) -> None:
    os.makedirs(_doc_dir(doc_id), exist_ok=True)
    with open(_path(doc_id, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({
            "version": ARTIFACT_VERSION,
            "sha256": sha256 or "",
            "artifacts": artifacts,
        }, f, ensure_ascii=False)


def _read_meta(doc_id) -> dict | None:
    try:
        with open(_path(doc_id, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or meta.get("version") != ARTIFACT_VERSION:
        return None
    return meta


def _meta_matches(doc_id, sha256: str | None) -> bool:
    """产物是否属于**这份文件内容**（sha256 一致）。

    没有 sha256 的历史行（早期上传没算指纹）一律不复用：无法证明产物与文件对应。
    """
    if not sha256:
        return False
    meta = _read_meta(doc_id)
    return bool(meta) and meta.get("sha256") == sha256


def save_parsed(doc_id, sha256: str | None, text: str) -> str | None:
    """落盘解析文本；失败只记日志（产物是可选的加速手段，不能因此让任务失败）。"""
    if not text:
        return None
    try:
        os.makedirs(_doc_dir(doc_id), exist_ok=True)
        path = _path(doc_id, "parsed.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        _write_meta(doc_id, sha256, ["parsed"])
        return path
    except OSError as exc:
        _logger.warning("解析产物落盘失败 doc_id=%s: %s", doc_id, exc)
        return None


def load_parsed(doc_id, sha256: str | None) -> str | None:
    if not _meta_matches(doc_id, sha256):
        return None
    path = _path(doc_id, "parsed.txt")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    return text if text.strip() else None


def save_extraction(doc_id, sha256: str | None, entities: list, relations: list) -> str | None:
    if not entities:
        return None
    try:
        os.makedirs(_doc_dir(doc_id), exist_ok=True)
        path = _path(doc_id, "extracted.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"entities": entities, "relations": relations}, f, ensure_ascii=False)
        _write_meta(doc_id, sha256, ["parsed", "extracted"])
        return path
    except (OSError, TypeError, ValueError) as exc:
        _logger.warning("抽取产物落盘失败 doc_id=%s: %s", doc_id, exc)
        return None


def load_extraction(doc_id, sha256: str | None) -> dict | None:
    if not _meta_matches(doc_id, sha256):
        return None
    path = _path(doc_id, "extracted.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    entities = data.get("entities") or []
    relations = data.get("relations") or []
    if not entities:
        return None
    return {"entities": entities, "relations": relations}


def available(doc_id, sha256: str | None) -> list[str]:
    """当前可复用的产物名（供启动恢复决策与排障）。"""
    names = []
    if load_parsed(doc_id, sha256):
        names.append("parsed")
    if load_extraction(doc_id, sha256):
        names.append("extracted")
    return names


def clear(doc_id) -> None:
    shutil.rmtree(_doc_dir(doc_id), ignore_errors=True)


def fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
