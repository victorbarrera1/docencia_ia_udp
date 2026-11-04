import io
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

from config import settings

logger = logging.getLogger(__name__)

class DocumentChunk:
    def __init__(self, content: str, source: str, page: int = 1, metadata: Dict[str, Any] = None):
        self.content = content
        self.source = source
        self.page = page
        self.metadata = metadata or {}

class CourseDocumentProcessor:
    """
    Procesador de documentos académicos (Syllabus, Cátedras, Solemnes anteriores).
    Divide textos en fragmentos semánticos respetando encabezados y carga cognitiva.
    """
    def __init__(self, chunk_size: int = 500, overlap: int = 50):
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk_text(self, text: str, source_name: str) -> List[DocumentChunk]:
        """Divide un texto plano en fragmentos con solapamiento."""
        words = text.split()
        chunks = []
        page = 1

        for i in range(0, len(words), self.chunk_size - self.overlap):
            chunk_words = words[i:i + self.chunk_size]
            chunk_str = " ".join(chunk_words)
            if len(chunk_str.strip()) > 30:
                chunks.append(DocumentChunk(
                    content=chunk_str,
                    source=source_name,
                    page=page,
                    metadata={"word_count": len(chunk_words)}
                ))
            if i > 0 and i % 800 == 0:
                page += 1

        return chunks

document_processor = CourseDocumentProcessor()


# --- IA-05: extracción de entregas en PDF con offsets sobre el texto normalizado ---

MIN_ALNUM_CHARS = 20  # por debajo de esto el PDF se considera sin texto (escaneado)


class PdfError(Exception):
    """Error de ingesta con código estable; el mensaje es apto para el cliente (sin detalles internos)."""
    code = "PDF_ERROR"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class PdfInvalidoError(PdfError):
    code = "PDF_INVALIDO"


class PdfSinTextoError(PdfError):
    code = "PDF_SIN_TEXTO"


class PdfDemasiadoLargoError(PdfError):
    code = "PDF_DEMASIADO_LARGO"


@dataclass(frozen=True)
class TextChunk:
    """Fragmento con offsets [char_start, char_end) en caracteres Unicode del texto normalizado."""
    idx: int
    texto: str
    char_start: int
    char_end: int


@dataclass(frozen=True)
class ExtractedPdf:
    text: str
    pages: int


_HSPACE_RE = re.compile(r"[^\S\n]+")
_BLANKS_RE = re.compile(r"\n{3,}")


def normalize_text(raw: str) -> str:
    """
    Normaliza el texto extraído; el resultado es el que se persiste en `texto_extraido` y la base
    sobre la que se calculan todos los offsets (chunks y citas). NFC, sin caracteres de control ni
    NUL (Postgres no los admite), espacios colapsados y como máximo una línea en blanco seguida.
    """
    text = unicodedata.normalize("NFC", raw).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(
        ch for ch in text
        if ch == "\n" or ch.isspace() or unicodedata.category(ch) not in ("Cc", "Cs", "Cf", "Co", "Cn")
    )
    text = _HSPACE_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANKS_RE.sub("\n\n", text).strip()


def extract_pdf_text(
    data: bytes, max_pages: Optional[int] = None, max_chars: Optional[int] = None
) -> ExtractedPdf:
    """Extrae y normaliza el texto de un PDF. Lanza PdfError con mensajes aptos para el cliente."""
    from pypdf import PdfReader  # import perezoso: solo el endpoint de ingesta lo necesita

    max_pages = max_pages or settings.max_pdf_pages
    max_chars = max_chars or settings.max_pdf_text_chars
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise PdfInvalidoError("El PDF está protegido con contraseña.")
        pages = len(reader.pages)
        if pages > max_pages:
            raise PdfDemasiadoLargoError("El PDF tiene demasiadas páginas.")
        parts: List[str] = []
        total = 0
        for page in reader.pages:
            part = page.extract_text() or ""
            total += len(part)
            if total > max_chars:
                raise PdfDemasiadoLargoError("El PDF contiene demasiado texto.")
            parts.append(part)
    except PdfError:
        raise
    except Exception as exc:  # pypdf lanza tipos muy variados ante archivos corruptos
        logger.warning("PDF ilegible (%s)", type(exc).__name__)
        raise PdfInvalidoError("No se pudo leer el PDF.") from None

    text = normalize_text("\n\n".join(parts))
    if sum(ch.isalnum() for ch in text) < MIN_ALNUM_CHARS:
        raise PdfSinTextoError("El PDF no contiene texto extraíble (¿está escaneado?).")
    return ExtractedPdf(text=text, pages=pages)


def chunk_with_offsets(
    text: str, chunk_chars: Optional[int] = None, overlap_chars: Optional[int] = None
) -> List[TextChunk]:
    """
    Divide `text` (ya normalizado) en fragmentos de ~`chunk_chars` con solape, cortando en límites
    de palabra. Invariante: `text[c.char_start:c.char_end] == c.texto` para cada fragmento, sin
    espacios en los extremos.
    """
    size = chunk_chars or settings.chunk_chars
    overlap = settings.chunk_overlap_chars if overlap_chars is None else overlap_chars
    if overlap >= size:
        raise ValueError("El solape debe ser menor que el tamaño del fragmento.")
    n = len(text)
    chunks: List[TextChunk] = []
    start = 0
    while start < n:
        while start < n and text[start].isspace():
            start += 1
        if start >= n:
            break
        end = min(start + size, n)
        if end < n and not text[end].isspace():
            cut = end
            while cut > start + size // 2 and not text[cut - 1].isspace():
                cut -= 1
            if cut > start + size // 2:
                end = cut  # corta antes de la palabra incompleta
        while end > start and text[end - 1].isspace():
            end -= 1
        chunks.append(TextChunk(idx=len(chunks), texto=text[start:end], char_start=start, char_end=end))
        if end >= n:
            break
        nxt = end - overlap
        while nxt < end and nxt > 0 and not text[nxt - 1].isspace():
            nxt += 1  # el solape arranca en inicio de palabra
        start = nxt if nxt > start else end
    return chunks

