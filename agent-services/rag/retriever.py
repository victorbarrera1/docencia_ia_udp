"""
Recuperación contextual (RAG) sobre pgvector (IA-06).

`RagRetriever.retrieve_for_criterion` busca por criterio en la versión actual y en la anterior de una entrega.
`CourseKnowledgeRetriever.retrieve_context` conserva la interfaz histórica (material de curso, por código).
El texto recuperado es el original (los offsets de las citas dependen de él): quien lo envíe a un LLM debe
volver a seudonimizarlo antes (IA-08).
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from core.llm_support import LLMCallContext
from memory.vector_store import ChunkHit, SupabaseVectorStore, VectorStore
from rag.embedder import PrivateEmbedder
from rag.indexer import AGENT, RagNotFound
from rag.pdf_processor import DocumentChunk
from rag.sources import RagSources, SupabaseRagSources

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 3
MAX_TOP_K = 20


@dataclass
class CriterionContext:
    current: List[ChunkHit] = field(default_factory=list)
    previous: List[ChunkHit] = field(default_factory=list)
    previous_version_id: Optional[str] = None


class RagRetriever:
    def __init__(self, store: VectorStore, sources: RagSources, embedder: Optional[PrivateEmbedder] = None):
        self.store, self.sources, self.embedder = store, sources, embedder or PrivateEmbedder()

    @classmethod
    def from_settings(cls) -> "RagRetriever":
        return cls(SupabaseVectorStore(), SupabaseRagSources())

    def retrieve_for_criterion(self, criterion: str, version_id: str, k: int = DEFAULT_TOP_K) -> CriterionContext:
        """
        Top-k de la versión `version_id` y de la inmediatamente anterior de la MISMA entrega. Los propietarios
        salen de la base a partir de `version_id`: el llamador no puede pedir chunks de otra entrega.
        """
        k = min(max(k, 1), MAX_TOP_K)
        info = self.sources.version_info(version_id)
        if info is None:
            raise RagNotFound("Versión inexistente")
        query = self.embed_query(criterion, self.sources.roster_for_section(info.section_id),
                                  LLMCallContext(agente=AGENT, section_id=info.section_id, version_id=version_id))
        previous_id = self.sources.previous_version_id(version_id)
        return CriterionContext(
            current=self.store.match(query, [version_id], k),
            previous=self.store.match(query, [previous_id], k) if previous_id else [],
            previous_version_id=previous_id,
        )

    def embed_query(self, text: str, people: list, context: Optional[LLMCallContext]) -> List[float]:
        return self.embedder.embed([text], people, context).vectors[0]


class CourseKnowledgeRetriever:
    """Material oficial de cátedra por código de curso (CIT3203, ...), vía `course_material` en pgvector."""

    def __init__(self, retriever: Optional[RagRetriever] = None):
        self._retriever = retriever

    def retrieve_context(self, course_code: str, query: str, top_k: int = 2) -> List[DocumentChunk]:
        """Fragmentos más relevantes del material del curso. Sin material, sin configuración o ante un fallo: []."""
        try:
            retriever = self._retriever or RagRetriever.from_settings()
            code = course_code.strip().upper()
            course_ids = retriever.sources.courses_by_code(code)
            if not course_ids or not query.strip():
                return []
            vector = retriever.embed_query(query, retriever.sources.roster_for_course(course_ids[0]), LLMCallContext(agente=AGENT))
            hits = retriever.store.match(vector, course_ids, min(max(top_k, 1), MAX_TOP_K))
        except Exception as exc:  # noqa: BLE001 - el contexto es opcional: el agente sigue sin él
            logger.warning("RAG: sin contexto de curso (%s)", type(exc).__name__)
            return []
        return [DocumentChunk(content=h.texto, source=f"Material del curso {code}", page=1,
                              metadata={"chunk_id": h.id, "idx": h.idx, "similarity": h.similarity})
                for h in hits]


course_retriever = CourseKnowledgeRetriever()
