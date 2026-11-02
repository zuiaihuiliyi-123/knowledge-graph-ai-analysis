"""阶段 5'：向量索引增量更新（只补缺失、只删过期，不再整篇重建）。

背景：`ensure_index` 原先是「kp_id 集合不一致 → build_index（清空 + 全量重算）」。
教师改一个知识点、或融合 apply/revoke 让少数节点进出，都会触发**整篇文档**的
embedding API 调用 —— 这是整条 RAG 链路里唯一的真实计费点。

本脚本守住：
1. 首次建索引：正常全量写入；
2. **集合无变化时不调用 embedding**（此前会判定 epoch/集合而未重建，但重建路径仍要防回归）；
3. 新增一个 kp：**只嵌入这一个**，不是整篇；
4. 删除一个 kp：只删该行，**不调用 embedding**；
5. 增删同时发生：两边都只处理差集；
6. 增量结果与「全量重建」完全一致（同样的 kp_id → 同样的向量）；
7. 未配置 embedding 时不写库、返回全 0。

运行：cd backend && python test_embedding_incremental.py
（临时库 + 假 embedder，不调外部 API、不连 Neo4j）
"""
import os
import shutil
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="kg_embed_inc_")
os.environ["SQLITE_DB_PATH"] = os.path.join(_TMP, "app.db")
os.environ["AUTO_BACKUP"] = "0"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

from app.core.sql_database import sql_db                        # noqa: E402
from app.services import embedding as E                         # noqa: E402
from app.services.embedding import KnowledgeEmbedder            # noqa: E402

CID, DOC = 960001, 960002
CID2, DOC2 = 960003, 960004
failures = []
total = 0
STATE = {"current": set(), "records": {}}


def check(label, ok, detail=""):
    global total
    total += 1
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


class FakeEmbedder:
    """确定性假 embedder：向量 = [文本长度, 首字符码点]，并记录每次调用收到的文本。"""

    def __init__(self):
        self.available = True
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [[float(len(t)), float(ord(t[0]) if t else 0)] for t in texts]


def make_embedder():
    ke = KnowledgeEmbedder()
    ke.embedding = FakeEmbedder()
    return ke


def seed(ids):
    STATE["current"] = set(ids)
    STATE["records"] = {
        k: {"kp_id": k, "name": f"名称{k}", "category": "概念", "description": f"描述{k}"}
        for k in ids
    }


def rows(doc=DOC):
    return {r["kp_id"] for r in sql_db.get_embeddings_by_document(CID, doc)}


def main() -> int:
    sql_db.init_tables()

    # 让向量清单完全由测试控制（不依赖 Neo4j）
    KnowledgeEmbedder._current_kp_ids = classmethod(lambda cls, c, d: set(STATE["current"]))
    KnowledgeEmbedder._load_kp_records = classmethod(
        lambda cls, c, d: [dict(v) for v in STATE["records"].values()])
    KnowledgeEmbedder._fusion_epoch_changed = staticmethod(lambda c, d: False)

    print("Step 1: 首次建索引（全量写入）")
    ke = make_embedder()
    seed({"kp1", "kp2", "kp3"})
    got = ke.ensure_index(CID, DOC)
    check("返回全部向量行", len(got) == 3, str(len(got)))
    check("embedding 被调用 1 次", len(ke.embedding.calls) == 1, str(len(ke.embedding.calls)))
    check("写入 3 行", rows() == {"kp1", "kp2", "kp3"}, str(sorted(rows())))

    print("\nStep 2: 集合无变化 → 不调用 embedding")
    calls_before = len(ke.embedding.calls)
    ke.ensure_index(CID, DOC)
    check("embedding 调用次数不变", len(ke.embedding.calls) == calls_before,
          f"{calls_before} -> {len(ke.embedding.calls)}")
    check("向量行数不变", len(rows()) == 3, str(len(rows())))

    print("\nStep 3: 新增一个 kp → 只嵌入这一个")
    seed({"kp1", "kp2", "kp3", "kp4"})
    calls_before = len(ke.embedding.calls)
    ke.ensure_index(CID, DOC)
    new_texts = ke.embedding.calls[-1] if len(ke.embedding.calls) > calls_before else []
    check("新增一次 embedding 调用", len(ke.embedding.calls) == calls_before + 1,
          f"{calls_before} -> {len(ke.embedding.calls)}")
    check("这次只嵌入了 1 条", len(new_texts) == 1, str(len(new_texts)))
    check("嵌入的是新增的那个知识点", "名称kp4" in (new_texts[0] if new_texts else ""),
          str(new_texts)[:80])
    check("向量共 4 行", rows() == {"kp1", "kp2", "kp3", "kp4"}, str(sorted(rows())))

    print("\nStep 4: 删除一个 kp → 只删行，不调用 embedding")
    seed({"kp1", "kp3", "kp4"})
    calls_before = len(ke.embedding.calls)
    ke.ensure_index(CID, DOC)
    check("embedding 调用次数不变", len(ke.embedding.calls) == calls_before,
          f"{calls_before} -> {len(ke.embedding.calls)}")
    check("kp2 的向量已删除", rows() == {"kp1", "kp3", "kp4"}, str(sorted(rows())))

    print("\nStep 5: 增删同时发生 → 两边都只处理差集")
    seed({"kp1", "kp4", "kp5"})       # 删 kp3，增 kp5
    calls_before = len(ke.embedding.calls)
    ke.ensure_index(CID, DOC)
    new_texts = ke.embedding.calls[-1] if len(ke.embedding.calls) > calls_before else []
    check("只嵌入新增的 1 条", len(new_texts) == 1 and "名称kp5" in (new_texts[0] if new_texts else ""),
          str(new_texts)[:80])
    check("最终集合正确", rows() == {"kp1", "kp4", "kp5"}, str(sorted(rows())))

    print("\nStep 6: 增量结果与全量重建一致")
    seed({"kp1", "kp4", "kp5"})
    ke.build_index(CID2, DOC2)        # 全量重建到另一个作用域
    inc = {r["kp_id"]: r["embedding"] for r in sql_db.get_embeddings_by_document(CID, DOC)}
    full = {r["kp_id"]: r["embedding"] for r in sql_db.get_embeddings_by_document(CID2, DOC2)}
    check("kp_id 集合一致", set(inc) == set(full), f"{sorted(inc)} vs {sorted(full)}")
    check("向量数值一致", inc == full, "向量不一致")

    print("\nStep 7: 未配置 embedding → 不写库、返回全 0")
    ke2 = make_embedder()
    ke2.embedding.available = False
    seed({"kp1", "kp4", "kp5", "kp9"})
    result = ke2.sync_index(CID, DOC)
    check("返回全 0", result == {"added": 0, "removed": 0, "kept": 0}, str(result))
    check("未新增 kp9 的向量", "kp9" not in rows(), str(sorted(rows())))

    print(f"\n通过 {total - len(failures)}/{total}" if not failures
          else f"\n失败 {len(failures)}/{total}：{failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
