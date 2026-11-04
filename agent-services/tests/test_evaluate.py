from core.models import SubmissionEvaluationResponse

PAYLOAD = {
    "student_solution": "Texto de prueba de una entrega ficticia.",
    "assignment_title": "Taller de prueba",
    "rubric": [
        {"criterio": "Fundamentación", "puntaje_max": 4.0, "descriptores": ["Justifica decisiones"]},
        {"criterio": "Diagramación", "puntaje_max": 6.0},
    ],
    "max_decimas": 0.6,
}


def test_evaluate_requires_service_key(client):
    assert client.post("/api/v1/agent/evaluate", json=PAYLOAD).status_code == 401


def test_evaluate_matches_current_schema(client, auth_headers):
    res = client.post("/api/v1/agent/evaluate", json=PAYLOAD, headers=auth_headers)
    assert res.status_code == 200
    parsed = SubmissionEvaluationResponse.model_validate(res.json())
    assert parsed.profesor_revisor_pendiente is True
    assert 1.0 <= parsed.nota_sugerida <= 7.0
    assert 0.0 <= parsed.decimas_sugeridas <= PAYLOAD["max_decimas"]
    assert [c.criterio for c in parsed.criterios_evaluados] == ["Fundamentación", "Diagramación"]
    assert parsed.resumen_feedback


def test_evaluate_rejects_invalid_body(client, auth_headers):
    res = client.post("/api/v1/agent/evaluate", json={"assignment_title": "x"}, headers=auth_headers)
    assert res.status_code == 422
