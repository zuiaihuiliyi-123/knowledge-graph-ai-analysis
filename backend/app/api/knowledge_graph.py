"""
知识图谱 CRUD API（legacy）

[DEPRECATED] 本模块为早期 Streamlit 原型的遗留接口，返回全局未按文档隔离的图谱
（按 name 去重，跨文档同名知识点会互相覆盖）。Vue 前端已改用 /api/v1/graph/{course_id}?document_id=，
本模块保留仅为不破坏旧代码，请勿新增依赖。Phase 9 收口：不删除、标记 deprecated。

课程中心改造：这两个端点原本【完全没有鉴权】，任何人（含未登录）都能拿到全库图谱，
与「学生只能查看自己加入课程的图谱」直接冲突。后补的教师闸门仍不够：任何教师都能
一次性导出全平台图谱。现改为**仅管理员**可访问；如需按课程导出，请使用
/api/v1/graph/{course_id}?document_id= 并走课程权限校验。
"""
from fastapi import APIRouter, Depends
from ..core.dependencies import require_admin
from ..services.kg_manager import KnowledgeGraphManager

router = APIRouter(prefix="/api/kg", tags=["知识图谱管理"])


@router.get("/all", deprecated=True)
async def get_all_graphs(current_user: dict = Depends(require_admin)):
    """[DEPRECATED] 获取所有课程的知识图谱（全局、未按文档隔离；仅管理员）"""
    kg_manager = KnowledgeGraphManager()
    graph_data = kg_manager.get_graph_data()
    return {"graph": graph_data}


@router.get("/stats", deprecated=True)
async def get_graph_stats(current_user: dict = Depends(require_admin)):
    """[DEPRECATED] 获取知识图谱统计信息（全局、未按文档隔离；仅管理员）"""
    kg_manager = KnowledgeGraphManager()
    graph_data = kg_manager.get_graph_data()
    return {
        "node_count": len(graph_data["nodes"]),
        "relation_count": len(graph_data["links"]),
        "categories": list(set(n.get("category", "其他") for n in graph_data["nodes"]))
    }
