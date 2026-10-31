"""
FastAPI 应用入口
"""
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from .core.config import settings
from .core import metrics
from .core.sql_database import sql_db
from .api import (auth, courses, knowledge_graph, qa, learning_path, graph, documents,
                  learning, dashboard, favorites, teacher,
                  course_members, invites, profile, questions, practice, grading,
                  admin, admin_fusion)

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
    yield


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
    """
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        metrics.record_request(request.url.path, 500,
                               (time.perf_counter() - started) * 1000)
        raise
    metrics.record_request(request.url.path, response.status_code,
                           (time.perf_counter() - started) * 1000)
    return response


# CORS 配置（允许前端跨域访问）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    return {"status": "ok"}
