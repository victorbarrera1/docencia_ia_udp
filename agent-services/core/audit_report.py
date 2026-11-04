"""Consulta del registro de uso de IA (IA-07): lista reciente y totales exactos por modelo para la rendición del fondo."""
import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

import httpx

from core.audit import AuditNotConfigured, supabase_rest_config

QUERY_TIMEOUT_S = 10.0
ITEM_COLUMNS = "id,ts,actor,section_id,version_id,agente,modelo,tokens_in,tokens_out,costo_usd,latencia_ms,ok,error"


class AuditQueryError(Exception):
    pass


def _money(value: Any) -> str:
    return format(Decimal(str(value)), "f")


def _filters(section_id: Optional[str], desde: Optional[date], hasta: Optional[date], agente: Optional[str]) -> List[tuple]:
    params: List[tuple] = []
    if section_id:
        params.append(("section_id", f"eq.{section_id}"))
    if agente:
        params.append(("agente", f"eq.{agente}"))
    if desde:  # fechas UTC, ambas inclusivas
        params.append(("ts", f"gte.{desde.isoformat()}T00:00:00Z"))
    if hasta:
        params.append(("ts", f"lt.{(hasta + timedelta(days=1)).isoformat()}T00:00:00Z"))
    return params


def _usage(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "llamadas": row["llamadas"], "llamadas_ok": row["llamadas_ok"], "llamadas_error": row["llamadas_error"],
        "tokens_in": row["tokens_in"], "tokens_out": row["tokens_out"],
        "costo_usd": _money(row["costo_usd"]), "llamadas_sin_costo": row["llamadas_sin_costo"],
    }


def _item(row: Dict[str, Any]) -> Dict[str, Any]:
    cost = row.get("costo_usd")
    return {**{k: row.get(k) for k in ITEM_COLUMNS.split(",")}, "costo_usd": None if cost is None else _money(cost)}


def _json(res: httpx.Response) -> Any:
    if res.status_code != 200:
        raise AuditQueryError(f"HTTP {res.status_code}")
    return json.loads(res.text, parse_float=Decimal)  # los costos llegan como números JSON: sin pasar por float


def fetch_audit_report(
    section_id: Optional[str], desde: Optional[date], hasta: Optional[date], agente: Optional[str], limit: int,
    transport: Optional[httpx.BaseTransport] = None,
) -> Dict[str, Any]:
    """Totales (RPC ai_audit_totals, exactos en la base) más las últimas `limit` filas. Lanza AuditNotConfigured/AuditQueryError."""
    base, headers = supabase_rest_config()
    rpc_body = {"p_section_id": section_id, "p_desde": desde.isoformat() if desde else None,
                "p_hasta": hasta.isoformat() if hasta else None, "p_agente": agente}
    list_params = [("select", ITEM_COLUMNS), ("order", "ts.desc"), ("limit", str(limit + 1)),
                   *_filters(section_id, desde, hasta, agente)]
    try:
        with httpx.Client(timeout=QUERY_TIMEOUT_S, transport=transport) as http:
            totals = _json(http.post(f"{base}/rpc/ai_audit_totals", headers=headers, json=rpc_body))
            rows = _json(http.get(f"{base}/ai_audit_log", headers=headers, params=list_params))
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise AuditQueryError(type(exc).__name__) from exc
    try:
        total = next(r for r in totals if r["es_total"])
        by_model = [_usage(r) | {"modelo": r["modelo"]} for r in totals if not r["es_total"]]
        return {"total": _usage(total), "por_modelo": by_model, "items": [_item(r) for r in rows[:limit]],
                "truncated": len(rows) > limit}
    except (StopIteration, KeyError, TypeError) as exc:
        raise AuditQueryError("respuesta inesperada") from exc
