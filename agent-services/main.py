import logging
import secrets
from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
import asyncio

from config import settings
from core.models import (
    AgentChatRequest,
    AgentChatResponse,
    SubmissionEvaluationRequest,
    SubmissionEvaluationResponse,
    ActivityDesignRequest,
    ActivityDesignResponse,
    SubmissionVersionResponse,
    AuditReportResponse,
)
from core.submission_ingest import IngestError, ingest_submission_pdf
from core.submission_store import StoreNotConfigured, SubmissionStore
from core.hierarchy import hierarchy_manager
from agents.root import root_agent
from agents.specialists.submission_evaluator import submission_evaluator_worker
from agents.specialists.activity_designer import activity_designer_worker
from protocols.agent_card import get_agent_card
from core.audit import AuditNotConfigured, setup_audit
from core.audit_report import AuditQueryError, fetch_audit_report

# IA-07: cada llamada a un LLM deja una fila de metadatos en ai_audit_log
setup_audit()

logger = logging.getLogger(__name__)

# En producción no se publica la documentación ni el esquema OpenAPI (revela la superficie de la API)
_is_production = settings.environment.strip().lower() == "production"

app = FastAPI(
    title=settings.app_name,
    description="Microservicio agéntico jerárquico y desacoplado para docencia universitaria UDP",
    version="1.0.0",
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
)

# Configuración CORS permisiva en dev para Next.js
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.allowed_origins.split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["POST"],
    allow_headers=["Content-Type", "X-Agent-Service-Key"],
)

# Margen para los campos y bordes del multipart sobre el límite del PDF.
_MULTIPART_OVERHEAD = 1024 * 1024

@app.middleware("http")
async def limit_submission_upload_size(request: Request, call_next):
    """Rechaza subidas enormes antes de que el parser multipart las escriba a disco."""
    if request.url.path.startswith("/api/v1/submissions/"):
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > settings.max_pdf_bytes + _MULTIPART_OVERHEAD:
            return JSONResponse(
                status_code=413,
                content={"error": "El archivo supera el límite de 20 MB.", "code": "ARCHIVO_DEMASIADO_GRANDE"},
            )
    return await call_next(request)

def require_service_key(request: Request):
    """Evita que el microservicio sea invocado directamente desde Internet."""
    expected = settings.agent_service_api_key
    if not expected:
        raise HTTPException(status_code=503, detail="Servicio no disponible.")
    if not secrets.compare_digest(request.headers.get("X-Agent-Service-Key", ""), expected):
        raise HTTPException(status_code=401, detail="No autorizado.")

def get_submission_store() -> SubmissionStore:
    try:
        return SubmissionStore.from_settings()
    except StoreNotConfigured:
        raise HTTPException(status_code=503, detail="Servicio no disponible.")

@app.get("/health")
def health_check():
    """Endpoint de monitoreo y liveness check para Railway y Cloud Run."""
    return {
        "status": "healthy",
        "service": settings.app_name,
        "environment": settings.environment,
    }

@app.get("/.well-known/agent-card.json")
def get_a2a_agent_card():
    """Descubrimiento de capacidades bajo el protocolo estándar A2A."""
    return get_agent_card()

@app.get("/api/v1/tree/hierarchy", dependencies=[Depends(require_service_key)])
def get_hierarchy_tree():
    """Retorna la estructura arbórea completa de agentes institucionalizados."""
    return {
        "tree": hierarchy_manager.list_tree(),
        "total_nodes": len(hierarchy_manager.list_tree())
    }

