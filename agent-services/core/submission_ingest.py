"""Orquestación de la ingesta de una entrega en PDF (IA-05). Sin dependencias de FastAPI."""
import logging
from dataclasses import dataclass

from config import settings
from core.submission_store import (
    InvalidUploader,
    StoreError,
    SubmissionNotFound,
    SubmissionStore,
    VersionRecord,
)
from rag.pdf_processor import PdfError, chunk_with_offsets, extract_pdf_text

logger = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"


class IngestError(Exception):
    """Error de ingesta con estado HTTP y mensaje seguro para el cliente."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass(frozen=True)
class IngestResult:
    version: VersionRecord
    pages: int
    characters: int


def ingest_submission_pdf(
    store: SubmissionStore, submission_id: str, uploaded_by: str, content_type: str, data: bytes
) -> IngestResult:
    if (content_type or "").split(";")[0].strip().lower() != "application/pdf":
        raise IngestError(415, "TIPO_NO_PERMITIDO", "Solo se aceptan archivos PDF.")
    if len(data) > settings.max_pdf_bytes:
        raise IngestError(413, "ARCHIVO_DEMASIADO_GRANDE", "El archivo supera el límite de 20 MB.")
    if not data.startswith(PDF_MAGIC):
        raise IngestError(415, "TIPO_NO_PERMITIDO", "Solo se aceptan archivos PDF.")

    try:
        extracted = extract_pdf_text(data)
    except PdfError as exc:
        status = 413 if exc.code == "PDF_DEMASIADO_LARGO" else 422
        raise IngestError(status, exc.code, exc.message) from None
    chunks = chunk_with_offsets(extracted.text)

    try:
        record = store.create_version(submission_id, uploaded_by, extracted.text, chunks)
    except SubmissionNotFound:
        raise IngestError(404, "ENTREGA_NO_ENCONTRADA", "La entrega no existe.") from None
    except InvalidUploader:
        raise IngestError(422, "USUARIO_INVALIDO", "El usuario que sube el archivo no existe.") from None
    except StoreError:
        raise _unavailable() from None

    try:
        store.upload_pdf(record.storage_path, data)
    except StoreError:
        # Compensación: sin archivo no debe quedar una versión que apunte a una ruta vacía.
        try:
            store.delete_version(record.id)
        except StoreError:
            logger.error("Versión %s quedó sin archivo y sin compensar", record.id)
        raise _unavailable() from None
    return IngestResult(version=record, pages=extracted.pages, characters=len(extracted.text))


def _unavailable() -> IngestError:
    return IngestError(502, "ALMACENAMIENTO_NO_DISPONIBLE", "No se pudo guardar la entrega en este momento.")
