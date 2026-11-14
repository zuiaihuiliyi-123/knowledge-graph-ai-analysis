# 六文档知识抽取评测产物（答辩版）

本目录是《[知识抽取准确率测试报告（六文档答辩版）](../../../docs/知识抽取准确率测试报告_六文档答辩版.md)》的原始数据与复现产物。

## 最终结果（`official_t03_r5/`，Prompt v1.9.1，deepseek-chat，temperature=0.3，5 轮）

| 文档 | 课程 | 字符 | Gold 实体 | Gold 关系 | 关系 F1（5 轮均值 ± std） |
|---|---|---:|---:|---:|---:|
| D1 数据结构·第 7 章 树 | 数据结构 | 12,416 | 49 | 33 | 0.8318 ± 0.0271 |
| D2 数据结构·第 2 章 复杂度分析 | 数据结构 | 16,410 | 40 | 47 | 0.8829 ± 0.0354 |
| D3 物理·第 2 章 运动学 | 物理 | 36,659 | 38 | 60 | 0.8305 ± 0.0326 |
| D4 化学·第 2 章 原子分子与离子 | 化学 | 40,093 | 62 | 74 | 0.8962 ± 0.0198 |
| D5 化学·第 4 章 化学反应计量 | 化学 | 30,732 | 45 | 39 | 0.7641 ± 0.0488 |
| D6 化学·第 1 章 化学基本概念 | 化学 | 33,128 | 45 | 40 | 0.7525 ± 0.0107 |
| **合计 / 均值** | **3 门课** | **169,438** | **279** | **293** | **0.8263** |

- pooled：P 0.7981 / R 0.8636 / F1 **0.8296**
- 人工抽样：关系 100/100（Wilson 95% CI [0.9630, 1.0000]）；实体 50/50（[0.9286, 1.0000]）
- PDF 独立验证：17,380 字符，45 实体 / 36 关系，人工全量复核 **31/36 = 86.1%**（CI [0.7134, 0.9392]）

## 目录结构

| 路径 | 内容 |
|---|---|
| `manifest.json` | 6 篇文档的源文件、字符数、Gold 实体/关系、形式修复记录、SHA256 |
| `gold/` / `texts/` | 冻结后的 Gold 与原文 |
| `official_t03_r5/summary.json` | 最终 5 轮汇总指标 |
| `official_t03_r5/pred/` | 6 篇 × 5 轮原始预测 |
| `official_t03_r5/eval_report/` | 6 篇完整评分 JSON |
| `official_t03_r5/report/` | 6 篇 Markdown 评分报告 |
| `official_t03_r5/logs/` | 6 篇控制台日志（含逐轮输出） |
| `official_t03_r5/manual_check/` | 人工抽样判定表（100 关系 + 50 实体）、抽样对照、元信息 |
| `official_t03_r5/manual_verdicts.json` | 人工判定结果 |
| `official_t03_r5/manual_accuracy.json/.md` | 人工准确率与 Wilson 区间 |
| `v18_official/` | **v1.8 原 Prompt 的六篇 5 轮正式复测**：整体 F1 0.7168 / pooled 0.7635；含人工抽样 |
| `v18_baseline/` | v1.8 Prompt 在 D5/D6 上的 1 轮基线（0.2947 / 0.3441），仅用于迭代对照 |
| `official_t03_r5/v19_archive_3run/` 等 | 早期 3 轮 / v1.9 结果，仅用于迭代对照，不作为最终数字 |
| `pdf/` | PDF 格式验证（v1.9.1 Prompt）：解析文本、抽取结果、36 条关系人工复核 |
| `pdf_v18/` | PDF 格式验证（v1.8 Prompt）：45 实体 / 23 关系，人工复核 17/23 |
| `v19_prompt.patch` | v1.9.1 本体扩展 Prompt 的补丁；当前仓库代码已回退到 v1.8，需要复现 v1.9.1 时先应用此补丁 |

## 复现

```bash
cd backend
python scripts/prepare_report6.py
python scripts/run_report6.py --temperature 0.3 --runs 5 --out-subdir official_t03_r5
# 人工抽样与汇总见报告 §8.1
EXTRACT_CACHE=0 python scripts/validate_pdf_format.py
```

> 注意：`run_report6.py` 设置 `EXTRACT_CACHE=0`，会真实调用 LLM；复现会产生 API 费用。
