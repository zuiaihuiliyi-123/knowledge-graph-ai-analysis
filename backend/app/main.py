"""
FastAPI 应用入口
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .core.config import settings
from .api import auth, courses, knowledge_graph, qa, learning_path

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="基于AIGC的课程知识图谱智能构建与学习系统"
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


@app.get("/")
async def root():
    return {
        "message": f"欢迎使用{settings.PROJECT_NAME}",
        "version": settings.VERSION
    }


@app.get("/health")
async def health_check():
    return {"status": "ok"}
