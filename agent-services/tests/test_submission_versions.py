import httpx
import pytest

from config import settings
from core.submission_store import InvalidUploader, StoreError, SubmissionNotFound, SubmissionStore, VersionRecord
from tests.pdf_fixtures import SAMPLE_LINES, make_blank_pdf, make_text_pdf

SUB = "e1050000-0000-4000-a000-000000000001"
USER = "a1050000-0000-4000-a000-000000000001"
URL = f"/api/v1/submissions/{SUB}/versions"


class FakeStore:
    """Equivale al contrato de SubmissionStore en memoria (la numeración real se prueba en SQL)."""

    def __init__(self, late=False, create_error=None, upload_error=None):
        self.versions, self.uploads, self.deleted = [], {}, []
        self.late, self.create_error, self.upload_error = late, create_error, upload_error

    def create_version(self, submission_id, uploaded_by, texto, chunks):
        if self.create_error:
            raise self.create_error
        n = len(self.versions) + 1
        record = VersionRecord(f"id-{n}", n, f"section/s/activity/a/submission/{submission_id}/v{n}.pdf", self.late, len(chunks))
        self.versions.append((record, texto, chunks, uploaded_by))
        return record

    def upload_pdf(self, storage_path, data):
        if self.upload_error:
            raise self.upload_error
        self.uploads[storage_path] = data

    def delete_version(self, version_id):
        self.deleted.append(version_id)


@pytest.fixture
def use_store(monkeypatch):
    # Se parchea from_settings (y no dependency_overrides): otros tests recargan `main`.
    def install(store):
        monkeypatch.setattr(SubmissionStore, "from_settings", classmethod(lambda cls: store))
        return store

    return install


def post(client, headers, pdf=b"", content_type="application/pdf", url=URL, uploaded_by=USER, name="entrega.pdf"):
    return client.post(url, headers=headers, data={"uploaded_by": uploaded_by}, files={"file": (name, pdf, content_type)})


def test_text_pdf_creates_version_and_uploads_original(client, auth_headers, use_store):
    store = use_store(FakeStore())
    pdf = make_text_pdf([SAMPLE_LINES])
    res = post(client, auth_headers, pdf)
    assert res.status_code == 201
    body = res.json()
    assert body["version_n"] == 1 and body["atrasada"] is False and body["paginas"] == 1 and body["chunks"] == 1
    assert set(body) == {"version_id", "version_n", "atrasada", "paginas", "caracteres", "chunks"}  # sin storage_path
    record, texto, chunks, uploaded_by = store.versions[0]
    assert uploaded_by == USER and "Diseño de una arquitectura" in texto
    assert [texto[c.char_start:c.char_end] for c in chunks] == [c.texto for c in chunks]
    assert store.uploads[record.storage_path] == pdf


def test_second_version_increments_and_late_flag_is_returned(client, auth_headers, use_store):
    use_store(FakeStore(late=True))
    first = post(client, auth_headers, make_text_pdf([SAMPLE_LINES])).json()
    second = post(client, auth_headers, make_text_pdf([SAMPLE_LINES])).json()
    assert (first["version_n"], second["version_n"]) == (1, 2)
    assert second["atrasada"] is True


def test_requires_service_key(client):
    res = client.post(URL, data={"uploaded_by": USER}, files={"file": ("a.pdf", b"%PDF-", "application/pdf")})
    assert res.status_code == 401


def test_returns_503_when_supabase_not_configured(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "supabase_service_role_key", "")
    assert post(client, auth_headers, make_text_pdf([SAMPLE_LINES])).status_code == 503


def test_scanned_pdf_gets_clear_422_and_nothing_is_stored(client, auth_headers, use_store):
    store = use_store(FakeStore())
    res = post(client, auth_headers, make_blank_pdf())
    assert res.status_code == 422 and res.json()["code"] == "PDF_SIN_TEXTO"
    assert store.versions == [] and store.uploads == {}


def test_corrupt_pdf_is_422(client, auth_headers, use_store):
    use_store(FakeStore())
    res = post(client, auth_headers, b"%PDF-1.4\nbasura")
    assert res.status_code == 422 and res.json()["code"] == "PDF_INVALIDO"


