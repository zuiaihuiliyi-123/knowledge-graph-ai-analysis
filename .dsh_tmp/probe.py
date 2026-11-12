import sqlite3, glob, os, json

for p in glob.glob('backend/data/*.db'):
    print("==", p, os.path.getsize(p))
    c = sqlite3.connect(p)
    ts = [r[0] for r in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")]
    print("tables:", len(ts))
    print(ts)
    for t in ts:
        try:
            n = c.execute(f"select count(*) from {t}").fetchone()[0]
            print(f"  {t}: {n}")
        except Exception as e:
            print("  err", t, e)
    c.close()
