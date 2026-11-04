"""
Único camino de texto hacia el endpoint de embeddings (IA-06).

Es la primera vez que texto de estudiantes sale hacia un proveedor externo, así que aquí se seudonimiza
(core/privacy.py) ANTES de cada llamada y se verifica que el resultado no cambie al repasarlo. Si algo falla,
no se envía nada. Nadie más debe llamar a `OpenRouterClient.embeddings` (lo vigila tests/test_embedder.py).
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from config import settings
from core.llm_support import LLMCallContext
from core.openrouter_client import OpenRouterClient, openrouter_client
from core.privacy import pseudonymize


class PrivacyGuardError(Exception):
    """El texto seudonimizado todavía contiene datos personales conocidos; se aborta antes de enviar."""


@dataclass(frozen=True)
class EmbeddedTexts:
    vectors: List[List[float]]
    model: str  # etiqueta para rag_chunks.embedding_model


class PrivateEmbedder:
    def __init__(self, client: Optional[OpenRouterClient] = None):
        self._client = client or openrouter_client

    def is_configured(self) -> bool:
        return self._client.is_configured()

    @staticmethod
    def model_label() -> str:
        """Valor que `embed` deja en rag_chunks.embedding_model con la configuración actual."""
        return f"fake/{settings.embedding_model}" if settings.llm_fake else settings.embedding_model

    def embed(
        self, texts: Sequence[str], known_people: List[Dict[str, Any]], context: Optional[LLMCallContext] = None
    ) -> EmbeddedTexts:
        """
        Seudonimiza cada texto con `known_people` (filas de `students` de la sección, ordenadas por id) y pide los
        vectores por lotes de `embedding_batch_size`. `known_people` es obligatorio: usar `[]` es una decisión
        explícita del llamador (solo RUT, correos y teléfonos se detectan sin lista).
        """
        safe = [self._pseudonymize(text, known_people) for text in texts]  # todo o nada, antes de salir a la red
        vectors: List[List[float]] = []
        model = ""
        size = settings.embedding_batch_size
        for start in range(0, len(safe), size):
            batch = self._client.embeddings(safe[start:start + size], context=context)
            vectors.extend(batch.vectors)
            model = batch.model
        return EmbeddedTexts(vectors, model)

    @staticmethod
    def _pseudonymize(text: str, known_people: List[Dict[str, Any]]) -> str:
        safe, _ = pseudonymize(text, known_people)
        if pseudonymize(safe, known_people)[0] != safe:
            raise PrivacyGuardError("La seudonimización no es estable; se cancela el envío.")
        return safe
