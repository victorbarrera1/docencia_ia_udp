"""
Contexto que ve el evaluador (IA-08): rúbrica, versión actual y fragmentos de la anterior.

- Versión actual: completa si cabe en `max_chars`; si no, los fragmentos que RAG (IA-06) recupera por criterio.
- Versión anterior: fragmentos RAG por criterio (o completa si es corta y RAG no responde).
Los fragmentos se toman del `texto_extraido` ORIGINAL por offsets (RAG devuelve texto original); el documento
completo se seudonimiza después, una sola vez (evidence.pseudonymize_document).
"""
import logging
import secrets
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from agents.specialists.evidence import EvidenceBuilder, EvidenceDocument
from core.models import RubricItem
from core.review_store import StoredVersion, VersionContext
from rag.retriever import RagRetriever

logger = logging.getLogger("submission_evaluator")

Ranges = List[Tuple[int, int]]


def _merge(ranges: Sequence[Tuple[int, int]], budget: int, length: int) -> Ranges:
    """Une rangos solapados o contiguos (acotados a [0, length)), en orden del texto, hasta `budget` caracteres."""
    merged: Ranges = []
    clamped = ((max(s, 0), min(e, length)) for s, e in ranges)
    for start, end in sorted(r for r in clamped if r[1] > r[0]):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    kept, used = [], 0
    for start, end in merged:
        if used >= budget:
            break
        end = min(end, start + budget - used)
        kept.append((start, end))
        used += end - start
    return kept


def _rag_ranges(
    retriever_factory: Callable[[], RagRetriever], rubric: List[RubricItem], version_id: str, top_k: int
) -> Optional[Dict[str, Ranges]]:
    """Rangos {"actual": [...], "anterior": [...]} recuperados por criterio. None si RAG no está disponible."""
    try:
        retriever = retriever_factory()
        found: Dict[str, Ranges] = {"actual": [], "anterior": []}
        for item in rubric:
            hits = retriever.retrieve_for_criterion(f"{item.criterio}. {' '.join(item.descriptores)}", version_id, top_k)
            found["actual"] += [(h.char_start, h.char_end) for h in hits.current]
            found["anterior"] += [(h.char_start, h.char_end) for h in hits.previous]
        return found
    except Exception as exc:  # noqa: BLE001 - el contexto RAG es opcional: se sigue con texto completo o truncado
        logger.warning("Evaluador: RAG no disponible (%s); se usa el texto disponible", type(exc).__name__)
        return None


def _add_version(builder: EvidenceBuilder, label: str, version_id: str, texto: str, ranges: Ranges, note: str, mark: str) -> None:
    builder.text(f"<<<ENTREGA versión={label} id={mark}{note}>>>\n")
    for i, (start, end) in enumerate(ranges):
        if len(ranges) > 1 or start > 0 or end < len(texto):
            builder.text(f"[fragmento {i + 1}]\n")
        builder.segment(label, version_id, start, texto[start:end])
        builder.text("\n")
    builder.text(f"<<<FIN id={mark}>>>\n\n")


def build_evidence(
    ctx: VersionContext, previous: Optional[StoredVersion], rubric: List[RubricItem], title: str,
    retriever_factory: Callable[[], RagRetriever], max_chars: int, top_k: int,
) -> EvidenceDocument:
    mark = secrets.token_hex(4)  # impredecible: la entrega no puede imitar el cierre de la evidencia
    current_fits = len(ctx.texto) <= max_chars
    rag = None if current_fits and previous is None else _rag_ranges(retriever_factory, rubric, ctx.version_id, top_k)

    builder = EvidenceBuilder().text(f"Actividad: {title or 'sin título'}\n\nRúbrica oficial:\n")
    for item in rubric:
        descriptors = "; ".join(d for d in item.descriptores if d.strip())
        builder.text(f"- {item.criterio} (máx. {item.puntaje_max:g} pts){': ' + descriptors if descriptors else ''}\n")
    if ctx.atrasada:
        builder.text("\nNota de contexto: la versión actual se entregó fuera de plazo.\n")
    builder.text("\n")

    if current_fits:
        current, note = [(0, len(ctx.texto))], ""
    else:
        current = _merge((rag or {}).get("actual", []), max_chars, len(ctx.texto))
        note = " (fragmentos relevantes)"
        if not current:
            current, note = [(0, max_chars)], " (texto truncado)"
            logger.warning("Evaluador: versión %s truncada a %s caracteres (sin RAG)", ctx.version_id, max_chars)
    _add_version(builder, "actual", ctx.version_id, ctx.texto, current, note, mark)

    if previous is not None and previous.texto.strip():
        budget = max_chars // 2
        earlier = _merge((rag or {}).get("anterior", []), budget, len(previous.texto))
        if not earlier and len(previous.texto) <= budget:
            earlier = [(0, len(previous.texto))]
        if earlier:
            _add_version(builder, "anterior", previous.version_id, previous.texto, earlier, " (para comparar avances)", mark)
    return builder.build()
