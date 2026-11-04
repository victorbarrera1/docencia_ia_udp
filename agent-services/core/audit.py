"""Registro auditable del uso de IA (IA-07): listener del hook de openrouter_client que inserta en ai_audit_log.

Solo guarda metadatos (quién, cuándo, modelo, tokens, costo, latencia, resultado). Nunca prompts, respuestas
ni mensajes de error crudos: `error` es un código sanitizado. Un fallo al escribir no rompe la llamada de IA.
"""
import json
import logging
import re
import uuid
from typing import Any, Dict, Optional

import httpx

from config import settings
from core.llm_support import LLMCallContext, LLMMeta
from core.openrouter_client import register_audit_listener

logger = logging.getLogger("ai_audit")

DEFAULT_AGENT = "sin_agente"
_HTTP_ERROR = re.compile(r"^HTTP (\d{3})$")
_TIMEOUT = re.compile(r"time[d\s-]*out", re.IGNORECASE)
_FK_VIOLATION = "23503"


class AuditNotConfigured(Exception):
    pass


class AuditWriteError(Exception):
    pass


def sanitize_error(error: Optional[str]) -> Optional[str]:
    """Convierte el error del cliente LLM en un código (^[A-Z][A-Z0-9_]*$). Nunca copia el texto original."""
    if not error:
        return None
    http = _HTTP_ERROR.match(error)
    if http:
        return f"LLM_HTTP_{http.group(1)}"
    lowered = error.lower()
    if "schema" in lowered or "json" in lowered:
        return "LLM_INVALID_RESPONSE"
    if _TIMEOUT.search(error):
        return "LLM_TIMEOUT"
    return "LLM_ERROR"


def _uuid_or_none(value: Optional[str], field: str) -> Optional[str]:
    if value is None:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        logger.warning("ai_audit: %s no es un UUID válido; se registra sin él", field)
        return None


def build_row(meta: LLMMeta, context: LLMCallContext) -> Dict[str, Any]:
    """Fila de ai_audit_log con exactamente las columnas permitidas (sin ts: lo pone la base)."""
    error = None if meta.ok else (sanitize_error(meta.error) or "LLM_ERROR")
    return {
        "actor": _uuid_or_none(context.actor, "actor"),
        "section_id": _uuid_or_none(context.section_id, "section_id"),
        "version_id": _uuid_or_none(context.version_id, "version_id"),
        "agente": (context.agente or DEFAULT_AGENT)[:200],
        "modelo": (meta.model or "desconocido")[:200],
        "tokens_in": max(int(meta.tokens_in), 0),
        "tokens_out": max(int(meta.tokens_out), 0),
        "costo_usd": None if meta.cost_usd is None else round(max(float(meta.cost_usd), 0.0), 10),
        "latencia_ms": max(int(meta.latency_ms), 0),
        "ok": bool(meta.ok),
        "error": error,
    }


def supabase_rest_config() -> tuple[str, Dict[str, str]]:
    """URL base REST y cabeceras con la service key. Nunca cae a la anon key ni simula datos."""
    url = settings.supabase_url.rstrip("/")
    key = settings.supabase_service_role_key
    if not url or not key or "tu-proyecto" in url or key.startswith("tu_"):
        raise AuditNotConfigured("Supabase no está configurado")
    return f"{url}/rest/v1", {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


class AuditSink:
    """Escribe una fila en ai_audit_log vía PostgREST (síncrono, con tope de tiempo)."""

    def __init__(self, transport: Optional[httpx.BaseTransport] = None):
        self._transport = transport  # inyectable en tests (httpx.MockTransport)

    def write(self, row: Dict[str, Any]) -> None:
        base, headers = supabase_rest_config()
        headers["Prefer"] = "return=minimal"
        with httpx.Client(timeout=settings.audit_timeout_s, transport=self._transport) as http:
            res = http.post(f"{base}/ai_audit_log", headers=headers, json=row)
            if res.status_code == 409 and self._is_fk_violation(res) and any(row[k] for k in ("actor", "section_id", "version_id")):
                # Un ID inexistente no debe perder tokens ni costo: se reintenta una vez sin las referencias.
                logger.warning("ai_audit: referencia inexistente; se registra la llamada sin actor/sección/versión")
                row = {**row, "actor": None, "section_id": None, "version_id": None}
                res = http.post(f"{base}/ai_audit_log", headers=headers, json=row)
        if res.status_code >= 300:
            raise AuditWriteError(f"HTTP {res.status_code}")

    @staticmethod
    def _is_fk_violation(res: httpx.Response) -> bool:
        try:
            return res.json().get("code") == _FK_VIOLATION
        except (ValueError, AttributeError):
            return False


audit_sink = AuditSink()
_warned_unconfigured = False


def audit_listener(meta: LLMMeta, context: LLMCallContext) -> None:
    """Hook de openrouter_client. Nunca lanza: si falla, deja los metadatos en el log del servicio."""
    global _warned_unconfigured
    row: Optional[Dict[str, Any]] = None
    try:
        row = build_row(meta, context)
        audit_sink.write(row)
    except AuditNotConfigured:
        if not _warned_unconfigured:
            _warned_unconfigured = True
            logger.warning("ai_audit: Supabase sin configurar; el uso de IA no se está registrando")
    except Exception as exc:  # noqa: BLE001 - el registro jamás debe romper la llamada principal
        # Sin texto de usuario: la fila solo trae metadatos, así que sirve para reconstruirla desde los logs.
        logger.error("ai_audit: no se pudo registrar la llamada (%s). Fila: %s", type(exc).__name__,
                     json.dumps(row, ensure_ascii=False) if row is not None else "no construida")


def setup_audit() -> None:
    """Registra el listener (idempotente). Se invoca al arrancar la app."""
    if settings.audit_enabled:
        register_audit_listener(audit_listener)
