"""
知识图谱 CRUD API
"""
from fastapi import APIRouter
from ..services.kg_manager import KnowledgeGraphManager

router = APIRouter(prefix="/api/kg", tags=["知识图谱管理"])


@router.get("/all")
async def get_all_graphs():
    """获取所有课程的知识图谱"""
    kg_manager = KnowledgeGraphManager()
    graph_data = kg_manager.get_graph_data()
    return {"graph": graph_data}


@router.get("/stats")
async def get_graph_stats():
    """获取知识图谱统计信息"""
    kg_manager = KnowledgeGraphManager()
    graph_data = kg_manager.get_graph_data()
    return {
        "node_count": len(graph_data["nodes"]),
        "relation_count": len(graph_data["links"]),
        "categories": list(set(n.get("category", "其他") for n in graph_data["nodes"]))
    }
