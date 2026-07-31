"""
课程管理 API
"""
import os
import uuid
from fastapi import APIRouter, UploadFile, File, HTTPException
from ..core.config import settings
from ..services.document_parser import DocumentParser
from ..services.knowledge_extractor import KnowledgeExtractor
from ..services.kg_manager import KnowledgeGraphManager

router = APIRouter(prefix="/api/courses", tags=["课程管理"])


@router.post("/upload")
async def upload_course_document(
    file: UploadFile = File(...),
    course_name: str = None
):
    """
    教师上传课程文档，自动提取知识并构建图谱
    """
    # 1. 校验文件格式
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {ext}。支持: {settings.ALLOWED_EXTENSIONS}"
        )

    # 2. 保存文件
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    course_id = str(uuid.uuid4())[:8]
    save_path = os.path.join(settings.UPLOAD_DIR, f"{course_id}_{file.filename}")

    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    # 3. 解析文档
    parser = DocumentParser()
    try:
        text = await parser.parse(save_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文档解析失败: {str(e)}")

    # 4. LLM提取知识
    extractor = KnowledgeExtractor()
    try:
        result = await extractor.extract(text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"知识提取失败: {str(e)}")

    # 5. 构建知识图谱
    kg_manager = KnowledgeGraphManager()
    build_result = kg_manager.build_graph(
        course_id=course_id,
        entities=result.get("entities", []),
        relations=result.get("relations", [])
    )

    return {
        "course_id": course_id,
        "course_name": course_name or file.filename,
        "file_name": file.filename,
        "extraction": {
            "entity_count": len(result.get("entities", [])),
            "relation_count": len(result.get("relations", [])),
            "raw_result": result
        },
        "graph": build_result
    }


@router.get("/{course_id}/graph")
async def get_course_graph(course_id: str):
    """获取课程知识图谱数据"""
    kg_manager = KnowledgeGraphManager()
    graph_data = kg_manager.get_graph_data(course_id)
    return {"course_id": course_id, "graph": graph_data}


@router.put("/{course_id}/graph/node")
async def update_node(course_id: str, name: str, properties: dict):
    """更新知识图谱节点"""
    kg_manager = KnowledgeGraphManager()
    kg_manager.update_node(name, properties)
    return {"status": "ok", "name": name}


@router.delete("/{course_id}/graph/node")
async def delete_node(course_id: str, name: str):
    """删除知识图谱节点"""
    kg_manager = KnowledgeGraphManager()
    kg_manager.delete_node(name)
    return {"status": "ok", "name": name}
