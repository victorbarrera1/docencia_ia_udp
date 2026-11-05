"""Revisiones (IA-08): GET de la revisión, decisión de observaciones y confirmación docente vía RPC de Supabase (simulado)."""
import json

import httpx
import pytest

from core.postgrest import Postgrest
from core.review_store import SupabaseReviewStore
from main import app, get_review_store
from tests.evaluator_fixtures import TEACHER, V1, V2

REVIEW = "a1080000-0000-4000-b000-000000000001"
OBS = "a1080000-0000-4000-c000-000000000001"
ROW = {
    "id": REVIEW, "version_id": V2, "agente": "submission_evaluator/evaluador-v1", "modelo": "qwen/qwen3.8-27b",
    "estado": "propuesta", "nota_preliminar": 5.1, "decimas_preliminares": 0.3, "resumen": "Resumen ficticio",
    "nota_final": None, "confirmada_por": None, "confirmada_en": None,
    "ai_observations": [
        {"id": OBS, "criterio": "Escalabilidad", "tipo": "criterio", "severidad": "media", "texto": "Obs ficticia",
         "cita_texto": "balanceador de carga", "cita_version_id": V2, "char_start": 40, "char_end": 60,
         "estado_docente": "pendiente"},
        {"id": "a1080000-0000-4000-c000-000000000002", "criterio": None, "tipo": "retroceso", "severidad": "baja",
         "texto": "Antes", "cita_texto": "Propone un monolito", "cita_version_id": V1, "char_start": 5, "char_end": 24,
         "estado_docente": "aceptada"}],
}


class FakeSupabase:
    """PostgREST simulado: responde según la ruta y guarda las peticiones (cuerpo de cada RPC)."""

    def __init__(self, rpc_status=200, rpc_body=None, row=ROW):
        self.rpc_status, self.rpc_body, self.row = rpc_status, rpc_body, row
        self.calls = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path, json.loads(request.content) if request.content else None))
        if path.endswith("/ai_reviews"):
            return httpx.Response(200, json=[self.row] if self.row else [])
        if "/rpc/" in path:
            return httpx.Response(self.rpc_status, json=self.rpc_body if self.rpc_body is not None else {"review_id": REVIEW})
        return httpx.Response(404, json={})


@pytest.fixture
def supa():
    fake = FakeSupabase()
    db = Postgrest("https://proyecto-ficticio.supabase.co", "service-key-ficticia",
                   client=httpx.Client(transport=httpx.MockTransport(fake.handle)))
    app.dependency_overrides[get_review_store] = lambda: SupabaseReviewStore(db)
    yield fake
    app.dependency_overrides.pop(get_review_store, None)


def test_review_endpoints_require_service_key(client):
    assert client.get(f"/api/v1/reviews/{REVIEW}").status_code == 401
    assert client.post(f"/api/v1/reviews/{REVIEW}/confirm", json={"actor": TEACHER}).status_code == 401
    assert client.patch(f"/api/v1/observations/{OBS}", json={"actor": TEACHER, "estado_docente": "aceptada"}).status_code == 401


def test_without_supabase_is_503(client, auth_headers):
    assert client.get(f"/api/v1/reviews/{REVIEW}", headers=auth_headers).status_code == 503


