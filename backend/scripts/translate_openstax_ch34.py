"""
OpenStax 化学/物理 章节翻译脚本
将 _work_extract 下的英文正文/题目/答案翻译为中文, 输出到 _work_translate
用法: python translate_openstax_ch34.py [--start N]
断点续跑: 每完成一个文件写日志, 重跑跳过已完成
"""
import os
import sys
import time

from _common import BASE, call_llm

WORK = os.path.join(BASE, 'data', 'sample_docs', '_work_extract')
OUT = os.path.join(BASE, 'data', 'sample_docs', '_work_translate')
os.makedirs(OUT, exist_ok=True)

BODY_SYS = """你是学术教材翻译专家。将《Chemistry 2e》/《College Physics 2e》(OpenStax) 教材章节从英文翻译成中文。
要求：
1. 翻译准确严谨，术语使用中国大陆大学教材的标准译法（如 stoichiometry=化学计量、kinematics=运动学、displacement=位移）
2. 化学式、数值、单位、物理符号、公式保留原文；人名保留原文或标准译名
3. 保持原有 markdown 结构（#/##/### 标题、表格、列表符号、小节编号）
4. 图注（Figure X.X ...）和图片来源说明也要翻译
5. 只输出翻译结果，不要任何解释或前后缀"""

QUESTION_SYS = """你是学术教材翻译专家。将教材章末习题从英文翻译成中文。
要求：
1. 保持题号和小节标题（### 1.1 / #### 2.1 等）不变
2. 化学式、数值、单位、公式保留原文
3. 术语使用标准译法
4. 只输出翻译结果，保持原结构（空行、分项(a)(b)）"""

ANSWER_SYS = """你是学术教材翻译专家。将教材习题答案从英文翻译成中文。
要求：
1. 保持题号（如 "1." "3."）不变，答案翻译为中文
2. 化学式、数值、单位、公式保留原文
3. 只输出翻译结果，保持原结构"""


def chunk_text(text, size=6000):
    """按段落边界分块（按字符数，与 LLM 翻译场景匹配）"""
    paras = text.split('\n')
    chunks, cur = [], ''
    for p in paras:
        if len(cur) + len(p) + 1 > size and cur:
            chunks.append(cur)
            cur = p
        else:
            cur = (cur + '\n' + p) if cur else p
    if cur:
        chunks.append(cur)
    return chunks


def translate_file(src_name, out_name, sys_prompt):
    out_path = os.path.join(OUT, out_name)
    if os.path.exists(out_path):
        print(f'[skip] {out_name} 已存在', flush=True)
        return True
    with open(os.path.join(WORK, src_name), encoding='utf-8') as f:
        text = f.read()
    chunks = chunk_text(text)
    print(f'[翻译] {src_name}: {len(text)}字符, {len(chunks)}块', flush=True)
    parts = []
    for i, ch in enumerate(chunks):
        r = call_llm(sys_prompt, ch, max_tokens=8192)
        if r is None:
            print(f'[失败] {src_name} 第{i}块', flush=True)
            return False
        parts.append(r)
        print(f'  [{i+1}/{len(chunks)}] ok', flush=True)
        time.sleep(1)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(parts))
    print(f'[完成] {out_name}', flush=True)
    return True


# (科目, 章, 类型) 直积生成任务清单；文件名与系统提示均机械推导
KINDS = {'body': BODY_SYS, 'questions': QUESTION_SYS, 'answer': ANSWER_SYS}
JOBS = [
    (f'{s}_ch{c}_{k}_en.txt', f'{s}_ch{c}_{k}_cn.txt', KINDS[k])
    for s in ('chem', 'phys') for c in ('3', '4') for k in ('body', 'questions', 'answer')
]

start = 0
if '--start' in sys.argv:
    start = int(sys.argv[sys.argv.index('--start') + 1])

ok = True
for i, (s, o, sp) in enumerate(JOBS):
    if i < start:
        continue
    if not translate_file(s, o, sp):
        ok = False
        print(f'中断于任务 {i} ({s}), 重跑: python translate_openstax_ch34.py --start {i}')
        break
print('ALL_DONE' if ok else 'PARTIAL')
