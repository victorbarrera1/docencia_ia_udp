from core.models import ActivityDesignResponse

PAYLOAD = {"objective": "Practicar patrones de arquitectura", "duration_minutes": 45, "format": "grupal"}


def test_design_activity_requires_service_key(client):
    assert client.post("/api/v1/agent/design-activity", json=PAYLOAD).status_code == 401


def test_design_activity_rejects_wrong_key(client):
    res = client.post("/api/v1/agent/design-activity", json=PAYLOAD, headers={"X-Agent-Service-Key": "otra"})
    assert res.status_code == 401


def test_design_activity_with_key_uses_offline_fallback(client, auth_headers):
    res = client.post("/api/v1/agent/design-activity", json=PAYLOAD, headers=auth_headers)
    assert res.status_code == 200
    parsed = ActivityDesignResponse.model_validate(res.json())
    assert parsed.instructions
    assert parsed.rubric
