"""
教师端题库管理 API

- POST   /api/v1/questions                        新增题目
- GET    /api/v1/questions                        列表（分页 + 课程/文档/知识点/题型/关键词筛选）
- GET    /api/v1/questions/stats                  题库总览
- GET    /api/v1/questions/coverage               知识点题目覆盖率（无题知识点 / 悬空 kp_id）
- GET    /api/v1/questions/favorites              题目收藏情况（哪些学生收藏了哪道题）
- POST   /api/v1/questions/auto-label             批量知识点自动标注（默认只出建议，不写库）
- POST   /api/v1/questions/{question_id}/kp-candidates  单题知识点候选（三层证据融合）
- GET    /api/v1/questions/{question_id}          题目详情
- PUT    /api/v1/questions/{question_id}          修改题目（未传字段沿用原值）
- DELETE /api/v1/questions/{question_id}          删除题目（已作答过则软删停用）
- PATCH  /api/v1/questions/{question_id}/active   启用/停用

权限：接口层 require_teacher，service 层再做「课程归属校验」（越权 → code 4003）。
⚠ 路由顺序：/stats 与 /favorites 必须声明在 /{question_id} 之前，否则会被当成整数路径参数（422）。
"""
import asyncio

from fastapi import APIRouter, Depends, Query

from ..core.dependencies import require_teacher
from ..core.response import success, error
from ..services.question_service import QuestionService
from ..schemas.questions import (
    QuestionCreate,
    QuestionUpdate,
    QuestionActive,
    KpCandidatesRequest,
    AutoLabelRequest,
    ImportPreviewRequest,
    ImportCommitRequest,
)

router = APIRouter(prefix="/api/v1/questions", tags=["题库管理"])


def _coerce_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _wrap(result: dict):
    """统一包装 service 结果（{ok, code, message, data} → {code,message,data,timestamp}）"""
    return success(result["data"]) if result["ok"] else error(result["code"], result["message"])


