# [IA-02] Seudonimización
**Depende de:** 00 · **Ola:** 2 · **Modelo:** Opus

## Contexto
La rama `cursor/feat-agentai-9e9d` (en upstream) tiene una "seudonimización ligera de nombres". Revísala primero (`git fetch upstream cursor/feat-agentai-9e9d`) y reutiliza lo que sirva (cherry-pick o copiar), citándolo en el PR.

## Entregables
- `agent-services/core/privacy.py`:
  - `pseudonymize(texto, known_people: list[dict]) -> (texto_seudo, mapping)` donde `known_people` trae nombres, apellidos, RUT y correo desde `students`.
  - `restore(texto, mapping) -> texto`.
  - `map_offsets(...)`: convierte offsets del texto seudonimizado al original (los necesita la validación de citas de IA-08).
- Detecta: nombres conocidos (con y sin tildes, mayúsculas, solo apellido, nombre + apellido), RUT (con y sin puntos, DV K), correos (incluido `@mail.udp.cl`), teléfonos `+56`.
- Tokens estables por persona: `[ESTUDIANTE_1]`, `[RUT_1]`, `[CORREO_1]`.
- Integrarlo en `core/openrouter_client.py` NO es parte de esta tarea (va en IA-03 y IA-08); solo la librería.

## Tests
Tildes, mayúsculas, RUT con K, correo institucional, ida y vuelta `restore(pseudonymize(x)) == x`, y offsets mapeados correctamente.
