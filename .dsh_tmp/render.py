import pypdfium2 as pdfium
import os

src = r"C:\Users\20472\Desktop\宁波大学A10五影会谈\作品材料\S6 项目详细方案（解决方案）\项目详细方案.pdf"
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pages")
os.makedirs(out, exist_ok=True)
pdf = pdfium.PdfDocument(src)
for pno in (21, 37, 38, 102, 103, 106, 108):
    page = pdf[pno - 1]
    img = page.render(scale=2.0).to_pil()
    p = os.path.join(out, f"p{pno}.png")
    img.save(p)
    print(p, img.size)
