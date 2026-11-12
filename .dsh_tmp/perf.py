import json, io, glob

d = json.load(io.open('backend/eval_data/perf_timing.json', encoding='utf-8'))
print('kind:', d.get('kind'), 'generated:', d.get('generated_at'))
print('params:', json.dumps(d.get('params'), ensure_ascii=False))
for f in d.get('files', []):
    print(f"- {f['file']} | {f['format']} | {f['size_bytes']}B | chars={f.get('parsed_chars')} | chunks={f.get('chunks')} | mean={f.get('total_mean_s')} max={f.get('total_max_s')}")
print('overall:', json.dumps({k: v for k, v in d.items() if k not in ('files',)}, ensure_ascii=False)[:800])
