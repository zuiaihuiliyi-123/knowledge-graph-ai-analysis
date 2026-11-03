"""
智能问答服务：基于知识图谱 + 真向量 RAG 检索

检索链路：问题 embedding → 与课程知识点向量做余弦相似度 → top_k 上下文 → LLM 生成。
未配置 embedding key 或向量检索失败时，自动退回关键词检索（保证功能可用）。

阶段 G（async 修复）：本模块的检索与 LLM 调用都是**同步阻塞**的
（openai 同步客户端 / Neo4j 同步驱动 / SQLite），故 `ask()` 把整段逻辑放进
`asyncio.to_thread` 执行，避免阻塞事件循环（见 `ask()` 的说明）。
"""
import asyncio
import logging
import math
import threading
from typing import List

from openai import OpenAI

from ..core.config import settings
from ..core.database import db
from ..core.metrics import track_llm_call
from .embedding import EmbeddingClient, KnowledgeEmbedder
from .fusion.rag_bridge import RagFusionView
from .vector_index import vector_index

_logger = logging.getLogger(__name__)


QA_SYSTEM_PROMPT = """你是一个课程学习助手。请基于提供的课程知识图谱内容回答学生的问题。

## 规则
1. 优先使用提供的知识内容回答问题
2. 如果知识库中没有相关信息，请诚实告知，不要编造
3. 回答要准确、简洁、易于理解
4. 如果合适，可以推荐相关的知识点供进一步学习

## 课程知识内容
{context}

请根据以上内容回答学生的问题。"""


def _coerce_course_id(course_id):
    """course_id 统一为整数（对齐 Neo4j 存储类型）；空值返回 None 表示不过滤"""
    if course_id is None or course_id == "":
        return None
    try:
        return int(course_id)
    except (TypeError, ValueError):
        return None


def _coerce_document_id(document_id):
    """document_id 统一为整数（对齐 Neo4j 存储类型）；空值返回 None 表示不过滤"""
    if document_id is None or document_id == "":
        return None
    try:
        return int(document_id)
    except (TypeError, ValueError):
        return None