@app.get("/api/v1/audit", response_model=AuditReportResponse, dependencies=[Depends(require_service_key)])
def get_ai_audit(
    section_id: Optional[UUID] = None,
    desde: Optional[date] = None,
    hasta: Optional[date] = None,
    agente: Optional[str] = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
):
    """
    Registro de uso de IA (IA-07): totales exactos por modelo y últimas filas, para la rendición del fondo.
    `desde`/`hasta` son fechas UTC inclusivas. Los totales cubren todo el filtro; `items` solo las últimas `limit`.
    """
    if desde and hasta and desde > hasta:
        raise HTTPException(status_code=422, detail="`desde` no puede ser posterior a `hasta`.")
    try:
        return fetch_audit_report(str(section_id) if section_id else None, desde, hasta, agente, limit)
    except AuditNotConfigured:
        return JSONResponse(status_code=503, content={"error": "Servicio de auditoría no disponible."})
    except AuditQueryError as exc:
        logger.error("Consulta de auditoría falló: %s", exc)
        return JSONResponse(status_code=502, content={"error": "No se pudo consultar la auditoría en este momento."})

@app.post("/api/v1/agent/chat", response_model=AgentChatResponse, dependencies=[Depends(require_service_key)])
def handle_agent_chat(request: AgentChatRequest):
    """
    Punto de entrada principal para consultas agénticas jerárquicas.
    Resuelve el linaje del nodo en el árbol y compila las políticas institucionales.
    """
    try:
        response = root_agent.process_request(request)
        return response
    except Exception as e:
        # Sanitización de errores según Regla 12 de AGENTS.md
        return JSONResponse(
            status_code=500,
            content={"error": "No se pudo procesar la solicitud con el agente en este momento."}
        )

@app.post("/api/v1/agent/evaluate", response_model=SubmissionEvaluationResponse, dependencies=[Depends(require_service_key)])
def handle_submission_evaluation(request: SubmissionEvaluationRequest):
    """
    Endpoint especializado para corrección asistida y cálculo de décimas.
    """
    try:
        return submission_evaluator_worker.evaluate_submission(request)
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "Error interno al evaluar la entrega."}
        )

@app.post("/api/v1/agent/design-activity", response_model=ActivityDesignResponse, dependencies=[Depends(require_service_key)])
def handle_activity_design(request: ActivityDesignRequest):
    """
    Agente 1: Diseñador de Actividades y Rúbricas (OpenRouter).
    Genera dinámicas de ayudantía, pauta de mediación y rúbrica sobre 100 puntos netos.
    """
    try:
        return activity_designer_worker.design_activity(request)
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "Error interno al diseñar la actividad con el Agente 1."}
        )

@app.post(
    "/api/v1/submissions/{submission_id}/versions",
    response_model=SubmissionVersionResponse,
    status_code=201,
    dependencies=[Depends(require_service_key)],
)
def handle_submission_version(
    submission_id: UUID,
    uploaded_by: UUID = Form(...),
    file: UploadFile = File(...),
    store: SubmissionStore = Depends(get_submission_store),
):
    """
    Ingesta de una entrega en PDF (IA-05): extrae texto con offsets, crea la versión siguiente
    (marca atrasada según due_date) y guarda el archivo en el bucket privado "entregas".
    """
    try:
        data = file.file.read(settings.max_pdf_bytes + 1)
        result = ingest_submission_pdf(store, str(submission_id), str(uploaded_by), file.content_type or "", data)
    except IngestError as e:
        return JSONResponse(status_code=e.status, content={"error": e.message, "code": e.code})
    except Exception:
        logger.exception("Fallo inesperado al ingerir la entrega")
        return JSONResponse(
            status_code=500,
            content={"error": "No se pudo procesar la solicitud en este momento."}
        )
    return SubmissionVersionResponse(
        version_id=result.version.id,
        version_n=result.version.version_n,
        atrasada=result.version.atrasada,
        paginas=result.pages,
        caracteres=result.characters,
        chunks=result.version.chunks,
    )

@app.post("/api/v1/agent/stream", dependencies=[Depends(require_service_key)])
async def stream_agent_chat(request: AgentChatRequest):
    """
    Streaming en tiempo real mediante Server-Sent Events (SSE).
    """
    async def event_generator():
        response = root_agent.process_request(request)
        chunks = response.reply.split(" ")
        for chunk in chunks:
            yield f"data: {chunk} \n\n"
            await asyncio.sleep(0.04)
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=True)
