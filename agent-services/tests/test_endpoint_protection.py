import importlib

import pytest
from fastapi.testclient import TestClient

import main
from config import settings

PUBLIC_PATHS = {"/health", "/.well-known/agent-card.json"}
DOC_PATHS = {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}


def test_every_api_route_requires_service_key(client):
    """Guardia: cualquier endpoint nuevo bajo la app debe exigir X-Agent-Service-Key."""
    checked = 0
    for route in main.app.routes:
        path = getattr(route, "path", "")
        if path in PUBLIC_PATHS or path in DOC_PATHS:
            continue
        for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
            res = client.request(method, path, json={})
            assert res.status_code == 401, f"{method} {path} respondió {res.status_code} sin llave de servicio"
            checked += 1
    assert checked >= 5  # hierarchy, chat, evaluate, design-activity, stream


def test_docs_available_outside_production(client):
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


@pytest.fixture
def production_app(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    yield importlib.reload(main).app
    monkeypatch.undo()
    importlib.reload(main)


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_disabled_in_production(production_app, path):
    assert TestClient(production_app).get(path).status_code == 404


def test_health_still_public_in_production(production_app):
    assert TestClient(production_app).get("/health").status_code == 200
