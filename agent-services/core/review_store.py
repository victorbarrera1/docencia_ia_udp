"""
Persistencia de las revisiones de IA (IA-08) vía PostgREST con la llave de servicio.

La escritura pasa solo por las RPCs de `20261112_evaluador_real.sql`: `create_ai_review` (propuesta + observaciones
en una transacción, sin nota final), `decide_ai_observation` y `confirm_ai_review` (docente identificado; único
camino a `nota_final`). El servicio ignora RLS: la autorización de lectura por sección la hace el puente (IA-09).
"""
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

from core.models import ObservationCitation, ReviewObservation, ReviewResponse
from core.postgrest import Postgrest, error_code
from core.submission_store import StoreError
from rag.sources import STUDENT_COLUMNS, SupabaseRagSources

logger = logging.getLogger(__name__)


class ReviewNotFound(StoreError):
    pass


class ReviewForbidden(StoreError):
    """El actor no es docente activo de la sección."""


class ReviewPending(StoreError):
    """Quedan observaciones pendientes."""


class ReviewAlreadyConfirmed(StoreError):
    pass


class ReviewInvalid(StoreError):
    """Datos rechazados por la base (nota fuera de escala, estado inválido, cita no literal)."""


_SQLSTATE_ERRORS = {
    "P0002": ReviewNotFound, "23503": ReviewNotFound, "42501": ReviewForbidden, "IA001": ReviewPending,
    "IA002": ReviewAlreadyConfirmed, "22023": ReviewInvalid, "23514": ReviewInvalid, "22P02": ReviewInvalid,
}


@dataclass(frozen=True)
class VersionContext:
    version_id: str
    submission_id: str
    version_n: int
    texto: str
    atrasada: bool
    activity_id: str
    activity_title: str
    section_id: str
    rubric_json: Any
    max_decimas: Optional[float]
    owner: Optional[Dict[str, Any]] = None  # fila de students del dueño de la entrega


@dataclass(frozen=True)
class StoredVersion:
    version_id: str
    texto: str


class ReviewStore(Protocol):
    def version_context(self, version_id: str) -> Optional[VersionContext]: ...
    def previous_version(self, submission_id: str, version_n: int) -> Optional[StoredVersion]: ...
    def roster_for_section(self, section_id: str) -> List[Dict[str, Any]]: ...
    def create_review(self, version_id: str, agente: str, modelo: str, nota: float, decimas: float,
                      resumen: str, observaciones: List[Dict[str, Any]]) -> Dict[str, Any]: ...
    def get_review(self, review_id: str) -> Optional[Dict[str, Any]]: ...
    def decide_observation(self, observation_id: str, actor: str, estado: str, texto: Optional[str]) -> Dict[str, Any]: ...
    def confirm_review(self, review_id: str, actor: str, nota_final: Optional[float]) -> Dict[str, Any]: ...


