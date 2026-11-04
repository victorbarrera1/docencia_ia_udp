import json
import logging
from typing import Dict, Any, List
from config import settings
from core.models import (
    ActivityDesignRequest,
    ActivityDesignResponse,
    ActivityInstructionStep,
    RubricItem,
)
from core.llm_support import LLMCallContext
from core.openrouter_client import openrouter_client

logger = logging.getLogger("activity_designer")

class ActivityDesignerWorker:
    """
    Agente 1: Diseñador de Actividades y Rúbricas (Worker Especialista).
    Alineado con los 6 principios de la Guía Maestra y OpenRouter:
    1. Workflow determinista con paso LLM estructurado.
    2. Un agente, una tarea: diseño de dinámicas activas de ayudantía.
    3. Modelo configurable en settings (OpenRouter).
    4. Salida estrictamente tipada con Pydantic.
    """
    def __init__(self):
        self.role_name = "activity_designer"
        self.default_model = settings.activity_designer_model

    def design_activity(self, request: ActivityDesignRequest) -> ActivityDesignResponse:
        system_prompt = (
            "Eres el Agente Oficial de Diseño Pedagógico de la Escuela de Informática y Centro CREA UDP.\n"
            "Tu misión es diseñar actividades de ayudantía de alta calidad pedagógica con rúbricas objetivas.\n"
            "Reglas inquebrantables:\n"
            "1. Tiempos acotados: Estructura la dinámica en 3 momentos que sumen exactamente la duración pedida.\n"
            "2. Rúbrica sobre 100 puntos netos con criterios e indicadores observables (sin ambigüedades).\n"
            "3. Pauta para el ayudante: Instrucciones claras de qué observar y cómo mediar.\n"
            "4. Responde estrictamente con un objeto JSON válido con los campos requeridos.\n"
        )

        user_content = (
            f"Diseña una actividad para la asignatura '{request.course_name}' ({request.course_code}).\n"
            f"Objetivo pedagógico: {request.objective}\n"
            f"Contenido temático: {request.topic_content}\n"
            f"Duración: {request.duration_minutes} minutos\n"
            f"Modalidad: {request.format}\n"
            f"Tipo de bonificación: {request.assessment_type} (máximo +{request.max_decimas} décimas)\n\n"
            "Formato JSON requerido:\n"
            "{\n"
            '  "title": "Título del taller",\n'
            '  "description": "Descripción general",\n'
            '  "duration_minutes": 45,\n'
            '  "format": "grupal",\n'
            '  "instructions": [\n'
            '    {"paso": 1, "titulo": "...", "descripcion": "...", "tiempo_minutos": 10, "puntaje": 30},\n'
            '    {"paso": 2, "titulo": "...", "descripcion": "...", "tiempo_minutos": 25, "puntaje": 40},\n'
            '    {"paso": 3, "titulo": "...", "descripcion": "...", "tiempo_minutos": 10, "puntaje": 30}\n'
            "  ],\n"
            '  "rubric": [\n'
            '    {"criterio": "...", "puntaje_max": 40, "descriptores": ["Excelente: ...", "Aceptable: ...", "Insuficiente: ..."]}\n'
            "  ],\n"
            '  "decimas_sugeridas": 0.3,\n'
            '  "pauta_ayudante": "Guía paso a paso para el ayudante en la sesión"\n'
            "}"
        )

        if openrouter_client.is_configured():
            res = openrouter_client.chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                model=self.default_model,
                response_format={"type": "json_object"},
                temperature=0.2,
                context=LLMCallContext(agente=self.role_name),
            )

            if not res.get("error") and res.get("content"):
                try:
                    raw_json = json.loads(res["content"])
                    return ActivityDesignResponse(
                        title=raw_json.get("title", f"Taller: {request.objective}"),
                        description=raw_json.get("description", ""),
                        duration_minutes=request.duration_minutes,
                        format=request.format,
                        instructions=[ActivityInstructionStep(**step) for step in raw_json.get("instructions", [])],
                        rubric=[RubricItem(**item) for item in raw_json.get("rubric", [])],
                        decimas_sugeridas=min(request.max_decimas, float(raw_json.get("decimas_sugeridas", 0.3))),
                        pauta_ayudante=raw_json.get("pauta_ayudante", ""),
                        model_used=res.get("model_used", self.default_model),
                    )
                except Exception as parse_err:
                    logger.warning(f"Error al parsear JSON de OpenRouter: {parse_err}. Usando fallback determinista.")

        # Fallback determinista institucional (garantía de ejecución sin fallas)
        return self._build_deterministic_activity(request)

    def _build_deterministic_activity(self, request: ActivityDesignRequest) -> ActivityDesignResponse:
        t1 = max(5, int(request.duration_minutes * 0.22))
        t2 = max(10, int(request.duration_minutes * 0.55))
        t3 = max(5, request.duration_minutes - t1 - t2)

        return ActivityDesignResponse(
            title=f"Taller Práctico: {request.objective}",
            description=(
                f"Actividad {request.format} de {request.duration_minutes} min para la asignatura "
                f"{request.course_name} ({request.course_code}). Desarrolla competencias en {request.topic_content}."
            ),
            duration_minutes=request.duration_minutes,
            format=request.format,
            instructions=[
                ActivityInstructionStep(
                    paso=1,
                    titulo="Contextualización y Desafío",
                    descripcion=f"Analizar el escenario propuesto sobre {request.topic_content} e identificar requerimientos.",
                    tiempo_minutos=t1,
                    puntaje=30.0,
                ),
                ActivityInstructionStep(
                    paso=2,
                    titulo="Resolución Técnica y Modelado",
                    descripcion=f"Implementar y justificar la solución para: {request.objective}.",
                    tiempo_minutos=t2,
                    puntaje=40.0,
                ),
                ActivityInstructionStep(
                    paso=3,
                    titulo="Puesta en Común y Conclusiones",
                    descripcion="Presentar trade-offs, riesgos identificados y lecciones aprendidas.",
                    tiempo_minutos=t3,
                    puntaje=30.0,
                ),
            ],
            rubric=[
                RubricItem(
                    criterio="Dominio Conceptual y Justificación Técnica",
                    puntaje_max=40.0,
                    descriptores=[
                        "Excelente (40 pts): Justifica la arquitectura con métricas cuantitativas y trade-offs claros.",
                        "Aceptable (25 pts): Justifica pero omite impacto en rendimiento o consistencia.",
                        "Insuficiente (10 pts): No fundamenta las decisiones de diseño.",
                    ],
                ),
                RubricItem(
                    criterio="Calidad de la Solución y Separación de Capas",
                    puntaje_max=40.0,
                    descriptores=[
                        "Excelente (40 pts): Aplica Clean Architecture o patrones definidos sin acoplamiento espurio.",
                        "Aceptable (25 pts): Diagrama funcional pero presenta dependencias circulares.",
                        "Insuficiente (10 pts): Solución monolítica desordenada sin separación de responsabilidades.",
                    ],
                ),
                RubricItem(
                    criterio="Trabajo en Equipo y Comunicación",
                    puntaje_max=20.0,
                    descriptores=[
                        "Excelente (20 pts): Participación activa de todos los integrantes con roles claros.",
                        "Insuficiente (5 pts): Disparidad evidente en aportes técnicos.",
                    ],
                ),
            ],
            decimas_sugeridas=min(request.max_decimas, 0.3),
            pauta_ayudante=(
                "📌 Pauta de Mediación para el Ayudante UDP:\n"
                "1. Durante los primeros 10 min, verifica que los grupos entiendan las restricciones del caso.\n"
                "2. En la fase de resolución, no des la respuesta directa; utiliza preguntas socráticas.\n"
                "3. En los 10 min finales, registra las décimas ganadas citando descriptores de la rúbrica oficial."
            ),
            model_used="openrouter/claude-3.5-haiku (deterministic fallback)",
        )

activity_designer_worker = ActivityDesignerWorker()
