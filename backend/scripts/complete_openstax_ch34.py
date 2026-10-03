"""
OpenStax 化学/物理 章节 缺失题目答案补全
读取 _work_translate 下的中文题目和教材答案, 用LLM补全缺失题答案
输出到 _work_translate 下的 补全_*.json
"""
import json
import os
import time

from _common import BASE, llm_json, parse_nums, question_numbers, split_phys_sections

T = os.path.join(BASE, 'data', 'sample_docs', '_work_translate')

SYS_TEMPLATE = """你是一位大学{book}课程的助教，负责为《{book_en}》教材章末习题补全参考答案。
要求：
1. 用中文回答，简洁直接，风格与教材标准答案一致；有 (a)(b)(c) 分项的题按分项作答
2. 计算题必须给出数值结果和单位，必要时保留简要计算过程
3. 概念题给出准确简明的判断与理由
4. 只输出 JSON 对象，键为题号字符串，值为答案字符串，不要输出任何其他内容"""

CHEM_SYS = SYS_TEMPLATE.format(book='化学', book_en='Chemistry 2e')
PHYS_SYS = SYS_TEMPLATE.format(book='物理', book_en='College Physics 2e')


def read_q_a(subj, ch):
    """读题目与教材答案文件，返回 (题目文本, 已有答案题号集合)"""
    with open(os.path.join(T, f'{subj}_ch{ch}_questions_cn.txt'), encoding='utf-8') as f:
        q = f.read()
    with open(os.path.join(T, f'{subj}_ch{ch}_answer_cn.txt'), encoding='utf-8') as f:
        an = question_numbers(f.read())
    return q, an


def batch(nums, need, system, tag):
    results = {}
    for i in range(0, len(nums), 10):
        bn = nums[i:i+10]
        data = llm_json(system, '\n'.join(f'{n}. {need[n]}' for n in bn))
        if data is None:
            print(f'[{tag}] 批次 {bn[0]}-{bn[-1]} 失败', flush=True)
            continue
        missing = set(bn) - {int(k) for k in data}
        if missing:
            print(f'[{tag}] 批次 {bn[0]}-{bn[-1]} 题号不齐: {sorted(missing)}', flush=True)
        results.update(data)
        print(f'[{tag}] 批次 {bn[0]}-{bn[-1]} 完成', flush=True)
        time.sleep(1)
    return results


def run():
    # ===== 化学: 单一题号序列 =====
    for ch in ['3', '4']:
        q, an = read_q_a('chem', ch)
        items = parse_nums(q)
        need = {n: items[n] for n in sorted(items) if n not in an}
        print(f'化学Ch{ch}: 需补全 {len(need)} 道', flush=True)
        results = batch(sorted(need), need, CHEM_SYS, f'chem{ch}')
        with open(os.path.join(T, f'chem_ch{ch}_补全.json'), 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
        print(f'化学Ch{ch}: 补全 {len(results)}/{len(need)}', flush=True)

    # ===== 物理: 概念题 + 习题两套编号 =====
    for ch in ['3', '4']:
        q, an = read_q_a('phys', ch)
        cq_text, pe_text = split_phys_sections(q)
        cq_items = parse_nums(cq_text)
        pe_items = parse_nums(pe_text)
        cq_need = cq_items                              # 概念题全部补（教材不提供答案；batch 只读不改）
        pe_need = {n: pe_items[n] for n in sorted(pe_items) if n not in an}
        print(f'物理Ch{ch}: 概念题补 {len(cq_need)}, 习题补 {len(pe_need)}', flush=True)
        cq_r = batch(sorted(cq_need), cq_need, PHYS_SYS, f'phys{ch}cq')
        pe_r = batch(sorted(pe_need), pe_need, PHYS_SYS, f'phys{ch}pe')
        with open(os.path.join(T, f'phys_ch{ch}_概念题补全.json'), 'w', encoding='utf-8') as f:
            json.dump(cq_r, f, ensure_ascii=False, indent=1)
        with open(os.path.join(T, f'phys_ch{ch}_习题补全.json'), 'w', encoding='utf-8') as f:
            json.dump(pe_r, f, ensure_ascii=False, indent=1)
        print(f'物理Ch{ch}: 概念题 {len(cq_r)}/{len(cq_need)}, 习题 {len(pe_r)}/{len(pe_need)}', flush=True)
    print('ALL_DONE')


if __name__ == '__main__':
    run()
