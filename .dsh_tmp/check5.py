import io, re
s = io.open('backend/app/services/question_service.py', encoding='utf-8').read()
m = [l for l in s.splitlines() if 'QUESTION_TYPES' in l or 'FILL' in l or 'ESSAY' in l]
print('question_service:', m[:6])
s2 = io.open('backend/app/services/question_recall.py', encoding='utf-8').read()
print('CHANNEL_QUOTA:', [l.strip() for l in s2.splitlines() if 'CHANNEL_QUOTA' in l or 'QUOTA' in l][:5])
print('channels:', sorted(set(re.findall(r'R\d', s2))))
