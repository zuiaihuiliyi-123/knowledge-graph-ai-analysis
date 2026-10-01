"""文档级知识融合与实体消歧（抽取后的独立后处理层）。

设计边界（务必先读）：

- **V1.1 抽取器冻结**：本包**不修改** `services/knowledge_extractor.py` 的任何逻辑、
  Prompt、实体类别、关系类型与阈值；只导入复用它的 `_normalize_entity_name`。
- **Neo4j 全程只读**：融合状态只落 SQLite（`t_kp_fusion_*`），**不删节点、不删关系、
  不新增关系类型**。读图时由 `kg_manager.get_graph_v1` 在 Python 层折叠。
- **隔离边界是 `(course_id, document_id)`**：所有读写都必须带上这两个值，
  禁止跨文档/跨课程融合（Neo4j 的 MERGE 键本就含 `document_id`，这里保持一致）。
"""