def _cosine(a: List[float], b: List[float], norm_a: float = None) -> float:
    """余弦相似度（纯 Python 实现，避免引入 numpy 重依赖）

    norm_a：a 的 L2 范数。批量比较（如对一个查询向量排序整个索引）时 a 固定不变，
    传入可省掉每次都重算 a 的范数——1024 维下这是相当可观的一笔重复计算。
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) if norm_a is None else norm_a
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _format_node(node) -> str:
    """格式化知识点为上下文/来源文本。

    若该知识点吸收过被融合的旧名称，**一并带上** —— 否则会出现
    「图谱上只有『广度优先搜索』、问答却对『BFS』闭口不谈」这种不一致，
    而学生问的恰恰可能是那个旧名称。
    """
    ctx = f"[{node.get('category', '知识点')}] {node.get('name', '')}"
    aliases = node.get("fused_aliases") or []
    if aliases:
        ctx += f"（又称：{'、'.join(aliases)}）"
    if node.get('description'):
        ctx += f": {node.get('description')}"
    return ctx


# 流式问答的保活间隔（秒）：超过这个时间没有任何输出，就发一个 ping 帧。
# 检索首次要建索引、模型也可能长时间不出 token，静默连接容易被代理直接掐断。
_HEARTBEAT_SECONDS = 10


def _close_quietly(stream) -> None:
    """关闭上游流（客户端断开时止损）。收尾阶段的任何异常都不该影响调用方。"""
    close = getattr(stream, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:  # noqa: BLE001 - 连接可能已被对端关闭
        pass


def _drain_task_exception(task) -> None:
    """取走后台任务的异常，避免客户端提前断开后出现
    「Task exception was never retrieved」这类噪音日志。"""
    if task.cancelled():
        return
    try:
        task.exception()
    except Exception:  # noqa: BLE001 - 已被取消/已取走
        pass


class QAService:
    """智能问答服务"""

    def __init__(self):
        self.client = OpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_API_BASE,
        )
        self.embedder = EmbeddingClient()
        self.indexer = KnowledgeEmbedder()

    # ---------- 向量检索 ----------

    @staticmethod
    def _node_dict(n, fused_aliases=None) -> dict:
        """Neo4j 节点 → 结构化引用（供前端"证据链"展示）。

        `fused_aliases`：该节点通过文档级融合吸收的旧名称。前端来源卡片据此
        与图谱视图保持一致；没有融合时不带这个字段（保持既有响应逐字节兼容）。
        """
        d = {
            "kp_id": n.get("kp_id"),
            "name": n.get("name", ""),
            "category": n.get("category", ""),
            "description": n.get("description", ""),
        }
        if fused_aliases:
            d["fused_aliases"] = list(fused_aliases)
        return d

    @staticmethod
    def _fold_to_root(records, fusion, course_id, document_id) -> list:
        """把命中的节点按融合映射折到规范节点，返回去重后的节点字典列表。

        关键词检索走的是 `n.name CONTAINS $keyword`，**会命中已被折掉的源节点名**
        （向量索引的过滤挡不住这条路径）。若不改写，学生搜「BFS」拿到的会是
        图谱上已经不存在的那个节点 —— 问答与图谱就对不上了。
        """
        roots, by_id = [], {}
        for rec in records:
            n = rec.get("n")
            if not n:
                continue
            kp_id = n.get("kp_id")
            root = fusion.root_of(kp_id)
            if root not in by_id:
                roots.append(root)
                by_id[root] = n if root == kp_id else None

        missing = [k for k, v in by_id.items() if v is None]
        if missing:
            extra = db.query(
                "MATCH (n:KnowledgePoint {course_id: $cid, document_id: $did}) "
                "WHERE n.kp_id IN $ids RETURN n",
                {"cid": course_id, "did": document_id, "ids": missing})
            for rec in extra:
                n = rec.get("n")
                if n and n.get("kp_id") in by_id:
                    by_id[n["kp_id"]] = n

        out = []
        for root in roots:
            n = by_id.get(root)
            if n:
                out.append(QAService._node_dict(n, fusion.aliases_of(root)))
        return out

    def _vector_search(self, question: str, course_id, document_id, top_k: int) -> List[dict]:
        """真向量检索：问题与文档知识点 embedding 做余弦相似度排序，返回结构化节点列表"""
        cid = _coerce_course_id(course_id)
        did = _coerce_document_id(document_id)
        if cid is None or did is None:
            return []  # 缺少文档作用域，退回关键词
        try:
            self.indexer.ensure_index(cid, did)
            q_vec = self.embedder.embed([question])[0]
        except Exception as e:
            # embedding 不可用（未配置 key / 网络异常等）退回关键词检索。
            # 这里必须留下日志：静默吞掉会让「向量检索整条失效」伪装成「知识库为空」，
            # 排查时无从下手。
            _logger.warning("向量检索不可用，本次退回关键词检索: %s", e, exc_info=True)
            return []

        # L2 阶段 E：检索改走 VectorIndex（numpy 矩阵 + 进程内缓存），
        # 不再每次「全量读库 + 逐条纯 Python 余弦」；无向量时返回空 → 上层退回关键词检索
        ranked = vector_index.kp_search(cid, did, q_vec, top_k)
        if not ranked:
            return []

        # 按 kp_id 回查节点元数据，拼接上下文（限定文档）
        fusion = RagFusionView(cid, did)
        # 索引构建时已排除被融合的源节点，这里再折一次是**防御性**的：
        # 万一拿到的是尚未重建的旧索引（跨进程缓存 / 刚 apply 完），
        # 也不能让图谱上已经不存在的节点重新冒到问答上下文里。
        wanted = [(fusion.root_of(kp_id), score) for kp_id, score in ranked]
        kp_ids = sorted({k for k, _ in wanted})

        nodes = {}
        recs = db.query(
            "MATCH (n:KnowledgePoint {course_id: $cid, document_id: $did}) "
            "WHERE n.kp_id IN $ids RETURN n",
            {"cid": cid, "did": did, "ids": kp_ids},
        )
        for rec in recs:
            n = rec.get("n")
            if n:
                nodes[n.get("kp_id")] = n

        out, seen = [], set()
        for kp_id, _score in wanted:
            if kp_id in seen or kp_id not in nodes:
                continue
            seen.add(kp_id)
            out.append(self._node_dict(nodes[kp_id], fusion.aliases_of(kp_id)))
            if len(out) >= top_k:
                break
        return out

    # ---------- 关键词检索（兜底） ----------

    def _keyword_search(self, question: str, course_id, document_id, top_k: int,
                        allowed_ids: List[int] = None) -> List[dict]:
        """关键词检索（向量不可用时的兜底），返回结构化节点列表。

        allowed_ids：调用方（当前用户）可访问的课程 id 列表。仅在「未指定 course_id」
        这条原本会全库扫描的分支上生效——把「不带课程参数」从全库泄漏收敛为
        「仅我的课程」。默认 None 表示不过滤，保证既有调用方行为逐字节不变。
        """
        cid = _coerce_course_id(course_id)
        did = _coerce_document_id(document_id)
        keyword = (question or "")[:20]

        # 未指定课程且用户没有任何可访问课程：直接返回空，避免退化成全库扫描
        if cid is None and allowed_ids is not None and len(allowed_ids) == 0:
            return []

        if cid is not None and did is not None:
            cypher = """
            MATCH (n:KnowledgePoint {course_id: $course_id, document_id: $document_id})
            WHERE n.name CONTAINS $keyword OR n.description CONTAINS $keyword
            RETURN n LIMIT $top_k
            """
            params = {"course_id": cid, "document_id": did, "keyword": keyword, "top_k": top_k}
        elif cid is not None:
            cypher = """
            MATCH (n:KnowledgePoint {course_id: $course_id})
            WHERE n.name CONTAINS $keyword OR n.description CONTAINS $keyword
            RETURN n LIMIT $top_k
            """
            params = {"course_id": cid, "keyword": keyword, "top_k": top_k}
        else:
            cypher = """
            MATCH (n:KnowledgePoint)
            WHERE (n.name CONTAINS $keyword OR n.description CONTAINS $keyword)
            """
            params = {"keyword": keyword, "top_k": top_k}
            if allowed_ids is not None:
                cypher += " AND n.course_id IN $allowed_course_ids\n"
                params["allowed_course_ids"] = list(allowed_ids)
            cypher += "RETURN n LIMIT $top_k"

        records = db.query(cypher, params)
        # 关键词检索会直接命中「已被融合折掉的源节点名」，这里按映射改写为规范节点，
        # 保证问答上下文与图谱上看到的完全一致。
        fusion = RagFusionView(cid, did)
        contexts = self._fold_to_root(records, fusion, cid, did)

        # 结果不足 top_k 时，用同文档节点兜底补足（**同样要排除已折掉的源节点**）
        if len(contexts) < top_k:
            existing_names = {c["name"] for c in contexts}
            fused = sorted(fusion.sources)
            if cid is not None and did is not None:
                records = db.query(
                    "MATCH (n:KnowledgePoint {course_id: $course_id, document_id: $document_id}) "
                    "WHERE NOT n.kp_id IN $fused RETURN n LIMIT $top_k",
                    {"course_id": cid, "document_id": did, "top_k": top_k, "fused": fused},
                )
            elif cid is not None:
                records = db.query(
                    "MATCH (n:KnowledgePoint {course_id: $course_id}) "
                    "WHERE NOT n.kp_id IN $fused RETURN n LIMIT $top_k",
                    {"course_id": cid, "top_k": top_k, "fused": fused},
                )
            else:
                fallback_cypher = "MATCH (n:KnowledgePoint)"
                fallback_params = {"top_k": top_k, "fused": fused}
                conditions = ["NOT n.kp_id IN $fused"]
                if allowed_ids is not None:
                    conditions.append("n.course_id IN $allowed_course_ids")
                    fallback_params["allowed_course_ids"] = list(allowed_ids)
                fallback_cypher += " WHERE " + " AND ".join(conditions)
                fallback_cypher += " RETURN n LIMIT $top_k"
                records = db.query(fallback_cypher, fallback_params)
            # 兜底分支同样走一遍折叠。Cypher 里已经有 `NOT n.kp_id IN $fused`，
            # 正常情况下不会被折掉的节点根本进不来；这里再折一次是**纵深的第二道**，
            # 避免"排除条件"和"映射表"万一不同步时把已折掉的节点漏进上下文。
            picked = [rec for rec in records
                      if rec.get("n") and rec["n"].get("name") not in existing_names]
            for d in self._fold_to_root(picked, fusion, cid, did):
                if d["name"] in existing_names:
                    continue
                contexts.append(d)
                if len(contexts) >= top_k:
                    break
        return contexts

    def search_related_nodes(self, question: str, course_id=None, document_id=None,
                             top_k: int = 5, allowed_ids: List[int] = None) -> List[dict]:
        """检索相关知识（优先向量，失败退回关键词），返回结构化节点（含 kp_id/name/category/description）"""
        nodes = self._vector_search(question, course_id, document_id, top_k)
        if not nodes:
            nodes = self._keyword_search(question, course_id, document_id, top_k, allowed_ids)
        return nodes

    def search_related_knowledge(self, question: str, course_id=None, document_id=None,
                                 top_k: int = 5, allowed_ids: List[int] = None) -> List[str]:
        """检索相关知识，返回格式化字符串（供 LLM 上下文 / 向后兼容）"""
        return [_format_node(n) for n in self.search_related_nodes(
            question, course_id, document_id, top_k, allowed_ids)]

    async def ask_with_sources(self, question: str, course_id=None, document_id=None,
                               allowed_ids: List[int] = None) -> dict:
        """回答问题（RAG 模式），返回 {answer, sources}。

        检索只跑一次，sources 就是本次真正喂给 LLM 的上下文——两者必然一致。
        需要引用来源时必须用这个方法，而不是 ask() 之后再自行检索一遍：那会让同一次
        提问把整条检索链路跑两遍（含两次外部 embedding 调用），且第二遍的结果可能
        与喂给 LLM 的上下文不同（例如期间索引被重建）。

        阶段 G（async 修复）——**为什么必须放线程池**：

        这条链路整段都是同步阻塞的：
          · `search_related_knowledge` → embedder 的 HTTP 调用、`VectorIndex` 的 SQLite 读、
            Neo4j 同步驱动查询；
          · `client.chat.completions.create` 是 **openai 同步客户端**，`QA_TIMEOUT` 量级是秒。
        在 `async def` 里直接跑会**占住整个事件循环**：一个学生提问期间，
        同进程内**所有**其他请求（包括别人的提问）都只能排队 —— 并发下体验直接塌掉。

        故把整段同步逻辑交给 `asyncio.to_thread`。这与项目内既有做法一致
        （`knowledge_extractor._extract_single` / `relation_completion._call_llm` 都这样处理），
        且**不改动任何业务逻辑与异常处理**（原 try/except 原样保留在同步实现里）。
        """
        return await asyncio.to_thread(self._ask_blocking, question, course_id,
                                       document_id, allowed_ids)

    def _ask_blocking(self, question: str, course_id=None, document_id=None,
                      allowed_ids: List[int] = None) -> dict:
        """`ask_with_sources()` 的同步实现体。**运行在线程池中，禁止在此使用 asyncio API。**"""
        # 1. 检索相关知识（向量优先，文档作用域）
        # 结构化节点既作喂给 LLM 的上下文，也作返回前端的引用来源——同一份数据，必然一致
        sources = self.search_related_nodes(question, course_id, document_id,
                                            allowed_ids=allowed_ids)
        contexts = [_format_node(n) for n in sources]
        context_text = "\n".join(contexts) if contexts else "暂无相关课程知识"

        # 2. 调用 LLM 生成回答
        try:
            with track_llm_call("qa"):
                response = self.client.chat.completions.create(
                    model=settings.LLM_MODEL,
                    messages=[
                        {"role": "system", "content": QA_SYSTEM_PROMPT.format(context=context_text)},
                        {"role": "user", "content": question},
                    ],
                    temperature=0.3,
                    max_tokens=1024,
                    timeout=settings.QA_TIMEOUT,
                )
            answer = response.choices[0].message.content.strip()
        except Exception as e:
            answer = f"抱歉，问答服务暂时不可用：{str(e)}"
        return {"answer": answer, "sources": sources}

    async def stream_answer(self, question: str, course_id=None, document_id=None,
                            allowed_ids: List[int] = None):
        """流式回答（SSE 用）：先给引用来源，再逐块给答案增量。

        yield 的事件：
          `{"event": "ping",    "data": {}}`                    ← 长时间无输出时的保活帧
          `{"event": "sources", "data": {"sources": [...]}}`
          `{"event": "delta",   "data": {"text": "…"}}`
          `{"event": "done",    "data": {"answer": "全文", "sources": [...],
                                        "finish_reason": "stop"}}`
          `{"event": "error",   "data": {"message": "…", "partial": "已生成的正文"}}`

        四条硬约束：
        1. **检索只跑一次**：与 `ask_with_sources` 同一入口，`sources` 就是真正喂给模型的上下文
           （不得为了拿来源再单独检索一遍，见 `ask_with_sources` 的说明）；
        2. **同步客户端不阻塞事件循环**：`openai` 的流式迭代放在**后台线程**里消费，
           通过 `call_soon_threadsafe` 投递到 asyncio 队列。这里不能简单用 `to_thread`——
           那样要等整段结束才能拿到结果，等于没有流式；
        3. **客户端断开就止损**：生成器被关闭（切走页面 / 点「停止生成」）时置停止标志并
           关闭上游流，否则后台线程会把整段答案读完，token 照付；
        4. **保留结束原因**：`finish_reason == "length"` 说明撞上 max_tokens，前端要能提示
           截断，不能把半截答案当完整答案展示。
        """
        # 检索可能较慢（首次还要建索引），期间给保活帧，避免代理/浏览器把空闲连接掐掉
        search_task = asyncio.create_task(asyncio.to_thread(
            self.search_related_nodes, question, course_id, document_id, 5, allowed_ids))
        # 客户端提前断开时，检索任务若接着抛错也要把异常取走（否则日志里全是
        # "Task exception was never retrieved"）
        search_task.add_done_callback(_drain_task_exception)
        while True:
            done, _ = await asyncio.wait({search_task}, timeout=_HEARTBEAT_SECONDS)
            if done:
                break
            yield {"event": "ping", "data": {}}
        sources = search_task.result()
        yield {"event": "sources", "data": {"sources": sources}}

        contexts = [_format_node(n) for n in sources]
        context_text = "\n".join(contexts) if contexts else "暂无相关课程知识"

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        stop = threading.Event()
        # 后台线程写、事件循环读；只放一个 key，跨线程读写由 GIL 保证原子性
        holder: dict = {}

        def _emit(kind, payload) -> None:
            """线程 → 事件循环投递；客户端已断开或事件循环已关闭时安静丢弃。"""
            if stop.is_set():
                return
            try:
                loop.call_soon_threadsafe(queue.put_nowait, (kind, payload))
            except RuntimeError:
                # 事件循环已关闭（服务停止）：没有人再消费了
                pass

        def _produce() -> None:
            """后台线程：消费 openai 同步流，逐块投递给事件循环（禁止在此使用 asyncio API）。"""
            stream = None
            finish_reason = None
            try:
                with track_llm_call("qa_stream"):
                    stream = self.client.chat.completions.create(
                        model=settings.LLM_MODEL,
                        messages=[
                            {"role": "system",
                             "content": QA_SYSTEM_PROMPT.format(context=context_text)},
                            {"role": "user", "content": question},
                        ],
                        temperature=0.3,
                        max_tokens=1024,
                        timeout=settings.QA_TIMEOUT,
                        stream=True,
                    )
                    holder["stream"] = stream
                    for chunk in stream:
                        if stop.is_set():
                            break
                        choices = getattr(chunk, "choices", None) or []
                        if not choices:
                            continue
                        finish_reason = getattr(choices[0], "finish_reason", None) or finish_reason
                        delta = getattr(choices[0], "delta", None)
                        text = getattr(delta, "content", None) if delta else None
                        if text:
                            _emit("delta", text)
                _emit("done", finish_reason)
            except Exception as e:  # noqa: BLE001 - 流式失败必须变成 error 事件，不能吞
                _logger.warning("流式问答失败: %s", e)
                _emit("error", str(e))
            finally:
                _close_quietly(stream)

        threading.Thread(target=_produce, daemon=True, name="qa-stream").start()

        parts: List[str] = []
        try:
            while True:
                try:
                    kind, payload = await asyncio.wait_for(queue.get(),
                                                           timeout=_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    # 模型在长时间思考 / 网络抖动：发保活帧，不影响正文
                    yield {"event": "ping", "data": {}}
                    continue
                if kind == "delta":
                    parts.append(payload)
                    yield {"event": "delta", "data": {"text": payload}}
                elif kind == "done":
                    yield {"event": "done",
                           "data": {"answer": "".join(parts).strip(),
                                    "sources": sources,
                                    "finish_reason": payload}}
                    return
                else:
                    # 与同步路径一致的口径：明确报错、绝不伪造答案；
                    # partial 让前端能把已经生成的半截正文保留下来
                    yield {"event": "error",
                           "data": {"message": f"抱歉，问答服务暂时不可用：{payload}",
                                    "partial": "".join(parts).strip()}}
                    return
        finally:
            # 正常收尾或客户端断开（GeneratorExit / CancelledError）都会走到这里：
            # 立刻置位并关掉上游流，别让后台线程继续把整段答案读完。
            stop.set()
            upstream = holder.get("stream")
            if upstream is not None:
                threading.Thread(target=_close_quietly, args=(upstream,),
                                 daemon=True, name="qa-stream-close").start()

    async def ask(self, question: str, course_id=None, document_id=None,
                  allowed_ids: List[int] = None) -> str:
        """回答问题（RAG 模式），仅返回答案文本；需要引用来源请用 ask_with_sources"""
        result = await self.ask_with_sources(question, course_id, document_id, allowed_ids)
        return result["answer"]
