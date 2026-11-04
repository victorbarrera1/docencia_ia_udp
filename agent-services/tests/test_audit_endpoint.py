import functools
import json
from datetime import date

import httpx
import pytest

import main
from core.audit_report import _filters, fetch_audit_report

URL = "/api/v1/audit"
SECTION = "b1040000-0000-4000-a000-000000000011"
ACTOR = "a1040000-0000-4000-a000-000000000001"

# Respuesta de la RPC ai_audit_totals tal como la entrega PostgREST (numeric como número JSON exacto).
TOTALS = """[
 {"modelo":"openai/gpt-4o","es_total":false,"llamadas":3,"llamadas_ok":2,"llamadas_error":1,"tokens_in":307,"tokens_out":53,
  "costo_usd":0.0007,"llamadas_sin_costo":0},
 {"modelo":"google/gemini-2.5-flash","es_total":false,"llamadas":1,"llamadas_ok":1,"llamadas_error":0,"tokens_in":50,"tokens_out":5,
  "costo_usd":0.0000,"llamadas_sin_costo":1},
 {"modelo":null,"es_total":true,"llamadas":4,"llamadas_ok":3,"llamadas_error":1,"tokens_in":357,"tokens_out":58,
  "costo_usd":0.0007,"llamadas_sin_costo":1}
]"""


def item(n, cost):
    return {"id": f"e1070000-0000-4000-a000-00000000000{n}", "ts": f"2026-11-0{n}T12:00:00+00:00", "actor": ACTOR,
            "section_id": SECTION, "version_id": None, "agente": "evaluador", "modelo": "openai/gpt-4o",
            "tokens_in": 10, "tokens_out": 2, "costo_usd": cost, "latencia_ms": 5, "ok": True, "error": None}


class Upstream:
    def __init__(self, totals=TOTALS, rows=None, status=200):
        self.totals = totals
        self.rows = rows if rows is not None else [item(3, 0.0004), item(2, None), item(1, 0.0000)]
        self.status, self.requests = status, []

    def handle(self, request):
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, text="relation ai_audit_log: detalle interno secreto")
        if request.url.path.endswith("/rpc/ai_audit_totals"):
            return httpx.Response(200, text=self.totals)
        return httpx.Response(200, text=json.dumps(self.rows))


@pytest.fixture
def upstream(monkeypatch, supabase_env):
    def install(**kwargs):
        fake = Upstream(**kwargs)
        monkeypatch.setattr(main, "fetch_audit_report", functools.partial(
            fetch_audit_report, transport=httpx.MockTransport(fake.handle)))
        return fake
    return install


def test_requires_service_key(client):
    assert client.get(URL).status_code == 401


def test_totals_by_model_and_items(client, auth_headers, upstream):
    upstream()
    body = client.get(URL, headers=auth_headers).json()
    assert body["total"] == {"llamadas": 4, "llamadas_ok": 3, "llamadas_error": 1, "tokens_in": 357, "tokens_out": 58,
                             "costo_usd": "0.0007", "llamadas_sin_costo": 1}
    assert [(m["modelo"], m["llamadas"], m["costo_usd"]) for m in body["por_modelo"]] == [
        ("openai/gpt-4o", 3, "0.0007"), ("google/gemini-2.5-flash", 1, "0.0000")]
    assert [i["costo_usd"] for i in body["items"]] == ["0.0004", None, "0.0"]  # NULL ≠ 0 (json.dumps escribe 0.0; PostgREST enviaría 0.0000)
    assert body["items"][0]["actor"] == ACTOR and body["truncated"] is False


def test_forwards_filters_to_rpc_and_list(client, auth_headers, upstream):
    fake = upstream()
    client.get(URL, headers=auth_headers, params={"section_id": SECTION, "desde": "2026-11-01", "hasta": "2026-11-30",
                                                  "agente": "evaluador", "limit": 2})
    rpc, listing = fake.requests
    assert json.loads(rpc.content) == {"p_section_id": SECTION, "p_desde": "2026-11-01", "p_hasta": "2026-11-30",
                                       "p_agente": "evaluador"}
    assert rpc.headers["Authorization"] == "Bearer service-key-ficticia"
    query = listing.url.params.multi_items()
    assert ("section_id", f"eq.{SECTION}") in query and ("agente", "eq.evaluador") in query
    assert ("ts", "gte.2026-11-01T00:00:00Z") in query and ("ts", "lt.2026-12-01T00:00:00Z") in query  # hasta inclusivo
    assert ("limit", "3") in query and ("order", "ts.desc") in query


def test_truncated_when_more_rows_than_limit(client, auth_headers, upstream):
    upstream()
    body = client.get(URL, headers=auth_headers, params={"limit": 2}).json()
    assert len(body["items"]) == 2 and body["truncated"] is True and body["total"]["llamadas"] == 4


def test_empty_range_returns_zero_total(client, auth_headers, upstream):
    zero = '[{"modelo":null,"es_total":true,"llamadas":0,"llamadas_ok":0,"llamadas_error":0,"tokens_in":0,"tokens_out":0,"costo_usd":0.0000,"llamadas_sin_costo":0}]'
    upstream(totals=zero, rows=[])
    body = client.get(URL, headers=auth_headers).json()
    assert body["por_modelo"] == [] and body["items"] == [] and body["total"]["costo_usd"] == "0.0000"


@pytest.mark.parametrize("params", [
    {"section_id": "no-es-uuid"}, {"desde": "ayer"}, {"limit": 0}, {"limit": 501},
    {"agente": "x" * 101}, {"desde": "2026-12-01", "hasta": "2026-11-01"},
])
def test_invalid_filters_are_rejected(client, auth_headers, upstream, params):
    fake = upstream()
    assert client.get(URL, headers=auth_headers, params=params).status_code == 422
    assert fake.requests == []


def test_503_without_supabase(client, auth_headers, monkeypatch):
    monkeypatch.setattr("config.settings.supabase_url", "")
    res = client.get(URL, headers=auth_headers)
    assert res.status_code == 503 and res.json() == {"error": "Servicio de auditoría no disponible."}


def test_upstream_failure_is_generic(client, auth_headers, upstream):
    upstream(status=500)
    res = client.get(URL, headers=auth_headers)
    assert res.status_code == 502 and "secreto" not in res.text and "ai_audit_log" not in res.text


def test_unexpected_upstream_shape_is_generic(client, auth_headers, upstream):
    upstream(totals='[{"modelo":"x"}]')
    assert client.get(URL, headers=auth_headers).status_code == 502


def test_filters_helper_skips_empty_values():
    assert _filters(None, None, None, None) == []
    assert _filters(None, date(2026, 11, 3), None, None) == [("ts", "gte.2026-11-03T00:00:00Z")]
