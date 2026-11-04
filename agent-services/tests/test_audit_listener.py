import json
import logging
import re

import httpx
import pytest
from pydantic import BaseModel

from config import settings
from core import audit
from core.audit import AuditSink, audit_listener, build_row, sanitize_error, setup_audit
from core.llm_support import LLMCallContext, LLMMeta
from core.openrouter_client import OpenRouterClient, _audit_listeners, register_audit_listener

SECRET_PROMPT = "ENTREGA-CONFIDENCIAL-DEL-ALUMNO"
SECRET_REPLY = "RESPUESTA-PRIVADA-DEL-MODELO"
SECTION = "b1040000-0000-4000-a000-000000000011"
ACTOR = "a1040000-0000-4000-a000-000000000001"
ROW_COLUMNS = {"actor", "section_id", "version_id", "agente", "modelo", "tokens_in", "tokens_out",
               "costo_usd", "latencia_ms", "ok", "error"}
ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,79}$")  # CHECK de ai_audit_log.error


class Out(BaseModel):
    resumen: str


class Supabase:
    """Supabase simulado (MockTransport): guarda las peticiones y responde con la cola dada (por defecto 201)."""

    def __init__(self, responses=()):
        self.requests, self.responses = [], list(responses)

    def handle(self, request):
        self.requests.append(request)
        item = self.responses.pop(0) if self.responses else httpx.Response(201)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def rows(self):
        return [json.loads(r.content) for r in self.requests]


@pytest.fixture
def supabase(monkeypatch, supabase_env):
    fake = Supabase()
    monkeypatch.setattr(audit, "audit_sink", AuditSink(transport=httpx.MockTransport(fake.handle)))
    register_audit_listener(audit_listener)
    return fake


def llm(handler):
    client = OpenRouterClient(transport=httpx.MockTransport(handler))
    client.api_key = "sk-or-test-key"
    client._sleep = lambda _: None
    return client


def completion(content, usage=None):
    usage = usage or {"prompt_tokens": 100, "completion_tokens": 40, "cost": 0.002}
    return httpx.Response(200, json={"model": "openai/gpt-4o", "usage": usage, "choices": [{"message": {"content": content}}]})


# ---------- una llamada genera una fila ----------

def test_fake_call_inserts_one_row(supabase, monkeypatch):
    monkeypatch.setattr(settings, "llm_fake", True)
    ctx = LLMCallContext(agente="evaluator", section_id=SECTION, actor=ACTOR)
    OpenRouterClient().complete_json("sys", SECRET_PROMPT, Out, context=ctx)

    assert len(supabase.requests) == 1
    req = supabase.requests[0]
    assert req.method == "POST" and req.url.path == "/rest/v1/ai_audit_log"
    assert req.headers["apikey"] == "service-key-ficticia"
    assert req.headers["Authorization"] == "Bearer service-key-ficticia"
    assert supabase.rows[0] == {
        "actor": ACTOR, "section_id": SECTION, "version_id": None, "agente": "evaluator",
        "modelo": "fake/deterministic", "tokens_in": 0, "tokens_out": 0, "costo_usd": 0.0,
        "latencia_ms": supabase.rows[0]["latencia_ms"], "ok": True, "error": None,
    }


def test_chat_completion_fake_also_inserts(supabase, monkeypatch):
    monkeypatch.setattr(settings, "llm_fake", True)
    OpenRouterClient().chat_completion([{"role": "user", "content": "hola"}])
    assert [r["agente"] for r in supabase.rows] == ["sin_agente"]


def test_real_call_records_tokens_cost_and_model(supabase):
    llm(lambda r: completion(json.dumps({"resumen": "ok"}))).complete_json("sys", "user", Out)
    row = supabase.rows[0]
    assert (row["modelo"], row["tokens_in"], row["tokens_out"], row["costo_usd"], row["ok"]) == ("openai/gpt-4o", 100, 40, 0.002, True)


def test_unknown_cost_is_null_not_zero(supabase):
    llm(lambda r: completion(json.dumps({"resumen": "ok"}), usage={"prompt_tokens": 5, "completion_tokens": 1})
        ).complete_json("sys", "user", Out)
    assert supabase.rows[0]["costo_usd"] is None


# ---------- nunca guarda texto ----------

def test_row_has_only_allowed_columns_and_no_prompt_or_reply(supabase, caplog):
    caplog.set_level(logging.DEBUG)
    llm(lambda r: completion(json.dumps({"resumen": SECRET_REPLY}))).complete_json(
        "sys", SECRET_PROMPT, Out, context=LLMCallContext(agente="evaluator"))
    assert set(supabase.rows[0]) == ROW_COLUMNS
    for text in (supabase.requests[0].content.decode(), caplog.text):
        assert SECRET_PROMPT not in text and SECRET_REPLY not in text


def test_failed_call_records_sanitized_code_not_provider_text(supabase):
    body = f"provider says: {SECRET_PROMPT}"
    client = llm(lambda r: httpx.Response(429, text=body))
    with pytest.raises(Exception):
        client.complete_json("sys", SECRET_PROMPT, Out)
    row = supabase.rows[0]
    assert (row["ok"], row["error"]) == (False, "LLM_HTTP_429")
    assert SECRET_PROMPT not in json.dumps(row)