class SupabaseReviewStore:
    def __init__(self, db: Optional[Postgrest] = None):
        self._db = db or Postgrest.from_settings()
        self._sources = SupabaseRagSources(self._db)

    def version_context(self, version_id: str) -> Optional[VersionContext]:
        rows = self._db.select("submission_versions", {
            "id": f"eq.{version_id}",
            "select": "id,submission_id,version_n,texto_extraido,atrasada,"
                      f"activity_submissions(students({STUDENT_COLUMNS}),"
                      "activities(id,title,section_id,rubric_json,max_decimas))"})
        if not rows:
            return None
        try:
            row = rows[0]
            submission = row["activity_submissions"]
            activity = submission["activities"]
            max_decimas = activity.get("max_decimas")
            return VersionContext(
                str(row["id"]), str(row["submission_id"]), int(row["version_n"]), str(row["texto_extraido"] or ""),
                bool(row["atrasada"]), str(activity["id"]), str(activity.get("title") or ""), str(activity["section_id"]),
                activity.get("rubric_json"), float(max_decimas) if max_decimas is not None else None,
                submission.get("students"))
        except (KeyError, TypeError, ValueError):
            raise StoreError("Respuesta inesperada de Supabase") from None

    def previous_version(self, submission_id: str, version_n: int) -> Optional[StoredVersion]:
        rows = self._db.select("submission_versions", {
            "submission_id": f"eq.{submission_id}", "version_n": f"lt.{version_n}",
            "order": "version_n.desc", "limit": "1", "select": "id,texto_extraido"})
        return StoredVersion(str(rows[0]["id"]), str(rows[0]["texto_extraido"] or "")) if rows else None

    def roster_for_section(self, section_id: str) -> List[Dict[str, Any]]:
        return self._sources.roster_for_section(section_id)

    def create_review(self, version_id: str, agente: str, modelo: str, nota: float, decimas: float,
                      resumen: str, observaciones: List[Dict[str, Any]]) -> Dict[str, Any]:
        return self._rpc("create_ai_review", {
            "p_version_id": version_id, "p_agente": agente, "p_modelo": modelo, "p_nota_preliminar": nota,
            "p_decimas_preliminares": decimas, "p_resumen": resumen, "p_observaciones": observaciones})

    def get_review(self, review_id: str) -> Optional[Dict[str, Any]]:
        rows = self._db.select("ai_reviews", {
            "id": f"eq.{review_id}",
            "select": "id,version_id,agente,modelo,estado,nota_preliminar,decimas_preliminares,resumen,nota_final,"
                      "confirmada_por,confirmada_en,ai_observations(id,criterio,tipo,severidad,texto,cita_texto,"
                      "cita_version_id,char_start,char_end,estado_docente)"})
        return rows[0] if rows else None

    def decide_observation(self, observation_id: str, actor: str, estado: str, texto: Optional[str]) -> Dict[str, Any]:
        return self._rpc("decide_ai_observation", {
            "p_observation_id": observation_id, "p_actor": actor, "p_estado": estado, "p_texto": texto})

    def confirm_review(self, review_id: str, actor: str, nota_final: Optional[float]) -> Dict[str, Any]:
        return self._rpc("confirm_ai_review", {"p_review_id": review_id, "p_actor": actor, "p_nota_final": nota_final})

    def _rpc(self, name: str, body: Dict[str, Any]) -> Dict[str, Any]:
        res = self._db.request("POST", f"/rpc/{name}", json=body)
        if res.status_code >= 400:
            code = error_code(res)
            logger.error("RPC %s falló (%s, sqlstate=%s)", name, res.status_code, code)
            raise _SQLSTATE_ERRORS.get(code, StoreError)(f"RPC {name} falló")
        try:
            data = res.json()
        except ValueError:
            raise StoreError("Respuesta inesperada de Supabase") from None
        if not isinstance(data, dict):
            raise StoreError("Respuesta inesperada de Supabase")
        return data


def to_review_response(row: Dict[str, Any]) -> ReviewResponse:
    """Fila de ai_reviews (con ai_observations embebidas) → ReviewResponse. Etiqueta cada cita actual/anterior."""
    def number(value: Any) -> Optional[float]:
        return float(value) if value is not None else None

    version_id = str(row["version_id"])
    observations = []
    for o in sorted(row.get("ai_observations") or [],  # versión actual primero, luego en orden del texto
                    key=lambda o: (str(o["cita_version_id"]) != version_id, int(o["char_start"]), str(o["id"]))):
        cited = str(o["cita_version_id"])
        observations.append(ReviewObservation(
            id=str(o["id"]), criterio=o.get("criterio"), tipo=o["tipo"], severidad=o["severidad"], texto=o["texto"],
            estado_docente=o["estado_docente"],
            cita=ObservationCitation(version="actual" if cited == version_id else "anterior", version_id=cited,
                                     texto=o["cita_texto"], char_start=int(o["char_start"]), char_end=int(o["char_end"]))))
    return ReviewResponse(
        id=str(row["id"]), version_id=version_id, agente=row["agente"], modelo=row["modelo"], estado=row["estado"],
        nota_preliminar=number(row.get("nota_preliminar")), decimas_preliminares=number(row.get("decimas_preliminares")),
        resumen=row.get("resumen") or "", nota_final=number(row.get("nota_final")),
        confirmada_por=str(row["confirmada_por"]) if row.get("confirmada_por") else None,
        confirmada_en=str(row["confirmada_en"]) if row.get("confirmada_en") else None,
        observaciones=observations, pendientes=sum(o.estado_docente == "pendiente" for o in observations))