@pytest.mark.parametrize("content_type,data", [
    ("image/png", b"%PDF-1.4 falso tipo"),
    ("application/pdf", b"MZ\x90\x00 no es pdf"),  # el Content-Type no basta: se verifica el magic
])
def test_only_pdf_is_accepted(client, auth_headers, use_store, content_type, data):
    use_store(FakeStore())
    res = post(client, auth_headers, data, content_type=content_type)
    assert res.status_code == 415 and res.json()["code"] == "TIPO_NO_PERMITIDO"


def test_file_over_limit_is_413(client, auth_headers, use_store, monkeypatch):
    use_store(FakeStore())
    monkeypatch.setattr(settings, "max_pdf_bytes", 1000)
    res = post(client, auth_headers, b"%PDF-1.4\n" + b"0" * 1200)
    assert res.status_code == 413 and res.json()["code"] == "ARCHIVO_DEMASIADO_GRANDE"


def test_declared_content_length_over_limit_rejected_before_parsing(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "max_pdf_bytes", 10)
    big = b"%PDF-1.4\n" + b"0" * (2 * 1024 * 1024)
    assert post(client, auth_headers, big).status_code == 413


def test_exactly_at_limit_is_not_rejected_as_too_big(client, auth_headers, use_store, monkeypatch):
    use_store(FakeStore())
    pdf = make_text_pdf([SAMPLE_LINES])
    monkeypatch.setattr(settings, "max_pdf_bytes", len(pdf))
    assert post(client, auth_headers, pdf).status_code == 201


def test_unknown_submission_is_404(client, auth_headers, use_store):
    use_store(FakeStore(create_error=SubmissionNotFound("interno")))
    res = post(client, auth_headers, make_text_pdf([SAMPLE_LINES]))
    assert res.status_code == 404 and res.json()["code"] == "ENTREGA_NO_ENCONTRADA"


def test_unknown_uploader_is_422(client, auth_headers, use_store):
    use_store(FakeStore(create_error=InvalidUploader("interno")))
    assert post(client, auth_headers, make_text_pdf([SAMPLE_LINES])).json()["code"] == "USUARIO_INVALIDO"


def test_malformed_ids_are_422(client, auth_headers):
    pdf = make_text_pdf([SAMPLE_LINES])
    assert post(client, auth_headers, pdf, url="/api/v1/submissions/no-es-uuid/versions").status_code == 422
    assert post(client, auth_headers, pdf, uploaded_by="no-es-uuid").status_code == 422


def test_failed_upload_compensates_and_hides_internals(client, auth_headers, use_store):
    store = use_store(FakeStore(upload_error=StoreError("Bucket not found en proyecto-secreto")))
    res = post(client, auth_headers, make_text_pdf([SAMPLE_LINES]))
    assert res.status_code == 502 and res.json()["code"] == "ALMACENAMIENTO_NO_DISPONIBLE"
    assert "secreto" not in res.text and "Bucket" not in res.text
    assert store.deleted == ["id-1"]


def test_unexpected_error_is_sanitized(client, auth_headers, use_store):
    use_store(FakeStore(create_error=RuntimeError("password=hunter2 en /srv/app")))
    res = post(client, auth_headers, make_text_pdf([SAMPLE_LINES]))
    assert res.status_code == 500 and "hunter2" not in res.text and "/srv" not in res.text


def test_end_to_end_with_real_store_against_simulated_supabase(client, auth_headers, monkeypatch):
    """Endpoint + SubmissionStore reales; Supabase simulado con httpx.MockTransport (sin red)."""
    calls = []

    def supabase(request: httpx.Request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/rpc/create_submission_version"):
            path = f"section/s/activity/a/submission/{SUB}/v1.pdf"
            return httpx.Response(200, json={"id": "v-1", "version_n": 1, "storage_path": path, "atrasada": False, "chunks": 1})
        return httpx.Response(200, json={})

    store = SubmissionStore("https://proj.supabase.test", "k", "entregas",
                            client=httpx.Client(transport=httpx.MockTransport(supabase)))
    monkeypatch.setattr(SubmissionStore, "from_settings", classmethod(lambda cls: store))
    res = post(client, auth_headers, make_text_pdf([SAMPLE_LINES]))
    assert res.status_code == 201
    assert calls == [("POST", "/rest/v1/rpc/create_submission_version"),
                     ("POST", f"/storage/v1/object/entregas/section/s/activity/a/submission/{SUB}/v1.pdf")]
