import json
import logging
import math
import random
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx
from pydantic import BaseModel, ValidationError

from config import settings
from core.llm_support import (
    EmbeddingBatch, LLMCallContext, LLMError, LLMHTTPError, LLMMeta, LLMNotConfigured, LLMTransportError,
    LLMValidationError, extract_json, fake_embedding, fake_instance, harden_schema, parse_retry_after,
    validation_summary,
)

logger = logging.getLogger("openrouter_client")

AuditListener = Callable[[LLMMeta, LLMCallContext], None]
_audit_listeners: List[AuditListener] = []


def register_audit_listener(fn: AuditListener) -> None:
    """Registra un hook que recibe (meta, contexto) tras cada llamada, exitosa o fallida (persistencia: IA-07)."""
    if fn not in _audit_listeners:
        _audit_listeners.append(fn)


def unregister_audit_listener(fn: AuditListener) -> None:
    if fn in _audit_listeners:
        _audit_listeners.remove(fn)


def clear_audit_listeners() -> None:
    _audit_listeners.clear()


class OpenRouterClient:
    """
    Cliente oficial y tipado para OpenRouter API v1 (Guía Maestra).
    Soporta Auto Router, Cost Tiers, Session Stickiness y Tool Calling, más salida JSON validada,
    reintentos con backoff, hook de auditoría y modo fake determinista (LLM_FAKE=1).
    """
    def __init__(self, transport: Optional[httpx.BaseTransport] = None):
        self.api_key = settings.openrouter_api_key
        self.base_url = settings.openrouter_base_url.rstrip("/")
        self.site_url = settings.openrouter_site_url
        self.site_name = settings.openrouter_site_name
        self._transport = transport  # inyectable en tests (httpx.MockTransport)
        self._sleep = time.sleep

    def is_configured(self) -> bool:
        return settings.llm_fake or bool(self.api_key and len(self.api_key) > 5)

    # ---------- API pública ----------

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        cost_tier: Optional[str] = None,
        session_id: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 2000,
        response_format: Optional[Dict[str, Any]] = None,
        context: Optional[LLMCallContext] = None,
    ) -> Dict[str, Any]:
        """
        Ejecuta una petición síncrona a OpenRouter /chat/completions.
        Nunca lanza: ante un fallo devuelve un dict con la clave `error`.
        """
        target_model = model or settings.default_openrouter_model
        if not self.is_configured():
            return {
                "error": "OPENROUTER_API_KEY no configurada",
                "content": "",
                "model_used": "none",
                "usage": {"total_tokens": 0}
            }

        started = time.perf_counter()
        if settings.llm_fake:
            content = "{}" if response_format else "Respuesta fake determinista."
            usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0.0}
            self._notify(self._meta("fake/deterministic", usage, started, 0), context)
            return {"content": content, "tool_calls": [], "model_used": "fake/deterministic", "usage": usage}

        payload = self._payload(messages, target_model, temperature, max_tokens)
        if session_id:
            payload["session_id"] = session_id
        if cost_tier and "auto" in target_model:
            payload["plugins"] = [{"id": "auto-router", "cost_tier": cost_tier}]
        if tools:
            payload["tools"] = tools
        if response_format:
            payload["response_format"] = response_format

        sent = {"n": 0}
        try:
            data = self._send(payload, sent)
            message = self._first_message(data)
        except LLMHTTPError as e:
            self._notify(self._meta(target_model, {}, started, sent["n"], error=str(e)), context)
            return {"error": f"HTTP {e.status}", "details": e.body, "content": "",
                    "model_used": target_model, "usage": {"total_tokens": 0}}
        except Exception as e:
            logger.error(f"Falla de conexión con OpenRouter: {e}")
            self._notify(self._meta(target_model, {}, started, sent["n"], error=type(e).__name__), context)
            return {"error": str(e), "content": "", "model_used": target_model, "usage": {"total_tokens": 0}}

        usage = data.get("usage", {"total_tokens": 0})
        self._notify(self._meta(data.get("model", target_model), usage, started, sent["n"]), context)
        return {
            "content": message.get("content", ""),
            "tool_calls": message.get("tool_calls", []),
            "model_used": data.get("model", target_model),
            "usage": usage,
        }

    def complete_json(
        self,
        system: str,
        user: str,
        schema: type[BaseModel],
        model: Optional[str] = None,
        *,
        context: Optional[LLMCallContext] = None,
        temperature: float = 0.2,
        max_tokens: int = 2000,
    ) -> Tuple[BaseModel, LLMMeta]:
        """
        Pide una respuesta JSON que cumpla `schema` y la valida con Pydantic. Si el modelo soporta
        json_schema estricto lo usa; si no, lo pide en el prompt. Ante JSON inválido reintenta una vez
        indicando el error. Lanza LLMError (con `.meta`); la auditoría se dispara siempre.
        Los tokens y el costo de `meta` suman todas las peticiones de la llamada.
        """
        if not self.is_configured():
            raise LLMNotConfigured("OPENROUTER_API_KEY no configurada")
        started = time.perf_counter()
        if settings.llm_fake:
            try:
                instance = fake_instance(schema)
            except LLMError as e:
                self._fail(e, self._meta("fake/deterministic", {}, started, 0, error=str(e)), context)
            meta = self._meta("fake/deterministic", {"cost": 0.0}, started, 0)
            self._notify(meta, context)
            return instance, meta

        target = model or settings.default_openrouter_model
        schema_dict = schema.model_json_schema()
        structured = any(target.startswith(p.strip()) for p in settings.llm_json_schema_models.split(",") if p.strip())
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        if not structured:
            messages[0]["content"] += (
                "\n\nResponde ÚNICAMENTE con un objeto JSON válido, sin texto adicional, que cumpla este JSON Schema:\n"
                + json.dumps(schema_dict, ensure_ascii=False)
            )
        payload = self._payload(messages, target, temperature, max_tokens)
        if structured:
            payload["response_format"] = {"type": "json_schema", "json_schema": {
                "name": schema.__name__, "strict": True, "schema": harden_schema(schema_dict)}}

        totals: Dict[str, Any] = {"prompt_tokens": 0, "completion_tokens": 0, "cost": None}
        sent, used_model = {"n": 0}, target
        try:
            for validation_attempt in range(2):
                data = self._send(payload, sent)
                used_model = data.get("model", used_model)
                usage = data.get("usage") or {}
                totals["prompt_tokens"] += usage.get("prompt_tokens", 0)
                totals["completion_tokens"] += usage.get("completion_tokens", 0)
                if usage.get("cost") is not None:
                    totals["cost"] = (totals["cost"] or 0.0) + usage["cost"]
                content = self._first_message(data).get("content") or ""
                try:
                    instance = schema.model_validate(extract_json(content))
                    meta = self._meta(used_model, totals, started, sent["n"])
                    self._notify(meta, context)
                    return instance, meta
                except (ValueError, ValidationError) as bad:  # JSONDecodeError y ValidationError son ValueError
                    problem = validation_summary(bad)
                    logger.warning(f"Respuesta LLM inválida (intento {validation_attempt + 1}): {problem}")
                    if validation_attempt == 1:
                        raise LLMValidationError(f"La respuesta no cumple el schema: {problem}") from None
                    payload["messages"] = messages + [
                        {"role": "assistant", "content": content},
                        {"role": "user", "content": f"Tu respuesta no es válida: {problem}. "
                                                    "Devuelve únicamente el JSON corregido."},
                    ]
        except LLMError as e:
            self._fail(e, self._meta(used_model, totals, started, sent["n"], error=str(e)), context)
        raise AssertionError("inalcanzable")  # pragma: no cover

    def embeddings(
        self,
        texts: List[str],
        model: Optional[str] = None,
        *,
        context: Optional[LLMCallContext] = None,
    ) -> EmbeddingBatch:
        """
        Vectores de `texts` vía OpenRouter /embeddings (misma ruta de reintentos y auditoría que el chat).
        Un lote = una llamada auditada. IMPORTANTE: los textos deben venir ya seudonimizados; el único
        llamador permitido es rag.embedder.PrivateEmbedder. Lanza LLMError (con `.meta`) ante cualquier fallo.
        """
        if not texts:
            raise ValueError("embeddings requiere al menos un texto")
        if not self.is_configured():
            raise LLMNotConfigured("OPENROUTER_API_KEY no configurada")
        target, dim = model or settings.embedding_model, settings.embedding_dim
        started = time.perf_counter()
        if settings.llm_fake:
            meta = self._meta("fake/deterministic", {"cost": 0.0}, started, 0)
            self._notify(meta, context)
            return EmbeddingBatch([fake_embedding(t, dim) for t in texts], f"fake/{target}", meta)

        payload = {"model": target, "input": list(texts), "encoding_format": "float"}
        sent, used_model, usage = {"n": 0}, target, {}
        try:
            data = self._send(payload, sent, path="/embeddings")
            if not isinstance(data, dict):
                raise LLMError("Respuesta de embeddings no es un objeto")
            used_model = data.get("model") or target
            usage = data.get("usage") or {}
            vectors = self._parse_embeddings(data, len(texts), dim)
        except LLMError as e:
            self._fail(e, self._meta(used_model, usage, started, sent["n"], error=str(e)), context)
        meta = self._meta(used_model, {**usage, "completion_tokens": 0}, started, sent["n"])
        self._notify(meta, context)
        return EmbeddingBatch(vectors, target, meta)

    @staticmethod
    def _parse_embeddings(data: Dict[str, Any], expected: int, dim: int) -> List[List[float]]:
        """Valida cantidad, orden y dimensión. Los mensajes llevan solo conteos, nunca contenido."""
        items = data.get("data")
        if not isinstance(items, list) or len(items) != expected:
            raise LLMValidationError(f"La respuesta de embeddings no cumple el schema: se esperaban {expected} vectores")
        try:
            ordered = sorted(items, key=lambda item: item["index"])
        except (KeyError, TypeError):
            raise LLMValidationError("La respuesta de embeddings no cumple el schema: falta el índice") from None
        if [item["index"] for item in ordered] != list(range(expected)):
            raise LLMValidationError("La respuesta de embeddings no cumple el schema: índices inconsistentes")
        vectors = []
        for item in ordered:
            vector = item.get("embedding")
            valid = (isinstance(vector, list) and len(vector) == dim
                     and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in vector))
            if not valid:
                raise LLMValidationError(f"La respuesta de embeddings no cumple el schema: se esperaban {dim} dimensiones")
            vectors.append([float(v) for v in vector])
        return vectors

    # ---------- Internos ----------

    def _fail(self, error: LLMError, meta: LLMMeta, context: Optional[LLMCallContext]) -> None:
        error.meta = meta
        self._notify(meta, context)
        raise error

    @staticmethod
    def _payload(messages: List[Dict[str, str]], model: str, temperature: float, max_tokens: int) -> Dict[str, Any]:
        return {"model": model, "messages": messages, "temperature": temperature,
                "max_tokens": max_tokens, "usage": {"include": True}}

    @staticmethod
    def _first_message(data: Dict[str, Any]) -> Dict[str, Any]:
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("Respuesta sin choices")
        return choices[0].get("message", {})

    @staticmethod
    def _meta(model: str, usage: Dict[str, Any], started: float, attempts: int, error: Optional[str] = None) -> LLMMeta:
        cost = usage.get("cost")
        return LLMMeta(
            model=model,
            tokens_in=usage.get("prompt_tokens", 0) or 0,
            tokens_out=usage.get("completion_tokens", 0) or 0,
            cost_usd=float(cost) if cost is not None else None,
            latency_ms=int((time.perf_counter() - started) * 1000),
            attempts=attempts,
            ok=error is None,
            error=error,
        )

    @staticmethod
    def _notify(meta: LLMMeta, context: Optional[LLMCallContext]) -> None:
        for listener in list(_audit_listeners):
            try:
                listener(meta, context or LLMCallContext())
            except Exception:
                logger.exception("Listener de auditoría LLM falló")

    def _send(self, payload: Dict[str, Any], sent: Dict[str, int], path: str = "/chat/completions") -> Dict[str, Any]:
        """POST con reintentos (429, 5xx, timeouts y errores de conexión). `sent["n"]` cuenta las peticiones."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": self.site_url,
            "X-OpenRouter-Title": self.site_name,
            "Content-Type": "application/json",
        }
        while True:
            sent["n"] += 1
            try:
                with httpx.Client(timeout=settings.llm_timeout_s, transport=self._transport) as http:
                    res = http.post(f"{self.base_url}{path}", headers=headers, json=payload)
                error = self._http_error(res)
                if error is None:
                    return res.json()
            except httpx.TransportError as e:
                error = LLMTransportError(str(e) or type(e).__name__)
            except ValueError as e:  # cuerpo 200 que no es JSON
                raise LLMError("Respuesta de OpenRouter no es JSON") from e
            retryable = isinstance(error, LLMTransportError) or error.status == 429 or error.status >= 500
            if not retryable or sent["n"] > settings.llm_max_retries:
                logger.error(f"Error OpenRouter ({error}) tras {sent['n']} intento(s)")
                raise error
            base = settings.llm_backoff_base_s
            delay = getattr(error, "retry_after", None)
            self._sleep(delay if delay is not None else base * 2 ** (sent["n"] - 1) + random.uniform(0, base))

    @staticmethod
    def _http_error(res: httpx.Response) -> Optional[LLMHTTPError]:
        """Convierte respuestas no 200, y 200 con cuerpo {"error": ...}, en LLMHTTPError."""
        if res.status_code != 200:
            return LLMHTTPError(res.status_code, res.text, parse_retry_after(res.headers.get("Retry-After")))
        try:
            body = res.json()
        except ValueError:
            return None
        if isinstance(body, dict) and body.get("error") and not body.get("choices"):
            code = body["error"].get("code") if isinstance(body["error"], dict) else None
            return LLMHTTPError(code if isinstance(code, int) else 502, res.text)
        return None


openrouter_client = OpenRouterClient()
