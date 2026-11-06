"""Endpoint configurable por agente (IA-08): el evaluador puede ir a un vLLM propio mientras los embeddings siguen en OpenRouter."""
import json

import httpx
from pydantic import BaseModel

from agents.specialists.submission_evaluator import evaluator_client
from config import settings
from core.audit import build_row
from core.llm_support import LLMCallContext, extract_json
from core.openrouter_client import OpenRouterClient, endpoint_client, register_audit_listener

VLLM = "https://llm.udp.example/v1"


class Out(BaseModel):
    resumen: str


def _capture(client: OpenRouterClient, body: dict, usage: dict):
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"model": body.get("model", "Qwen/Qwen3.8-27B"), "usage": usage,
                                         "choices": [{"message": {"content": json.dumps({"resumen": "ok"})}}]})
    client._transport = httpx.MockTransport(handle)
    return seen


def test_without_own_endpoint_falls_back_to_openrouter_and_its_key(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "llave-respaldo-ficticia")
    client = endpoint_client("", "")
    assert client.is_openrouter and client.api_key == "llave-respaldo-ficticia"
    assert endpoint_client("", "llave-evaluador-ficticia").api_key == "llave-evaluador-ficticia"
    assert endpoint_client(settings.openrouter_base_url + "/", "").api_key == "llave-respaldo-ficticia"


def test_own_endpoint_never_receives_the_openrouter_key(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "llave-respaldo-ficticia")
    client = endpoint_client(VLLM + "/", "")
    assert client.base_url == VLLM and client.api_key == "" and not client.is_openrouter
    assert client.is_configured()  # vLLM puede no exigir llave
    seen = _capture(client, {}, {"prompt_tokens": 50, "completion_tokens": 7})
    client.complete_json("s", "u", Out, model="Qwen/Qwen3.8-27B")
    request = seen[0]
    assert str(request.url) == f"{VLLM}/chat/completions"
    assert "authorization" not in request.headers and "http-referer" not in request.headers
    assert "usage" not in json.loads(request.content)  # extra propio de OpenRouter


def test_own_endpoint_with_key_sends_bearer():
    client = endpoint_client(VLLM, "token-del-vllm-udp")
    seen = _capture(client, {}, {})
    client.complete_json("s", "u", Out, model="Qwen/Qwen3.8-27B")
    assert seen[0].headers["authorization"] == "Bearer token-del-vllm-udp"


def test_local_server_without_cost_audits_null_cost_and_tokens():
    rows = []
    register_audit_listener(lambda meta, ctx: rows.append(build_row(meta, ctx)))
    client = endpoint_client(VLLM, "")
    _capture(client, {}, {"prompt_tokens": 1200, "completion_tokens": 300})
    _, meta = client.complete_json("s", "u", Out, model="Qwen/Qwen3.8-27B", context=LLMCallContext(agente="submission_evaluator"))
    assert meta.cost_usd is None
    assert rows[0]["costo_usd"] is None and rows[0]["tokens_in"] == 1200 and rows[0]["tokens_out"] == 300
    assert rows[0]["modelo"] == "Qwen/Qwen3.8-27B" and rows[0]["ok"] is True


def test_json_schema_by_endpoint_or_model_prefix(monkeypatch):
    monkeypatch.setattr(settings, "llm_json_schema_models", f"qwen/, {VLLM}")
    vllm = endpoint_client(VLLM, "")
    seen = _capture(vllm, {}, {})
    vllm.complete_json("s", "u", Out, model="Qwen/Qwen3.8-27B")  # el nombre servido por vLLM no tiene prefijo qwen/
    body = json.loads(seen[0].content)
    assert body["response_format"]["type"] == "json_schema" and body["response_format"]["json_schema"]["strict"] is True
    assert "JSON Schema" not in body["messages"][0]["content"]

    monkeypatch.setattr(settings, "llm_json_schema_models", "openai/")
    vllm = endpoint_client(VLLM, "")
    seen = _capture(vllm, {}, {})
    vllm.complete_json("s", "u", Out, model="Qwen/Qwen3.8-27B")
    body = json.loads(seen[0].content)
    assert "response_format" not in body and "JSON Schema" in body["messages"][0]["content"]


def test_openrouter_requests_keep_usage_accounting(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "llave-respaldo-ficticia")
    client = endpoint_client("", "")
    seen = _capture(client, {}, {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.0001})
    _, meta = client.complete_json("s", "u", Out, model="qwen/qwen3.8-27b")
    assert json.loads(seen[0].content)["usage"] == {"include": True} and meta.cost_usd == 0.0001
    assert seen[0].headers["x-openrouter-title"]


def test_evaluator_client_reads_settings(monkeypatch):
    monkeypatch.setattr(settings, "evaluator_base_url", VLLM)
    monkeypatch.setattr(settings, "evaluator_api_key", "token-del-vllm-udp")
    monkeypatch.setattr(settings, "evaluator_timeout_s", 90.0)
    client = evaluator_client()
    assert client.base_url == VLLM and client.api_key == "token-del-vllm-udp" and client._timeout_s == 90.0


def test_extract_json_ignores_visible_reasoning():
    assert extract_json('<think>pienso {en llaves}</think>\n{"resumen": "ok"}') == {"resumen": "ok"}
