# Proposal

## Why

El render estático propaga los errores de MATLAB, pero no conserva un historial estructurado por invocación que permita localizar el fallo entre inicialización, simulación/procesado, escritura y terminación del proceso. Hace falta conservar esa evidencia sin alterar las reglas actuales de estado de artefactos, reanudación ni propagación de errores.

## What Changes

- Añadir una identidad única y un directorio de diagnóstico por intento real de invocación, incluidos reintentos de una misma variante.
- Conservar la salida de consola y eventos estructurados de inicio y fin de etapas, persistidos durante la ejecución.
- Registrar comando, identidad de escena/variante, manifiesto, tiempos, duración, código de salida o error de lanzamiento, progreso y rutas de artefactos.
- Distinguir archivos observados de escrituras confirmadas por el intento; no inferir éxito ni causa de fallo por archivos preexistentes.
- Identificar conservadoramente un fallo posterior al entrypoint, sin afirmar que se conoce la causa del cierre de MATLAB.
- Mantener intactos el índice funcional, las decisiones de reanudación, los reintentos T30, la planificación y el comportamiento acústico.

## Capabilities

### New Capabilities

- `matlab-render-diagnostics`: evidencia persistente por invocación del renderer estático, con trazabilidad de proceso, etapas y artefactos independiente del estado funcional de la variante. Es comportamiento propuesto, aún no implementado.

### Modified Capabilities

Ninguna. Las capacidades existentes no cambian sus requisitos de render, procedencia o elegibilidad de Clarity.

## Impact

- `apps/orchestrator`: `pipeline/matlab_runner.py`, `pipeline/output_paths.py` y el punto de llamada en `pipeline/render_pipeline.py`; nuevas pruebas de diagnóstico y regresión.
- `apps/matlab`: `run_raven_static_render.m`, `static/render_static_scene.m` y un pequeño helper de eventos; contexto de diagnóstico opcional, compatible con llamadas anteriores.
- Nuevos archivos bajo el directorio de artefactos de la ejecución; documentación de interpretación y límites.
- Sin MATLAB Engine, servicios, dependencias externas nuevas, cambios de esquema del experimento ni modificación del contrato WAV/metadatos acústicos. No incluye render dinámico, timeouts, cancelación ni recuperación automática.
