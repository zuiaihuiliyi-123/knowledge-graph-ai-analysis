"""邀请接受请求模型。"""

from pydantic import BaseModel


class AcceptRequest(BaseModel):
    token: str
