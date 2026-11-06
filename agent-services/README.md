# 🎓 Ecosistema de Agentes de IA UDP (`/agent-services`)

Microservicio desacoplado y autónomo en Python para la orquestación de agentes docentes jerárquicos basados en el marco institucional de la **Universidad Diego Portales (UDP)**.

> 📖 **Para desarrolladores:** Consulta la guía técnica de fases y onboarding completa en el [ROADMAP.md](file:///c:/Users/Benjamin/Desktop/docencia_ia_udp/agent-services/ROADMAP.md).

---

## 🏛️ 1. Arquitectura de Árbol Jerárquico

El sistema no utiliza un chatbot plano, sino un árbol de herencia de políticas en cascada:

```
                  ┌─────────────────────────────────────────┐
                  │      NIVEL 1: ORQUESTADOR RAÍZ          │
                  │   (Vicerrectoría Académica / CREA UDP)  │
                  └────────────────────┬────────────────────┘
                                       │ Hereda Reglamento UDP & HITL
                                       ▼
                  ┌─────────────────────────────────────────┐
                  │         NIVEL 2: FACULTAD               │
                  │   (Facultad de Ingeniería y Ciencias)   │
                  └────────────────────┬────────────────────┘
                                       │ Hereda Rigor ABET & Proyectos
                                       ▼
                  ┌─────────────────────────────────────────┐
                  │       NIVEL 3: CARRERA / ESCUELA        │
                  │   (Ing. Civil Informática y Telecom)    │
                  └────────────────────┬────────────────────┘
                                       │ Hereda Clean Architecture & Git
                                       ▼
                  ┌─────────────────────────────────────────┐
                  │        NIVEL 4: DOCENTE DE CURSO        │
                  │       ("El Mini-Yo del Profesor")       │
                  │   CIT3203 - Arquitectura de Software    │
                  └────────────────────┬────────────────────┘
                                       │ Delega tareas operativas
         ┌─────────────────────────────┼─────────────────────────────┐
         ▼                             ▼                             ▼
┌──────────────────┐          ┌──────────────────┐          ┌──────────────────┐
│ WORKER: ACTIVIDAD│          │ WORKER: EVALUADOR│          │  WORKER: EXCEL   │
│   Y AYUDANTÍAS   │          │  Y PAUTAS (HITL) │          │ Y PONDERACIONES  │
└──────────────────┘          └──────────────────┘          └──────────────────┘
```

---

## 🚀 2. Ejecución Local

1. Crear entorno virtual (opcional) e instalar dependencias:
   ```bash
   pip install -r requirements.txt
   ```
2. Iniciar el servidor FastAPI:
   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8080 --reload
   ```
3. Probar el estado de salud:
   ```bash
   curl http://localhost:8080/health
   ```

---

## 🌐 3. Protocolos y Endpoints

- `GET /.well-known/agent-card.json`: Manifiesto estándar **Agent2Agent (A2A)**.
- `GET /api/v1/tree/hierarchy`: Exploración del árbol completo y sus directivas.
- `POST /api/v1/agent/chat`: Consulta con linaje jerárquico compilado.
- `POST /api/v1/agent/evaluate`: Pre-corrección de una versión (`version_id`) con citas verificadas; guarda una propuesta (IA-08, ver `docs/backend-ia/evaluador-ia.md`).
- `GET /api/v1/reviews/{id}`, `PATCH /api/v1/observations/{id}`, `POST /api/v1/reviews/{id}/confirm`: revisión docente; confirmar es el único camino a la nota final.
- `POST /api/v1/agent/stream`: Streaming Server-Sent Events (SSE).
