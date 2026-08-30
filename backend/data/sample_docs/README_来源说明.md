# 测试素材来源说明

> 本目录素材用于赛题交付物「知识抽取测试报告」与「图谱构建示例」：选取一门正式课程的一章正文（3+ chunk、12000+ tokens）。

## 素材清单

| 文件 | 内容 |
|------|------|
| `第2章_复杂度分析.txt` | 《数据结构与算法》第 2 章「复杂度分析」全文纯文本（正式测试素材） |
| `第2章_复杂度分析_chunks.json` | 按项目分块逻辑（`chunk_text_for_llm`, max_tokens=3000）切好的 chunk 清单，含每块 token 统计 |
| `raw_hello_algo/` | 原始 Markdown 源文件（转换前，供溯源与重跑转换） |
| `数据结构第一章.txt / .pdf` | 早期小规模初测素材（见 `docs/知识抽取准确率测试报告.md`） |

## 素材来源

- 来源：**《Hello 算法》**（GitHub: `krahets/hello-algo`，开源数据结构与算法教程）
- 章节：**第 2 章 复杂度分析**，含 5 节：
  - 2.1 算法效率评估（performance_evaluation.md）
  - 2.2 迭代与递归（iteration_and_recursion.md）
  - 2.3 时间复杂度（time_complexity.md）
  - 2.4 空间复杂度（space_complexity.md）
  - 2.5 本章小结（summary.md）
- 许可协议：**CC BY-NC-SA 4.0**（署名-非商业性使用-相同方式共享）

## 许可使用说明

1. 本项目为**非商业**竞赛用途（中国大学生服务外包创新创业大赛），符合 CC BY-NC-SA 的 NC 条款。
2. **署名**：本目录文件与相关报告、演示材料中需保留来源署名——"部分测试文本选自《Hello 算法》(github.com/krahets/hello-algo)，作者靳宇栋，遵循 CC BY-NC-SA 4.0 协议"。
3. **相同方式共享**：由该文本衍生的材料（如整理后的 txt、分块结果）同样以 CC BY-NC-SA 4.0 共享，请勿另设限制。
4. 正式交付时如赛事要求"教材类资料需授权"，可联系版权方（hello-algo 官方渠道）获取书面许可；或改用团队自有教材（如仓库中已上传的《面向对象程序设计》课程讲义）。

## 转换方法

`backend/scripts/prepare_chapter.py`（可复现）：
- 去除 mkdocs 多语言 tab 标记，代码实现仅保留 Python 版本（其余 11 种语言跳过）
- 图片保留说明文字、表格转为文本行、去除 HTML/markdown 标记
- 拼接章节标题后输出 txt，并按 `app/utils/text_processor.py` 的分块逻辑切块

## 规模统计（2026-08-30）

| 指标 | 数值 |
|------|------|
| 字符数 | 16,410 |
| 估算 tokens（项目口径 `estimate_tokens`） | 8,288 |
| 实际 tokens（tiktoken cl100k_base） | **14,198** ✅（目标 12000+） |
| chunk 数（max_tokens=3000） | **3** ✅（目标 3+），各块 3083 / 3087 / 2114 |
