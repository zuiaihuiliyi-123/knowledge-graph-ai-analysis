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
    # 知识抽取要求模型直接输出 JSON：开启后请求携带 response_format=json_object，
    # 从源头减少「夹带说明文字/代码块」导致的解析失败。
    # 个别 OpenAI 兼容供应商不支持该参数，抽取器会自动回退普通模式（只回退一次）。
    LLM_JSON_MODE: bool = (
        os.getenv("LLM_JSON_MODE", "1").strip().lower() not in ("0", "false", "no", "off")
    )

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

    # ---- 启动前自动备份（数据保障）----
    # 每次启动在 init_tables()（一串幂等迁移，属写操作）之前留一份库快照，
    # 轮转只保留最近 AUTO_BACKUP_KEEP 份。为什么必须有：
    # 本项目的库曾被 git checkout「静默覆盖」过一次（历史提交跟踪过 app.db），
    # 而迁移本身也可能写坏数据 —— 两类事故都靠「最近一次启动时的快照」回退。
    # 备份目录默认 backend/backup/auto（backend/backup/ 已在 .gitignore 中）。
    AUTO_BACKUP: bool = (
        os.getenv("AUTO_BACKUP", "1").strip().lower() not in ("0", "false", "no", "off")
    )
    AUTO_BACKUP_KEEP: int = max(1, int(os.getenv("AUTO_BACKUP_KEEP", "10") or 10))
    BACKUP_DIR: str = _resolve_path(os.getenv("BACKUP_DIR", "./backup"))

    # 文件上传配置
    UPLOAD_DIR: str = _resolve_path(os.getenv("UPLOAD_DIR", "./data/uploads"))
    MAX_UPLOAD_SIZE: int = 50 * 1024 * 1024  # 50MB
    ALLOWED_EXTENSIONS: set = {".pdf", ".docx", ".txt", ".md"}

    # 头像上传配置（个人中心）。与课程文档分目录存放，避免与 course_id 目录混淆
    AVATAR_DIR: str = _resolve_path(os.getenv("AVATAR_DIR", "./data/uploads/avatars"))
    MAX_AVATAR_SIZE: int = 2 * 1024 * 1024  # 2MB
    ALLOWED_AVATAR_EXTENSIONS: dict = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
    }

    # 知识提取配置
    EXTRACTION_TIMEOUT: int = 60  # 秒
    QA_TIMEOUT: int = 15  # 秒

    # 认证（JWT 签名密钥，务必在 .env 中覆盖为随机值）
    SECRET_KEY: str = os.getenv("SECRET_KEY", "please-set-a-random-secret-key-of-at-least-32-bytes")

    # 教师注册邀请码（阶段 1A 安全收口）
    # 背景：`/api/auth/register` 原先让请求体自选 role="teacher"，任何人都能注册成教师。
    # 规则：配置了该值 → 教师注册必须携带且匹配；未配置 → **教师自助注册关闭**
    #      （学生注册不受影响；教师账号由管理员在后台创建或提升）。
    # 建议用不含空格的长随机串；它只参与服务端比较，永远不会出现在响应里。
    TEACHER_INVITE_CODE: str = (os.getenv("TEACHER_INVITE_CODE", "") or "").strip()


settings = Settings()

