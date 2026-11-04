import os

# Debe ejecutarse antes de importar la app: config.py carga .env y no pisa variables ya definidas.
os.environ["AGENT_SERVICE_API_KEY"] = "test-service-key"
os.environ["ENVIRONMENT"] = "test"
os.environ["OPENROUTER_API_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from config import settings  # noqa: E402
from core.openrouter_client import openrouter_client  # noqa: E402
from main import app  # noqa: E402

SERVICE_KEY = "test-service-key"


def _guard_sync(original):
    def guarded(self, *args, **kwargs):
        if isinstance(self, TestClient):  # TestClient habla con la app en memoria
            return original(self, *args, **kwargs)
        raise AssertionError("Los tests no deben hacer llamadas HTTP salientes.")

    return guarded


def _guard_async(original):
    async def guarded(self, *args, **kwargs):
        if isinstance(getattr(self, "_transport", None), httpx.ASGITransport):
            return await original(self, *args, **kwargs)
        raise AssertionError("Los tests no deben hacer llamadas HTTP salientes.")

    return guarded


@pytest.fixture(autouse=True)
def no_llm_keys(monkeypatch):
    """Fuerza el modo sin llave y falla cualquier camino que intente salir a la red."""
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "agent_service_api_key", SERVICE_KEY)
    monkeypatch.setattr(openrouter_client, "api_key", "")
    monkeypatch.setattr(httpx.Client, "post", _guard_sync(httpx.Client.post))
    monkeypatch.setattr(httpx.AsyncClient, "post", _guard_async(httpx.AsyncClient.post))
    monkeypatch.setattr(httpx.AsyncClient, "send", _guard_async(httpx.AsyncClient.send))


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_headers():
    return {"X-Agent-Service-Key": SERVICE_KEY}
