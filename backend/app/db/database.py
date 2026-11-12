"""SQLite 门面：组合各域 Mixin，保持 sql_db 单例与原有方法签名。"""
from .bootstrap import BootstrapMixin
from .connection import ConnectionMixin
from .migrations import MigrationMixin
from .repositories.admin import AdminMixin
from .repositories.courses import CoursesMixin
from .repositories.documents import DocumentsMixin
from .repositories.embeddings import EmbeddingsMixin
from .repositories.fusion import FusionMixin
from .repositories.invite_codes import InviteCodesMixin
from .repositories.learning import LearningMixin
from .repositories.members import MembersMixin
from .repositories.practice import PracticeMixin
from .repositories.questions import QuestionsMixin
from .repositories.users import UsersMixin


class SQLDatabase(
    ConnectionMixin,
    MigrationMixin,
    BootstrapMixin,
    UsersMixin,
    CoursesMixin,
    MembersMixin,
    DocumentsMixin,
    LearningMixin,
    EmbeddingsMixin,
    QuestionsMixin,
    PracticeMixin,
    FusionMixin,
    InviteCodesMixin,
    AdminMixin,
):
    """SQLite 数据库管理类（第一阶段；第二阶段由 MySQL 实现替换底层连接）"""


# 全局关系型数据库实例
sql_db = SQLDatabase()
