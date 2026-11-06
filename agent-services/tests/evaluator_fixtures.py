"""Dobles en memoria para los tests del evaluador (IA-08): almacén, LLM falso y retriever. Nada sale a la red."""
import json
import re
from typing import Any, Callable, Dict, List, Optional

import httpx

from core.openrouter_client import OpenRouterClient
from core.review_store import ReviewNotFound, StoredVersion, VersionContext
from memory.vector_store import ChunkHit
from rag.retriever import CriterionContext
from tests.rag_fixtures import MAIL_A, PEOPLE, RUT_A, RUT_UNKNOWN

# UUID con prefijo de letras: el escáner de PII confunde números largos con RUT.
SECTION = "a1080000-0000-4000-a000-000000000001"
SUBMISSION = "e1080000-0000-4000-a000-000000000001"
ACTIVITY = "d1080000-0000-4000-a000-000000000001"
V1 = "f1080000-0000-4000-a000-000000000001"
V2 = "f1080000-0000-4000-a000-000000000002"
TEACHER = "c1080000-0000-4000-a000-000000000001"
RUT_OTHER = "8.765" + ".432-1"  # desconocido, distinto de RUT_UNKNOWN

RUBRIC = [
    {"criterio": "Fundamentación", "puntaje_max": 4.0, "descriptores": ["Justifica decisiones de arquitectura"]},
    {"criterio": "Escalabilidad", "puntaje_max": 6.0, "descriptores": ["Explica cómo crece el sistema"]},
]

TEXT_V1 = f"Informe preliminar de Juan Andrés Pérez Soto. Propone un monolito sin justificar la escalabilidad. Revisó {RUT_OTHER}."
TEXT_V2 = (
    f"Informe de arquitectura de Juan Andrés Pérez Soto (RUT {RUT_A}, correo {MAIL_A}).\n"
    "La solución usa microservicios con un balanceador de carga.\n\n"
    "Se justifica el RTO de cuatro horas con   replicación entre zonas.\n"
    f"Trabajo revisado por María José Núñez Rojas y por {RUT_UNKNOWN}."
)


class FakeReviewStore:
    def __init__(self, texto: str = TEXT_V2, previous: Optional[str] = TEXT_V1, rubric: Any = None,
                 atrasada: bool = False, max_decimas: Optional[float] = 0.5) -> None:
        self.context = VersionContext(V2, SUBMISSION, 2, texto, atrasada, ACTIVITY, "Taller ficticio de arquitectura",
                                      SECTION, RUBRIC if rubric is None else rubric, max_decimas)
        self.previous = StoredVersion(V1, previous) if previous is not None else None
        self.created: List[Dict[str, Any]] = []
        self.calls: List[str] = []

    def version_context(self, version_id: str) -> Optional[VersionContext]:
        self.calls.append("version_context")
        return self.context if version_id == V2 else None

    def previous_version(self, submission_id: str, version_n: int) -> Optional[StoredVersion]:
        return self.previous

    def roster_for_section(self, section_id: str) -> List[Dict[str, Any]]:
        return list(PEOPLE)

    def create_review(self, version_id, agente, modelo, nota, decimas, resumen, observaciones) -> Dict[str, Any]:
        """Replica el contrato de create_ai_review: cada cita debe ser literal en el texto de su versión."""
        texts = {V2: self.context.texto, **({V1: self.previous.texto} if self.previous else {})}
        for obs in observaciones:
            source = texts[obs["cita_version_id"]]
            assert source[obs["char_start"]:obs["char_end"]] == obs["cita_texto"], "cita no literal"
        self.created.append({"version_id": version_id, "agente": agente, "modelo": modelo, "nota_preliminar": nota,
                             "decimas_preliminares": decimas, "resumen": resumen, "observaciones": observaciones})
        return {"review_id": "a1080000-0000-4000-b000-000000000001",
                "observation_ids": [f"a1080000-0000-4000-c000-00000000000{i}" for i in range(len(observaciones))]}

    def get_review(self, review_id):  # pragma: no cover - no se usa en el evaluador
        raise ReviewNotFound("no")


def _user_text(request: httpx.Request) -> str:
    return json.loads(request.content)["messages"][1]["content"]


class FakeLLM:
    """LLM con MockTransport: `respond(user_text)` arma la respuesta a partir de lo que realmente se envió."""

    def __init__(self, respond: Callable[[str], Dict[str, Any]], model: str = "qwen/qwen3.8-27b",
                 usage: Optional[Dict[str, Any]] = None) -> None:
        self.respond, self.model = respond, model
        self.usage = usage or {"prompt_tokens": 900, "completion_tokens": 120, "cost": 0.0003}
        self.requests: List[Dict[str, Any]] = []
        self.client = OpenRouterClient(transport=httpx.MockTransport(self._handle), api_key="sk-or-test-key")
        self.client._sleep = lambda _s: None

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        content = json.dumps(self.respond(_user_text(request)), ensure_ascii=False)
        return httpx.Response(200, json={"model": self.model, "usage": self.usage,
                                         "choices": [{"message": {"content": content}}]})

    @property
    def sent_text(self) -> str:
        return "\n".join(m["content"] for r in self.requests for m in r["messages"])


def token_after(text: str, prefix: str) -> str:
    """Primer token de seudonimización que sigue a `prefix` en el texto enviado (p. ej. 'arquitectura de ')."""
    match = re.search(re.escape(prefix) + r"(\[[A-Z]+_\d+\])", text)
    assert match, f"no se encontró token tras {prefix!r}"
    return match.group(1)


def answer(observaciones: List[Dict[str, Any]], resumen: str = "Buen trabajo.", decimas: float = 0.3,
           criterios: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {
        "resumen": resumen,
        "criterios": criterios if criterios is not None else [
            {"criterio": "Fundamentación", "puntaje_obtenido": 3.0, "comentario": "Justifica el RTO."},
            {"criterio": "Escalabilidad", "puntaje_obtenido": 4.5, "comentario": "Explica el balanceador."},
        ],
        "decimas_preliminares": decimas,
        "observaciones": observaciones,
        "sugerencias_mejora": ["Cuantificar la disponibilidad esperada."],
    }


def obs(texto_cita: str, version: str = "actual", criterio: Optional[str] = "Escalabilidad", texto: str = "Observación.") -> Dict[str, Any]:
    return {"criterio": criterio, "tipo": "criterio", "severidad": "media", "texto": texto,
            "cita": {"version": version, "texto": texto_cita}}


class StubRetriever:
    """retrieve_for_criterion con hits fijos (offsets sobre el texto original), o error si `fail`."""

    def __init__(self, current: List[tuple], previous: List[tuple] = (), fail: Optional[Exception] = None) -> None:
        self.current, self.previous, self.fail = current, list(previous), fail
        self.calls = 0

    def retrieve_for_criterion(self, criterion: str, version_id: str, k: int = 3) -> CriterionContext:
        self.calls += 1
        if self.fail:
            raise self.fail

        def hits(owner, ranges):
            return [ChunkHit("x", "version", owner, i, "", s, e, 0.9) for i, (s, e) in enumerate(ranges)]
        return CriterionContext(hits(V2, self.current), hits(V1, self.previous), V1 if self.previous else None)
