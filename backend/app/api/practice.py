"""
学生端做题练习 API

- GET    /api/v1/practice/questions              出题（**不含答案**，提交后才下发）
- POST   /api/v1/practice/recommend              智能推荐出题（按学情选卷，**同样不含答案**）
- POST   /api/v1/practice/submit                 提交作答（服务端判分 + 落答题记录）
- GET    /api/v1/practice/records                我的答题记录（only_wrong=true 即错题原始记录）
- GET    /api/v1/practice/wrong-book             错题本（每题最近一次错误 + 关联知识点）
- GET    /api/v1/practice/stats                  我的练习统计
- GET    /api/v1/practice/favorites              我的题目收藏
- POST   /api/v1/practice/favorites              收藏题目
- DELETE /api/v1/practice/favorites/{question_id} 取消收藏

防泄题：出题与收藏列表均走 service 的 _public_view 白名单投影，响应中不含 answer/analysis。
"""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..core.dependencies import get_current_user
from ..core.permissions import Permissions
from ..core.response import success, error
from ..core.sql_database import sql_db
from ..services.question_service import PracticeService

router = APIRouter(prefix="/api/v1/practice", tags=["做题练习"])


def _coerce_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _wrap(result: dict):
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


def _guard_course(cid: int, current_user: dict):
    """带 course_id 的练习接口统一闸门：非课程成员一律 4003。

    为什么每个端点都要过：出题 / 推荐会返回题干与选项，`/submit` 更会下发标准答案；
    只要猜一个 `course_id`，就能把别人课程的题库连同答案整套导出来。
    判定复用 `Permissions.require_course_content`（课程创建者 / 协作教师 / 已通过学生）。
    返回 None 表示放行，否则返回可直接 return 的错误信封。
    """
    perm = Permissions.require_course_content(cid, current_user)
    return None if perm["ok"] else error(perm["code"], perm["message"])


class SubmitRequest(BaseModel):
    question_id: int
    user_answer: object = None     # 单选/判断：字符串；多选：数组；未作答可传 null


class RecommendRequest(BaseModel):
    """智能推荐出题入参（mode 见 services/question_recommender.py 模块文档）"""
    course_id: str
    document_id: str | None = None
    kp_id: str | None = None
    q_type: str | None = None
    count: int = 10
    mode: str = "mixed"            # weak/review/new/advanced/mixed/random
    seed: int | None = None        # 固定随机种子（便于复现与 A/B）


class QuestionFavoriteRequest(BaseModel):
    course_id: str
    question_id: int


