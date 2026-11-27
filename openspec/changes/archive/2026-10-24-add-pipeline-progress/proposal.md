# Proposal

## Why

La planificación, el render MATLAB y el degradado Clarity pueden permanecer largos periodos sin feedback visible hasta el resumen final. Se necesita distinguir trabajo activo, reutilización, reintentos y fallos, y ofrecer una estimación temporal honesta sin cambiar las decisiones del pipeline.

## What Changes

- Añadir progreso de terminal por etapa: planificación por escenas evaluadas, render canónico por escenas resueltas, variantes restantes por trabajos resueltos y degradado por jobs procesados.
- Mostrar contadores de resultados separados, actividad actual y tiempo transcurrido; ofrecer ETA aproximado por etapa tras reunir muestras suficientes, excluyendo trabajo reutilizado.
- Mostrar la última actividad MATLAB observada leyendo sus eventos diagnósticos existentes, sin asignar porcentajes ficticios a RAVEN ni interpretar logs humanos.
- Mantener la preparación y el envío de Clarity diferenciados: habilitar degradado no equivale a ejecutar el backend.
- Usar presentación en stderr, modo automático según terminal y `--no-progress` en los comandos afectados; conservar resúmenes y códigos de salida actuales.
- Mantener silenciosas por defecto las APIs programáticas y aislar errores de presentación de los resultados funcionales.
- No añadir ETA global, historial temporal persistente, cambios DSP, cambios al renderer MATLAB ni nuevas políticas de ejecución.

## Capabilities

### New Capabilities

- `pipeline-progress`: observabilidad interactiva del avance, actividad y ETA de las etapas estáticas y del backend de degradado, sin autoridad funcional.

### Modified Capabilities

Ninguna. Se consumen los diagnósticos de `matlab-render-diagnostics` y se preservan las reglas de aceptación, reanudación, reintentos y elegibilidad de Clarity; no se modifican sus requisitos.

## Impact

- Orquestador: `cli.py`, `pipeline/static_plan.py`, `pipeline/render_pipeline.py`, `pipeline/clarity_handoff.py`, `pipeline/clarity_runner.py` y observación de eventos de `pipeline/matlab_diagnostics.py`/`pipeline/matlab_runner.py`.
- Clarity: CLI `run-manifest`, presentación local independiente y dependencia directa de Rich. El orquestador ya declara Rich.
- Pruebas: contabilidad secuencial/paralela, ETA con reloj controlado, aislamiento de errores, consola no interactiva, handoff y regresiones funcionales existentes.
- Documentación de ambos CLI y lockfile de dependencias aplicable durante implementación.
- Compatibilidad: nuevas opciones CLI y callbacks opcionales; sin cambios al YAML experimental, manifiestos acústicos, jobs JSONL, rutas de salida, metadatos funcionales ni algoritmo de generación.
