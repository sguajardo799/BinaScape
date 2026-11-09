# Proposal

## Why

Un render válido puede detener el lote por un error de persistencia: en `directivity_experiment2`, Windows denegó reemplazar `outcome.json` tras terminar MATLAB correctamente. La CLI ocultó la causa al imprimir solo el resumen, y la llamada siguiente queda bloqueada por el estado terminal aunque el audio sea recuperable.

## What Changes

- Mostrar en stderr la causa concreta del fallo de `render-static`, su tipo, rutas y contexto de escena/intento/cadena disponible, conservando el resumen y el código de salida no cero; también con `--no-progress`.
- En la siguiente llamada con `resume_if_possible=true`, recuperar automáticamente la publicación funcional de intentos cuyo único impedimento fue persistir la aceptación, sin repetir el render ni exigir sobrescritura.
- Exigir resultado de proceso observado, constancia íntegra del mismo intento, compatibilidad de entradas y validación funcional completa; conservar el fallo original y una auditoría de reparación idempotente.
- Completar coherentemente outcome, índice y cadena antes de reutilizar la variante y continuar las pendientes. Distinguir reparación de persistencia, recuperación `post_completion` y reintento de proceso en el resumen.
- Mantener el bloqueo ante evidencia insuficiente/contradictoria, escritores no resueltos, locks o circuito abierto. No reparar automáticamente cualquier `PermissionError` ni regenerar salidas inválidas.

## Capabilities

### New Capabilities

Ninguna; se amplían contratos existentes.

### Modified Capabilities

- `static-render-acceptance`: permitir confirmación posterior auditable de una aceptación no persistida, sin aceptar por mera existencia de archivos.
- `static-render-resumption`: descubrir y reparar esos casos automáticamente en la siguiente invocación protegida y conservar resultados aceptados.
- `matlab-process-retries`: hacer explícito el error terminal y distinguir reparación de estado de nuevas invocaciones, sin cambiar el presupuesto de proceso.

## Impact

- Solo implementación posterior en `apps/orchestrator`: `cli.py`, `pipeline/render_acceptance.py`, `render_history.py`, `render_pipeline.py`, `process_retries.py`, persistencia de intentos/índice y sus tests.
- **BREAKING (lectores anteriores):** outcomes reparados con `schema_version=2`, rechazados por lectores que solo admiten versión 1. Los intentos sin reparar y las constancias conservan versión 1; se siguen leyendo y reparando outcomes versión 1 verificables, incluido el caso observado. Decisión autorizada por el usuario para impedir aceptación insegura durante un rollback.
- Documentación en `apps/orchestrator/README.md`; sin cambios en YAML obligatorio, nombres públicos de audio, algoritmo acústico, MATLAB/RAVEN ni Clarity. La lógica compartida se verificará también con Pyroomacoustics.
- Cambio de comportamiento intencional: el test que hoy exige bloqueo permanente tras fallo de commit deberá separar casos recuperables de casos inseguros. La exclusión de reintentos MATLAB por `storage_failure` se conserva.
