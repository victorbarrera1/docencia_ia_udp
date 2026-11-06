"""
Documento de evidencia que se envía al evaluador (IA-08): rúbrica, instrucciones y fragmentos de la versión actual
y de la anterior, seudonimizado UNA sola vez.

Un único mapping para todo el mensaje evita que un dato desconocido (RUT o correo que no está en la lista) reciba
el mismo token en dos textos distintos, y permite restaurar la respuesta y ubicar cada cita en el texto original:
cada segmento recuerda en qué versión y en qué offset de `texto_extraido` empieza.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.privacy import PseudonymMapping, pseudonymize


class PrivacyGuardError(Exception):
    """El texto seudonimizado aún contiene datos personales conocidos: no se envía nada."""


@dataclass(frozen=True)
class Segment:
    """Trozo literal de una versión dentro del documento. `start/end`: offsets en el documento original."""
    label: str  # "actual" | "anterior"
    version_id: str
    source_start: int  # offset del trozo dentro de texto_extraido de esa versión
    start: int
    end: int


@dataclass
class EvidenceDocument:
    original: str
    segments: List[Segment]
    pseudo: str = ""
    mapping: PseudonymMapping = field(default_factory=PseudonymMapping)

    def pseudo_span(self, segment: Segment) -> Tuple[int, int]:
        """Rango del segmento en el texto seudonimizado."""
        return to_pseudo(segment.start, self.mapping), to_pseudo(segment.end, self.mapping)


class EvidenceBuilder:
    def __init__(self) -> None:
        self._parts: List[str] = []
        self._segments: List[Segment] = []
        self._pos = 0

    def text(self, value: str) -> "EvidenceBuilder":
        self._parts.append(value)
        self._pos += len(value)
        return self

    def segment(self, label: str, version_id: str, source_start: int, value: str) -> "EvidenceBuilder":
        self._segments.append(Segment(label, version_id, source_start, self._pos, self._pos + len(value)))
        return self.text(value)

    def build(self) -> EvidenceDocument:
        return EvidenceDocument("".join(self._parts), list(self._segments))


def to_pseudo(pos: int, mapping: PseudonymMapping) -> int:
    """Offset del texto original → texto seudonimizado (inverso de map_offsets en límites de entidad)."""
    shift = 0
    for r in mapping.replacements:
        if pos < r.end:
            if pos > r.start:
                return r.pseudo_start  # dentro de una entidad: su token completo
            break
        shift = r.pseudo_end - r.end
    return pos + shift


def pseudonymize_document(doc: EvidenceDocument, people: List[Dict[str, Any]]) -> EvidenceDocument:
    """Seudonimiza todo el documento con la lista de la sección y verifica que el resultado sea estable."""
    doc.pseudo, doc.mapping = pseudonymize(doc.original, people)
    ensure_private(doc.pseudo, people)
    return doc


def ensure_private(text: str, people: Optional[List[Dict[str, Any]]]) -> None:
    """Falla cerrado si `text` todavía tiene un nombre, RUT, correo o teléfono detectable (mismo criterio del embedder)."""
    if pseudonymize(text, people or [])[0] != text:
        raise PrivacyGuardError("El texto contiene datos personales; se cancela el envío.")
