"""
应用全局配置管理
"""
import os
import secrets
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


# ---- 密钥管理（阶段 1B）----
# 历史上这里内置过真实的图库口令与固定的 JWT 兜底密钥（口令与密钥本体已从源码移除，
# 这里不再复述）。后果是「拿到仓库 = 拿到图库口令 = 能伪造任意用户的 JWT」。现在的规则：
#   1. 不内置任何口令；缺什么由启动自检告警（见 main._audit_secrets）；
#   2. SECRET_KEY 优先取环境变量；缺失 / 过短 / 仍是历史默认值时，改用本地随机密钥文件
#      （默认 backend/data/.secret_key，可用 SECRET_KEY_FILE 覆盖）。
#      持久化的好处：重启与多 worker 共用同一密钥（否则各 worker 互不认对方的 token），
#      又不存在「公开已知密钥」；
#   3. APP_ENV=production 时若 SECRET_KEY 不是来自环境变量，启动即失败（部署必须显式配置）。
APP_ENV: str = (os.getenv("APP_ENV", "dev") or "dev").strip().lower()
MIN_SECRET_KEY_LEN = 32
_LEGACY_DEFAULT_SECRET = "please-set-a-random-secret-key-of-at-least-32-bytes"
_SECRET_FILE = Path(os.getenv("SECRET_KEY_FILE") or (BASE_DIR / "data" / ".secret_key"))


def _load_secret_key() -> tuple[str, str]:
    """返回 (密钥, 来源)。来源 ∈ {"env", "local-file", "ephemeral"}。"""
    raw = (os.getenv("SECRET_KEY") or "").strip()
    if len(raw) >= MIN_SECRET_KEY_LEN and raw != _LEGACY_DEFAULT_SECRET:
        return raw, "env"

    # 环境变量不合规：用本地随机密钥文件（首次生成，尽量收紧权限）
    try:
        _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        saved = _SECRET_FILE.read_text(encoding="utf-8").strip() if _SECRET_FILE.exists() else ""
        if len(saved) < MIN_SECRET_KEY_LEN:
            saved = secrets.token_urlsafe(48)
            _SECRET_FILE.write_text(saved, encoding="utf-8")
            try:
                os.chmod(_SECRET_FILE, 0o600)
            except OSError:
                pass
        return saved, "local-file"
    except OSError:
        # 只读文件系统等极端场景：退化为进程内随机密钥。
        # 重启后旧 token 全部失效（可接受），但绝不回退到公开的固定值。
        return secrets.token_urlsafe(48), "ephemeral"


class Settings:
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
    # 刻意**不内置任何默认口令**：此前这里是真实口令，等于把图库交给所有拿到仓库的人。
    # 缺失时图库连接会失败（问答/图谱接口按既有降级路径处理），并在启动自检里告警。
    NEO4J_PASSWORD: str = os.getenv("NEO4J_PASSWORD", "")

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

    # 认证（JWT 签名密钥）
    # 取值优先级见文件上方 _load_secret_key()：合规环境变量 → 本地随机密钥文件 → 进程内随机。
    # SECRET_KEY_SOURCE 供启动自检提示「当前用的是哪一种」，不含密钥本体。
    SECRET_KEY, SECRET_KEY_SOURCE = _load_secret_key()

    # 生产环境硬性要求：密钥必须来自环境变量（本地文件在多实例部署下不共享，
    # 会出现「A 实例签发的 token 到了 B 实例就被判无效」）。
    if APP_ENV in ("prod", "production") and SECRET_KEY_SOURCE != "env":
        raise RuntimeError(
            "生产环境（APP_ENV=production）必须通过环境变量配置 SECRET_KEY"
            f"（长度 >= {MIN_SECRET_KEY_LEN} 的随机串），当前来源：{SECRET_KEY_SOURCE}"
        )

    # 教师注册邀请码（阶段 1A 安全收口）
    # 背景：`/api/auth/register` 原先让请求体自选 role="teacher"，任何人都能注册成教师。
    # 规则：配置了该值 → 教师注册必须携带且匹配；未配置 → **教师自助注册关闭**
    #      （学生注册不受影响；教师账号由管理员在后台创建或提升）。
    # 建议用不含空格的长随机串；它只参与服务端比较，永远不会出现在响应里。
    TEACHER_INVITE_CODE: str = (os.getenv("TEACHER_INVITE_CODE", "") or "").strip()


settings = Settings()

