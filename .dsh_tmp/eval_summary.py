import json, io, glob, os

def summ(path):
    d = json.load(io.open(path, encoding='utf-8'))
    st = d.get('stability', {})
    out = {'file': os.path.basename(path), 'runs': d.get('runs'), 'warnings': d.get('warnings')}
    for k in ('relation_primary_f1', 'relation_primary_precision', 'relation_primary_recall',
              'entity_f1', 'entity_precision', 'entity_recall', 'entity_category_accuracy'):
        if k in st and isinstance(st[k], dict):
            out[k] = {kk: st[k].get(kk) for kk in ('mean', 'std', 'min', 'max')}
    return out

for pat in ('backend/eval_data/eval_report_pred_*_v18.json', 'backend/eval_data/eval_report_第7章_树_v15.json'):
    for f in sorted(glob.glob(pat)):
        print(json.dumps(summ(f), ensure_ascii=False, indent=1))
        print('-' * 60)
