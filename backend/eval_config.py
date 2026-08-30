"""
实验参数冻结与记录。

正式评测（overlap A/B 与 Gold 精度）必须把影响结果的参数全部冻结，
并在每次抽取实验后写出 experiment_config.json，保证可复现、可追溯
（A10 明确要求提交知识抽取准确率测试报告与提示词工程完整记录）。

用法（在 backend/ 目录下）：
    from eval_config import make_config, save_config
    cfg = make_config(temperature=0, overlap_tokens=400)
    save_config(cfg, "eval_data/experiment_config.json")
"""
import json

from app.core.config import settings
from app.services.knowledge_extractor import (
    _RELATION_CONFIDENCE_THRESHOLDS,
    _DEFAULT_RELATION_CONFIDENCE,
    _CHUNK_MAX_TOKENS,
    _OVERLAP_TOKENS,
    _MAX_OUTPUT_TOKENS,
)

# Prompt 版本号：每次修改 EXTRACTION_PROMPT 时递增，便于把评测结果对应到 Prompt 版本
PROMPT_VERSION = "v1.2"


def make_config(temperature: float = 0, overlap_tokens: int = _OVERLAP_TOKENS) -> dict:
    """返回一次抽取实验的完整冻结参数快照（单一事实来源，勿在此手写重复数值）。"""
    return {
        "prompt_version": PROMPT_VERSION,
        "model": settings.LLM_MODEL,
        "temperature": temperature,
        "chunk_tokens": _CHUNK_MAX_TOKENS,
        "overlap_tokens": overlap_tokens,
        "max_output_tokens": _MAX_OUTPUT_TOKENS,
        # 注意：default_relation_confidence 仅在 LLM 未返回 confidence 时用于缺省值填充与工程数据完整性，
        # 不参与 P/R/F1 准确率计算，也不代表"系统认为该关系有 80% 准确率"。
        "default_relation_confidence": _DEFAULT_RELATION_CONFIDENCE,
        "thresholds": dict(_RELATION_CONFIDENCE_THRESHOLDS),
    }


def save_config(config: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(f"实验配置已冻结并保存到 {path}")
