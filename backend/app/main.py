"""
FastAPI 应用入口
"""
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .core.config import settings
from .core import metrics
from .core.database import db
from .core.sql_database import sql_db
from .services.document_service import DocumentService
from .api import (auth, courses, knowledge_graph, qa, learning_path, graph, documents,
                  learning, dashboard, favorites, teacher,
                  course_members, invites, profile, questions, practice, grading,
                  admin, admin_fusion)

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
_logger = logging.getLogger(__name__)


def _audit_secrets() -> list[str]:
    """启动自检：列出「会造成静默降级或安全暴露」的配置问题。

    刻意只告警不阻塞启动（生产环境的 SECRET_KEY 已由 config 强制校验）：
    图谱/向量/LLM 都有既定的降级路径，缺密钥时系统仍可启动，
    但**必须让人看见**——本项目历史上多次出现「功能静默失效却被当成数据为空」。

    返回的文案里不含任何密钥本体，可以安全写进日志。
    """
    issues = []
    if settings.SECRET_KEY_SOURCE != "env":
        where = "本地密钥文件" if settings.SECRET_KEY_SOURCE == "local-file" else "进程内临时密钥"
        issues.append(
            f"SECRET_KEY 未通过环境变量配置，当前使用{where}；"
            f"部署前请在 .env 配置长度 >= {32} 的随机串"
        )
    if not settings.NEO4J_PASSWORD:
        issues.append("NEO4J_PASSWORD 未配置：图谱相关接口会走降级/失败路径")
    if not settings.LLM_API_KEY or settings.LLM_API_KEY == "your-api-key-here":
        issues.append("LLM_API_KEY 未配置或仍是占位值：抽取与问答生成不可用")
    if not settings.EMBEDDING_API_KEY:
        issues.append("EMBEDDING_API_KEY 未配置：RAG 会静默退回关键词检索")
    return issues


@asynccontextmanager
async def lifespan(app: FastAPI):
    for issue in _audit_secrets():
        _logger.warning("启动自检：%s", issue)
    # 启动时初始化关系型数据库表结构（SQLite，幂等），并确保存在默认教师/管理员账号。
    # 两者都是「不存在才创建」，重复启动不会覆盖已有账号的密码或角色。
    #
    # 先自动备份、再迁移：init_tables() 里跑的是补列/回填/重建表这类**写操作**，
    # 一旦某次迁移出问题，用这次启动前的快照即可回到上一个可用状态。
    # 备份失败不阻塞启动（见 SQLDatabase.auto_backup 的取舍说明）。
    sql_db.auto_backup()
    sql_db.init_tables()
    sql_db.ensure_default_teacher()
    sql_db.ensure_default_admin()

    # 重启恢复：解析/抽取/建图都在进程内的 asyncio 任务里跑，进程重启后库里会残留
    # PARSING / EXTRACTING / BUILDING —— 没有任务在跑却一直显示「处理中」。这里收敛为
    # FAILED 并写明原因，教师才能看到「重试」。多 worker 部署请设 RECONCILE_ON_STARTUP=0。
    if settings.RECONCILE_ON_STARTUP:
        try:
            DocumentService.reconcile_interrupted()
        except Exception:  # noqa: BLE001 - 恢复失败不应阻止服务启动
            _logger.exception("启动恢复失败（不影响启动）")

    # 图谱索引/约束（幂等 IF NOT EXISTS）。此前 `init_schema()` 只定义、从未被调用——
    # 本机之所以有索引是历史手工建的，**全新部署不会建**：MERGE 会退化成 Label 扫描，
    # 批量建图与图谱读取都会慢一个量级，`kp_id` 唯一约束也缺失。
    # 图库不可用时只告警：不能让「没装 Neo4j」阻止整个服务启动（既有降级路径保留）。
    try:
        if db.init_schema():
            _logger.info("Neo4j 约束与索引初始化完成")
        else:
            _logger.warning("Neo4j 不可达，已跳过约束/索引初始化（服务仍可启动，图谱功能会降级）")
    except Exception as exc:  # noqa: BLE001
        _logger.warning("初始化 Neo4j 索引/约束失败（图谱功能可能变慢或降级）：%s", exc)
    try:
        yield
    finally:
        # 驱动在 lifespan 里可能是惰性创建后才存在的；关闭失败不应阻止进程退出。
        try:
            db.close()
        except Exception:  # noqa: BLE001
            _logger.warning("关闭 Neo4j 驱动失败（不影响退出）", exc_info=True)


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="基于AIGC的课程知识图谱智能构建与学习系统",
    lifespan=lifespan,
)


