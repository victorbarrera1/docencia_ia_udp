import pytest

from config import settings
from core.llm_support import LLMHTTPError, LLMNotConfigured
from core.submission_store import StoreError, SubmissionStore
from memory.vector_store import InMemoryVectorStore
from rag.indexer import RagIndexer, RagInputError, RagNotFound
from rag.pdf_processor import TextChunk
from tests.pdf_fixtures import SAMPLE_LINES, make_text_pdf
from tests.rag_fixtures import ACTIVITY, COURSE, SECTION, SUBMISSION, V1, ActivityInfo, FakeSources
from tests.test_submission_versions import FakeStore

ENDPOINTS = [
    ("/api/v1/rag/versions/{v}/index", None),
    ("/api/v1/rag/activities/{a}/rubric/index", None),
    ("/api/v1/rag/courses/{c}/material", {"texto": "Material de prueba"}),
    ("/api/v1/rag/backfill", None),
]


def url(template):
    return template.format(v=V1, a=ACTIVITY, c=COURSE)


@pytest.fixture
def rag(monkeypatch):
    """Indexador real sobre el almacén en memoria y embeddings fake (sin red)."""
    monkeypatch.setattr(settings, "llm_fake", True)
    store, sources = InMemoryVectorStore(), FakeSources()
    sources.add_version(V1, SUBMISSION, 1)
    sources.activities[ACTIVITY] = ActivityInfo(ACTIVITY, SECTION, [{"criterio": "Diseño", "puntaje_max": 1}])
    sources.courses[COURSE] = "TEST_IA"
    store.add_chunks("version", V1, [TextChunk(0, "Primer fragmento", 0, 16)])
    indexer = RagIndexer(store, sources)
    monkeypatch.setattr(RagIndexer, "from_settings", classmethod(lambda cls: indexer))
    return indexer


@pytest.mark.parametrize("template,body", ENDPOINTS)
def test_every_rag_endpoint_requires_the_service_key(client, template, body):
    assert client.post(url(template), json=body).status_code == 401
    assert client.post(url(template), json=body, headers={"X-Agent-Service-Key": "otra"}).status_code == 401


def test_index_version_reports_and_is_idempotent(client, auth_headers, rag):
    first = client.post(f"/api/v1/rag/versions/{V1}/index", headers=auth_headers)
    assert first.status_code == 200 and first.json() == {"embebidos": 1, "omitidos_modelo_distinto": 0, "chunks": None}
    assert client.post(f"/api/v1/rag/versions/{V1}/index?force=true", headers=auth_headers).json()["embebidos"] == 0


def test_index_rubric_and_course_material(client, auth_headers, rag):
    rubric = client.post(f"/api/v1/rag/activities/{ACTIVITY}/rubric/index", headers=auth_headers).json()
    assert rubric["embebidos"] == 1 and rubric["chunks"]["insertados"] == 1
    material = client.post(f"/api/v1/rag/courses/{COURSE}/material", headers=auth_headers, json={"texto": "Unidad uno del curso."})
    assert material.status_code == 200 and material.json()["chunks"]["insertados"] == 1
    assert client.post(f"/api/v1/rag/courses/{COURSE}/material", headers=auth_headers, json={"texto": ""}).status_code == 422


def test_backfill_limit_is_validated_and_applied(client, auth_headers, rag):
    assert client.post("/api/v1/rag/backfill?limit=0", headers=auth_headers).status_code == 422
    assert client.post("/api/v1/rag/backfill?limit=5000", headers=auth_headers).status_code == 422
    assert client.post("/api/v1/rag/backfill?limit=10", headers=auth_headers).json()["embebidos"] == 1


@pytest.mark.parametrize("error,status,code", [
    (RagNotFound("detalle interno secreto"), 404, "NO_ENCONTRADO"),
    (RagInputError("detalle interno secreto"), 422, "TEXTO_INVALIDO"),
    (LLMNotConfigured("detalle interno secreto"), 503, "EMBEDDINGS_NO_CONFIGURADOS"),
    (LLMHTTPError(500, "detalle interno secreto"), 502, "EMBEDDINGS_NO_DISPONIBLES"),
    (StoreError("detalle interno secreto"), 502, "ALMACENAMIENTO_NO_DISPONIBLE"),
])
def test_errors_are_sanitized(client, auth_headers, rag, monkeypatch, error, status, code):
    def boom(*args, **kwargs):
        raise error

    monkeypatch.setattr(rag, "index_version", boom)
    res = client.post(f"/api/v1/rag/versions/{V1}/index", headers=auth_headers)
    assert res.status_code == status and res.json()["code"] == code
    assert "secreto" not in res.text


def test_unexpected_errors_do_not_leak(client, auth_headers, rag, monkeypatch):
    monkeypatch.setattr(rag, "backfill", lambda *a, **k: 1 / 0)
    res = client.post("/api/v1/rag/backfill", headers=auth_headers)
    assert res.status_code == 500 and "division" not in res.text and "Traceback" not in res.text


def test_unconfigured_supabase_is_503(client, auth_headers):
    assert client.post("/api/v1/rag/backfill", headers=auth_headers).status_code == 503


# ---------- Indexado automático tras POST /versions ----------

SUB = "e1050000-0000-4000-a000-000000000001"
USER = "a1050000-0000-4000-a000-000000000001"


def upload(client, headers, content_type="application/pdf"):
    pdf = make_text_pdf([SAMPLE_LINES])
    return client.post(f"/api/v1/submissions/{SUB}/versions", headers=headers, data={"uploaded_by": USER},
                       files={"file": ("entrega.pdf", pdf, content_type)})


@pytest.fixture
def uploads(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(SubmissionStore, "from_settings", classmethod(lambda cls: store))
    queued = []
    monkeypatch.setattr("main.auto_index_version", queued.append)
    return queued


def test_upload_schedules_indexing_of_the_new_version(client, auth_headers, uploads):
    res = upload(client, auth_headers)
    assert res.status_code == 201 and uploads == [res.json()["version_id"]]


def test_auto_index_flag_off_skips_indexing(client, auth_headers, uploads, monkeypatch):
    monkeypatch.setattr(settings, "rag_auto_index", False)
    assert upload(client, auth_headers).status_code == 201 and uploads == []


def test_failed_upload_schedules_nothing(client, auth_headers, uploads):
    assert upload(client, auth_headers, content_type="text/plain").status_code == 415 and uploads == []


def test_a_failing_background_index_does_not_break_the_upload(client, auth_headers, monkeypatch):
    monkeypatch.setattr(SubmissionStore, "from_settings", classmethod(lambda cls: FakeStore()))
    monkeypatch.setattr(RagIndexer, "from_settings", classmethod(lambda cls: (_ for _ in ()).throw(RuntimeError("boom"))))
    monkeypatch.setattr(settings, "llm_fake", True)
    assert upload(client, auth_headers).status_code == 201
