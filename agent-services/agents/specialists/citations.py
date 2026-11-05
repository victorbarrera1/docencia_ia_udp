"""
Validación de citas del evaluador (IA-08). Lógica pura: sin red ni base de datos.

El LLM vio el documento seudonimizado, así que cita con tokens (`[ESTUDIANTE_1]`). Para cada cita:
1. se normalizan los espacios y se busca literal en los segmentos de la versión indicada (`actual`/`anterior`);
2. el rango encontrado se lleva al documento original con `map_offsets` y de ahí al offset de `texto_extraido`;
3. se compara contra el `texto_extraido` ORIGINAL de esa versión (el que guarda la base).
Si algo no calza, la cita se descarta: una observación sin cita verificable no se guarda.
"""
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from agents.specialists.evidence import EvidenceDocument, Segment
from core.privacy import map_offsets

_WS_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"\[(?:ESTUDIANTE|RUT|CORREO|TELEFONO)_\d+\]")
_QUOTES = "\"'“”«»‘’`"
_ELLIPSIS_RE = re.compile(r"^(?:\.\.\.|…)\s*|\s*(?:\.\.\.|…)$")


@dataclass(frozen=True)
class Citation:
    version: str  # "actual" | "anterior"
    version_id: str
    texto: str  # fragmento literal del texto original (con los nombres reales)
    char_start: int
    char_end: int


def clean_quote(raw: str) -> str:
    """Quita comillas y puntos suspensivos de los extremos y colapsa espacios. No toca el interior."""
    text = (raw or "").strip()
    for _ in range(2):
        text = _ELLIPSIS_RE.sub("", text.strip(_QUOTES + " \n\t")).strip(_QUOTES + " \n\t")
    return _WS_RE.sub(" ", text)


def _collapsed(text: str) -> Tuple[str, List[int]]:
    """Texto con cada racha de espacios reducida a uno, más la posición original de cada carácter."""
    chars: List[str] = []
    index: List[int] = []
    previous_space = False
    for i, ch in enumerate(text):
        space = ch.isspace()
        if space and previous_space:
            continue
        chars.append(" " if space else ch)
        index.append(i)
        previous_space = space
    return "".join(chars), index


def _find_in_segment(doc: EvidenceDocument, segment: Segment, needle: str) -> Optional[Tuple[int, int]]:
    """Rango [inicio, fin) del texto seudonimizado donde aparece `needle` dentro del segmento."""
    p_start, p_end = doc.pseudo_span(segment)
    haystack, index = _collapsed(doc.pseudo[p_start:p_end])
    at = haystack.find(needle)
    if at < 0:
        return None
    return p_start + index[at], p_start + index[at + len(needle) - 1] + 1


def validate_citation(
    doc: EvidenceDocument, version: str, raw_quote: str, originals: Dict[str, str], min_chars: int
) -> Optional[Citation]:
    """
    Cita verificada (con offsets sobre `texto_extraido`) o None si no es literal. `originals`: version_id → texto
    original. Rechaza citas de menos de `min_chars` caracteres reales (sin contar tokens de seudonimización).
    """
    needle = clean_quote(raw_quote)
    if len(_TOKEN_RE.sub("", needle).replace(" ", "")) < min_chars:
        return None
    for segment in doc.segments:
        if segment.label != version:
            continue
        found = _find_in_segment(doc, segment, needle)
        if found is None:
            continue
        start, end = map_offsets(found[0], found[1], doc.mapping)
        if start < segment.start or end > segment.end:
            continue  # el rango se salió del segmento: no es una cita de esta versión
        source = originals.get(segment.version_id)
        char_start = segment.source_start + (start - segment.start)
        char_end = segment.source_start + (end - segment.start)
        if source is None or char_end > len(source):
            continue
        texto = source[char_start:char_end]
        if not texto.strip() or texto != doc.original[start:end]:
            continue
        return Citation(version, segment.version_id, texto, char_start, char_end)
    return None