@router.post("")
async def create_question(body: QuestionCreate, current_user: dict = Depends(require_teacher)):
    """新增题目（course_id 必传；document_id 留空表示课程通用题）"""
    cid = _coerce_int(body.course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    result = QuestionService.create_question(
        current_user["user_id"], cid, body.model_dump(),
    )
    return _wrap(result)


@router.get("")
async def list_questions(
    course_id: str = Query(..., description="课程 ID"),
    document_id: str = Query(None, description="文档 ID（可选，含课程通用题）"),
    kp_id: str = Query(None, description="关联知识点 ID（可选）"),
    q_type: str = Query(None, description="题型：SINGLE/MULTI/JUDGE"),
    keyword: str = Query(None, description="题干关键词"),
    is_active: bool = Query(None, description="启用状态筛选（不传=全部）"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    current_user: dict = Depends(require_teacher),
):
    """题库列表（教师视角：含答案/解析/作答正确率/收藏数）"""
    cid = _coerce_int(course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    result = QuestionService.list_questions(
        current_user["user_id"], cid, document_id=document_id, kp_id=kp_id,
        q_type=q_type, keyword=keyword, is_active=is_active,
        page=page, page_size=page_size,
    )
    return _wrap(result)


@router.get("/stats")
async def question_stats(course_id: str = Query(..., description="课程 ID"),
                         current_user: dict = Depends(require_teacher)):
    """题库总览：题量/启用停用/题型分布/作答总数/平均正确率/收藏总数"""
    cid = _coerce_int(course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    return _wrap(QuestionService.stats(current_user["user_id"], cid))


@router.get("/favorites")
async def question_favorites(course_id: str = Query(..., description="课程 ID"),
                             question_id: str = Query(None, description="可选：只看某题"),
                             current_user: dict = Depends(require_teacher)):
    """题目收藏情况：哪些学生收藏了哪道题"""
    cid = _coerce_int(course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    qid = _coerce_int(question_id) if question_id not in (None, "") else None
    return _wrap(QuestionService.favorites(current_user["user_id"], cid, qid))


@router.get("/coverage")
async def question_coverage(
    course_id: str = Query(..., description="课程 ID"),
    document_id: str = Query(None, description="文档 ID（可选；含课程通用题）"),
    current_user: dict = Depends(require_teacher),
):
    """知识点题目覆盖率：无题知识点清单 + 悬空 kp_id（教师补题指引）。

    口径：只数启用中的题目；document_id 传入时统计「该文档题目 + 课程通用题」。
    图谱不可用时返回 graph_available=False（仍给出题量统计），不整页报错。
    """
    cid = _coerce_int(course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    return _wrap(QuestionService.coverage(
        current_user["user_id"], cid, document_id=document_id,
    ))


@router.post("/import/preview")
async def import_preview(body: ImportPreviewRequest,
                         current_user: dict = Depends(require_teacher)):
    """从课程文档解析题目候选（**预览，不写库**；Scope D）。

    确定性规则解析：切「题目区/答案区」→ 按题号切块 → 答案映射 → 题型判定与质量标记。
    每道题返回 q_type/options/answer/warnings/import_status/confidence，供教师复核编辑。
    """
    cid = _coerce_int(body.course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    return _wrap(await QuestionService.import_preview(
        current_user["user_id"], cid, body.document_id, max_questions=body.max_questions,
    ))


@router.post("/import/commit")
async def import_commit(body: ImportCommitRequest,
                        current_user: dict = Depends(require_teacher)):
    """提交导入：把教师确认/编辑过的题目入库为**停用的暂存题**（`activate=true` 才直接启用）。

    逐题走 `validate_question_payload`，不合格（如主观题缺参考答案）一律拒绝并回报原因。
    """
    cid = _coerce_int(body.course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    return _wrap(QuestionService.import_commit(
        current_user["user_id"], cid, body.document_id, body.items, activate=body.activate,
    ))


@router.get("/import/batch/{batch_id}")
async def import_batch_detail(batch_id: str,
                              current_user: dict = Depends(require_teacher)):
    """导入批次明细（批次信息 + 已入库题目，含答案，教师视角）"""
    return _wrap(QuestionService.import_batch_detail(current_user["user_id"], batch_id))


@router.post("/auto-label")
async def auto_label_questions(body: AutoLabelRequest,
                               current_user: dict = Depends(require_teacher)):
    """批量知识点自动标注（Scope C）。

    默认只返回候选建议、**不写库**；`apply=true` 时只把「分数 >= apply_threshold 且在
    本课程图谱清单内」的候选写入题目，且默认只处理尚未挂知识点的题（only_missing）。
    """
    cid = _coerce_int(body.course_id)
    if cid is None:
        return error(4001, "参数错误：course_id 必须为整数")
    # auto_label 内部会调用外部 embedding API（批量题时可能持续数秒到数十秒），
    # 且全是同步代码。直接在 async 端点里执行会占住整个事件循环，期间所有请求都卡住；
    # 放到线程池执行，与问答/抽取的既有做法一致。
    # 线程安全说明：sql_db 每次 _connect() 新建连接（不共享 sqlite3.Connection），
    # Neo4j Driver 本身线程安全；vector_index 的进程内缓存已用锁保护。
    result = await asyncio.to_thread(
        QuestionService.auto_label,
        current_user["user_id"], cid, document_id=body.document_id,
        question_ids=body.question_ids, only_missing=body.only_missing,
        apply=body.apply, top_k=body.top_k, apply_threshold=body.apply_threshold,
    )
    return _wrap(result)


@router.post("/{question_id}/kp-candidates")
async def question_kp_candidates(question_id: int, body: KpCandidatesRequest,
                                current_user: dict = Depends(require_teacher)):
    """单题知识点候选（字面匹配 + 向量召回 + 图谱扩展，融合打分）。

    不可用的证据层不报错，只在 `meta.graph_available / vector_available` 中如实标注。
    """
    return _wrap(QuestionService.kp_candidates(
        current_user["user_id"], question_id, top_k=body.top_k,
    ))


@router.get("/{question_id}")
async def get_question(question_id: int, current_user: dict = Depends(require_teacher)):
    """题目详情（教师视角）"""
    return _wrap(QuestionService.get_question(current_user["user_id"], question_id))


@router.put("/{question_id}")
async def update_question(question_id: int, body: QuestionUpdate,
                          current_user: dict = Depends(require_teacher)):
    """修改题目（exclude_unset：未传字段沿用原值；显式传 document_id=null 改为课程通用题）"""
    payload = body.model_dump(exclude_unset=True)
    return _wrap(QuestionService.update_question(current_user["user_id"], question_id, payload))


@router.delete("/{question_id}")
async def delete_question(question_id: int, current_user: dict = Depends(require_teacher)):
    """删除题目（已被学生作答过的题目只做软删停用，保护答题记录）"""
    return _wrap(QuestionService.delete_question(current_user["user_id"], question_id))


@router.patch("/{question_id}/active")
async def set_question_active(question_id: int, body: QuestionActive,
                              current_user: dict = Depends(require_teacher)):
    """启用/停用题目（停用 = 移出出题池，不影响历史答题记录）"""
    return _wrap(QuestionService.set_active(current_user["user_id"], question_id, body.is_active))
