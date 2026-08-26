"""
应用全局配置管理
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # 项目信息
    PROJECT_NAME: str = "课程知识图谱智能构建系统"
    VERSION: str = "1.0.0"

    # LLM API 配置（推荐 DeepSeek）
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "your-api-key-here")
    LLM_API_BASE: str = os.getenv("LLM_API_BASE", "https://api.deepseek.com/v1")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-chat")

    # Neo4j 图数据库配置
    NEO4J_URI: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    NEO4J_USER: str = os.getenv("NEO4J_USER", "neo4j")
    NEO4J_PASSWORD: str = os.getenv("NEO4J_PASSWORD", "password")

    # SQLite 关系型数据库配置（第一阶段，第二阶段迁移 MySQL）
    SQLITE_DB_PATH: str = os.getenv("SQLITE_DB_PATH", "./data/app.db")

    # 文件上传配置
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "./data/uploads")
    MAX_UPLOAD_SIZE: int = 50 * 1024 * 1024  # 50MB
    ALLOWED_EXTENSIONS: set = {".pdf", ".docx", ".txt", ".md"}

    # 知识提取配置
    EXTRACTION_TIMEOUT: int = 60  # 秒
    QA_TIMEOUT: int = 15  # 秒

    #认证
    SECRET_KEY: str = "default-secret-key"


settings = Settings()
