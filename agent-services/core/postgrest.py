"""Acceso mínimo a PostgREST (Supabase) con la llave de servicio, compartido por el almacén vectorial y las fuentes RAG."""
import logging
from typing import Any, Dict, List, Optional

import httpx

from config import settings
from core.submission_store import StoreError, StoreNotConfigured

logger = logging.getLogger(__name__)


class Postgrest:
    def __init__(self, url: str, service_key: str, client: Optional[httpx.Client] = None, timeout_s: float = 30.0):
        self.url = url.rstrip("/")
        self._client = client
        self._timeout_s = timeout_s
        self._headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}

    @classmethod
    def from_settings(cls) -> "Postgrest":
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise StoreNotConfigured("Supabase no está configurado.")
        return cls(settings.supabase_url, settings.supabase_service_role_key)

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self.url}/rest/v1{path}"
        try:
            if self._client is not None:
                return self._client.request(method, url, headers=self._headers, **kwargs)
            with httpx.Client(timeout=self._timeout_s) as client:
                return client.request(method, url, headers=self._headers, **kwargs)
        except httpx.HTTPError as exc:
            logger.error("Supabase inaccesible (%s)", type(exc).__name__)
            raise StoreError("Supabase inaccesible") from None

    def select(self, table: str, params: Dict[str, str]) -> List[Dict[str, Any]]:
        res = self.request("GET", f"/{table}", params=params)
        if res.status_code >= 400:
            logger.error("Consulta a %s falló (%s)", table, res.status_code)
            raise StoreError(f"Consulta a {table} falló")
        return self._json(res, list)

    def rpc(self, name: str, body: Dict[str, Any]) -> Any:
        res = self.request("POST", f"/rpc/{name}", json=body)
        if res.status_code >= 400:
            logger.error("RPC %s falló (%s, sqlstate=%s)", name, res.status_code, error_code(res))
            raise StoreError(f"RPC {name} falló")
        return self._json(res, (list, dict, int))

    @staticmethod
    def _json(res: httpx.Response, expected: Any) -> Any:
        try:
            data = res.json()
        except ValueError:
            raise StoreError("Respuesta inesperada de Supabase") from None
        if not isinstance(data, expected) or isinstance(data, bool):
            raise StoreError("Respuesta inesperada de Supabase")
        return data


def error_code(res: httpx.Response) -> str:
    try:
        return str(res.json().get("code", ""))
    except (ValueError, AttributeError):
        return ""
