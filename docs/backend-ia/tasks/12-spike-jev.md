# [IA-12] Spike: Jev (TypeSafe) como verificador de observaciones
**Depende de:** 08, 11 · **Ola:** 7 (opcional) · **Modelo:** Sonnet

## Contexto
Jev (TypeSafe AI, early access) no genera texto: recibe estado y devuelve decisiones tipadas con confianza calibrada, rápido y barato. No reemplaza al evaluador (no puede escribir feedback ni citas), pero puede servir de verificador.
No está en OpenRouter: requiere llave y API propias (`TYPESAFE_API_KEY`).

## Entregables
- `core/jev_client.py` detrás de la misma interfaz de auditoría (IA-07) y con texto seudonimizado (IA-02).
- Tres usos, cada uno con flag de configuración:
  1. **Verificador:** para cada observación del evaluador, ¿la cita respalda el texto? → `respalda | parcial | no_respalda` + confianza.
  2. **Triage para el docente:** prioridad de revisión `alta | media | baja` según la confianza; las de baja confianza se marcan para revisión humana.
  3. **Estado de feedback previo** en IA-10: `abordada | parcial | no_abordada` como alternativa barata al LLM.
- Comparación en el set de IA-11: precisión vs LLM, costo, latencia y calibración (¿la confianza alta acierta más?).

## Criterio de decisión (va en el PR)
Adoptar solo si mejora precisión o costo de forma medible **y** su política de datos es compatible con lo comprometido en el fondo. Si no, queda documentado y apagado.
