"""Puerta de privacidad de los embeddings: lo que sale hacia el proveedor está seudonimizado, o no sale."""
import json
import re
from pathlib import Path

import httpx
import pytest

from config import settings
from core.llm_support import fake_embedding
from core.openrouter_client import OpenRouterClient
from memory.vector_store import InMemoryVectorStore
from rag.embedder import PrivateEmbedder, PrivacyGuardError
from rag.indexer import RagIndexer
from rag.pdf_processor import TextChunk
from tests.rag_fixtures import (
    FORBIDDEN, MAIL_A, PEOPLE, PHONE, RUT_A, RUT_B, RUT_UNKNOWN, SECTION, SUBMISSION, V1, FakeSources, store_down,
)

DIM = 16

TEXT = (
    f"Informe de Juan Andrés Pérez Soto ({MAIL_A}, RUT {RUT_A}). Integrante: María José Núñez Rojas, RUT {RUT_B}. "
    f"Contacto externo RUT {RUT_UNKNOWN}, teléfono {PHONE}. "
    "La disponibilidad del servicio se logra con failover y heartbeat."
)


class Provider:
    """Proveedor simulado: guarda lo que recibe (cuerpo HTTP crudo) y devuelve vectores válidos."""

    def __init__(self):
        self.bodies, self.raw = [], []
        self.client = OpenRouterClient(transport=httpx.MockTransport(self.handle))
        self.client.api_key = "sk-or-test-key"

    def handle(self, request):
        self.raw.append(request.content.decode("utf-8"))
        texts = json.loads(request.content)["input"]
        self.bodies.append(texts)
        data = [{"index": i, "embedding": fake_embedding(t, DIM)} for i, t in enumerate(texts)]
        return httpx.Response(200, json={"data": data, "usage": {"prompt_tokens": 1}})

    @property
    def sent(self):
        return " ".join(t for batch in self.bodies for t in batch)


@pytest.fixture(autouse=True)
def small_dim(monkeypatch):
    monkeypatch.setattr(settings, "embedding_dim", DIM)


def assert_clean(provider):
    wire = provider.raw and " ".join(provider.raw)
    for fragment in FORBIDDEN:
        assert fragment not in wire, f"salió hacia el proveedor: {fragment!r}"


def test_embedder_sends_only_pseudonymized_text():
    provider = Provider()
    result = PrivateEmbedder(provider.client).embed([TEXT], PEOPLE)
    assert len(result.vectors) == 1
    assert_clean(provider)
    sent = provider.sent
    for token in ("[ESTUDIANTE_1]", "[ESTUDIANTE_2]", "[RUT_1]", "[RUT_2]", "[CORREO_1]"):
        assert token in sent
    assert re.search(r"\[RUT_3\]", sent) and re.search(r"\[TELEFONO_\d+\]", sent)  # RUT y teléfono fuera de la lista
    assert "disponibilidad" in sent and "failover" in sent  # el contenido académico se conserva


def test_sensitive_values_are_absent_even_without_a_roster():
    provider = Provider()
    PrivateEmbedder(provider.client).embed([TEXT], [])
    for fragment in (RUT_A, RUT_B, RUT_UNKNOWN, MAIL_A, "8765 4321"):
        assert fragment not in " ".join(provider.raw)  # RUT, correo y teléfono se detectan sin lista


def test_batches_respect_batch_size_and_keep_order(monkeypatch):
    monkeypatch.setattr(settings, "embedding_batch_size", 2)
    provider = Provider()
    texts = [f"texto numero {n} de prueba" for n in range(5)]
    result = PrivateEmbedder(provider.client).embed(texts, PEOPLE)
    assert [len(b) for b in provider.bodies] == [2, 2, 1]
    assert result.vectors == [fake_embedding(t, DIM) for t in texts]


def test_unstable_pseudonymization_aborts_before_any_request(monkeypatch):
    provider = Provider()
    monkeypatch.setattr("rag.embedder.pseudonymize", lambda text, people: (text + "x", None))
    with pytest.raises(PrivacyGuardError):
        PrivateEmbedder(provider.client).embed([TEXT, "otro"], PEOPLE)
    assert provider.bodies == []


def make_indexer(provider, sources=None):
    store, sources = InMemoryVectorStore(), sources or FakeSources()
    sources.add_version(V1, SUBMISSION, 1)
    store.add_chunks("version", V1, [TextChunk(0, TEXT, 0, len(TEXT)), TextChunk(1, "Texto sin datos personales.", 0, 27)])
    return store, sources, RagIndexer(store, sources, PrivateEmbedder(provider.client))


def test_indexing_a_version_end_to_end_never_leaks_but_keeps_original_text():
    provider = Provider()
    store, _, indexer = make_indexer(provider)
    assert indexer.index_version(V1).embebidos == 2
    assert_clean(provider)
    stored = store.stored("version", V1)
    assert stored[0]["texto"] == TEXT and stored[0]["embedding"] is not None  # el original queda para las citas


def test_roster_failure_is_fail_closed():
    provider = Provider()
    sources = FakeSources()
    sources.roster_error = store_down()
    store, _, indexer = make_indexer(provider, sources)
    with pytest.raises(Exception) as err:
        indexer.index_version(V1)
    assert type(err.value).__name__ == "StoreError"
    assert provider.bodies == [] and all(r["embedding"] is None for r in store.stored("version", V1))


def test_only_the_embedder_calls_the_embeddings_client():
    """Nadie más puede mandar texto al endpoint de embeddings sin pasar por la seudonimización."""
    root = Path(__file__).resolve().parents[1]
    allowed = {root / "core" / "openrouter_client.py", root / "rag" / "embedder.py"}
    skip = {"tests", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if not skip.intersection(path.relative_to(root).parts) and path not in allowed
        and re.search(r"\.embeddings\(", path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
