"""
应用全局配置管理
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 后端根目录（backend/）。用于把 .env 与相对路径锚定到固定位置，
# 避免因启动时工作目录不同而导致读错 .env 或数据目录漂移。
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # backend/

# 显式指定 .env 路径，不依赖 find_dotenv 的向上搜索（防止误拾取上级目录的其他 .env）
load_dotenv(BASE_DIR / ".env")


def _resolve_path(value: str) -> str:
    """把 .env 中的相对路径解析为基于 backend/ 的绝对路径；已是绝对路径则原样返回。"""
    p = Path(value)
    if not p.is_absolute():
        p = BASE_DIR / p
    return str(p.resolve())


class Settings:
    # 项目信息
    PROJECT_NAME: str = "课程知识图谱智能构建系统"
    VERSION: str = "1.0.0"

    # LLM API 配置（推荐 DeepSeek）
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "your-api-key-here")
    LLM_API_BASE: str = os.getenv("LLM_API_BASE", "https://api.deepseek.com/v1")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-chat")

    # Embedding API 配置（RAG 向量检索；DeepSeek 无 embedding 接口，改用 SiliconFlow BAAI/bge-m3）
    EMBEDDING_API_KEY: str = os.getenv("EMBEDDING_API_KEY", "")
    EMBEDDING_API_BASE: str = os.getenv("EMBEDDING_API_BASE", "https://api.siliconflow.cn/v1")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")

    # Neo4j 图数据库配置
    NEO4J_URI: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    NEO4J_USER: str = os.getenv("NEO4J_USER", "neo4j")
    NEO4J_PASSWORD: str = os.getenv("NEO4J_PASSWORD", "Dream@161616")

    # SQLite 关系型数据库配置（第一阶段，第二阶段迁移 MySQL）
    # 锚定到 backend/ 目录，消除“从哪个目录启动”导致的路径漂移
    SQLITE_DB_PATH: str = _resolve_path(os.getenv("SQLITE_DB_PATH", "./data/app.db"))

    # 文件上传配置
    UPLOAD_DIR: str = _resolve_path(os.getenv("UPLOAD_DIR", "./data/uploads"))
    MAX_UPLOAD_SIZE: int = 50 * 1024 * 1024  # 50MB
    ALLOWED_EXTENSIONS: set = {".pdf", ".docx", ".txt", ".md"}

    # 知识提取配置
    EXTRACTION_TIMEOUT: int = 60  # 秒
    QA_TIMEOUT: int = 15  # 秒

    # 认证（JWT 签名密钥，务必在 .env 中覆盖为随机值）
    SECRET_KEY: str = os.getenv("SECRET_KEY", "please-set-a-random-secret-key-of-at-least-32-bytes")


settings = Settings()

