"""Lecturas de Supabase que necesita el RAG (versión, rúbrica, curso y listas de estudiantes para seudonimizar)."""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

from core.postgrest import Postgrest
from core.submission_store import StoreError

STUDENT_COLUMNS = "id,nombres,apellidos,rut,email"


@dataclass(frozen=True)
class VersionInfo:
    version_id: str
    submission_id: str
    version_n: int
    section_id: str


@dataclass(frozen=True)
class ActivityInfo:
    activity_id: str
    section_id: str
    rubric_json: Any


class RagSources(Protocol):
    def version_info(self, version_id: str) -> Optional[VersionInfo]: ...
    def previous_version_id(self, version_id: str) -> Optional[str]: ...
    def activity_info(self, activity_id: str) -> Optional[ActivityInfo]: ...
    def course_exists(self, course_id: str) -> bool: ...
    def courses_by_code(self, code: str) -> List[str]: ...
    def roster_for_section(self, section_id: str) -> List[Dict[str, Any]]: ...
    def roster_for_course(self, course_id: str) -> List[Dict[str, Any]]: ...


def _stable_people(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Estudiantes únicos ordenados por id: la posición fija el número de los tokens de seudonimización."""
    people = {str(r["students"]["id"]): r["students"] for r in rows if isinstance(r.get("students"), dict)}
    return [people[key] for key in sorted(people)]


class SupabaseRagSources:
    def __init__(self, db: Optional[Postgrest] = None):
        self._db = db or Postgrest.from_settings()

    def version_info(self, version_id: str) -> Optional[VersionInfo]:
        rows = self._db.select("submission_versions", {
            "id": f"eq.{version_id}",
            "select": "id,submission_id,version_n,activity_submissions(activities(section_id))"})
        if not rows:
            return None
        try:
            row = rows[0]
            section = row["activity_submissions"]["activities"]["section_id"]
            return VersionInfo(str(row["id"]), str(row["submission_id"]), int(row["version_n"]), str(section))
        except (KeyError, TypeError, ValueError):
            raise StoreError("Respuesta inesperada de Supabase") from None

    def previous_version_id(self, version_id: str) -> Optional[str]:
        info = self.version_info(version_id)
        if info is None:
            return None
        rows = self._db.select("submission_versions", {
            "submission_id": f"eq.{info.submission_id}", "version_n": f"lt.{info.version_n}",
            "order": "version_n.desc", "limit": "1", "select": "id"})
        return str(rows[0]["id"]) if rows else None

    def activity_info(self, activity_id: str) -> Optional[ActivityInfo]:
        rows = self._db.select("activities", {"id": f"eq.{activity_id}", "select": "id,section_id,rubric_json"})
        if not rows:
            return None
        return ActivityInfo(str(rows[0]["id"]), str(rows[0]["section_id"]), rows[0].get("rubric_json"))

    def course_exists(self, course_id: str) -> bool:
        return bool(self._db.select("courses", {"id": f"eq.{course_id}", "select": "id"}))

    def courses_by_code(self, code: str) -> List[str]:
        rows = self._db.select("courses", {"code": f"eq.{code}", "is_active": "eq.true", "select": "id"})
        return [str(r["id"]) for r in rows]

    def roster_for_section(self, section_id: str) -> List[Dict[str, Any]]:
        rows = self._db.select("enrollments", {
            "section_id": f"eq.{section_id}", "student_id": "not.is.null", "select": f"students({STUDENT_COLUMNS})"})
        return _stable_people(rows)

    def roster_for_course(self, course_id: str) -> List[Dict[str, Any]]:
        rows = self._db.select("enrollments", {
            "sections.course_id": f"eq.{course_id}", "student_id": "not.is.null",
            "select": f"students({STUDENT_COLUMNS}),sections!inner(course_id)"})
        return _stable_people(rows)
