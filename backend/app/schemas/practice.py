"""学生练习请求模型（提交 / 推荐 / 收藏）。"""

from pydantic import BaseModel


class SubmitRequest(BaseModel):
    question_id: int
    user_answer: object = None     # 单选/判断：字符串；多选：数组；未作答可传 null


class QuestionFavoriteRequest(BaseModel):
    course_id: str
    question_id: int
