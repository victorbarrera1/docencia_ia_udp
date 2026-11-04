import json

import httpx
import pytest

from config import settings
from core import audit
from core.audit import AuditSink, audit_listener
from core.engine import agent_loop_engine
from core.openrouter_client import OpenRouterClient, register_audit_listener

SYSTEM = "Eres el agente UDP."
MESSAGE = "Hola, ¿cómo estás?"


class Recorder:
    def __init__(self, llm_response):
        self.llm_response, self.llm_requests, self.audit_rows = llm_response, [], []

    def llm(self, request):
        self.llm_requests.append(json.loads(request.content))
        return self.llm_response

    def supabase(self, request):
        self.audit_rows.append(json.loads(request.content))
        return httpx.Response(201)


@pytest.fixture
def wired(monkeypatch, supabase_env):
    def build(llm_response):
        rec = Recorder(llm_response)
        client = OpenRouterClient(transport=httpx.MockTransport(rec.llm))
        client.api_key = "sk-or-test-key"
        client._sleep = lambda _: None
        monkeypatch.setattr("core.engine.openrouter_client", client)
        monkeypatch.setattr(audit, "audit_sink", AuditSink(transport=httpx.MockTransport(rec.supabase)))
        register_audit_listener(audit_listener)
        return rec
    return build


def completion(content, usage):
    return httpx.Response(200, json={"model": "google/gemini-2.5-flash", "usage": usage,
                                     "choices": [{"message": {"content": content}}]})


def test_chat_goes_through_openrouter_with_chat_model_and_is_audited(wired):
    rec = wired(completion("Respuesta del modelo", {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42, "cost": 0.0001}))
    reply, _, sources, tokens = agent_loop_engine.run_turn(SYSTEM, MESSAGE, "CIT3203", "excel_engine")
    assert reply == "Respuesta del modelo" and tokens == 42
    sent = rec.llm_requests[0]
    assert sent["model"] == settings.chat_model == "google/gemini-2.5-flash"
    assert [m["role"] for m in sent["messages"]] == ["system", "user"] and MESSAGE in sent["messages"][1]["content"]
    row = rec.audit_rows[0]
    assert (row["agente"], row["modelo"], row["tokens_in"], row["tokens_out"], row["costo_usd"], row["ok"]) == (
        "chat:excel_engine", "google/gemini-2.5-flash", 30, 12, 0.0001, True)
    assert MESSAGE not in json.dumps(row) and "Respuesta del modelo" not in json.dumps(row)


def test_chat_agent_name_without_worker_role(wired):
    rec = wired(completion("ok", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}))
    agent_loop_engine.run_turn(SYSTEM, MESSAGE, "CIT3203")
    assert rec.audit_rows[0]["agente"] == "chat"


def test_failed_llm_falls_back_to_deterministic_reply_without_leaking_error(wired):
    rec = wired(httpx.Response(401, text="No auth credentials found: detalle interno"))
    reply, _, sources, tokens = agent_loop_engine.run_turn(SYSTEM, MESSAGE, "CIT3203")
    assert "Agente Jerárquico UDP" in reply and tokens > 0
    assert any("Fallo llamada LLM" in s for s in sources) and not any("detalle interno" in s for s in sources)
    assert (rec.audit_rows[0]["ok"], rec.audit_rows[0]["error"]) == (False, "LLM_HTTP_401")


def test_chat_in_fake_mode_is_audited(wired, monkeypatch):
    rec = wired(httpx.Response(500))
    monkeypatch.setattr(settings, "llm_fake", True)
    reply, *_ = agent_loop_engine.run_turn(SYSTEM, MESSAGE, "CIT3203")
    assert reply == "Respuesta fake determinista." and rec.llm_requests == []
    assert rec.audit_rows[0]["modelo"] == "fake/deterministic"


def test_without_llm_key_no_call_and_no_audit_row(wired, monkeypatch):
    rec = wired(httpx.Response(500))
    monkeypatch.setattr("core.engine.openrouter_client.api_key", "")
    agent_loop_engine.run_turn(SYSTEM, MESSAGE, "CIT3203")
    assert rec.llm_requests == [] and rec.audit_rows == []
