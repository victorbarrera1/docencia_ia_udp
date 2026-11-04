# [IA-07] Registro auditable del uso de IA
**Depende de:** 03, 04 · **Ola:** 4 · **Modelo:** Sonnet

## Entregables
- Listener del hook de IA-03 que inserta en `ai_audit_log` (sin texto enviado ni respuesta).
- `GET /api/v1/audit?section_id=&desde=&hasta=&agente=`: lista + totales de tokens y costo por modelo (sirve para la rendición del fondo).
- Fallo al escribir el log no rompe la llamada principal, pero queda en logs del servicio.

## Tests
Una llamada fake genera una fila; el endpoint suma bien por modelo.
