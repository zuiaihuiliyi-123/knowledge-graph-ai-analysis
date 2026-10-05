"""
OpenStax 化学/物理 章节 最终文件组装
读取 _work_translate 的翻译与补全结果, 生成最终中文版文件到各 openstax_* 目录
"""
import json
import os
import re

from _common import BASE, parse_nums, split_phys_sections

T = os.path.join(BASE, 'data', 'sample_docs', '_work_translate')
CHEM = os.path.join(BASE, 'data', 'sample_docs', 'openstax_化学')
PHYS = os.path.join(BASE, 'data', 'sample_docs', 'openstax_物理')


def read(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


def write(p, c):
    with open(p, 'w', encoding='utf-8') as f:
        f.write(c)


def norm_headers(c):
    """翻译输出中裸行的结构词补 ## 标题（翻译 prompt 要求保持 markdown，此处为兜底）"""
    return re.sub(r'^(?!#)(本章大纲|引言|术语表|关键术语|本章小结|章节小结)\s*$', r'## \1', c, flags=re.M)


def drop_dup_title(c):
    """删除第一行标题后可能出现的拆行残留（"物质与\n溶液的组成" 这类孤行）"""
    lines = c.split('\n')
    if len(lines) > 2 and lines[1].strip() and lines[2].strip() == '' \
            and len(lines[1].strip()) < 40 and not re.match(r'^(图|##|#|>|\d)', lines[1].strip()):
        lines.pop(1)
        if lines[1].strip() == '':
            lines.pop(1)
    return '\n'.join(lines)


def fmt_answers(items, total, extra=None):
    """按 1..total 顺序输出答案；items=教材答案(翻译), extra=补全json dict(str键)"""
    lines = []
    for n in range(1, total + 1):
        if n in items and items[n]:
            a = items[n]
        elif extra and str(n) in extra:
            a = extra[str(n)]
        else:
            a = '（暂无答案）'
        lines.append(f'{n}. {a}')
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def assemble_body(book, ch, m, out_dir):
    """正文文件组装：去翻译自带标题行 → 结构兜底 → 拼规范头部（book 为 (名称, 链接, 备注行)）"""
    body = read(os.path.join(T, m['bsrc']))
    body = drop_dup_title(norm_headers(body))
    body = re.sub(r'^#.*\n\n', '', body)  # 去掉翻译自带标题行
    name, link, notes = book
    header = f'# 第{ch}章：{m["cn"]}（{m["en"]}）\n\n> 原文：{name}，第{ch}章 {m["en"]}\n> {link}\n'
    header += ''.join(f'> {n}\n' for n in notes) + '\n'
    write(os.path.join(out_dir, f'第{ch}章_{m["cn"]}_中文版.txt'), header + body)


CHEM_BOOK = ('Chemistry 2e (OpenStax, CC BY 4.0)',
             '本书免费获取地址：http://cnx.org/content/col26069/1.5',
             ['本译文为中文翻译版，章节结构与原文一致；原文中的页码页脚已省略。'])
PHYS_BOOK = ('College Physics 2e (OpenStax, CC BY 4.0)',
             '免费获取地址：https://openstax.org/details/books/college-physics-2e',
             ['本译文为中文翻译版，章节结构与原文一致；原文中的页码页脚已省略。',
              '说明：原书公式以图片形式排版，文本提取后公式本体缺失；译文中以【公式 3.x】保留公式编号位置，公式内容请对照原书。个别按上下文可直接确定的公式以标准形式写出。'])

chem_meta = {
    '3': dict(en='Composition of Substances and Solutions', cn='物质组成与溶液',
              total=80, answer='chem_ch3_answer_cn.txt', extra='chem_ch3_补全.json',
              qsrc='chem_ch3_questions_cn.txt', bsrc='chem_ch3_body_cn.txt'),
    '4': dict(en='Stoichiometry of Chemical Reactions', cn='化学反应计量',
              total=95, answer='chem_ch4_answer_cn.txt', extra='chem_ch4_补全.json',
              qsrc='chem_ch4_questions_cn.txt', bsrc='chem_ch4_body_cn.txt'),
}
phys_meta = {
    '3': dict(en='Two-Dimensional Kinematics', cn='二维运动学',
              cq=21, pe=71, answer='phys_ch3_answer_cn.txt',
              cq_extra='phys_ch3_概念题补全.json', pe_extra='phys_ch3_习题补全.json',
              qsrc='phys_ch3_questions_cn.txt', bsrc='phys_ch3_body_cn.txt'),
    '4': dict(en='Dynamics: Force and Newton\'s Laws of Motion', cn='动力学_力与牛顿运动定律',
              cq=27, pe=55, answer='phys_ch4_answer_cn.txt',
              cq_extra='phys_ch4_概念题补全.json', pe_extra='phys_ch4_习题补全.json',
              qsrc='phys_ch4_questions_cn.txt', bsrc='phys_ch4_body_cn.txt'),
}


def assemble_chem(ch, m):
    assemble_body(CHEM_BOOK, ch, m, CHEM)
    # 测试题（化学单一题号序列，去掉题目区的 Exercises 分区行）
    q = re.sub(r'^\s*(Exercises|习题)\s*$', '', read(os.path.join(T, m['qsrc'])), flags=re.M)
    items = parse_nums(read(os.path.join(T, m['answer'])))
    with open(os.path.join(T, m['extra']), encoding='utf-8') as f:
        extra = json.load(f)
    qhead = f'''# 第{ch}章 {m["cn"]}（{m["en"]}）—— 测试题与答案（中文版）

> 教材：{CHEM_BOOK[0]}
> 题目来源：本章章末 Exercises（教材自带，共 {m["total"]} 道）
> 答案来源：书末 Answer Key（教材原书仅选答部分题目，未提供答案的题目已附参考解答）
> 本文件为中文翻译版，题目与答案均已译成中文；化学式、数值与符号保留原文。

## 一、题目（按小节分类）

'''
    write(os.path.join(CHEM, f'第{ch}章_{m["cn"]}_测试题与答案_中文版.txt'),
          qhead + q.strip() + '\n\n## 二、参考答案\n\n' + fmt_answers(items, m['total'], extra))


def assemble_phys(ch, m):
    assemble_body(PHYS_BOOK, ch, m, PHYS)
    cq_text, pe_text = split_phys_sections(read(os.path.join(T, m['qsrc'])))
    items = parse_nums(read(os.path.join(T, m['answer'])))
    with open(os.path.join(T, m['cq_extra']), encoding='utf-8') as f:
        cq_extra = json.load(f)
    with open(os.path.join(T, m['pe_extra']), encoding='utf-8') as f:
        pe_extra = json.load(f)
    qhead = f'''# 第{ch}章 {m["cn"]}（{m["en"]}）—— 测试题与答案（中文版）

> 教材：{PHYS_BOOK[0]}
> 题目来源：本章章末 Conceptual Questions + Problems & Exercises（教材自带，共 {m["cq"]} 道概念题 + {m["pe"]} 道习题）
> 本文件为中文翻译版，题目与答案均已译成中文；数值、单位与符号保留原文。
> 答案来源：书末 Answer Key（教材原书仅选答部分题目，未提供答案的题目已附参考解答）

## 一、题目（按题型分类）

### 概念题（Conceptual Questions）

{cq_text.strip()}

### 习题（Problems & Exercises）

{pe_text.strip()}

## 二、参考答案

### 概念题答案

{fmt_answers({}, m['cq'], cq_extra)}
### 习题答案

{fmt_answers(items, m['pe'], pe_extra)}
'''
    write(os.path.join(PHYS, f'第{ch}章_{m["cn"]}_测试题与答案_中文版.txt'), qhead)


if __name__ == '__main__':
    for ch, m in chem_meta.items():
        assemble_chem(ch, m)
        print(f'化学Ch{ch}: 已组装 正文+测试题')
    for ch, m in phys_meta.items():
        assemble_phys(ch, m)
        print(f'物理Ch{ch}: 已组装 正文+测试题')
    print('ALL_DONE')