@router.get("/questions")
async def practice_questions(
    course_id: str = Query(..., description="课程 ID"),
    document_id: str = Query(None, description="文档 ID（可选；含课程通用题）"),
    kp_id: str = Query(None, description="指定知识点出题（可选，配合学习路径推荐）"),
    q_type: str = Query(None, description="题型：SINGLE/MULTI/JUDGE"),
    count: int = Query(10, ge=1, le=50, description="出题数量"),
    current_user: dict = Depends(get_current_user),
):
    """出题：随机取启用题目，返回题面与选项（不含答案与解析）"""
    cid = _coerce_int(course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    denied = _guard_course(cid, current_user)
    if denied is not None:
        return denied
    return _wrap(PracticeService.get_questions(
        current_user["user_id"], cid, document_id=document_id,
        kp_id=kp_id, q_type=q_type, count=count,
    ))


@router.post("/recommend")
async def recommend_questions(body: RecommendRequest,
                              current_user: dict = Depends(get_current_user)):
    """智能推荐出题：按学情（掌握度/遗忘/难度适配/错题…）选卷。

    与 `/questions` 同一套防泄题纪律：返回体**不含 answer/analysis**，提交后才下发答案与解析。
    每题附带 `reason`（推荐理由）与 `bucket`（薄弱强化/复习巩固/路径新知识/进阶提升）。
    """
    cid = _coerce_int(body.course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    denied = _guard_course(cid, current_user)
    if denied is not None:
        return denied
    return _wrap(PracticeService.recommend_questions(
        current_user["user_id"], cid, document_id=body.document_id, kp_id=body.kp_id,
        q_type=body.q_type, count=body.count, mode=body.mode, seed=body.seed,
    ))


@router.post("/submit")
async def submit_answer(body: SubmitRequest, current_user: dict = Depends(get_current_user)):
    """提交作答：自动判分并落库，返回正确答案与解析

    `/submit` 是**唯一会下发标准答案**的练习接口，因此必须按题目所属课程校验成员关系：
    否则任意登录用户拿一个 question_id 就能把别人课程的答案一问一问套出来。
    """
    question = sql_db.get_question(body.question_id)
    if not question:
        return error(2002, f"题目不存在: question_id={body.question_id}")
    denied = _guard_course(int(question["course_id"]), current_user)
    if denied is not None:
        return denied
    return _wrap(PracticeService.submit(
        current_user["user_id"], body.question_id, body.user_answer,
    ))


@router.get("/records")
async def answer_records(
    course_id: str = Query(None, description="课程 ID（可选）"),
    document_id: str = Query(None, description="文档 ID（可选）"),
    only_wrong: bool = Query(False, description="仅返回答错的记录"),
    limit: int = Query(100, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
):
    """我的答题记录（时间倒序）"""
    cid = _coerce_int(course_id) if course_id not in (None, "") else None
    if course_id not in (None, "") and cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    if cid is not None:
        denied = _guard_course(cid, current_user)
        if denied is not None:
            return denied
    return _wrap(PracticeService.records(
        current_user["user_id"], course_id=cid, document_id=document_id,
        only_wrong=only_wrong, limit=limit,
    ))


@router.get("/wrong-book")
async def wrong_book(course_id: str = Query(..., description="课程 ID"),
                     document_id: str = Query(None, description="文档 ID（可选）"),
                     current_user: dict = Depends(get_current_user)):
    """错题本：按题取最近一次答错，附关联知识点名称"""
    cid = _coerce_int(course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    denied = _guard_course(cid, current_user)
    if denied is not None:
        return denied
    return _wrap(PracticeService.wrong_book(
        current_user["user_id"], cid, document_id=document_id,
    ))


@router.get("/stats")
async def practice_stats(course_id: str = Query(..., description="课程 ID"),
                         document_id: str = Query(None, description="文档 ID（可选）"),
                         current_user: dict = Depends(get_current_user)):
    """我的练习统计：累计作答/正确率/错题数/收藏数"""
    cid = _coerce_int(course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    denied = _guard_course(cid, current_user)
    if denied is not None:
        return denied
    return _wrap(PracticeService.stats(
        current_user["user_id"], cid, document_id=document_id,
    ))


@router.get("/favorites")
async def list_favorites(course_id: str = Query(..., description="课程 ID"),
                         current_user: dict = Depends(get_current_user)):
    """我的题目收藏"""
    cid = _coerce_int(course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    denied = _guard_course(cid, current_user)
    if denied is not None:
        return denied
    return _wrap(PracticeService.list_favorites(current_user["user_id"], cid))


@router.post("/favorites")
async def add_favorite(body: QuestionFavoriteRequest,
                       current_user: dict = Depends(get_current_user)):
    """收藏题目（幂等）"""
    cid = _coerce_int(body.course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    denied = _guard_course(cid, current_user)
    if denied is not None:
        return denied
    return _wrap(PracticeService.favorite(
        current_user["user_id"], cid, body.question_id,
    ))


@router.delete("/favorites/{question_id}")
async def remove_favorite(question_id: int,
                          course_id: str = Query(..., description="课程 ID"),
                          current_user: dict = Depends(get_current_user)):
    """取消题目收藏"""
    cid = _coerce_int(course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    denied = _guard_course(cid, current_user)
    if denied is not None:
        return denied
    return _wrap(PracticeService.unfavorite(current_user["user_id"], cid, question_id))
