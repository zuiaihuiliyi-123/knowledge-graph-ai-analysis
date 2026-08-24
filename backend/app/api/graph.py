"""
图谱查询 API（对齐规划文档 6.3.2）
"""
from fastapi import APIRouter, Query

from ..core.response import success, error
from ..services.kg_manager import KnowledgeGraphManager

router = APIRouter(prefix="/api/v1/graph", tags=["知识图谱"])


@router.get("/{course_id}")
async def get_graph(
    course_id: str,
    limit: int = Query(500, ge=1, le=2000, description="节点数量上限"),
    node_type: str = Query(None, description="按类别过滤：概念/定理/公式/方法"),
):
    """获取指定课程的知识图谱数据（节点 + 关系）"""
    # course_id 统一为整数（对齐决策：SQLite 自增整数 -> Neo4j 存整数 -> API 传整数）
    try:
        course_id_int = int(course_id)
    except ValueError:
        return error(1001, f"course_id 必须为整数，收到: {course_id}")
    try:
        graph = KnowledgeGraphManager.get_graph_v1(course_id_int, limit=limit, node_type=node_type)
    except Exception as e:
        return error(3000, f"图谱查询失败: {str(e)}")
    return success(graph)
