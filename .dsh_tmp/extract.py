import pdfplumber, io, sys
p = r"C:\Users\20472\Desktop\宁波大学A10五影会谈\作品材料\S6 项目详细方案（解决方案）\项目详细方案.pdf"
out = []
with pdfplumber.open(p) as pdf:
    for i, page in enumerate(pdf.pages, 1):
        t = page.extract_text() or ""
        out.append(f"\n===== PAGE {i} =====\n{t}")
        tables = page.extract_tables()
        for ti, tb in enumerate(tables, 1):
            out.append(f"--- table {ti} ---")
            for row in tb:
                out.append(" | ".join("" if c is None else str(c).replace("\n"," ") for c in row))
with io.open(r".dsh_tmp\pdf_text.txt", "w", encoding="utf-8") as f:
    f.write("".join(out))
print("done", sum(len(x) for x in out))
