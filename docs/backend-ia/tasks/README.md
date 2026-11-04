# Loop de desarrollo — Backend IA

Cada tarea es un workspace de Conductor, con una rama `ia/NN-...` y un PR contra `staging` **del fork**.
Las tareas de una misma ola van en paralelo. No se abre una ola hasta mergear la anterior.

| Ola | Tareas en paralelo | Modelo |
|---|---|---|
| 1 | 00 tests + CI | Sonnet |
| 2 | 01 RLS · 02 seudonimización · 03 cliente LLM | Opus · Opus · Sonnet |
| 3 | 04 modelo de datos IA | Opus |
| 4 | 05 ingesta PDF · 06 RAG pgvector · 07 auditoría | Sonnet ×3 (06 parte cuando 05 mergea si hay choque) |
| 5 | 08 evaluador real · 09 puente Next | Opus · Sonnet (09 puede partir con stubs del contrato) |
| 6 | 10 revisor de versiones · 11 evals vs ayudantes | Opus · Sonnet |
| 7 | 12 spike Jev (opcional) | Sonnet |

## Ciclo por ola

1. `gh repo sync victorbarrera1/docencia_ia_udp -b staging` y `git pull` en local.
2. Abrir un workspace por tarea y pegar el prompt de abajo, cambiando solo `NN`.
3. Revisar el plan y aprobarlo.
4. El agente implementa y abre el PR. Revisar con Codex las tareas 01, 02, 04 y 08.
5. Merge (squash) en el fork. Si otra tarea de la ola ya entró: "haz rebase sobre staging y resuelve conflictos".
6. Al cerrar la ola: PR del fork hacia el repo de Benjamín.

## Prompt por tarea

```
Tarea IA-NN. Lee CLAUDE.md, AGENTS.md, docs/backend-ia/SPEC.md y docs/backend-ia/tasks/NN-*.md.
Ejecuta SOLO esa tarea.

1. Crea la rama ia/NN-<nombre-corto> desde staging actualizado.
2. Explora el código existente que toca la tarea antes de proponer nada.
3. Muéstrame el plan: archivos a crear/modificar, tests, riesgos, y si afecta al frontend.
   Espera mi aprobación.
4. Implementa. Extiende lo existente; no dupliques módulos ni rompas firmas que ya se usan.
5. Cierra con: cd agent-services && python -m pytest (y python evals/run_evals.py si tocaste agentes;
   supabase db reset si tocaste migraciones). Todo en verde.
6. Abre el PR contra staging de este repo (no upstream) con título "[IA-NN] <descripción>"
   y cuerpo: qué se hizo, cómo probar, pendientes, impacto en frontend.
```

## Prompt de revisión (Codex)

```
Revisa este PR contra CLAUDE.md, docs/backend-ia/SPEC.md y la tarea indicada en el título.
Busca: datos de estudiantes que salgan sin seudonimizar, accesos indebidos por RLS,
escrituras de nota_final sin confirmación docente, citas no validadas, secretos en el repo,
y cambios fuera del alcance de la tarea. Lista hallazgos por severidad con archivo y línea.
```

## PR hacia el repo original (al cerrar cada ola)

```
gh pr create --repo benjamin09111/docencia_ia_udp --base staging \
  --head victorbarrera1:staging --title "Backend IA: ola N" --body "Incluye IA-.. IA-.."
```
