import io, glob
for f in glob.glob('backend/eval_data/*7*.txt'):
    s = io.open(f, encoding='utf-8').read()
    print(f, 'chars=', len(s), 'no_ws=', len(''.join(s.split())), 'bytes=', len(s.encode('utf-8')))