@pytest.mark.parametrize("raw, code", [
    (None, None), ("HTTP 500", "LLM_HTTP_500"), ("ReadTimeout", "LLM_TIMEOUT"),
    ("The read operation timed out", "LLM_TIMEOUT"),
    ("La respuesta no cumple el schema: nota: Input should be less than or equal to 7", "LLM_INVALID_RESPONSE"),
    ("Respuesta de OpenRouter no es JSON", "LLM_INVALID_RESPONSE"),
    (f"algo con {SECRET_PROMPT}", "LLM_ERROR"),
])
def test_sanitize_error(raw, code):
    assert sanitize_error(raw) == code
    assert code is None or ERROR_CODE.match(code)


def test_failed_call_without_error_text_still_gets_code():
    meta = LLMMeta(model="m/x", tokens_in=0, tokens_out=0, cost_usd=None, latency_ms=1, attempts=1, ok=False, error=None)
    assert build_row(meta, LLMCallContext())["error"] == "LLM_ERROR"


# ---------- un fallo al escribir no rompe la llamada ----------

@pytest.mark.parametrize("failure", [
    httpx.Response(500, text=f"boom {SECRET_PROMPT}"),
    httpx.ConnectTimeout("timed out"),
    httpx.Response(401, json={"message": "Invalid API key"}),
])
def test_write_failure_does_not_break_the_call_and_is_logged(supabase, caplog, failure):
    supabase.responses.append(failure)
    result, meta = llm(lambda r: completion(json.dumps({"resumen": "ok"}))).complete_json(
        "sys", SECRET_PROMPT, Out, context=LLMCallContext(agente="evaluator", section_id=SECTION))
    assert result.resumen == "ok" and meta.ok
    errors = [r for r in caplog.records if r.name == "ai_audit" and r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "openai/gpt-4o" in errors[0].getMessage() and SECTION in errors[0].getMessage()  # metadatos para reconstruir la fila
    assert SECRET_PROMPT not in caplog.text and "service-key-ficticia" not in caplog.text


def test_listener_never_raises(supabase, monkeypatch):
    monkeypatch.setattr(audit, "build_row", lambda *a: (_ for _ in ()).throw(RuntimeError("falla interna")))
    meta = LLMMeta(model="m/x", tokens_in=0, tokens_out=0, cost_usd=None, latency_ms=1, attempts=1, ok=True)
    assert audit_listener(meta, LLMCallContext()) is None
    assert supabase.requests == []


def test_unknown_reference_retries_without_ids_to_keep_cost(supabase):
    supabase.responses.append(httpx.Response(409, json={"code": "23503", "message": "violates foreign key"}))
    llm(lambda r: completion(json.dumps({"resumen": "ok"}))).complete_json(
        "sys", "user", Out, context=LLMCallContext(agente="evaluator", section_id=SECTION, actor=ACTOR))
    first, second = supabase.rows
    assert first["section_id"] == SECTION and second["section_id"] is None and second["actor"] is None
    assert second["tokens_in"] == 100 and second["costo_usd"] == 0.002


def test_other_conflicts_are_not_retried(supabase, caplog):
    supabase.responses.append(httpx.Response(409, json={"code": "23505"}))
    llm(lambda r: completion(json.dumps({"resumen": "ok"}))).complete_json(
        "sys", "user", Out, context=LLMCallContext(section_id=SECTION))
    assert len(supabase.requests) == 1
    assert "no se pudo registrar" in caplog.text


def test_invalid_uuid_is_dropped_but_row_is_written(supabase):
    llm(lambda r: completion(json.dumps({"resumen": "ok"}))).complete_json(
        "sys", "user", Out, context=LLMCallContext(section_id="no-es-uuid", version_id=SECTION))
    row = supabase.rows[0]
    assert row["section_id"] is None and row["version_id"] == SECTION


def test_without_supabase_config_nothing_is_sent_and_warns_once(monkeypatch, caplog):
    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "supabase_service_role_key", "")
    monkeypatch.setattr(audit, "_warned_unconfigured", False)
    sent = Supabase()
    monkeypatch.setattr(audit, "audit_sink", AuditSink(transport=httpx.MockTransport(sent.handle)))
    register_audit_listener(audit_listener)
    client = llm(lambda r: completion(json.dumps({"resumen": "ok"})))
    for _ in range(3):
        client.complete_json("sys", "user", Out)
    assert sent.requests == []
    assert caplog.text.count("Supabase sin configurar") == 1


def test_placeholder_env_values_count_as_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "https://tu-proyecto.supabase.co")
    monkeypatch.setattr(settings, "supabase_service_role_key", "tu_service_role_key_aqui")
    with pytest.raises(audit.AuditNotConfigured):
        audit.supabase_rest_config()


def test_setup_audit_is_idempotent_and_can_be_disabled(monkeypatch):
    setup_audit()
    setup_audit()
    assert _audit_listeners.count(audit_listener) == 1
    _audit_listeners.clear()
    monkeypatch.setattr(settings, "audit_enabled", False)
    setup_audit()
    assert audit_listener not in _audit_listeners
