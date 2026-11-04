@AGENTS.md

# Backend IA (Víctor) — reglas para agentes

Además de AGENTS.md, si tu tarea está en `docs/backend-ia/tasks/`:

1. Lee `docs/backend-ia/SPEC.md` y tu archivo `docs/backend-ia/tasks/NN-*.md`. Haz solo esa tarea.
2. Rama: `ia/NN-nombre-corto`, creada desde `staging`. El PR va contra `staging` de este repo (el fork), nunca contra `main`.
3. Antes de escribir código, muestra un plan (archivos a tocar, tests) y espera aprobación.
4. Respeta lo existente: extiende `agent-services/` y `supabase/`; no crees un segundo backend ni carpetas paralelas. No toques `src/` salvo que la tarea lo diga.
5. Migraciones nuevas en `supabase/migrations/YYYYMMDD_nombre.sql`, idempotentes (`IF NOT EXISTS`, `DROP POLICY IF EXISTS`). No edites migraciones ya existentes. Refleja el cambio en `supabase/schema.sql` si la tarea lo pide.
6. Cierra con `cd agent-services && python -m pytest` en verde (y `python evals/run_evals.py` si tocaste agentes).
7. PR con título `[IA-NN] <descripción>` y cuerpo: qué se hizo, cómo probarlo, qué quedó pendiente, y si afecta al frontend de Benjamín.

## Reglas no negociables

- La IA nunca pone notas: solo `nota_preliminar`. La nota final la confirma un docente.
- Toda observación de la IA cita un fragmento literal verificable de la entrega; sin cita, se descarta.
- Ningún texto de estudiante sale hacia un LLM sin pasar por la seudonimización.
- Toda llamada a un LLM pasa por `core/openrouter_client.py` y queda registrada en `ai_audit_log`.
- Sin secretos ni datos reales de estudiantes en el repo (el fork es público).
