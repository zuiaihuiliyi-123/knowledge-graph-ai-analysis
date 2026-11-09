# Knowledge Extraction v3.0

## 目录
- `extraction.py`: 可替换/迁移的两阶段抽取服务实现。

## 接口
```python
extractor = KnowledgeExtractor()
result = await extractor.extract(text)
# 可指定领域
result = await extractor.extract(text, domain="computer_science")
```

## 需要核对的项目接口
本实现沿用上传文件中可见的依赖：
- `settings.LLM_API_KEY`
- `settings.LLM_API_BASE`
- `settings.LLM_MODEL`
- `settings.EXTRACTION_TIMEOUT`
- `settings.LLM_JSON_MODE`（可选，缺省按 True）
- `VALID_RELATION_TYPES`
- `chunk_text_for_llm(text, max_tokens=..., overlap_tokens=...)`
- `extraction_cache.enabled/build_key/load/store`

## 迁移注意
1. 将 `extraction.py` 放回原服务目录，先备份旧文件。
2. 当前 `DOMAIN_RULES` 是可运行的基础规则包。原文件中较长的 Gold 专项规则应逐条迁移到相应领域包；不要因为采用新架构而删除旧规则。
3. 新的 `entities` 会增加 `evidence`，关系会增加 `direction_basis`；若数据库写入层只读取旧字段，通常可忽略额外字段，但请检查实际入库代码。
4. `dropped_counts` 新增 `invalid_evidence`、`direction_check_failed`，评测脚本若使用固定原因白名单，需要同步扩展；旧原因名保持不变。
5. 跨块关系补充没有默认启用。建议先单独做实验，不要直接让模型凭实体清单推断关系。
6. 先用 Gold 数据比较旧版与新版 P/R/F1、方向准确率、实体召回率和请求成本，再决定切换。

## 一个重要限制
两阶段拆分降低了单次任务复杂度，但会增加调用次数；不能预先保证费用下降或 F1 一定提高。
