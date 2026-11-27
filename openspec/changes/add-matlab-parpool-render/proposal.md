# Proposal

## Why

En `directivity_experiment104` todos los renders RAVEN fallaron con `Unable to communicate with required MathWorks services (error 5001)`. Hoy cada variante abre su propio `matlab -batch` y `num_workers` decide cuántos corren a la vez, así que cada variante pide licencia por separado. Con una sola licencia, los arranques simultáneos chocan. Los reintentos se relanzan casi en paralelo y el corte del lote se activó en unos 90 s. Bajar a `num_workers: 1` lo evita, pero el usuario necesita concurrencia para generar el volumen de estímulos de la tesis.

## What Changes

- Nuevo modo de ejecución RAVEN, opcional, que se elige con `execution.matlab_runner: pool`. El valor por defecto es `process`, que mantiene el comportamiento actual.
- En modo `pool`, el orquestador abre una sola sesión de MATLAB por lote (un único `matlab -batch`, una sola petición de licencia). Esa sesión abre un pool local de Parallel Computing Toolbox de `num_workers` workers y renderiza las variantes en paralelo dentro del pool.
- Cada variante sigue siendo un intento con el mismo contrato que hoy: snapshot inmutable, `outcome.json`, `completion.json`/`failure.json`, `console.log`, `events.jsonl`, código de salida y terminación observada. Así, la aceptación, los reintentos, el corte del lote, la reanudación, `reconcile-static` y `reset-circuit` funcionan igual en ambos modos.
- La sesión se arranca una vez y se cierra al terminar el lote. Si la sesión o un worker mueren, los intentos afectados terminan como fallos de proceso observados y pasan por la política de reintentos vigente.
- La sesión usa una carpeta privada de preferencias de MATLAB, como en el cambio `isolate-matlab-prefdir`. El primer acceso del ITA-Toolbox a las preferencias se serializa entre workers para que no se repita la carrera sobre `matlabprefs.mat`.
- El intento registra la identidad de la sesión de pool y del worker que lo renderizó.
- Sin cambios en el backend Pyroomacoustics, los manifiestos, la salida de audio ni la metadata de render.

Fuera de alcance:
- Clasificar el error 5001 como fallo de licencia con su propia espera.
- MATLAB Engine para Python. La sesión se sigue lanzando con `subprocess`, como pide `apps/orchestrator/AGENTS.md`.

Supuestos que se verifican antes de implementar (tarea 1):
- Los workers de un pool local no piden licencias de MATLAB adicionales en esta máquina. Parallel Computing Toolbox está instalado y con licencia (`license('test','Distrib_Computing_Toolbox') = 1`), y el equipo tiene 4 núcleos físicos.
- RAVEN e ITA-Toolbox funcionan dentro de workers de un pool `Processes`.

## Capabilities

### New Capabilities

- `matlab-render-pool`: ejecución de renders RAVEN en una sola sesión de MATLAB con pool local de workers. Cubre la elección del modo, el ciclo de vida de la sesión, cómo cada trabajo del pool se convierte en un intento con terminación y código de salida observados, el manejo de la caída de la sesión o de un worker, el aislamiento de preferencias y la trazabilidad.

### Modified Capabilities

_(ninguna)_ En modo `pool`, los requisitos de `matlab-process-retries`, `static-render-acceptance`, `static-render-resumption`, `matlab-render-diagnostics`, `pipeline-progress` y `raven-render-isolation` siguen vigentes sin cambios. La nueva capability define cómo un trabajo del pool cumple esos contratos.

## Impact

- `apps/orchestrator/src/acoustic_orchestrator/config/models.py`: `ExecutionConfig.matlab_runner` y, si hace falta, el bloque de tiempos de arranque de la sesión; validación (solo backend `raven`).
- `apps/orchestrator/src/acoustic_orchestrator/pipeline/matlab_runner.py`: nuevo camino para enviar trabajos a la sesión y esperar su resultado, con el mismo `MatlabProcessResult`.
- `apps/orchestrator/src/acoustic_orchestrator/pipeline/render_pipeline.py`: arranque y cierre de la sesión alrededor de las etapas de render. El `ThreadPoolExecutor`, `_RenderCoordinator`, reintentos y corte no cambian.
- `apps/matlab/`: nuevo entrypoint de servidor de pool que reutiliza `run_raven_static_render` por trabajo.
- Depende de `isolate-matlab-prefdir` (carpeta privada de preferencias), hoy en curso con 8 de 9 tareas.
- Docs: `docs/architecture/contracts.md` y `apps/orchestrator/README.md` (sección Static Render y diagnóstico).
- Tests en `apps/orchestrator/tests/` y `apps/matlab/tests/`.
