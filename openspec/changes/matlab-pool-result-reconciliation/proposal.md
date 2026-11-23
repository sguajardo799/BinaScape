# Proposal

## Why

Tras el reintento de lectura del resultado del pool (PR #59), relanzar `directivity_experiment111` siguió fallando antes de renderizar: `render-static` aborta en la validación previa con `RetryBlockedError: retry chain process termination was not observed`. El intento `f5479653…` de `directivity_0657__binaural_hrtf` quedó registrado como `launch_failed` (`termination_observed=false`, `exit_code=null`) porque el orquestador no pudo leer `results/<attempt_id>.json`, aunque MATLAB publicó ese resultado con `exit_code` 0, un `completion.json` válido y un render válido. Ninguna vía existente lo desbloquea: `preflight_chain` rechaza toda terminación no observada (incluso con `force`) y `reconcile-static` solo admite cadenas `running` con `process_status` `running`/`termination_unobserved`. Un lote de horas queda bloqueado para siempre por un render correcto.

## What Changes

- Nuevo comando de operador `reconcile-pool-result <run_root> --chain <UUID>`: inspección sin escrituras por defecto; aplicar exige `--apply`, `--attempt <UUID>`, `--confirm-stopped` y `--reason`, con las mismas guardas que `reconcile-static` (lock de escritor, consulta de procesos escritores, circuito cerrado).
- Solo es elegible un intento del pool en cadena `not_retryable` con `process_status` `launch_failed` o `termination_unobserved`, terminación no observada y `decision=abort`, cuyo resultado publicado por el pool es legible, coincide en `attempt_id` y tiene `exit_code` 0; además deben existir `completion.json` válido, ningún informe de fallo y salidas que validen como render válido sin rechazo acústico.
- Si `pool_session_id` no quedó registrado (caso real del 111), se busca el resultado en las sesiones del lote y se exige exactamente uno.
- Al aplicar se conservan preimágenes exactas de cadena y outcome y un registro (`pool_result_reconciliation/record.json`) con el resultado del pool, su huella, la validación, la fila previa del índice, operador y motivo; luego se publican outcome (`succeeded`, `exit_code=0`, terminación observada, `accepted`), cadena (`accepted`, sigue `not_retryable`) y fila de `render_index.jsonl` (`decision=continue`). La publicación es idempotente y recuperable.
- Al reanudar, la cadena verifica que outcome e intento coinciden exactamente con la proyección del registro; cualquier alteración bloquea.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `matlab-process-retries`: se añade la reconciliación explícita de un resultado del pool sin leer.

## Impact

- Código: `pipeline/pool_result_recovery.py` (nuevo), `pipeline/process_retries.py` (`_resolved_outcome` verifica la reconciliación), `cli.py` (comando).
- Tests: `apps/orchestrator/tests/test_pool_result_recovery.py`.
- Docs: `docs/architecture/contracts.md`.
- No lanza MATLAB, no renueva presupuestos y no reinicia el circuito. Sin cambios en `apps/matlab` ni en la configuración.
