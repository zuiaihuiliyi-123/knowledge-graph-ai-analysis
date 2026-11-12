import json, io, glob, os

# 1) question recall eval: bias vs filter
p = 'backend/eval_data/eval_report_question_recall.json'
d = json.load(io.open(p, encoding='utf-8'))
print('== question_recall keys:', list(d.keys())[:20])
print(json.dumps(d, ensure_ascii=False)[:1200])

# 2) text length of 第7章 in v15 report
for f in sorted(glob.glob('backend/eval_data/eval_report_*第7章_树_v15.json')) + sorted(glob.glob('backend/eval_data/eval_report_?7?*v15*.json')):
    print('== ', f)
    try:
        d2 = json.load(io.open(f, encoding='utf-8'))
        for k in ('corpus', 'text', 'dataset', 'meta', 'input'):
            if k in d2:
                print(k, json.dumps(d2[k], ensure_ascii=False)[:600])
        print('top keys:', list(d2.keys())[:25])
    except Exception as e:
        print('err', e)
