import pytest

from config import settings
from core.openrouter_client import openrouter_client
from memory.vector_store import InMemoryVectorStore
from rag.embedder import PrivateEmbedder
from rag.indexer import RagIndexer, RagNotFound
from rag.pdf_processor import DocumentChunk, TextChunk
from rag.retriever import CourseKnowledgeRetriever, RagRetriever
from tests.rag_fixtures import (
    COURSE, OTHER_SUBMISSION, PEOPLE, SUBMISSION, V1, V2, V_OTHER, FakeSources,
)

T_OLD = ["Diagrama de clases del sistema de pagos", "La disponibilidad se mide con heartbeat y failover"]
T_NEW = ["Diagrama de clases del sistema de pagos", "El failover y el heartbeat garantizan la disponibilidad",
         "Conclusiones sobre el costo de la nube"]
T_OTHER = ["Failover y heartbeat de otra entrega ajena"]


def chunks(texts):
    return [TextChunk(i, t, 0, len(t)) for i, t in enumerate(texts)]


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(settings, "llm_fake", True)
    store, sources = InMemoryVectorStore(), FakeSources()
    sources.add_version(V1, SUBMISSION, 1)
    sources.add_version(V2, SUBMISSION, 2)
    sources.add_version(V_OTHER, OTHER_SUBMISSION, 1)
    for version, texts in ((V1, T_OLD), (V2, T_NEW), (V_OTHER, T_OTHER)):
        store.add_chunks("version", version, chunks(texts))
    RagIndexer(store, sources).backfill()
    return store, sources, RagRetriever(store, sources)


def test_current_and_previous_versions_are_searched_separately(env):
    _, _, retriever = env
    ctx = retriever.retrieve_for_criterion("disponibilidad con failover y heartbeat", V2, k=2)
    assert ctx.previous_version_id == V1
    assert {h.owner_id for h in ctx.current} == {V2} and {h.owner_id for h in ctx.previous} == {V1}
    assert ctx.current[0].texto == T_NEW[1] and ctx.previous[0].texto == T_OLD[1]
    assert ctx.current[0].similarity >= ctx.current[1].similarity
    assert all(h.char_end - h.char_start == len(h.texto) for h in ctx.current + ctx.previous)


def test_never_returns_chunks_of_another_submission(env):
    _, _, retriever = env
    ctx = retriever.retrieve_for_criterion("failover heartbeat ajena", V2, k=10)
    assert V_OTHER not in {h.owner_id for h in ctx.current + ctx.previous}


def test_first_version_has_no_previous_and_k_is_respected(env):
    _, _, retriever = env
    ctx = retriever.retrieve_for_criterion("failover", V1, k=1)
    assert ctx.previous == [] and ctx.previous_version_id is None and len(ctx.current) == 1


def test_unembedded_chunks_are_not_returned(env):
    store, sources, retriever = env
    store.add_chunks("version", V2, [TextChunk(9, "failover pendiente", 0, 18)])
    assert "failover pendiente" not in [h.texto for h in retriever.retrieve_for_criterion("failover", V2, k=10).current]


def test_unknown_version(env):
    with pytest.raises(RagNotFound):
        env[2].retrieve_for_criterion("x", "f1060000-0000-4000-a000-0000000000ff")


def test_the_criterion_is_pseudonymized_before_leaving(env, monkeypatch):
    _, _, retriever = env
    sent = []
    original = openrouter_client.embeddings
    monkeypatch.setattr(openrouter_client, "embeddings", lambda texts, model=None, *, context=None: (sent.extend(texts), original(texts, model, context=context))[1])
    retriever.retrieve_for_criterion(f"Revisar a {PEOPLE[0]['nombres']} {PEOPLE[0]['apellidos']}", V2)
    assert sent == ["Revisar a [ESTUDIANTE_1]"]


# ---------- Interfaz histórica retrieve_context ----------

def course_env(monkeypatch):
    monkeypatch.setattr(settings, "chunk_chars", 60)
    monkeypatch.setattr(settings, "chunk_overlap_chars", 10)
    store, sources = InMemoryVectorStore(), FakeSources()
    sources.courses[COURSE] = "TEST_IA"
    RagIndexer(store, sources).index_course_material(
        COURSE, "El teorema CAP contrasta latencia y consistencia.\n\nLos atributos de calidad guían la arquitectura.")
    return CourseKnowledgeRetriever(RagRetriever(store, sources))


def test_retrieve_context_keeps_its_signature_and_returns_document_chunks(env, monkeypatch):
    retriever = course_env(monkeypatch)
    found = retriever.retrieve_context("test_ia", "consistencia en el teorema CAP", top_k=1)
    assert len(found) == 1 and isinstance(found[0], DocumentChunk)
    assert "CAP" in found[0].content and found[0].source == "Material del curso TEST_IA" and found[0].page == 1
    assert len(retriever.retrieve_context("TEST_IA", "arquitectura", 2)) == 2


def test_retrieve_context_degrades_to_empty_list(env, monkeypatch):
    retriever = course_env(monkeypatch)
    assert retriever.retrieve_context("OTRO_CODIGO", "algo") == []
    assert retriever.retrieve_context("TEST_IA", "   ") == []
    assert CourseKnowledgeRetriever().retrieve_context("CIT3203", "arquitectura") == []  # sin Supabase: sin semilla ni error


def test_retrieve_context_swallows_provider_failures(env, monkeypatch):
    retriever = course_env(monkeypatch)
    monkeypatch.setattr(PrivateEmbedder, "embed", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert retriever.retrieve_context("TEST_IA", "algo") == []
