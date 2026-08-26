from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from passlib.context import CryptContext
import jwt
from datetime import datetime, timedelta

from ..core.sql_database import sql_db  # 导入全局 SQLite 实例
from ..core.config import settings  # 配置中应有 SECRET_KEY 等

router = APIRouter(prefix="/api/auth", tags=["用户认证"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class UserRegister(BaseModel):
    username: str
    password: str
    role: str = "student"
    email: str | None = None
    display_name: str | None = None

class UserLogin(BaseModel):
    username: str
    password: str

@router.post("/register")
def register(user: UserRegister):
    # 检查用户名是否已存在
    existing = sql_db.get_user_by_username(user.username)
    if existing:
        raise HTTPException(status_code=400, detail="用户名已存在")
    # 密码哈希
    hashed = pwd_context.hash(user.password)
    # 创建用户
    user_id = sql_db.create_user(
        username=user.username,
        password_hash=hashed,
        role=user.role,
        display_name=user.display_name,
        email=user.email,
    )
    return {"user_id": user_id, "username": user.username}

@router.post("/login")
def login(user: UserLogin):
    db_user = sql_db.get_user_by_username(user.username)
    if not db_user or not pwd_context.verify(user.password, db_user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    # 生成 JWT
    token_data = {"sub": str(db_user["user_id"]), "username": db_user["username"], "role": db_user["role"]}
    token = jwt.encode(token_data, settings.SECRET_KEY, algorithm="HS256")
    return {"access_token": token, "token_type": "bearer"}