@app.middleware("http")
async def collect_request_metrics(request: Request, call_next):
    """采集真实请求量与响应耗时（供管理员端「系统监控」展示）。

    只做内存计数，不写库、不落日志：监控数据本身不应该成为新的故障点或性能负担。
    计数在响应返回后追加，异常路径也计入（errors），故 5xx 不会被漏掉。

    指标 key 使用路由模板（如 /api/v1/documents/{doc_id}）而不是原始 URL：
    否则每个文档 id / 用户 id 都会生成一个独立桶，长跑进程内存持续增长，
    管理端的「按路径统计」也会被大量一次性路径淹没。
    """
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        metrics.record_request(_metric_path(request), 500,
                               (time.perf_counter() - started) * 1000)
        raise
    metrics.record_request(_metric_path(request), response.status_code,
                           (time.perf_counter() - started) * 1000)
    return response


def _metric_path(request: Request) -> str:
    """路由匹配成功时用模板路径，否则回退到原始 path（未匹配的 404 数量通常有限）。"""
    route = request.scope.get("route")
    return getattr(route, "path", None) or request.url.path


# CORS 配置：白名单来自 CORS_ORIGINS，生产环境不要使用 *。
# 前端使用 Authorization: Bearer（localStorage），不依赖 Cookie，故关闭 credentials。
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _error_response(status_code: int, code: int, message: str) -> JSONResponse:
    """统一错误响应形状：{code, message, detail, data, timestamp}。

    保留 detail 字段与 FastAPI 默认习惯兼容，前端拦截器会优先读 detail。
    """
    return JSONResponse(
        status_code=status_code,
        content={
            "code": code,
            "message": message,
            "detail": message,
            "data": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """把 FastAPI 默认的英文 422 数组转换成一条可读的中文错误。

    之前前端会把 detail 数组 JSON.stringify 后塞进 toast，用户看到的是
    [{"type":"missing","loc":["body","course_id"],...}] 这类内容。
    """
    errors = exc.errors() or []
    first = errors[0] if errors else {}
    loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
    msg = first.get("msg", "参数不合法")
    # 常见 Pydantic 英文错误转中文，避免把 "Field required" 这类文本直接给终端用户
    msg = {
        "Field required": "缺少必填字段",
        "Input should be a valid integer": "必须是整数",
        "Input should be a valid string": "必须是字符串",
        "Input should be a valid list": "必须是列表",
    }.get(msg, msg)
    field = f"「{loc}」" if loc else "请求体"
    return _error_response(422, 4001, f"参数校验失败：{field}{msg}")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """兜底未捕获异常：记录完整堆栈到服务端日志，对外只返回固定文案。"""
    _logger.exception("未捕获异常 %s %s", request.method, request.url.path)
    return _error_response(500, 5000, "服务器内部错误，请稍后重试")

# 注册路由
app.include_router(auth.router)
app.include_router(courses.router)
app.include_router(knowledge_graph.router)
app.include_router(qa.router)
app.include_router(learning_path.router)
app.include_router(learning.router)
app.include_router(graph.router)
app.include_router(documents.router)
app.include_router(dashboard.router)
app.include_router(favorites.router)
app.include_router(teacher.router)
# 课程中心：成员管理（复用 /api/v1/courses 前缀）、邀请、个人中心
app.include_router(course_members.router)
app.include_router(invites.router)
app.include_router(profile.router)
# 题库：教师手动出题 + 学生练习（合作者 PR #3）
app.include_router(questions.router)
app.include_router(practice.router)
# 题库 Scope B：主观题（填空/解答）教师批改
app.include_router(grading.router)
# 管理员端：平台治理 / 用户管理 / 课程治理 / 资源管理 / 系统监控 / 审计日志
app.include_router(admin.router)
# 管理员端 · 文档级知识融合（候选扫描 / 审核 / 应用 / 撤销，全部按 document_id 隔离）
app.include_router(admin_fusion.router)


@app.get("/")
async def root():
    return {
        "message": f"欢迎使用{settings.PROJECT_NAME}",
        "version": settings.VERSION
    }


@app.get("/health")
async def health_check():
    """存活检查（liveness）：只表示进程能响应，不查数据库。

    为什么不能在这里查 Neo4j：图库不可达时，驱动连接超时是 2 秒（IPv6/IPv4 各一次），
    单次请求会到 4 秒；前端健康检查只等 3 秒，会被误判为「服务不可用」。
    依赖状态请访问 /health/deps（readiness，允许慢）。
    """
    return {"status": "ok"}


@app.get("/health/deps")
async def health_dependencies():
    """依赖检查（readiness）：SQLite 与 Neo4j 的真实连通状态，允许较慢。

    运维/管理端使用；前端登录页不使用该接口，避免 Neo4j 故障拖慢登录页。
    """
    deps = {"sqlite": "ok", "neo4j": "ok"}
    try:
        sql_db.ping()
    except Exception as exc:  # noqa: BLE001
        _logger.warning("健康检查：SQLite 不可用：%s", exc)
        deps["sqlite"] = "error"
    try:
        db.query("RETURN 1 AS ok")
    except Exception as exc:  # noqa: BLE001
        _logger.warning("健康检查：Neo4j 不可用：%s", exc)
        deps["neo4j"] = "error"
    return {
        "status": "ok" if all(v == "ok" for v in deps.values()) else "degraded",
        "dependencies": deps,
    }
