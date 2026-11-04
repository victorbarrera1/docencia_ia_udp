import json
import logging
from typing import List, Literal

import httpx
import pytest
from pydantic import BaseModel, Field

from agents.specialists.activity_designer import activity_designer_worker
from config import settings
from core.llm_support import (
    LLMCallContext, LLMHTTPError, LLMNotConfigured, LLMTransportError, LLMValidationError,
    extract_json,
)
from core.models import ActivityDesignRequest
from core.openrouter_client import OpenRouterClient, register_audit_listener, unregister_audit_listener

SECRET_PROMPT = "ENTREGA-CONFIDENCIAL-DEL-ALUMNO"


class Obs(BaseModel):
    criterio: str
    severidad: Literal["baja", "media", "alta"]


class Out(BaseModel):
    resumen: str
    nota: float = Field(ge=1.0, le=7.0)
    observaciones: List[Obs]


VALID = {"resumen": "ok", "nota": 5.5, "observaciones": [{"criterio": "c1", "severidad": "baja"}]}


def completion(content, model="openai/gpt-4o", usage=None):
    usage = usage or {"prompt_tokens": 100, "completion_tokens": 40, "cost": 0.002}
    body = {"model": model, "usage": usage, "choices": [{"message": {"content": content}}]}
    return httpx.Response(200, json=body)


