"""题库请求模型（题目 CRUD / 自动标注 / 导入）。"""

from pydantic import BaseModel


class QuestionCreate(BaseModel):
    course_id: str
    document_id: str | None = None
    kp_id: str | None = None
    q_type: str
    stem: str
    options: list = []
    answer: object = None          # 单选/判断为字符串；多选为数组
    analysis: str | None = None
    difficulty: int = 3


class QuestionUpdate(BaseModel):
    document_id: str | None = None
    kp_id: str | None = None
    q_type: str | None = None
    stem: str | None = None
    options: list | None = None
    answer: object = None
    analysis: str | None = None
    difficulty: int | None = None


class QuestionActive(BaseModel):
    is_active: bool = True


class KpCandidatesRequest(BaseModel):
    top_k: int = 5


class AutoLabelRequest(BaseModel):
    course_id: str
    document_id: str | None = None
    question_ids: list | None = None
    only_missing: bool = True          # 只处理尚未挂知识点的题（默认，不覆盖教师判断）
    apply: bool = False                # 默认只出建议，不写库
    top_k: int = 3
    apply_threshold: float = 0.6       # 自动写库最低分（>= 该分才写入）


class ImportPreviewRequest(BaseModel):
    course_id: str
    document_id: str
    max_questions: int | None = None   # 可选：只预览前 N 题


class ImportCommitRequest(BaseModel):
    course_id: str
    document_id: str
    items: list                        # 预览里（可能被教师编辑过的）题目条目
    activate: bool = False             # 默认 False：导入为停用的暂存题
