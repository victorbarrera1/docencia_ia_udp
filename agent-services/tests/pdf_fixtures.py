"""PDFs sintéticos para tests: texto ficticio, sin datos reales ni dependencias nuevas."""
import io
from typing import List

from pypdf import PdfWriter


def _escape(line: str) -> bytes:
    raw = line.encode("cp1252")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def make_text_pdf(pages: List[List[str]]) -> bytes:
    """PDF mínimo válido con una línea de texto Helvetica por elemento (WinAnsi, admite tildes)."""
    objs: List[bytes] = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    kids = []
    for lines in pages:
        stream = b"BT /F1 12 Tf 72 720 Td 14 TL\n" + b"".join(b"(" + _escape(l) + b") Tj T*\n" for l in lines) + b"ET"
        content = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
        page_no = len(objs) + 1
        objs.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
            b"/Resources << /Font << /F1 3 0 R >> >> >>" % (page_no + 1)
        )
        objs.append(content)
        kids.append(b"%d 0 R" % page_no)
    objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = b"<< /Type /Pages /Kids [" + b" ".join(kids) + b"] /Count %d >>" % len(kids)
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()


def make_blank_pdf(pages: int = 1) -> bytes:
    """PDF sin texto extraíble (equivale a una entrega escaneada sin OCR)."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def make_encrypted_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("secreto-de-prueba", algorithm="RC4-128")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


SAMPLE_LINES = [
    "Diseño de una arquitectura de microservicios para el taller.",
    "La solución propone un balanceador, tres servicios y una cola de mensajes.",
    "Conclusión: el acoplamiento se reduce mediante eventos asíncronos.",
]
