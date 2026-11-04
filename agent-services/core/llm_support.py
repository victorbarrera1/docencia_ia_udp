"""Piezas puras del cliente LLM: tipos, excepciones, schema estricto, extracción de JSON y modo fake."""
import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ValidationError


@dataclass(frozen=True)
class LLMCallContext:
    """Contexto de auditoría de una llamada. No incluye texto de prompts ni de respuestas."""
    agente: Optional[str] = None
    section_id: Optional[str] = None
    version_id: Optional[str] = None
    actor: Optional[str] = None  # users.id de quien dispara la llamada (lo aporta el puente de IA-09)


@dataclass(frozen=True)
class LLMMeta:
    """Metadatos de una llamada (alimenta ai_audit_log). Nunca contiene el texto enviado."""
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: Optional[float]
    latency_ms: int
    attempts: int  # peticiones HTTP realizadas, incluyendo reintentos
    ok: bool
    error: Optional[str] = None


class LLMError(Exception):
    """Base de los errores del cliente. `meta` queda disponible tras la auditoría."""
    meta: Optional[LLMMeta] = None


class LLMNotConfigured(LLMError):
    pass


class LLMTransportError(LLMError):
    pass


class LLMHTTPError(LLMError):
    def __init__(self, status: int, body: str = "", retry_after: Optional[float] = None):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body
        self.retry_after = retry_after


class LLMValidationError(LLMError):
    pass


def parse_retry_after(value: Optional[str], cap: float = 10.0) -> Optional[float]:
    """Retry-After en segundos (la forma fecha HTTP se ignora). Acotado para no colgar la petición."""
    try:
        return min(max(float(value), 0.0), cap) if value else None
    except ValueError:
        return None


def harden_schema(node: Any) -> Any:
    """Prepara un JSON Schema de Pydantic para `strict`: additionalProperties=false y todo requerido."""
    if isinstance(node, list):
        return [harden_schema(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {k: harden_schema(v) for k, v in node.items()}
    if out.get("type") == "object" or "properties" in out:
        out["additionalProperties"] = False
        out["required"] = list(out.get("properties", {}))
    return out


def extract_json(text: str) -> Any:
    """Parsea JSON de una respuesta, tolerando fences ```json y texto alrededor del objeto."""
    text = (text or "").strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start:end + 1])


def validation_summary(exc: Exception) -> str:
    """Resumen sin valores de entrada (solo rutas y mensajes), seguro para logs y para el reintento."""
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or '(raíz)'}: {e['msg']}" for e in exc.errors())
    if isinstance(exc, json.JSONDecodeError):
        return f"JSON inválido: {exc.msg} (posición {exc.pos})"
    return type(exc).__name__


def _fake_value(node: Dict[str, Any], defs: Dict[str, Any], depth: int = 0) -> Any:
    if depth > 8:
        return None
    if "$ref" in node:
        node = defs[node["$ref"].split("/")[-1]]
    if "default" in node:
        return node["default"]
    if "const" in node:
        return node["const"]
    if "enum" in node:
        return node["enum"][0]
    for key in ("anyOf", "oneOf", "allOf"):
        if key in node:
            options = [o for o in node[key] if o.get("type") != "null"] or node[key]
            return _fake_value(options[0], defs, depth + 1)
    kind = node.get("type")
    if kind == "object":
        return {k: _fake_value(v, defs, depth + 1) for k, v in node.get("properties", {}).items()}
    if kind == "array":
        item = _fake_value(node.get("items", {}), defs, depth + 1)
        return [item] * max(node.get("minItems", 1), 1)
    if kind == "string":
        return "texto-fake".ljust(node.get("minLength", 0), "x")
    if kind in ("integer", "number"):
        low = node.get("minimum", node.get("exclusiveMinimum", 0))
        value = min(low, node["maximum"]) if "maximum" in node else low
        return int(value) if kind == "integer" else float(value)
    if kind == "boolean":
        return False
    return None


def fake_instance(schema: type[BaseModel]) -> BaseModel:
    """Instancia determinista que cumple el schema (modo LLM_FAKE)."""
    json_schema = schema.model_json_schema()
    data = _fake_value(json_schema, json_schema.get("$defs", {}))
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise LLMValidationError(f"El modo fake no pudo satisfacer el schema: {validation_summary(exc)}") from exc


_WORD_RE = re.compile(r"\w+", re.UNICODE)


def fake_embedding(text: str, dim: int) -> List[float]:
    """
    Embedding determinista para LLM_FAKE y tests: bolsa de palabras con hashing, normalizada a norma 1.
    Textos que comparten palabras quedan cerca (similitud coseno alta), así que el ranking es verificable.
    """
    vector = [0.0] * dim
    for word in _WORD_RE.findall(text.lower()):
        digest = hashlib.sha256(word.encode("utf-8")).digest()
        vector[int.from_bytes(digest[:4], "big") % dim] += 1.0 if digest[4] % 2 == 0 else -1.0
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        vector[0], norm = 1.0, 1.0
    return [v / norm for v in vector]


@dataclass(frozen=True)
class EmbeddingBatch:
    """Resultado de una llamada de embeddings. `model` es la etiqueta a guardar en rag_chunks.embedding_model."""
    vectors: List[List[float]]
    model: str
    meta: LLMMeta
