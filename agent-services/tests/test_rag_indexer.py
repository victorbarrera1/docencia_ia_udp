import pytest

from config import settings
from core.llm_support import LLMHTTPError
from core.models import RubricItem
from core.openrouter_client import openrouter_client
from memory.vector_store import InMemoryVectorStore
from rag.indexer import RagIndexer, RagInputError, RagNotFound, auto_index_version
from rag.pdf_processor import TextChunk, chunk_with_offsets
from rag.rubric_text import SEPARATOR, parse_rubric, rubric_chunks
from tests.rag_fixtures import ACTIVITY, COURSE, SECTION, SUBMISSION, V1, ActivityInfo, FakeSources, store_down

CHUNKS = [TextChunk(i, t, 0, len(t)) for i, t in enumerate(["Primer fragmento", "Segundo fragmento", "Tercer fragmento"])]
RUBRIC = [
    {"criterio": "Disponibilidad", "puntaje_max": 2, "descriptores": ["Define RTO", "Define RPO"]},
    {"criterio": "Diagramas", "puntaje_max": 1.5, "descriptores": []},
]


@pytest.fixture
def fake_llm(monkeypatch):
    """Modo LLM_FAKE (vectores deterministas, sin red) con un espía sobre las llamadas de embeddings."""
    monkeypatch.setattr(settings, "llm_fake", True)
    calls = []
    original = openrouter_client.embeddings

    def spy(texts, model=None, *, context=None):
        calls.append({"texts": list(texts), "context": context})
        return original(texts, model, context=context)

    monkeypatch.setattr(openrouter_client, "embeddings", spy)
    return calls


@pytest.fixture
def env():
    store, sources = InMemoryVectorStore(), FakeSources()
    sources.add_version(V1, SUBMISSION, 1)
    store.add_chunks("version", V1, CHUNKS)
    return store, sources, RagIndexer(store, sources)


def test_index_version_fills_missing_embeddings_and_records_model(fake_llm, env):
    store, _, indexer = env
    report = indexer.index_version(V1)
    assert report.embebidos == 3 and len(fake_llm) == 1
    rows = store.stored("version", V1)
    assert all(r["embedding"] is not None and r["model"] == f"fake/{settings.embedding_model}" for r in rows)
    ctx = fake_llm[0]["context"]
    assert (ctx.agente, ctx.section_id, ctx.version_id) == ("rag_embeddings", SECTION, V1)


def test_reindexing_is_idempotent_no_duplicates_no_provider_calls(fake_llm, env):
    store, _, indexer = env
    indexer.index_version(V1)
    before = store.stored("version", V1)
    for force in (False, True):
        assert indexer.index_version(V1, force=force).embebidos == 0
    assert len(fake_llm) == 1 and store.stored("version", V1) == before


def test_other_model_is_reported_and_only_force_reembeds_it(fake_llm, env):
    store, _, indexer = env
    indexer.index_version(V1)
    row = store.chunks_to_embed("x", force=True, owner_type="version", owner_id=V1)  # todas cuentan como foráneas
    assert len(row) == 3
    store.set_embeddings("otro/modelo", [(r, [1.0] + [0.0] * (settings.embedding_dim - 1)) for r in row[:1]])
    report = indexer.index_version(V1)
    assert (report.embebidos, report.omitidos_modelo_distinto) == (0, 1)  # detecta la mezcla pero no la toca
    report = indexer.index_version(V1, force=True)
    assert (report.embebidos, report.omitidos_modelo_distinto) == (1, 0)  # solo el chunk con otro modelo
    assert fake_llm[-1]["texts"] == [row[0].texto]
    assert {r["model"] for r in store.stored("version", V1)} == {f"fake/{settings.embedding_model}"}
    assert len(store.stored("version", V1)) == 3


def test_changing_embedding_model_makes_force_reembed_everything(fake_llm, env, monkeypatch):
    store, _, indexer = env
    indexer.index_version(V1)
    monkeypatch.setattr(settings, "embedding_model", "otro/modelo-de-prueba")
    assert indexer.index_version(V1).omitidos_modelo_distinto == 3
    assert indexer.index_version(V1, force=True).embebidos == 3
    assert {r["model"] for r in store.stored("version", V1)} == {"fake/otro/modelo-de-prueba"}


