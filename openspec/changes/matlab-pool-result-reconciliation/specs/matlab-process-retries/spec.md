# Spec Delta

## ADDED Requirements

### Requirement: Reconciliación explícita de un resultado del pool sin leer

El orquestador SHALL ofrecer `reconcile-pool-result <run_root> --chain <UUID>` como inspección sin escrituras por defecto. Aplicar SHALL exigir `--apply`, `--attempt <UUID>`, `--confirm-stopped` y un `--reason` no vacío, y las mismas guardas que la reconciliación de interrupciones: exclusión de escritor, ningún proceso escritor activo o no verificable y circuito cerrado. Solo SHALL ser elegible un intento del pool en una cadena `not_retryable` con `process_status` `launch_failed` o `termination_unobserved`, terminación no observada y `decision=abort`, cuyo `results/<attempt_id>.json` publicado por el pool sea legible, coincida en `attempt_id` y tenga `exit_code` 0, con `completion.json` válido, sin informe de fallo y con salidas que validen como render válido sin rechazo acústico. La operación SHALL NOT lanzar MATLAB, renovar presupuestos ni reiniciar el circuito.

#### Scenario: Resultado bloqueado que impide reanudar
- **WHEN** un intento del pool quedó como `launch_failed` porque su resultado con `exit_code` 0 no pudo leerse, y su recibo y salidas son válidos
- **THEN** `render-static` se bloquea con terminación no observada hasta que el operador aplica `reconcile-pool-result`; después reanuda sin volver a renderizar esa variante

#### Scenario: Evidencia ausente o contradictoria
- **WHEN** el resultado del pool falta, informa un `exit_code` distinto de 0, falta el recibo de finalización o el WAV no es válido
- **THEN** la reconciliación se rechaza sin modificar ningún archivo

#### Scenario: Intento no elegible
- **WHEN** se indica la cadena de un intento ya aceptado
- **THEN** la operación se rechaza

#### Scenario: Confirmaciones incompletas o escritores activos
- **WHEN** falta `--confirm-stopped`, `--attempt` o `--reason`, o la consulta de procesos devuelve escritores
- **THEN** no se aplica la reconciliación y no cambia ningún archivo

### Requirement: Evidencia auditable de la reconciliación del pool

Aplicar SHALL conservar preimágenes exactas de cadena y outcome y un registro versionado con el resultado del pool y su huella, la validación del render, la fila previa del índice, operador, fecha y motivo, antes de cualquier cambio de estado. Luego SHALL publicar el outcome con `process_status=succeeded`, `exit_code=0`, `termination_observed=true`, `decision=continue`, clasificación `accepted` y la sesión y worker del resultado; el intento de la cadena como `accepted` (cadena `not_retryable`); y la fila del índice con `decision=continue`. La publicación SHALL ser idempotente y recuperable, y al reanudar la cadena SHALL verificar que outcome e intento coinciden exactamente con la proyección del registro.

#### Scenario: Repetición de una reconciliación completada
- **WHEN** se repite la autorización sobre el mismo intento ya reconciliado
- **THEN** no cambia ningún archivo

#### Scenario: Outcome reconciliado alterado
- **WHEN** después de reconciliar se modifica el outcome publicado
- **THEN** la reanudación se bloquea
