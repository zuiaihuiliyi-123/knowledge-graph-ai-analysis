"""
知识图谱 CRUD API（legacy）

[DEPRECATED] 本模块为早期 Streamlit 原型的遗留接口，返回全局未按文档隔离的图谱
（按 name 去重，跨文档同名知识点会互相覆盖）。Vue 前端已改用 /api/v1/graph/{course_id}?document_id=，
本模块保留仅为不破坏旧代码，请勿新增依赖。Phase 9 收口：不删除、标记 deprecated。
"""
from fastapi import APIRouter
from ..services.kg_manager import KnowledgeGraphManager

router = APIRouter(prefix="/api/kg", tags=["知识图谱管理"])


@router.get("/all", deprecated=True)
async def get_all_graphs():
    """[DEPRECATED] 获取所有课程的知识图谱（全局、未按文档隔离）"""
    kg_manager = KnowledgeGraphManager()
    graph_data = kg_manager.get_graph_data()
    return {"graph": graph_data}


@router.get("/stats", deprecated=True)
async def get_graph_stats():
    """[DEPRECATED] 获取知识图谱统计信息（全局、未按文档隔离）"""
    kg_manager = KnowledgeGraphManager()
    graph_data = kg_manager.get_graph_data()
    return {
        "node_count": len(graph_data["nodes"]),
        "relation_count": len(graph_data["links"]),
        "categories": list(set(n.get("category", "其他") for n in graph_data["nodes"]))
    }
