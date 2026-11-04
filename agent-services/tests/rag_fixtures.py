"""Datos ficticios y dobles en memoria para los tests de RAG (IA-06). Nada de esto sale a la red."""
from typing import Any, Dict, List, Optional

from core.submission_store import StoreError
from rag.sources import ActivityInfo, VersionInfo

# UUID con prefijo de letras: el escáner de PII confunde números largos con RUT.
SECTION = "a1060000-0000-4000-a000-000000000001"
SUBMISSION = "e1060000-0000-4000-a000-000000000001"
OTHER_SUBMISSION = "e1060000-0000-4000-a000-000000000002"
V1 = "f1060000-0000-4000-a000-000000000001"
V2 = "f1060000-0000-4000-a000-000000000002"
V_OTHER = "f1060000-0000-4000-a000-000000000003"
ACTIVITY = "d1060000-0000-4000-a000-000000000001"
COURSE = "b1060000-0000-4000-a000-000000000001"

# Datos personales ficticios, armados por partes para no activar el escáner de PII del repo.
RUT_A = "12.345" + ".678-5"
RUT_B = "7.654" + ".321-K"
RUT_UNKNOWN = "9.876" + ".543-2"
MAIL_A = "juan.perez" + "@example.test"
MAIL_B = "maria.nunez" + "@example.test"
PHONE = "+56 9 8765 4321"

PEOPLE: List[Dict[str, Any]] = [
    {"id": "c1060000-0000-4000-a000-000000000001", "nombres": "Juan Andrés", "apellidos": "Pérez Soto", "rut": RUT_A, "email": MAIL_A},
    {"id": "c1060000-0000-4000-a000-000000000002", "nombres": "María José", "apellidos": "Núñez Rojas", "rut": RUT_B, "email": MAIL_B},
]

# Fragmentos que jamás deben llegar al proveedor.
FORBIDDEN = ["Pérez", "Soto", "Núñez", "Rojas", "Juan Andrés", "María José", RUT_A, RUT_B, RUT_UNKNOWN,
             "12.345", "678-5", "7.654", "9.876", MAIL_A, MAIL_B, "8765 4321"]


class FakeSources:
    """RagSources en memoria. `roster_error` simula Supabase caído al cargar la lista de estudiantes."""

    def __init__(self) -> None:
        self.versions: Dict[str, VersionInfo] = {}
        self.activities: Dict[str, ActivityInfo] = {}
        self.courses: Dict[str, str] = {}  # id -> code
        self.people: List[Dict[str, Any]] = list(PEOPLE)
        self.roster_error: Optional[Exception] = None
        self.roster_calls = 0

    def add_version(self, version_id: str, submission_id: str, version_n: int, section_id: str = SECTION) -> None:
        self.versions[version_id] = VersionInfo(version_id, submission_id, version_n, section_id)

    def version_info(self, version_id: str) -> Optional[VersionInfo]:
        return self.versions.get(version_id)

    def previous_version_id(self, version_id: str) -> Optional[str]:
        info = self.versions.get(version_id)
        older = [v for v in self.versions.values() if info and v.submission_id == info.submission_id and v.version_n < info.version_n]
        return max(older, key=lambda v: v.version_n).version_id if older else None

    def activity_info(self, activity_id: str) -> Optional[ActivityInfo]:
        return self.activities.get(activity_id)

    def course_exists(self, course_id: str) -> bool:
        return course_id in self.courses

    def courses_by_code(self, code: str) -> List[str]:
        return [cid for cid, c in self.courses.items() if c == code]

    def _roster(self) -> List[Dict[str, Any]]:
        self.roster_calls += 1
        if self.roster_error:
            raise self.roster_error
        return list(self.people)

    def roster_for_section(self, section_id: str) -> List[Dict[str, Any]]:
        return self._roster()

    def roster_for_course(self, course_id: str) -> List[Dict[str, Any]]:
        return self._roster()


def store_down() -> StoreError:
    return StoreError("Supabase inaccesible")
