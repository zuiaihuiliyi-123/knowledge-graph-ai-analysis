"""
FastAPI 应用入口
"""
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
                  admin)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化关系型数据库表结构（SQLite，幂等），并确保存在默认教师/管理员账号。
    # 两者都是「不存在才创建」，重复启动不会覆盖已有账号的密码或角色。
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


@app.get("/")
async def root():
    return {
        "message": f"欢迎使用{settings.PROJECT_NAME}",
        "version": settings.VERSION
    }


@app.get("/health")
async def health_check():
    return {"status": "ok"}