def test_a_failed_batch_keeps_progress_and_the_next_run_resumes(fake_llm, env, monkeypatch):
    store, _, indexer = env
    monkeypatch.setattr(settings, "embedding_batch_size", 2)
    original = openrouter_client.embeddings
    state = {"n": 0}

    def flaky(texts, model=None, *, context=None):
        state["n"] += 1
        if state["n"] == 2:
            raise LLMHTTPError(503)
        return original(texts, model, context=context)

    monkeypatch.setattr(openrouter_client, "embeddings", flaky)
    with pytest.raises(LLMHTTPError):
        indexer.index_version(V1)
    assert sum(r["embedding"] is not None for r in store.stored("version", V1)) == 2
    assert indexer.index_version(V1).embebidos == 1
    assert all(r["embedding"] is not None for r in store.stored("version", V1))


def test_unknown_version_makes_no_calls(fake_llm, env):
    with pytest.raises(RagNotFound):
        env[2].index_version("f1060000-0000-4000-a000-0000000000ff")
    assert fake_llm == []


def test_without_api_key_nothing_is_embedded(env):
    store, _, indexer = env
    with pytest.raises(Exception) as err:
        indexer.index_version(V1)
    assert type(err.value).__name__ == "LLMNotConfigured"
    assert all(r["embedding"] is None for r in store.stored("version", V1))


# ---------- Rúbrica ----------

def test_rubric_text_one_chunk_per_criterion_with_offsets_over_the_full_text():
    items = parse_rubric(RUBRIC)
    chunks = rubric_chunks(items)
    assert [c.texto for c in chunks] == ["Disponibilidad (máx. 2 pts): Define RTO; Define RPO", "Diagramas (máx. 1.5 pts)"]
    full = SEPARATOR.join(c.texto for c in chunks)
    assert all(full[c.char_start:c.char_end] == c.texto for c in chunks)
    assert rubric_chunks(items) == chunks  # determinista


def test_parse_rubric_tolerates_shapes_and_drops_invalid_entries():
    assert [i.criterio for i in parse_rubric({"rubric": RUBRIC})] == ["Disponibilidad", "Diagramas"]
    assert parse_rubric([{"criterio": "ok", "puntaje_max": 1}, {"puntaje_max": 1}, "x", {"criterio": " ", "puntaje_max": 1}]) == [
        RubricItem(criterio="ok", puntaje_max=1)]
    assert parse_rubric(None) == [] and parse_rubric("texto") == [] and parse_rubric({}) == []


def rubric_env(rubric):
    store, sources = InMemoryVectorStore(), FakeSources()
    sources.activities[ACTIVITY] = ActivityInfo(ACTIVITY, SECTION, rubric)
    return store, sources, RagIndexer(store, sources)


def test_index_rubric_is_idempotent_and_only_reembeds_what_changed(fake_llm):
    store, sources, indexer = rubric_env(RUBRIC)
    first = indexer.index_rubric(ACTIVITY)
    assert first.embebidos == 2 and first.chunks["insertados"] == 2
    again = indexer.index_rubric(ACTIVITY)
    assert again.embebidos == 0 and again.chunks["sin_cambios"] == 2 and len(fake_llm) == 1
    sources.activities[ACTIVITY] = ActivityInfo(ACTIVITY, SECTION, [RUBRIC[0], {**RUBRIC[1], "criterio": "Diagramas UML"}])
    changed = indexer.index_rubric(ACTIVITY)
    assert (changed.chunks["actualizados"], changed.chunks["sin_cambios"], changed.embebidos) == (1, 1, 1)
    assert fake_llm[-1]["texts"] == ["Diagramas UML (máx. 1.5 pts)"]
    sources.activities[ACTIVITY] = ActivityInfo(ACTIVITY, SECTION, [RUBRIC[0]])
    assert indexer.index_rubric(ACTIVITY).chunks["borrados"] == 1
    assert len(store.stored("rubric", ACTIVITY)) == 1
    sources.activities[ACTIVITY] = ActivityInfo(ACTIVITY, SECTION, None)
    indexer.index_rubric(ACTIVITY)
    assert store.stored("rubric", ACTIVITY) == []