def test_get_review_labels_citations_and_counts_pending(client, auth_headers, supa):
    res = client.get(f"/api/v1/reviews/{REVIEW}", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["estado"] == "propuesta" and body["nota_final"] is None and body["pendientes"] == 1
    assert [o["cita"]["version"] for o in body["observaciones"]] == ["actual", "anterior"]


def test_get_unknown_review_is_404(client, auth_headers, supa):
    supa.row = None
    res = client.get(f"/api/v1/reviews/{REVIEW}", headers=auth_headers)
    assert res.status_code == 404 and res.json()["code"] == "NO_ENCONTRADO"


def test_confirm_calls_rpc_with_actor(client, auth_headers, supa):
    res = client.post(f"/api/v1/reviews/{REVIEW}/confirm", json={"actor": TEACHER, "nota_final": 5.5}, headers=auth_headers)
    assert res.status_code == 200
    rpc = [c for c in supa.calls if "/rpc/" in c[1]]
    assert rpc == [("POST", "/rest/v1/rpc/confirm_ai_review", {"p_review_id": REVIEW, "p_actor": TEACHER, "p_nota_final": 5.5})]


@pytest.mark.parametrize("sqlstate, status, code", [
    ("IA001", 409, "OBSERVACIONES_PENDIENTES"), ("42501", 403, "NO_AUTORIZADO"), ("IA002", 409, "REVISION_CONFIRMADA"),
    ("P0002", 404, "NO_ENCONTRADO"), ("22023", 422, "DATOS_INVALIDOS"), ("XX000", 502, "ALMACENAMIENTO_NO_DISPONIBLE")])
def test_confirm_errors_are_mapped_and_sanitized(client, auth_headers, supa, sqlstate, status, code):
    supa.rpc_status, supa.rpc_body = 400, {"code": sqlstate, "message": "detalle interno de la base"}
    res = client.post(f"/api/v1/reviews/{REVIEW}/confirm", json={"actor": TEACHER}, headers=auth_headers)
    assert res.status_code == status and res.json()["code"] == code
    assert "detalle interno" not in res.text


def test_confirm_validates_body(client, auth_headers, supa):
    assert client.post(f"/api/v1/reviews/{REVIEW}/confirm", json={"nota_final": 5}, headers=auth_headers).status_code == 422
    assert client.post(f"/api/v1/reviews/{REVIEW}/confirm", json={"actor": TEACHER, "nota_final": 7.5},
                       headers=auth_headers).status_code == 422
    assert not [c for c in supa.calls if "/rpc/" in c[1]]


def test_decide_observation_returns_updated_review(client, auth_headers, supa):
    supa.rpc_body = {"id": OBS, "review_id": REVIEW, "estado_docente": "editada"}
    res = client.patch(f"/api/v1/observations/{OBS}", headers=auth_headers,
                       json={"actor": TEACHER, "estado_docente": "editada", "texto": "Texto docente"})
    assert res.status_code == 200 and res.json()["id"] == REVIEW
    rpc = [c for c in supa.calls if "/rpc/" in c[1]][0]
    assert rpc[1].endswith("/decide_ai_observation")
    assert rpc[2] == {"p_observation_id": OBS, "p_actor": TEACHER, "p_estado": "editada", "p_texto": "Texto docente"}


def test_decide_rejects_pending_as_decision(client, auth_headers, supa):
    res = client.patch(f"/api/v1/observations/{OBS}", headers=auth_headers, json={"actor": TEACHER, "estado_docente": "pendiente"})
    assert res.status_code == 422


def test_create_review_rpc_payload_has_no_final_grade(supa):
    store = SupabaseReviewStore(Postgrest("https://proyecto-ficticio.supabase.co", "k",
                                          client=httpx.Client(transport=httpx.MockTransport(supa.handle))))
    supa.rpc_body = {"review_id": REVIEW, "observation_ids": []}
    store.create_review(V2, "submission_evaluator/evaluador-v1", "qwen/qwen3.8-27b", 5.1, 0.3, "r", [])
    method, path, body = supa.calls[-1]
    assert path == "/rest/v1/rpc/create_ai_review"
    assert not any("final" in key or "confirm" in key or "estado" in key for key in body)


def _store_with(rows):
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=rows)
    return SupabaseReviewStore(Postgrest("https://proyecto-ficticio.supabase.co", "k",
                                         client=httpx.Client(transport=httpx.MockTransport(handle))))


def test_version_context_parses_postgrest_row():
    owner = {"id": "c1080000-0000-4000-a000-000000000002", "nombres": "Ana", "apellidos": "Ficticia", "rut": None, "email": None}
    row = {"id": V2, "submission_id": "e1080000-0000-4000-a000-000000000001", "version_n": 2, "texto_extraido": "Texto",
           "atrasada": True, "activity_submissions": {"students": owner, "activities": {
               "id": "d1080000-0000-4000-a000-000000000001", "title": "Taller", "section_id": "a1080000-0000-4000-a000-000000000001",
               "rubric_json": [{"criterio": "C", "puntaje_max": 1}], "max_decimas": "0.50"}}}
    ctx = _store_with([row]).version_context(V2)
    assert ctx.texto == "Texto" and ctx.atrasada is True and ctx.version_n == 2
    assert ctx.max_decimas == 0.5 and ctx.activity_title == "Taller" and ctx.owner == owner
    assert _store_with([]).version_context(V2) is None
