"""
FastAPI 应用入口
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .core.config import settings
from .core.sql_database import sql_db
from .api import auth, courses, knowledge_graph, qa, learning_path, graph, documents, learning, dashboard, favorites, teacher


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化关系型数据库表结构（SQLite，幂等），并确保存在默认教师账号
    sql_db.init_tables()
    sql_db.ensure_default_teacher()
    yield


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="基于AIGC的课程知识图谱智能构建与学习系统",
    lifespan=lifespan,
)

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


@app.get("/")
async def root():
    return {
        "message": f"欢迎使用{settings.PROJECT_NAME}",
        "version": settings.VERSION
    }


@app.get("/health")
async def health_check():
    return {"status": "ok"}
