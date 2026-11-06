"""POST /api/v1/agent/evaluate (IA-08): contrato compatible, errores claros y nada de notas inventadas."""
import httpx
import pytest

import main
from core.openrouter_client import OpenRouterClient
from agents.specialists.submission_evaluator import SubmissionEvaluatorWorker
from core.models import SubmissionEvaluationResponse
from tests.evaluator_fixtures import V2, FakeLLM, FakeReviewStore, answer, obs

PAYLOAD = {"version_id": V2, "assignment_title": "Taller de prueba", "max_decimas": 0.6}
# Cuerpo del contrato anterior: sigue aceptado si trae version_id (student_solution y rubric se ignoran/son respaldo).
LEGACY = {**PAYLOAD, "student_solution": "Texto que se ignora.", "rubric": [{"criterio": "Fundamentación", "puntaje_max": 4.0}]}


@pytest.fixture
def fake_worker(monkeypatch):
    llm = FakeLLM(lambda _u: answer([obs("usa microservicios con un balanceador de carga"), obs("texto que no existe en la entrega")]))
    store = FakeReviewStore()
    monkeypatch.setattr(main, "submission_evaluator_worker", SubmissionEvaluatorWorker(lambda: llm.client, lambda: store))
    return llm, store


def test_evaluate_requires_service_key(client):
    assert client.post("/api/v1/agent/evaluate", json=PAYLOAD).status_code == 401


def test_evaluate_without_model_is_a_clear_error_not_a_grade(client, auth_headers):
    res = client.post("/api/v1/agent/evaluate", json=PAYLOAD, headers=auth_headers)
    assert res.status_code == 503
    assert res.json()["code"] == "IA_NO_CONFIGURADA" and "nota" not in res.text


def test_evaluate_requires_version_id(client, auth_headers):
    res = client.post("/api/v1/agent/evaluate", json={"assignment_title": "x", "student_solution": "y", "rubric": []},
                      headers=auth_headers)
    assert res.status_code == 422


@pytest.mark.parametrize("body", [PAYLOAD, LEGACY])
def test_evaluate_matches_compatible_schema(client, auth_headers, fake_worker, body):
    res = client.post("/api/v1/agent/evaluate", json=body, headers=auth_headers)
    assert res.status_code == 200
    parsed = SubmissionEvaluationResponse.model_validate(res.json())
    assert parsed.profesor_revisor_pendiente is True
    assert 1.0 <= parsed.nota_sugerida <= 7.0 and 0.0 <= parsed.decimas_sugeridas <= 0.6
    assert [c.criterio for c in parsed.criterios_evaluados] == ["Fundamentación", "Escalabilidad"]
    assert parsed.resumen_feedback and parsed.review_id
    assert parsed.citas_descartadas == 1 and len(parsed.observaciones) == 1
    assert parsed.observaciones[0].estado_docente == "pendiente"


def test_evaluate_unknown_version_is_404(client, auth_headers, fake_worker):
    res = client.post("/api/v1/agent/evaluate", json={**PAYLOAD, "version_id": "f1080000-0000-4000-a000-0000000000ff"},
                      headers=auth_headers)
    assert res.status_code == 404 and res.json()["code"] == "VERSION_NO_ENCONTRADA"


def test_evaluate_provider_failure_is_sanitized(client, auth_headers, monkeypatch):
    failing = OpenRouterClient(transport=httpx.MockTransport(
        lambda _r: httpx.Response(503, text="detalle interno del proveedor")), api_key="sk-or-test-key")
    failing._sleep = lambda _s: None
    monkeypatch.setattr(main, "submission_evaluator_worker", SubmissionEvaluatorWorker(lambda: failing, FakeReviewStore))
    res = client.post("/api/v1/agent/evaluate", json=PAYLOAD, headers=auth_headers)
    assert res.status_code == 502 and res.json()["code"] == "IA_NO_DISPONIBLE"
    assert "detalle interno" not in res.text


def test_evaluate_invalid_model_output_is_502(client, auth_headers, monkeypatch):
    llm = FakeLLM(lambda _u: {"resumen": "falta todo lo demás"})
    monkeypatch.setattr(main, "submission_evaluator_worker", SubmissionEvaluatorWorker(lambda: llm.client, FakeReviewStore))
    res = client.post("/api/v1/agent/evaluate", json=PAYLOAD, headers=auth_headers)
    assert res.status_code == 502 and res.json()["code"] == "IA_RESPUESTA_INVALIDA"
    assert len(llm.requests) == 2  # un reintento indicando el error de schema


def test_evaluate_without_supabase_after_key_is_503(client, auth_headers, monkeypatch):
    llm = FakeLLM(lambda _u: answer([]))
    monkeypatch.setattr(main, "submission_evaluator_worker", SubmissionEvaluatorWorker(lambda: llm.client))
    res = client.post("/api/v1/agent/evaluate", json=PAYLOAD, headers=auth_headers)
    assert res.status_code == 503 and res.json()["code"] == "ALMACENAMIENTO_NO_CONFIGURADO"
    assert llm.requests == []
