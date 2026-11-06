"""
Contrato de salida del LLM evaluador (IA-08) y cálculos deterministas (nota, décimas). Lógica pura.

El schema no usa restricciones numéricas ni rasgos propios de un proveedor: sirve igual para json_schema estricto
(OpenAI, vLLM con decodificación guiada) que para pedirlo en el prompt. Los rangos se aplican aquí, después.
La nota la calcula el sistema a partir de los puntajes por criterio, no el modelo.
"""
import math
import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel

from core.models import ObservationType, RubricItem, Severity

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "evaluador.md"
_VERSION_RE = re.compile(r"<!--\s*prompt:\s*([\w.-]+)\s*-->")
_COMMENT_RE = re.compile(r"<!--.*?-->\s*", re.DOTALL)


class LLMCita(BaseModel):
    version: Literal["actual", "anterior"]
    texto: str


class LLMObservacion(BaseModel):
    criterio: Optional[str]
    tipo: ObservationType
    severidad: Severity
    texto: str
    cita: LLMCita


class LLMCriterio(BaseModel):
    criterio: str
    puntaje_obtenido: float
    comentario: str


class EvaluadorLLMOutput(BaseModel):
    resumen: str
    criterios: List[LLMCriterio]
    decimas_preliminares: float
    observaciones: List[LLMObservacion]
    sugerencias_mejora: List[str]


def load_prompt(path: Path = PROMPT_PATH) -> tuple[str, str]:
    """(versión, texto) del prompt de sistema. Los comentarios HTML no se envían al modelo."""
    raw = path.read_text(encoding="utf-8")
    match = _VERSION_RE.search(raw)
    if not match:
        raise ValueError("El prompt del evaluador no declara su versión")
    return match.group(1), _COMMENT_RE.sub("", raw).strip()


def render_prompt(template: str, values: Dict[str, str]) -> str:
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def criterion_key(name: Optional[str]) -> str:
    """Clave para emparejar nombres de criterio: sin tildes, minúsculas y espacios simples."""
    folded = "".join(c for c in unicodedata.normalize("NFD", name or "") if not unicodedata.combining(c))
    return " ".join(folded.casefold().split())


def round_half_up(value: float, digits: int = 1) -> float:
    quantum = Decimal(1).scaleb(-digits)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def grade_from_ratio(ratio: float, exigencia: float) -> float:
    """Escala chilena 1,0–7,0: `exigencia` del puntaje equivale a 4,0; lineal a cada lado."""
    p = min(max(ratio, 0.0), 1.0)
    if p < exigencia:
        grade = 1.0 + 3.0 * p / exigencia
    else:
        grade = 4.0 + 3.0 * (p - exigencia) / (1.0 - exigencia)
    return min(max(round_half_up(grade), 1.0), 7.0)


def clamp_decimas(value: float, tope: float) -> float:
    """Décimas a un decimal dentro de [0, tope]; un valor no finito del modelo cuenta como 0."""
    bounded = min(max(float(value), 0.0), tope) if math.isfinite(value) else 0.0
    return min(round_half_up(bounded), tope)


def score_criteria(rubric: List[RubricItem], criterios: List[LLMCriterio]) -> List[tuple[RubricItem, float, str]]:
    """
    Puntaje acotado a [0, máx] para cada criterio de la rúbrica (en su orden). Un criterio que el modelo no evaluó
    queda en 0 con un aviso para el docente; los que el modelo inventó se ignoran.
    """
    given: Dict[str, LLMCriterio] = {}
    for item in criterios:
        given.setdefault(criterion_key(item.criterio), item)
    scored = []
    for item in rubric:
        answer = given.get(criterion_key(item.criterio))
        if answer is None:
            scored.append((item, 0.0, "El modelo no evaluó este criterio: revisar manualmente."))
            continue
        raw = float(answer.puntaje_obtenido)
        points = min(max(raw, 0.0), float(item.puntaje_max)) if math.isfinite(raw) else 0.0
        scored.append((item, round_half_up(points, 2), answer.comentario.strip()))
    return scored