def test_index_rubric_unknown_activity(fake_llm):
    with pytest.raises(RagNotFound):
        rubric_env(RUBRIC)[2].index_rubric("d1060000-0000-4000-a000-0000000000ff")


# ---------- Material de curso ----------

MATERIAL = ("Unidad 1. Arquitectura de software y atributos de calidad. " * 120).strip()


def material_env():
    store, sources = InMemoryVectorStore(), FakeSources()
    sources.courses[COURSE] = "TEST_IA"
    return store, sources, RagIndexer(store, sources)


def test_index_course_material_chunks_with_offsets_and_is_idempotent(fake_llm):
    store, _, indexer = material_env()
    report = indexer.index_course_material(COURSE, MATERIAL)
    rows = store.stored("course_material", COURSE)
    assert report.embebidos == len(rows) > 1 and report.chunks["insertados"] == len(rows)
    assert all(MATERIAL[r["char_start"]:r["char_end"]] == r["texto"] for r in rows)
    calls = len(fake_llm)
    again = indexer.index_course_material(COURSE, MATERIAL)
    assert again.embebidos == 0 and again.chunks["sin_cambios"] == len(rows) and len(fake_llm) == calls
    indexer.index_course_material(COURSE, MATERIAL[:300])
    assert len(store.stored("course_material", COURSE)) == 1


def test_index_course_material_validates_input(fake_llm, monkeypatch):
    _, _, indexer = material_env()
    with pytest.raises(RagInputError):
        indexer.index_course_material(COURSE, "  \n ")
    monkeypatch.setattr(settings, "max_pdf_text_chars", 50)
    with pytest.raises(RagInputError):
        indexer.index_course_material(COURSE, MATERIAL)
    with pytest.raises(RagNotFound):
        indexer.index_course_material("b1060000-0000-4000-a000-0000000000ff", "Texto de prueba válido")
    assert fake_llm == []


def test_material_chunks_use_the_shared_chunker():
    assert chunk_with_offsets(MATERIAL)[0].char_start == 0


# ---------- Backfill y automático ----------

def test_backfill_embeds_pending_chunks_of_every_owner_up_to_the_limit(fake_llm):
    store, sources = InMemoryVectorStore(), FakeSources()
    sources.add_version(V1, SUBMISSION, 1)
    sources.activities[ACTIVITY] = ActivityInfo(ACTIVITY, SECTION, RUBRIC)
    sources.courses[COURSE] = "TEST_IA"
    store.add_chunks("version", V1, CHUNKS)
    store.add_chunks("rubric", ACTIVITY, [TextChunk(0, "Criterio uno", 0, 12)])
    store.add_chunks("course_material", COURSE, [TextChunk(0, "Material uno", 0, 12)])
    indexer = RagIndexer(store, sources)
    assert indexer.backfill(limit=2).embebidos == 2
    assert indexer.backfill(limit=100).embebidos == 3
    assert indexer.backfill().embebidos == 0


def test_backfill_skips_owners_that_no_longer_exist(fake_llm):
    store, sources = InMemoryVectorStore(), FakeSources()
    store.add_chunks("version", V1, CHUNKS)  # sin VersionInfo: la versión se borró
    assert RagIndexer(store, sources).backfill().embebidos == 0 and fake_llm == []


def test_auto_index_never_raises_and_leaves_chunks_null(fake_llm, env, monkeypatch):
    store, sources, indexer = env
    sources.roster_error = store_down()
    monkeypatch.setattr(RagIndexer, "from_settings", classmethod(lambda cls: indexer))
    auto_index_version(V1)  # no lanza
    assert all(r["embedding"] is None for r in store.stored("version", V1))
    sources.roster_error = None
    auto_index_version(V1)
    assert all(r["embedding"] is not None for r in store.stored("version", V1))


def test_auto_index_is_skipped_without_openrouter_key(monkeypatch):
    def boom(cls):
        raise AssertionError("no debe construir el indexador sin OpenRouter")

    monkeypatch.setattr(RagIndexer, "from_settings", classmethod(boom))
    auto_index_version(V1)


def test_auto_index_is_skipped_without_supabase(fake_llm):
    auto_index_version(V1)  # llave "configurada" (fake) pero Supabase vacío en conftest: se omite sin lanzar
    assert fake_llm == []
