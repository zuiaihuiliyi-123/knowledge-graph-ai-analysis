"""
图谱查询与手动编辑 API（对齐规划文档 6.3.2 查询 + 6.3.3/6.3.4 手动编辑）
"""
from fastapi import APIRouter, Query, Depends

from ..core.response import success, error
from ..core.dependencies import get_current_user, require_teacher
from ..core.permissions import Permissions
from ..services.kg_manager import KnowledgeGraphManager
from ..schemas.graph import (
    NodeCreate,
    NodeUpdate,
    EdgeCreate,
)

router = APIRouter(prefix="/api/v1/graph", tags=["知识图谱"])


def _coerce_course_id(course_id: str) -> int:
    """course_id 统一为整数；非法则抛 ValueError"""
    try:
        return int(course_id)
    except (TypeError, ValueError):
        raise ValueError(f"course_id 必须为整数，收到: {course_id}")


def _coerce_document_id(document_id: str) -> int:
    """document_id 统一为整数；非法则抛 ValueError"""
    try:
        return int(document_id)
    except (TypeError, ValueError):
        raise ValueError(f"document_id 必须为整数，收到: {document_id}")


def _guard(course_id: str, current_user: dict, manage: bool = False):
    """课程权限闸门：返回 (cid, None) 或 (None, 错误响应)。

    课程中心改造：图谱读写原先完全不校验归属（任何登录用户猜 course_id 即可读写
    别人课程的图谱）。判定统一走 Permissions：
    - 读（get_graph）：课程成员（创建者 / 协作教师 / 已加入学生）
    - 写（节点与关系的增删改）：课程教师
    判定刻意放在任何 Neo4j 调用之前——这样即使图数据库没启动，
    无权限请求也会稳定返回 4003 而不是 3000。
    """
    try:
        cid = _coerce_course_id(course_id)
    except ValueError as e:
        return None, error(1001, str(e))
    perm = (Permissions.require_course_manage(cid, current_user) if manage
            else Permissions.require_course_content(cid, current_user))
    if not perm["ok"]:
        return None, error(perm["code"], perm["message"])
    return cid, None


# 请求体见 app/schemas/graph.py。


# ---------------- 查询 ----------------

@router.get("/{course_id}")
async def get_graph(
    course_id: str,
    document_id: str = Query(..., description="文档 ID（图谱按文档隔离，必填）"),
    limit: int = Query(500, ge=1, le=2000, description="节点数量上限"),
    node_type: str = Query(None, description="按类别过滤：概念/定理/公式/方法"),
    current_user: dict = Depends(get_current_user),
):
    """获取指定文档的知识图谱数据（节点 + 关系；仅课程成员可读）"""
    course_id_int, denied = _guard(course_id, current_user)
    if denied is not None:
        return denied
    try:
        did = _coerce_document_id(document_id)
    except ValueError as e:
        return error(1001, str(e))
    try:
        graph = KnowledgeGraphManager.get_graph_v1(course_id_int, did, limit=limit, node_type=node_type)
    except Exception as e:
        return error(3000, f"图谱查询失败: {str(e)}")
    return success(graph)


# ---------------- 手动编辑：节点 ----------------

@router.post("/{course_id}/nodes")
async def create_node(course_id: str, body: NodeCreate,
                      document_id: str = Query(..., description="文档 ID（必填）"),
                      current_user: dict = Depends(require_teacher)):
    """教师手动新增知识点（is_manual=True；仅该课程教师）"""
    cid, denied = _guard(course_id, current_user, manage=True)
    if denied is not None:
        return denied
    try:
        did = _coerce_document_id(document_id)
        node = KnowledgeGraphManager.create_node(cid, did, body.name, body.category, body.description)
    except ValueError as e:
        return error(1001, str(e))
    return success(node)


@router.put("/{course_id}/nodes/{node_id}")
async def update_node(course_id: str, node_id: str, body: NodeUpdate,
                      document_id: str = Query(..., description="文档 ID（必填）"),
                      current_user: dict = Depends(require_teacher)):
    """教师手动更新知识点（按 kp_id 定位；仅该课程教师）"""
    cid, denied = _guard(course_id, current_user, manage=True)
    if denied is not None:
        return denied
    try:
        did = _coerce_document_id(document_id)
        node = KnowledgeGraphManager.update_node(
            cid, did, node_id, name=body.name, category=body.category, description=body.description,
        )
    except ValueError as e:
        return error(1001, str(e))
    return success(node)


@router.delete("/{course_id}/nodes/{node_id}")
async def delete_node(course_id: str, node_id: str,
                      document_id: str = Query(..., description="文档 ID（必填）"),
                      current_user: dict = Depends(require_teacher)):
    """教师手动删除知识点及其关系（按 kp_id 定位；仅该课程教师）"""
    cid, denied = _guard(course_id, current_user, manage=True)
    if denied is not None:
        return denied
    try:
        did = _coerce_document_id(document_id)
        result = KnowledgeGraphManager.delete_node(cid, did, node_id)
    except ValueError as e:
        return error(1001, str(e))
    return success(result)


# ---------------- 手动编辑：关系 ----------------

@router.post("/{course_id}/edges")
async def create_edge(course_id: str, body: EdgeCreate,
                      document_id: str = Query(..., description="文档 ID（必填）"),
                      current_user: dict = Depends(require_teacher)):
    """教师手动新增关系（source/target 为 kp_id；仅该课程教师）"""
    cid, denied = _guard(course_id, current_user, manage=True)
    if denied is not None:
        return denied
    try:
        did = _coerce_document_id(document_id)
        edge = KnowledgeGraphManager.create_relationship(cid, did, body.source, body.target, body.type)
    except ValueError as e:
        return error(1001, str(e))
    return success(edge)


@router.delete("/{course_id}/edges/{edge_id}")
async def delete_edge(course_id: str, edge_id: str,
                      document_id: str = Query(..., description="文档 ID（必填）"),
                      current_user: dict = Depends(require_teacher)):
    """教师手动删除关系（按 edge_id = elementId(r)；仅该课程教师）"""
    cid, denied = _guard(course_id, current_user, manage=True)
    if denied is not None:
        return denied
    try:
        did = _coerce_document_id(document_id)
        result = KnowledgeGraphManager.delete_relationship(cid, did, edge_id)
    except ValueError as e:
        return error(1001, str(e))
    return success(result)