class Harness:
    """Cliente con MockTransport que responde con la cola dada y registra peticiones y esperas."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.sleeps = []
        self.client = OpenRouterClient(transport=httpx.MockTransport(self.handle))
        self.client.api_key = "sk-or-test-key"
        self.client._sleep = self.sleeps.append

    def handle(self, request):
        self.requests.append(json.loads(request.content))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def audit():
    events = []
    listener = lambda meta, ctx: events.append((meta, ctx))  # noqa: E731
    register_audit_listener(listener)
    yield events
    unregister_audit_listener(listener)


# ---------- complete_json ----------

def test_json_schema_model_sends_strict_response_format(audit):
    h = Harness([completion(json.dumps(VALID))])
    ctx = LLMCallContext(agente="evaluator", section_id="s1", version_id="v1")
    result, meta = h.client.complete_json("sys", "user", Out, model="openai/gpt-4o", context=ctx)

    assert result == Out.model_validate(VALID)
    fmt = h.requests[0]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    schema = fmt["json_schema"]["schema"]
    assert schema["additionalProperties"] is False and schema["required"] == ["resumen", "nota", "observaciones"]
    assert schema["$defs"]["Obs"]["additionalProperties"] is False
    assert h.requests[0]["usage"] == {"include": True}
    assert (meta.model, meta.tokens_in, meta.tokens_out, meta.cost_usd) == ("openai/gpt-4o", 100, 40, 0.002)
    assert meta.ok and meta.attempts == 1 and meta.latency_ms >= 0
    assert audit == [(meta, ctx)]


def test_model_without_schema_support_asks_json_in_prompt():
    h = Harness([completion("```json\n" + json.dumps(VALID) + "\n```", model="openrouter/free")])
    result, _ = h.client.complete_json("sys", "user", Out, model="openrouter/free")
    assert result.nota == 5.5
    assert "response_format" not in h.requests[0]
    assert "JSON Schema" in h.requests[0]["messages"][0]["content"]


def test_invalid_json_is_retried_once_with_the_error():
    bad = json.dumps({**VALID, "nota": 99})
    h = Harness([completion(bad), completion(json.dumps(VALID))])
    result, meta = h.client.complete_json("sys", "user", Out)
    assert result.nota == 5.5
    assert len(h.requests) == 2
    retry_msgs = h.requests[1]["messages"]
    assert retry_msgs[-2] == {"role": "assistant", "content": bad}
    assert "nota" in retry_msgs[-1]["content"] and "99" not in retry_msgs[-1]["content"]
    assert (meta.attempts, meta.tokens_in, meta.tokens_out, meta.cost_usd) == (2, 200, 80, 0.004)


def test_non_json_text_is_retried():
    h = Harness([completion("No puedo responder eso"), completion(json.dumps(VALID))])
    result, _ = h.client.complete_json("sys", "user", Out)
    assert result.resumen == "ok"


def test_invalid_twice_raises_and_audits_failure(audit):
    h = Harness([completion("{}"), completion("{}")])
    with pytest.raises(LLMValidationError) as exc:
        h.client.complete_json("sys", "user", Out)
    assert len(h.requests) == 2
    assert exc.value.meta.ok is False and exc.value.meta.tokens_in == 200
    assert len(audit) == 1 and audit[0][0].ok is False and audit[0][0].error


def test_not_configured_raises_without_audit(audit):
    client = OpenRouterClient()
    with pytest.raises(LLMNotConfigured):
        client.complete_json("sys", "user", Out)
    assert audit == []


def test_extract_json_tolerates_surrounding_text():
    assert extract_json('Claro: {"a": 1} listo') == {"a": 1}


# ---------- reintentos ----------

def test_429_is_retried_honoring_retry_after():
    h = Harness([httpx.Response(429, headers={"Retry-After": "2"}), completion(json.dumps(VALID))])
    _, meta = h.client.complete_json("sys", "user", Out)
    assert h.sleeps == [2.0] and meta.attempts == 2 and len(h.requests) == 2


def test_5xx_exhausts_retries_with_exponential_backoff(audit):
    h = Harness([httpx.Response(503, text="down")] * 3)
    with pytest.raises(LLMHTTPError) as exc:
        h.client.complete_json("sys", "user", Out)
    assert exc.value.status == 503 and len(h.requests) == 1 + settings.llm_max_retries
    assert len(h.sleeps) == settings.llm_max_retries
    assert h.sleeps[1] > h.sleeps[0] >= settings.llm_backoff_base_s
    assert audit[0][0].attempts == 3 and audit[0][0].ok is False


def test_4xx_is_not_retried():
    h = Harness([httpx.Response(400, text="bad request")])
    with pytest.raises(LLMHTTPError) as exc:
        h.client.complete_json("sys", "user", Out)
    assert exc.value.status == 400 and len(h.requests) == 1 and h.sleeps == []


def test_timeout_is_retried():
    h = Harness([httpx.ReadTimeout(""), completion(json.dumps(VALID))])
    _, meta = h.client.complete_json("sys", "user", Out)
    assert meta.attempts == 2 and len(h.sleeps) == 1


def test_persistent_timeout_raises_transport_error():
    h = Harness([httpx.ConnectTimeout("")] * 3)
    with pytest.raises(LLMTransportError):
        h.client.complete_json("sys", "user", Out)


def test_200_with_error_body_is_treated_as_http_error():
    err = httpx.Response(200, json={"error": {"code": 429, "message": "rate limited"}})
    h = Harness([err, completion(json.dumps(VALID))])
    h.client.complete_json("sys", "user", Out)
    assert len(h.requests) == 2


def test_timeout_setting_is_applied(monkeypatch):
    seen = {}
    real_init = httpx.Client.__init__

    def spy(self, *args, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(settings, "llm_timeout_s", 7.0)
    monkeypatch.setattr(httpx.Client, "__init__", spy)
    Harness([completion(json.dumps(VALID))]).client.complete_json("sys", "user", Out)
    assert seen["timeout"] == 7.0


# ---------- hook de auditoría ----------

def test_hook_receives_tokens_and_context_from_chat_completion(audit):
    h = Harness([completion("hola", usage={"prompt_tokens": 7, "completion_tokens": 3, "cost": 0.0005})])
    ctx = LLMCallContext(agente="tutor", section_id="s9")
    res = h.client.chat_completion([{"role": "user", "content": "hi"}], context=ctx)
    meta, got_ctx = audit[0]
    assert res["content"] == "hola"
    assert (meta.tokens_in, meta.tokens_out, meta.cost_usd, meta.ok) == (7, 3, 0.0005, True)
    assert got_ctx == ctx


def test_hook_called_on_failure_and_listener_errors_do_not_break_calls(audit):
    def broken(meta, ctx):
        raise RuntimeError("persistencia caída")

    register_audit_listener(broken)
    h = Harness([httpx.Response(400, text="x"), completion(json.dumps(VALID))])
    assert h.client.chat_completion([{"role": "user", "content": "hi"}])["error"] == "HTTP 400"
    result, _ = h.client.complete_json("sys", "user", Out)
    assert result.nota == 5.5
    assert [ev[0].ok for ev in audit] == [False, True]


def test_meta_and_logs_never_contain_prompt_text(audit, caplog):
    caplog.set_level(logging.DEBUG)
    h = Harness([httpx.Response(500, text="boom")] * 3)
    with pytest.raises(LLMHTTPError):
        h.client.complete_json(SECRET_PROMPT, SECRET_PROMPT, Out)
    assert SECRET_PROMPT not in repr(audit) and SECRET_PROMPT not in caplog.text


# ---------- modo fake ----------

def test_fake_mode_is_deterministic_and_makes_no_http_calls(monkeypatch, audit):
    monkeypatch.setattr(settings, "llm_fake", True)
    client = OpenRouterClient()  # sin llave ni transporte: cualquier HTTP fallaría en el guard
    assert client.is_configured() is True
    first, meta = client.complete_json("sys", "user", Out)
    second, _ = client.complete_json("sys", "user", Out)
    assert first == second and 1.0 <= first.nota <= 7.0 and len(first.observaciones) == 1
    assert (meta.model, meta.ok, meta.tokens_in, meta.cost_usd) == ("fake/deterministic", True, 0, 0.0)
    assert len(audit) == 2
    assert client.chat_completion([{"role": "user", "content": "hi"}])["content"]


def test_missing_key_without_fake_keeps_legacy_behavior():
    client = OpenRouterClient()
    assert client.is_configured() is False
    assert client.chat_completion([{"role": "user", "content": "hi"}]) == {
        "error": "OPENROUTER_API_KEY no configurada", "content": "", "model_used": "none",
        "usage": {"total_tokens": 0},
    }


# ---------- compatibilidad de chat_completion ----------

def test_chat_completion_success_keeps_legacy_shape_and_payload():
    h = Harness([completion("hola", model="auto-resolved/model")])
    res = h.client.chat_completion(
        [{"role": "user", "content": "hi"}], "openrouter/auto", [{"type": "function"}], "low", "sess-1", 0.5, 123,
        {"type": "json_object"},
    )
    assert res == {"content": "hola", "tool_calls": [], "model_used": "auto-resolved/model",
                   "usage": {"prompt_tokens": 100, "completion_tokens": 40, "cost": 0.002}}
    req = h.requests[0]
    assert (req["model"], req["temperature"], req["max_tokens"], req["session_id"]) == ("openrouter/auto", 0.5, 123, "sess-1")
    assert req["plugins"] == [{"id": "auto-router", "cost_tier": "low"}]
    assert req["tools"] == [{"type": "function"}] and req["response_format"] == {"type": "json_object"}


def test_chat_completion_http_error_keeps_legacy_shape():
    h = Harness([httpx.Response(400, text="detalle")])
    assert h.client.chat_completion([{"role": "user", "content": "hi"}], model="m/x") == {
        "error": "HTTP 400", "details": "detalle", "content": "", "model_used": "m/x",
        "usage": {"total_tokens": 0},
    }


def test_chat_completion_never_raises_on_connection_failure():
    h = Harness([httpx.ConnectError("sin red")] * 3)
    res = h.client.chat_completion([{"role": "user", "content": "hi"}], model="m/x")
    assert res["error"] == "sin red" and res["content"] == "" and res["model_used"] == "m/x"


def test_chat_completion_retries_429():
    h = Harness([httpx.Response(429), completion("listo")])
    assert h.client.chat_completion([{"role": "user", "content": "hi"}])["content"] == "listo"


# ---------- quien ya lo usaba: activity_designer ----------

REQUEST = ActivityDesignRequest(objective="Patrones de diseño", duration_minutes=45)


def test_activity_designer_falls_back_without_key():
    res = activity_designer_worker.design_activity(REQUEST)
    assert res.title.startswith("Taller Práctico") and res.rubric


def test_activity_designer_uses_openrouter_after_retry(monkeypatch):
    design = {"title": "Diseño IA", "description": "d", "instructions": [], "rubric": [],
              "decimas_sugeridas": 0.1, "pauta_ayudante": "p"}
    h = Harness([httpx.Response(503), completion(json.dumps(design))])
    from agents.specialists import activity_designer as module
    monkeypatch.setattr(module, "openrouter_client", h.client)
    res = activity_designer_worker.design_activity(REQUEST)
    assert res.title == "Diseño IA" and len(h.requests) == 2


def test_activity_designer_falls_back_when_provider_keeps_failing(monkeypatch):
    h = Harness([httpx.Response(500)] * 3)
    from agents.specialists import activity_designer as module
    monkeypatch.setattr(module, "openrouter_client", h.client)
    assert activity_designer_worker.design_activity(REQUEST).title.startswith("Taller Práctico")
