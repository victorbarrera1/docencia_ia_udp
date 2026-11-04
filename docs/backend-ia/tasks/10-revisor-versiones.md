# [IA-10] Revisor de avances entre versiones
**Depende de:** 08 · **Ola:** 6 · **Modelo:** Opus

## Entregables
- `agents/specialists/version_reviewer.py`: recibe versión actual, anterior y observaciones aceptadas/confirmadas de la review anterior.
- Por cada observación previa: `abordada | parcial | no_abordada`, con cita de la versión actual cuando corresponda.
- Detecta secciones u objetivos que desaparecieron (diff por encabezados + comparación semántica vía RAG).
- Agrega observaciones `feedback_no_abordado`, `retroceso`, `inconsistencia`, `atraso` a la review.
- Registrar el worker en el árbol jerárquico (`agents/` y `core/hierarchy.py`) siguiendo el patrón existente.

## Tests
Fixture v1, feedback sobre v1 y v2 que corrige una cosa y omite otra.
