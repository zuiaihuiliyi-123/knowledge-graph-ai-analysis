"""
学习记录 API（对齐规划文档 6.6 学习记录持久化）

mark：标记知识点掌握状态（学生点击"掌握"按钮 / 图谱节点勾选）
progress：查询学生在某课程下的学习进度（已掌握知识点 + 总进度百分比）
"""
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..core.database import db
from ..core.dependencies import get_current_user
from ..core.response import success, error
from ..core.sql_database import sql_db

router = APIRouter(prefix="/api/v1/learning", tags=["学习记录"])

# 状态取值（对齐 sql_database 的 RECORD_STATUS）
VALID_STATUS = {"MASTERED", "LEARNING", "RECOMMENDED"}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _coerce_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class MarkRequest(BaseModel):
    course_id: str
    document_id: str = None
    kp_id: str
    status: str = "MASTERED"     # MASTERED / LEARNING / RECOMMENDED
    mastery_level: int = 100     # 0-100


@router.post("/mark")
async def mark_knowledge(request: MarkRequest, current_user: dict = Depends(get_current_user)):
    """标记/更新知识点掌握状态（默认 MASTERED 表示已掌握）"""
    cid = _coerce_int(request.course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    did = _coerce_int(request.document_id)
    if did is None:
        return error(4001, "参数错误：document_id 必须为整数")
    kp_id = (request.kp_id or "").strip()
    if not kp_id:
        return error(4001, "参数错误：kp_id 不能为空")
    if request.status not in VALID_STATUS:
        return error(4001, f"参数错误：status 取值应为 {sorted(VALID_STATUS)}")
    if not (0 <= request.mastery_level <= 100):
        return error(4001, "参数错误：mastery_level 应在 0-100 之间")

    # 校验知识点存在（文档级），避免写入孤立学习记录
    recs = db.query(
        "MATCH (n:KnowledgePoint {course_id: $cid, document_id: $did, kp_id: $kp_id}) "
        "RETURN n.kp_id AS kp_id",
        {"cid": cid, "did": did, "kp_id": kp_id},
    )
    if not recs:
        return error(4002, f"知识点不存在：course_id={cid}, document_id={did}, kp_id={kp_id}")

    record_id = sql_db.upsert_learning_record(
        user_id=current_user["user_id"],
        course_id=cid,
        document_id=did,
        kp_id=kp_id,
        status=request.status,
        mastery_level=request.mastery_level,
        source="MANUAL",
        last_learned_at=_now(),
    )
    return success({
        "record_id": record_id,
        "course_id": cid,
        "document_id": did,
        "kp_id": kp_id,
        "status": request.status,
        "mastery_level": request.mastery_level,
    })


@router.delete("/mark")
async def unmark_knowledge(course_id: str, document_id: str, kp_id: str,
                           current_user: dict = Depends(get_current_user)):
    """取消知识点掌握标记（删除对应学习记录）"""
    cid = _coerce_int(course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    did = _coerce_int(document_id)
    if did is None:
        return error(4001, "参数错误：document_id 必须为整数")
    deleted = sql_db.delete_learning_record(
        current_user["user_id"], cid, did, (kp_id or "").strip(),
    )
    return success({"deleted": deleted})


@router.get("/progress")
async def get_progress(course_id: str = None, document_id: str = None,
                       current_user: dict = Depends(get_current_user)):
    """查询学生学习进度；指定 course_id + document_id 返回该文档进度，否则返回全部学习记录（按课程分组）"""
    user_id = current_user["user_id"]

    if course_id is not None and course_id != "" and document_id is not None and document_id != "":
        cid = _coerce_int(course_id)
        if cid is None:
            return error(4001, "参数错误：course_id 必须为整数")
        did = _coerce_int(document_id)
        if did is None:
            return error(4001, "参数错误：document_id 必须为整数")
        records = sql_db.list_records_by_user_course(user_id, cid, did)
        mastered = [r for r in records if r["status"] == "MASTERED"]
        total = db.query(
            "MATCH (n:KnowledgePoint {course_id: $cid, document_id: $did}) RETURN count(n) AS cnt",
            {"cid": cid, "did": did},
        )
        total_nodes = total[0]["cnt"] if total else 0
        return success({
            "course_id": cid,
            "document_id": did,
            "total_nodes": total_nodes,
            "mastered_count": len(mastered),
            "mastered_kp_ids": [r["kp_id"] for r in mastered],
            "progress": round(len(mastered) / total_nodes * 100, 1) if total_nodes else 0.0,
            "records": records,
        })

    # 不指定文档：返回该用户全部学习记录（按课程分组）
    all_records = sql_db.list_records_by_user(user_id)
    by_course = {}
    for r in all_records:
        by_course.setdefault(r["course_id"], []).append(r)
    return success({
        "courses": [
            {
                "course_id": cid,
                "mastered_kp_ids": [r["kp_id"] for r in rs if r["status"] == "MASTERED"],
                "record_count": len(rs),
            }
            for cid, rs in by_course.items()
        ],
    })
