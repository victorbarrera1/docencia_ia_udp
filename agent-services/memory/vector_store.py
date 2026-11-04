"""
Almacén vectorial sobre `rag_chunks` y `match_rag_chunks` (IA-04), con la llave de servicio (IA-06).

`SupabaseVectorStore` es el adaptador real (PostgREST + RPC). `InMemoryVectorStore` replica su contrato
solo para tests. El servicio ignora RLS: quien llama debe autorizar y acotar los propietarios antes de buscar.
"""
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

from core.postgrest import Postgrest
from core.submission_store import StoreError
from rag.pdf_processor import TextChunk

CHUNK_COLUMNS = "id,owner_type,owner_id,idx,texto,char_start,char_end"


@dataclass(frozen=True)
class ChunkRow:
    id: str
    owner_type: str
    owner_id: str
    idx: int
    texto: str
    char_start: int
    char_end: int


@dataclass(frozen=True)
class ChunkHit(ChunkRow):
    similarity: float = 0.0


class VectorStore(Protocol):
    def chunks_to_embed(
        self, model: str, *, force: bool = False, owner_type: Optional[str] = None,
        owner_id: Optional[str] = None, limit: int = 500,
    ) -> List[ChunkRow]: ...

    def count_foreign(self, model: str, owner_type: Optional[str] = None, owner_id: Optional[str] = None) -> int: ...

    def set_embeddings(self, model: str, items: Sequence[Tuple[ChunkRow, List[float]]]) -> int: ...

    def replace_chunks(self, owner_type: str, owner_id: str, chunks: Sequence[TextChunk]) -> Dict[str, int]: ...

    def match(self, query_embedding: List[float], owner_ids: Sequence[str], k: int) -> List[ChunkHit]: ...


