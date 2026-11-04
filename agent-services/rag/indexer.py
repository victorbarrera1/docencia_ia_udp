"""
Indexado de chunks (IA-06): rellena `rag_chunks.embedding` y deja `embedding_model`. Idempotente: repetirlo no
duplica filas ni vuelve a llamar al proveedor por chunks ya embebidos con el modelo actual.
"""
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from config import settings
from core.llm_support import LLMCallContext, LLMNotConfigured
from core.openrouter_client import openrouter_client
from core.submission_store import StoreNotConfigured
from memory.vector_store import ChunkRow, SupabaseVectorStore, VectorStore
from rag.embedder import PrivateEmbedder
from rag.pdf_processor import chunk_with_offsets, normalize_text
from rag.rubric_text import parse_rubric, rubric_chunks
from rag.sources import RagSources, SupabaseRagSources

logger = logging.getLogger(__name__)

AGENT = "rag_embeddings"
BACKFILL_MAX = 1000


class RagNotFound(Exception):
    pass


class RagInputError(Exception):
    pass


@dataclass
class IndexReport:
    embebidos: int = 0
    omitidos_modelo_distinto: int = 0  # ya embebidos con otro modelo; solo `force` los re-embebe
    chunks: Optional[Dict[str, int]] = None  # resultado de replace_rag_chunks (rúbrica y material)


class RagIndexer:
    def __init__(self, store: VectorStore, sources: RagSources, embedder: Optional[PrivateEmbedder] = None):
        self.store, self.sources, self.embedder = store, sources, embedder or PrivateEmbedder()

    @classmethod
    def from_settings(cls) -> "RagIndexer":
        return cls(SupabaseVectorStore(), SupabaseRagSources())

    # ---------- API ----------

    def index_version(self, version_id: str, force: bool = False) -> IndexReport:
        if self.sources.version_info(version_id) is None:
            raise RagNotFound("Versión inexistente")
        return self._index_owner("version", version_id, force, IndexReport())

    def index_rubric(self, activity_id: str, force: bool = False) -> IndexReport:
        activity = self.sources.activity_info(activity_id)
        if activity is None:
            raise RagNotFound("Actividad inexistente")
        chunks = rubric_chunks(parse_rubric(activity.rubric_json))
        report = IndexReport(chunks=self.store.replace_chunks("rubric", activity_id, chunks))
        return self._index_owner("rubric", activity_id, force, report)

    def index_course_material(self, course_id: str, texto: str, force: bool = False) -> IndexReport:
        normalized = normalize_text(texto)
        if not normalized:
            raise RagInputError("El material no tiene texto.")
        if len(normalized) > settings.max_pdf_text_chars:
            raise RagInputError("El material supera el límite de texto.")
        if not self.sources.course_exists(course_id):
            raise RagNotFound("Curso inexistente")
        report = IndexReport(chunks=self.store.replace_chunks("course_material", course_id, chunk_with_offsets(normalized)))
        return self._index_owner("course_material", course_id, force, report)

    def backfill(self, limit: int = 200, force: bool = False) -> IndexReport:
        """Embebe hasta `limit` chunks pendientes de cualquier propietario (recoge los fallos del indexado automático)."""
        model = self.embedder.model_label()
        rows = self.store.chunks_to_embed(model, force=force, limit=min(max(limit, 1), BACKFILL_MAX))
        report = IndexReport(omitidos_modelo_distinto=0 if force else self.store.count_foreign(model))
        self._embed_rows(rows, model, report)
        return report

    # ---------- Internos ----------

    def _index_owner(self, owner_type: str, owner_id: str, force: bool, report: IndexReport) -> IndexReport:
        model = self.embedder.model_label()
        if not force:
            report.omitidos_modelo_distinto = self.store.count_foreign(model, owner_type, owner_id)
            if report.omitidos_modelo_distinto:
                logger.warning("RAG: %s chunk(s) de %s %s usan otro modelo de embedding; usar force=true",
                               report.omitidos_modelo_distinto, owner_type, owner_id)
        seen: set = set()
        while True:
            rows = [r for r in self.store.chunks_to_embed(model, force=force, owner_type=owner_type, owner_id=owner_id)
                    if r.id not in seen]
            if not rows:
                return report
            seen.update(r.id for r in rows)
            if self._embed_rows(rows, model, report) == 0:
                return report  # nada se pudo escribir (p. ej. el texto cambió): no insistir

    def _scope(self, owner_type: str, owner_id: str) -> Optional[Tuple[Optional[str], List[Dict[str, Any]]]]:
        """(section_id, estudiantes a seudonimizar) del propietario; None si ya no existe."""
        if owner_type == "version":
            info = self.sources.version_info(owner_id)
            return (info.section_id, self.sources.roster_for_section(info.section_id)) if info else None
        if owner_type == "rubric":
            activity = self.sources.activity_info(owner_id)
            return (activity.section_id, self.sources.roster_for_section(activity.section_id)) if activity else None
        return None, self.sources.roster_for_course(owner_id)

    def _embed_rows(self, rows: List[ChunkRow], model: str, report: IndexReport) -> int:
        """Embebe y guarda por lotes (el progreso queda guardado si un lote falla). Devuelve cuántos guardó."""
        if rows and not self.embedder.is_configured():
            raise LLMNotConfigured("OPENROUTER_API_KEY no configurada")
        groups: Dict[Tuple[str, str], List[ChunkRow]] = {}
        for row in rows:
            groups.setdefault((row.owner_type, row.owner_id), []).append(row)
        saved = 0
        for (owner_type, owner_id), owned in groups.items():
            scope = self._scope(owner_type, owner_id)  # si falla, no se envía nada (falla cerrado)
            if scope is None:
                logger.warning("RAG: propietario %s %s ya no existe; se omite", owner_type, owner_id)
                continue
            section_id, people = scope
            context = LLMCallContext(
                agente=AGENT, section_id=section_id, version_id=owner_id if owner_type == "version" else None)
            size = settings.embedding_batch_size
            for start in range(0, len(owned), size):
                batch = owned[start:start + size]
                embedded = self.embedder.embed([r.texto for r in batch], people, context)
                written = self.store.set_embeddings(embedded.model, list(zip(batch, embedded.vectors)))
                saved += written
                report.embebidos += written
        return saved


def auto_index_version(version_id: str) -> None:
    """Tarea en segundo plano tras subir una versión. Nunca lanza: si falla, los chunks quedan NULL para el backfill."""
    try:
        if not openrouter_client.is_configured():
            logger.info("RAG: indexado automático omitido (OpenRouter sin configurar)")
            return
        report = RagIndexer.from_settings().index_version(version_id)
        logger.info("RAG: versión %s indexada (%s chunks)", version_id, report.embebidos)
    except StoreNotConfigured:
        logger.info("RAG: indexado automático omitido (Supabase sin configurar)")
    except Exception as exc:  # noqa: BLE001 - un fallo aquí no debe afectar la subida; el backfill reintenta
        logger.error("RAG: indexado automático de la versión %s falló (%s)", version_id, type(exc).__name__)
