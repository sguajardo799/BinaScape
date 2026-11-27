# Proposal

## Why

Cuando el lote alcanza `max_consecutive_failures` por una falla del entorno (por ejemplo, licencia MathWorks no disponible, `error 5001`, o `matlabprefs.mat` ilegible por concurrencia), el corte queda persistido en `metadata/indexes/matlab_process_circuit.json` con `open: true` y el render no puede reanudarse: `_preflight_render_artifacts` lanza `RetryBlockedError("Corte de procesos MATLAB persistido")`. La única salida actual es el modo forzado implícito (`resume_if_possible: false` + `overwrite_existing: true`), que archiva y vuelve a renderizar todas las variantes ya aceptadas. En `directivity_experiment103` eso implica repetir 346 renders aceptados para destrabar dos variantes pendientes.

Además, editar el JSON a mano no basta: la cadena cuyo fallo se resolvió con `retry_decision=circuit_open` queda en un estado terminal y `preflight_chain` la rechaza (`same-input retry chain is terminal (circuit_open)`), y editar estados sin evidencia vinculada contradice la política de auditoría existente.

## What Changes

- Nuevo comando CLI `reset-circuit <run_root>` en el orquestador, de solo inspección por defecto. Muestra el estado del circuito, la racha, las cadenas afectadas y su transición prevista, sin escribir archivos ni crear un lock.
- Aplicar exige `--apply`, `--confirm-stopped` y un `--reason` no vacío. Bajo `static_writer_lock`, y sin escritores activos (`active_render_writers`), el comando:
  - publica primero un registro de intervención versionado con las preimágenes exactas y los hashes del circuito, las cadenas y los outcomes afectados;
  - archiva el circuito anterior, sin borrarlo, y publica uno nuevo con `open: false` y racha cero;
  - reprograma solo las cadenas cuyo último intento es un fallo de proceso (`unknown_process_failure`) resuelto con `retry_decision=circuit_open`: pasan a `scheduled` si queda presupuesto o a `exhausted` si no, sin renovar el presupuesto;
  - no toca las cadenas cuyo último intento fue aceptado (aunque su decisión registrada sea `circuit_open`), ni las salidas públicas, ni ninguna cadena terminal de otro tipo.
- El comando no lanza MATLAB ni renderiza. El render se reanuda por separado con `render-static`.
- El resumen y la evidencia distinguen los fallos y el corte anteriores del reinicio. El historial previo sigue visible.
- La configuración de forzado existente (`resume_if_possible: false` + `overwrite_existing: true`) no cambia.

## Capabilities

### New Capabilities

_(ninguna)_

### Modified Capabilities

- `matlab-process-retries`: se añade la intervención explícita y auditable para reiniciar un corte del lote persistido, y la reprogramación acotada de las cadenas detenidas por el corte. La reconciliación de interrupciones conserva su regla de no reiniciar el circuito.
- `static-render-resumption`: se aclara que, tras un reinicio aplicado, la reanudación reutiliza las variantes aceptadas y despacha solo las pendientes o reprogramadas.

## Impact

- `apps/orchestrator/src/acoustic_orchestrator/cli.py`: nuevo comando `reset-circuit`.
- `apps/orchestrator/src/acoustic_orchestrator/pipeline/`: nuevo módulo de reinicio del circuito (sigue el patrón de `interruption_recovery.py`). Ajustes en `process_retries.py` (`BatchCircuit`, `preflight_chain`, `archive_attempt`) para aceptar la evidencia de reinicio.
- Estado persistido: nueva transición `circuit_open → scheduled|exhausted` respaldada por un registro de intervención. Nuevo archivo de auditoría bajo `metadata/indexes/`. El formato de `matlab_process_circuit.json` se mantiene (`schema_version: 1`).
- Docs: `docs/architecture/contracts.md` (sección del circuito y la reconciliación) y la ayuda de la CLI.
- Tests: `apps/orchestrator/tests/` (comando, guardas, idempotencia y reanudación sin re-render de aceptadas).
- Sin cambios en MATLAB, Clarity ni output-analyzer.
