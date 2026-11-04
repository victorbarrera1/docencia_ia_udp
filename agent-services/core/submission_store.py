"""
Persistencia de versiones de entrega (IA-05): RPC transaccional en Supabase (PostgREST) y
Storage privado. Todo con la llave de servicio; nada de esto se expone directamente al cliente.
"""
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

from config import settings
from rag.pdf_processor import TextChunk

logger = logging.getLogger(__name__)


class StoreError(Exception):
    """Fallo de infraestructura; el detalle técnico se registra, nunca se propaga al cliente."""


class StoreNotConfigured(StoreError):
    pass


class SubmissionNotFound(StoreError):
    pass


class InvalidUploader(StoreError):
    pass


@dataclass(frozen=True)
class VersionRecord:
    id: str
    version_n: int
    storage_path: str
    atrasada: bool
    chunks: int


class SubmissionStore:
    def __init__(
        self,
        url: str,
        service_key: str,
        bucket: str,
        client: Optional[httpx.Client] = None,
        timeout_s: float = 30.0,
    ):
        self.url = url.rstrip("/")
        self.bucket = bucket
        self._client = client
        self._timeout_s = timeout_s
        self._headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}

    @classmethod
    def from_settings(cls) -> "SubmissionStore":
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise StoreNotConfigured("Supabase no está configurado.")
        return cls(settings.supabase_url, settings.supabase_service_role_key, settings.submission_bucket)

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {**self._headers, **kwargs.pop("headers", {})}
        url = f"{self.url}{path}"
        try:
            if self._client is not None:
                return self._client.request(method, url, headers=headers, **kwargs)
            with httpx.Client(timeout=self._timeout_s) as client:
                return client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            logger.error("Supabase inaccesible (%s)", type(exc).__name__)
            raise StoreError("Supabase inaccesible") from None

    def create_version(
        self, submission_id: str, uploaded_by: str, texto: str, chunks: List[TextChunk]
    ) -> VersionRecord:
        """Una sola llamada RPC = una transacción: reserva version_n, inserta versión y chunks."""
        body = {
            "p_submission_id": submission_id,
            "p_subido_por": uploaded_by,
            "p_texto": texto,
            "p_chunks": [
                {"idx": c.idx, "texto": c.texto, "char_start": c.char_start, "char_end": c.char_end}
                for c in chunks
            ],
        }
        res = self._send("POST", "/rest/v1/rpc/create_submission_version", json=body)
        if res.status_code >= 400:
            code = _error_code(res)
            logger.error("RPC create_submission_version falló (%s, sqlstate=%s)", res.status_code, code)
            if code == "P0002":
                raise SubmissionNotFound("Entrega inexistente")
            if code == "23503":
                raise InvalidUploader("Usuario inexistente")
            raise StoreError("RPC create_submission_version falló")
        try:
            data: Dict[str, Any] = res.json()
            return VersionRecord(
                id=str(data["id"]),
                version_n=int(data["version_n"]),
                storage_path=str(data["storage_path"]),
                atrasada=bool(data["atrasada"]),
                chunks=int(data["chunks"]),
            )
        except (ValueError, KeyError, TypeError):
            raise StoreError("Respuesta inesperada de la RPC") from None

    def upload_pdf(self, storage_path: str, data: bytes) -> None:
        res = self._send(
            "POST",
            f"/storage/v1/object/{quote(self.bucket)}/{quote(storage_path, safe='/')}",
            content=data,
            headers={"Content-Type": "application/pdf", "x-upsert": "false"},
        )
        if res.status_code >= 400:
            logger.error("Subida a Storage falló (%s)", res.status_code)
            raise StoreError("Subida a Storage falló")

    def delete_version(self, version_id: str) -> None:
        """Compensación cuando la subida falla: borra la versión (los chunks caen en cascada)."""
        res = self._send("DELETE", "/rest/v1/submission_versions", params={"id": f"eq.{version_id}"})
        if res.status_code >= 400:
            logger.error("No se pudo compensar la versión %s (%s)", version_id, res.status_code)
            raise StoreError("Compensación fallida")


def _error_code(res: httpx.Response) -> str:
    try:
        return str(res.json().get("code", ""))
    except (ValueError, AttributeError):
        return ""