def _quote(value: str) -> str:
    """Valor entre comillas para un filtro PostgREST (protege comas y paréntesis)."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"


def _row(data: Dict[str, Any]) -> ChunkRow:
    return ChunkRow(str(data["id"]), str(data["owner_type"]), str(data["owner_id"]), int(data["idx"]),
                    str(data["texto"]), int(data["char_start"]), int(data["char_end"]))


class SupabaseVectorStore:
    def __init__(self, db: Optional[Postgrest] = None):
        self._db = db or Postgrest.from_settings()

    @staticmethod
    def _owner_filters(owner_type: Optional[str], owner_id: Optional[str]) -> Dict[str, str]:
        filters = {}
        if owner_type:
            filters["owner_type"] = f"eq.{owner_type}"
        if owner_id:
            filters["owner_id"] = f"eq.{owner_id}"
        return filters

    def chunks_to_embed(
        self, model: str, *, force: bool = False, owner_type: Optional[str] = None,
        owner_id: Optional[str] = None, limit: int = 500,
    ) -> List[ChunkRow]:
        """Chunks sin embedding; con `force`, también los embebidos con otro modelo (o de origen desconocido)."""
        params = {"select": CHUNK_COLUMNS, "order": "owner_type,owner_id,idx", "limit": str(limit),
                  **self._owner_filters(owner_type, owner_id)}
        if force:
            params["or"] = f"(embedding.is.null,embedding_model.is.null,embedding_model.neq.{_quote(model)})"
        else:
            params["embedding"] = "is.null"
        return [_row(r) for r in self._db.select("rag_chunks", params)]

    def count_foreign(self, model: str, owner_type: Optional[str] = None, owner_id: Optional[str] = None) -> int:
        """Chunks ya embebidos con un modelo distinto del actual: mezclarían espacios vectoriales."""
        params = {"select": "id", "embedding": "not.is.null",
                  "or": f"(embedding_model.is.null,embedding_model.neq.{_quote(model)})",
                  **self._owner_filters(owner_type, owner_id)}
        return len(self._db.select("rag_chunks", params))

    def set_embeddings(self, model: str, items: Sequence[Tuple[ChunkRow, List[float]]]) -> int:
        if not items:
            return 0
        body = {"p_model": model, "p_items": [
            {"id": row.id, "texto": row.texto, "embedding": _vector_literal(vector)} for row, vector in items]}
        return int(self._db.rpc("set_rag_chunk_embeddings", body))

    def replace_chunks(self, owner_type: str, owner_id: str, chunks: Sequence[TextChunk]) -> Dict[str, int]:
        body = {"p_owner_type": owner_type, "p_owner_id": owner_id, "p_chunks": [
            {"idx": c.idx, "texto": c.texto, "char_start": c.char_start, "char_end": c.char_end} for c in chunks]}
        result = self._db.rpc("replace_rag_chunks", body)
        if not isinstance(result, dict):
            raise StoreError("Respuesta inesperada de la RPC")
        return {k: int(v) for k, v in result.items()}

    def match(self, query_embedding: List[float], owner_ids: Sequence[str], k: int) -> List[ChunkHit]:
        if not owner_ids:
            return []
        body = {"query_embedding": _vector_literal(query_embedding), "owner_ids": list(owner_ids), "k": k}
        hits = self._db.rpc("match_rag_chunks", body)
        if not isinstance(hits, list):
            raise StoreError("Respuesta inesperada de la RPC")
        return [ChunkHit(**_row(h).__dict__, similarity=float(h["similarity"])) for h in hits]


class InMemoryVectorStore:
    """Mismo contrato que SupabaseVectorStore, solo para tests: sin red y con coseno exacto."""

    def __init__(self) -> None:
        self._rows: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
        self._ids = 0

    def _put(self, owner_type: str, owner_id: str, chunk: TextChunk) -> None:
        self._ids += 1
        self._rows[(owner_type, owner_id, chunk.idx)] = {
            "id": f"mem-{self._ids}", "texto": chunk.texto, "char_start": chunk.char_start,
            "char_end": chunk.char_end, "embedding": None, "model": None}

    def add_chunks(self, owner_type: str, owner_id: str, chunks: Sequence[TextChunk]) -> None:
        """Simula la creación de chunks sin embedding (create_submission_version)."""
        for chunk in chunks:
            self._put(owner_type, owner_id, chunk)

    def _as_row(self, key: Tuple[str, str, int]) -> ChunkRow:
        r = self._rows[key]
        return ChunkRow(r["id"], key[0], key[1], key[2], r["texto"], r["char_start"], r["char_end"])

    def _foreign(self, r: Dict[str, Any], model: str) -> bool:
        return r["embedding"] is not None and r["model"] != model

    def chunks_to_embed(
        self, model: str, *, force: bool = False, owner_type: Optional[str] = None,
        owner_id: Optional[str] = None, limit: int = 500,
    ) -> List[ChunkRow]:
        keys = sorted(k for k, r in self._rows.items()
                      if (owner_type in (None, k[0])) and (owner_id in (None, k[1]))
                      and (r["embedding"] is None or (force and self._foreign(r, model))))
        return [self._as_row(k) for k in keys[:limit]]

    def count_foreign(self, model: str, owner_type: Optional[str] = None, owner_id: Optional[str] = None) -> int:
        return sum(1 for k, r in self._rows.items()
                   if (owner_type in (None, k[0])) and (owner_id in (None, k[1])) and self._foreign(r, model))

    def set_embeddings(self, model: str, items: Sequence[Tuple[ChunkRow, List[float]]]) -> int:
        updated = 0
        for row, vector in items:
            current = self._rows.get((row.owner_type, row.owner_id, row.idx))
            if current and current["id"] == row.id and current["texto"] == row.texto:
                current["embedding"], current["model"] = list(vector), model
                updated += 1
        return updated

    def replace_chunks(self, owner_type: str, owner_id: str, chunks: Sequence[TextChunk]) -> Dict[str, int]:
        if owner_type not in ("rubric", "course_material"):
            raise StoreError("Solo se reemplazan chunks de rúbrica o material de curso")
        incoming = {c.idx: c for c in chunks}
        existing = {k[2]: k for k in self._rows if k[0] == owner_type and k[1] == owner_id}
        result = {"insertados": 0, "actualizados": 0, "sin_cambios": 0, "borrados": 0}
        for idx, key in existing.items():
            if idx not in incoming:
                del self._rows[key]
                result["borrados"] += 1
        for idx, chunk in incoming.items():
            key = (owner_type, owner_id, idx)
            current = self._rows.get(key)
            if current is None:
                self._put(owner_type, owner_id, chunk)
                result["insertados"] += 1
            elif (current["texto"], current["char_start"], current["char_end"]) != (chunk.texto, chunk.char_start, chunk.char_end):
                self._put(owner_type, owner_id, chunk)
                result["actualizados"] += 1
            else:
                result["sin_cambios"] += 1
        return result

    def match(self, query_embedding: List[float], owner_ids: Sequence[str], k: int) -> List[ChunkHit]:
        qn = math.sqrt(sum(v * v for v in query_embedding)) or 1.0
        scored = []
        for key, r in self._rows.items():
            if key[1] in owner_ids and r["embedding"] is not None:
                vn = math.sqrt(sum(v * v for v in r["embedding"])) or 1.0
                sim = sum(a * b for a, b in zip(query_embedding, r["embedding"])) / (qn * vn)
                scored.append((sim, key))
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [ChunkHit(**self._as_row(key).__dict__, similarity=sim) for sim, key in scored[:k]]

    def stored(self, owner_type: str, owner_id: str) -> List[Dict[str, Any]]:
        """Filas de un propietario (para asserts de tests), ordenadas por idx."""
        return [{"idx": k[2], **r} for k, r in sorted(self._rows.items()) if k[0] == owner_type and k[1] == owner_id]
