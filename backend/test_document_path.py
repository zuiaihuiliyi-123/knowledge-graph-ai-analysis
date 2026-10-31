"""阶段 2 数据一致性：t_document.file_path 改为相对 UPLOAD_DIR，并兼容历史三种形态。

背景：`file_path` 原先存**绝对路径**，app.db 随备份/仓库换机器后全部失效——
预览、融合上下文重建、删除本地文件都会找不到文件（`test_fusion_context_rebuild` 即因此失败）。

本脚本守住：
1. 新上传写入的是**相对 UPLOAD_DIR** 的路径（不含盘符、不依赖机器）；
2. 该路径能被 `resolve_document_path` 解析成本机存在的绝对路径；
3. 历史形态仍可解析：其他机器绝对路径 / 相对 backend/ 的路径 → 按上传约定回退定位；
4. 文件确实缺失时解析器返回原值（由调用方报 2003），不会伪装成功；
5. 端到端：HTTP 读文档内容可用（走 resolver，不再直接拼 file_path）。

运行：cd backend && python test_document_path.py
（临时库 + 临时 UPLOAD_DIR，不触碰真实 app.db 与真实上传目录）
"""
import asyncio
import os
import shutil
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="kg_docpath_")
os.environ["SQLITE_DB_PATH"] = os.path.join(_TMP, "app.db")
os.environ["UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["AUTO_BACKUP"] = "0"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient                      # noqa: E402

from app.core.config import settings                           # noqa: E402
from app.core.security import hash_password                    # noqa: E402
from app.core.sql_database import sql_db                       # noqa: E402
from app.core.storage import document_relative_path, resolve_document_path  # noqa: E402
from app.main import app                                       # noqa: E402
from app.services.document_service import DocumentService      # noqa: E402

PASSWORD = "pw123456"
CONTENT = "知识图谱测试文档：栈是后进先出的线性表。".encode("utf-8")
failures = []
total = 0


def check(label, ok, detail=""):
    global total
    total += 1
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    client = TestClient(app)
    with client:
        teacher_id = sql_db.create_user("dp_teacher", hash_password(PASSWORD), role="teacher")
        course_id = sql_db.create_course("文档路径回归课程", teacher_id)

        print("Step 1: 上传后库里存的是相对 UPLOAD_DIR 的路径")
        res = asyncio.run(DocumentService._persist_document(
            course_id, teacher_id, "讲义.txt", "TXT", CONTENT))
        check("落盘成功", res.get("ok") is True, str(res)[:160])
        doc_id = res.get("document_id")
        doc = sql_db.get_document(doc_id)
        raw = doc.get("file_path") or ""
        expected = document_relative_path(course_id, doc_id, "讲义.txt")
        check("file_path 不是绝对路径", not os.path.isabs(raw), raw)
        check("file_path 等于约定相对路径", raw == expected, f"{raw} != {expected}")

        print("\nStep 2: 新形态可被解析成本机存在的文件")
        resolved = resolve_document_path(doc)
        check("解析结果存在", bool(resolved) and os.path.exists(resolved), str(resolved))
        check("解析到当前 UPLOAD_DIR 下",
              resolved and resolved.startswith(os.path.abspath(settings.UPLOAD_DIR)),
              str(resolved))

        print("\nStep 3: 历史形态仍可解析（换机器回退）")
        foreign = f"C:\\Users\\20472\\PycharmProjects\\PythonProject1\\uploads\\{course_id}\\{doc_id}_讲义.txt"
        legacy_abs = dict(doc, file_path=foreign)
        got = resolve_document_path(legacy_abs)
        check("他机绝对路径 → 按上传约定回退命中", bool(got) and os.path.exists(got), str(got))

        legacy_rel = dict(doc, file_path=f"./data/uploads/{course_id}/{doc_id}_讲义.txt")
        got2 = resolve_document_path(legacy_rel)
        check("相对 backend/ 旧路径 → 回退命中", bool(got2) and os.path.exists(got2), str(got2))

        print("\nStep 4: 文件确实缺失时返回原值（不伪装成功）")
        real_path = resolve_document_path(doc)
        os.remove(real_path)
        missing = resolve_document_path(doc)
        check("解析器返回原值", missing == raw, str(missing))
        check("原值指向的文件确实不存在", not os.path.exists(missing or ""), str(missing))

        # 复原文件，供端到端读取
        os.makedirs(os.path.dirname(real_path), exist_ok=True)
        with open(real_path, "wb") as f:
            f.write(CONTENT)

        print("\nStep 5: 端到端读取（HTTP 走 resolver）")
        token = client.post("/api/auth/login", json={
            "username": "dp_teacher", "password": PASSWORD}).json()["data"]["access_token"]
        resp = client.get(f"/api/v1/documents/{doc_id}/content",
                          headers={"Authorization": f"Bearer {token}"})
        check("读文档内容返回 200", resp.status_code == 200, f"HTTP {resp.status_code}")
        check("内容与上传一致", resp.content == CONTENT, f"len={len(resp.content)}")

    print(f"\n通过 {total - len(failures)}/{total}" if not failures
          else f"\n失败 {len(failures)}/{total}：{failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
