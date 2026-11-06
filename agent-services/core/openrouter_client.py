import json
import logging
import math
import random
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

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

    También habla con cualquier servidor compatible con la API de OpenAI (p. ej. vLLM en la UDP) si se le
    pasa `base_url`: en ese caso no envía los extras propios de OpenRouter y la llave es opcional.
    Usar `endpoint_client` para construirlo: nunca envía OPENROUTER_API_KEY a otro host.
    """
    def __init__(
        self,
        transport: Optional[httpx.BaseTransport] = None,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_s: Optional[float] = None,
    ):
        self.api_key = settings.openrouter_api_key if api_key is None else api_key
        self.base_url = (base_url or settings.openrouter_base_url).rstrip("/")
        self.site_url = settings.openrouter_site_url
        self.site_name = settings.openrouter_site_name
        self._transport = transport  # inyectable en tests (httpx.MockTransport)
        self._timeout_s = timeout_s
        self._sleep = time.sleep

    @property
    def is_openrouter(self) -> bool:
        return (urlparse(self.base_url).hostname or "").endswith("openrouter.ai")

    def is_configured(self) -> bool:
        if settings.llm_fake or bool(self.api_key and len(self.api_key) > 5):
            return True
        # Un servidor propio (vLLM) puede no exigir llave; OpenRouter siempre la exige.
        return not self.is_openrouter and bool(urlparse(self.base_url).hostname)

    def uses_json_schema(self, model: str) -> bool:
        """
        ¿Pedir response_format json_schema? LLM_JSON_SCHEMA_MODELS admite prefijos de modelo (`qwen/`) y
        prefijos de endpoint (`https://llm.udp.cl/v1`), separados por coma.
        """
        for entry in (e.strip() for e in settings.llm_json_schema_models.split(",")):
            if not entry:
                continue
            if entry.startswith(("http://", "https://")):
                if self.base_url.startswith(entry.rstrip("/")):
                    return True
            elif model.startswith(entry):
                return True
        return False

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
        structured = self.uses_json_schema(target)
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

    def _payload(self, messages: List[Dict[str, str]], model: str, temperature: float, max_tokens: int) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if self.is_openrouter:
            payload["usage"] = {"include": True}  # costo en la respuesta; un servidor propio no lo informa (NULL)
        return payload

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
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.is_openrouter:
            headers.update({"HTTP-Referer": self.site_url, "X-OpenRouter-Title": self.site_name})
        timeout = self._timeout_s or settings.llm_timeout_s
        while True:
            sent["n"] += 1
            try:
                with httpx.Client(timeout=timeout, transport=self._transport) as http:
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


def endpoint_client(base_url: str = "", api_key: str = "", timeout_s: Optional[float] = None) -> OpenRouterClient:
    """
    Cliente para un agente con endpoint configurable (p. ej. EVALUATOR_BASE_URL → vLLM de la UDP).

    Sin `base_url` propia (o igual a OPENROUTER_BASE_URL) usa OpenRouter con `api_key` o, si viene vacía,
    OPENROUTER_API_KEY. Con `base_url` propia usa solo `api_key` (puede ir vacía): la llave de OpenRouter
    nunca viaja a otro host. Mismos reintentos, auditoría y modo fake que el cliente por defecto.
    """
    custom = base_url.strip().rstrip("/")
    if not custom or custom == settings.openrouter_base_url.rstrip("/"):
        return OpenRouterClient(api_key=api_key or None, timeout_s=timeout_s)
    return OpenRouterClient(base_url=custom, api_key=api_key, timeout_s=timeout_s)
