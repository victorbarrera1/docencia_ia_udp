"""Texto indexable de `activities.rubric_json` (lógica pura): un chunk por criterio, con offsets sobre el texto completo."""
from typing import Any, List

from core.models import RubricItem
from rag.pdf_processor import TextChunk, normalize_text

SEPARATOR = "\n\n"


def parse_rubric(raw: Any) -> List[RubricItem]:
    """Acepta una lista de RubricItem o `{"rubric": [...]}`; descarta lo que no cumple el formato."""
    if isinstance(raw, dict):
        raw = raw.get("rubric") or raw.get("criterios")
    items: List[RubricItem] = []
    for entry in raw if isinstance(raw, list) else []:
        try:
            item = RubricItem.model_validate(entry)
        except ValueError:
            continue
        if item.criterio.strip():
            items.append(item)
    return items


def rubric_chunks(items: List[RubricItem]) -> List[TextChunk]:
    """
    Cada criterio se renderiza como "Criterio (máx. N pts): descriptor; descriptor". Los chunks son consecutivos
    en el texto completo de la rúbrica (criterios separados por una línea en blanco): char_start/char_end son
    offsets de ese texto. El texto es determinista: el mismo rubric_json produce los mismos chunks.
    """
    chunks: List[TextChunk] = []
    position = 0
    for item in items:
        points = f"{item.puntaje_max:g}"
        text = f"{item.criterio} (máx. {points} pts)"
        descriptors = [d for d in item.descriptores if d.strip()]
        if descriptors:
            text += ": " + "; ".join(descriptors)
        text = normalize_text(text)
        chunks.append(TextChunk(idx=len(chunks), texto=text, char_start=position, char_end=position + len(text)))
        position += len(text) + len(SEPARATOR)
    return chunks
