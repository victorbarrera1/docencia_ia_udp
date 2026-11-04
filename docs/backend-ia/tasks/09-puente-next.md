# [IA-09] Puente Next.js ↔ agent-services
**Depende de:** 08 · **Ola:** 5 · **Modelo:** Sonnet · **Toca `src/`: coordinar con Benjamín**

## Entregables
- Revisa `src/app/api/ai/chat` y `activity-draft` para seguir su mismo patrón (servicio en `src/services/`, ruta en `src/app/api/ai/`), respetando AGENTS.md (≤200 líneas por archivo, 3 capas).
- Rutas: `POST /api/ai/submissions/[id]/versions`, `POST /api/ai/evaluate`, `GET /api/ai/reviews/[id]`, `PATCH /api/ai/observations/[id]`, `POST /api/ai/reviews/[id]/confirm`. Validan sesión y rol; llaman a agent-services con `AGENT_SERVICE_API_KEY` desde el servidor (nunca desde el navegador).
- Tipos TypeScript en `src/types/` que reflejen el contrato del SPEC.
- Sin componentes de UI: eso es de Benjamín.
- `npm run build` solo al final, como dice AGENTS.md.

## Criterio de listo
Smoke script (como `scripts/smoke_agent_ai.mjs` de la rama de Cursor) que sube una versión, evalúa y confirma contra un agent-services local en modo fake.
