import io
from pptx import Presentation
prs = Presentation(r'materials\决赛答辩PPT_v2.pptx')
out=[]
for i, s in enumerate(prs.slides, 1):
    texts=[]
    for sh in s.shapes:
        if sh.has_text_frame:
            t=sh.text_frame.text.strip()
            if t: texts.append(t.replace('\n',' | '))
        if sh.has_table:
            for r in sh.table.rows:
                texts.append(' || '.join(c.text.strip().replace('\n',' ') for c in r.cells))
    out.append(f'--- slide {i} ---\n' + '\n'.join(texts))
io.open(r'.dsh_tmp\ppt_v2.txt','w',encoding='utf-8').write('\n'.join(out))
print('slides', len(prs.slides))
