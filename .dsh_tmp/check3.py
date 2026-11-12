import json, io, glob, os
f = [x for x in glob.glob('backend/eval_data/eval_report_*v15*.json') if '7' in os.path.basename(x)]
print(f)
d = json.load(io.open(f[0], encoding='utf-8'))
docs = d.get('documents')
print(json.dumps(docs, ensure_ascii=False)[:900])
per = d.get('documents_per_run')
print('per_run:', json.dumps(per, ensure_ascii=False)[:600])
print('summary:', json.dumps(d.get('summary'), ensure_ascii=False)[:800])
