import secrets

from fastapi import Depends, FastAPI, HTTPException, Request
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
    ActivityDesignResponse
)
from core.hierarchy import hierarchy_manager
from agents.root import root_agent
from agents.specialists.submission_evaluator import submission_evaluator_worker
from agents.specialists.activity_designer import activity_designer_worker
from protocols.agent_card import get_agent_card

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

def require_service_key(request: Request):
    """Evita que el microservicio sea invocado directamente desde Internet."""
    expected = settings.agent_service_api_key
    if not expected:
        raise HTTPException(status_code=503, detail="Servicio no disponible.")
    if not secrets.compare_digest(request.headers.get("X-Agent-Service-Key", ""), expected):
        raise HTTPException(status_code=401, detail="No autorizado.")

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
