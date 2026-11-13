# Design

## Context

Ver `proposal.md` (Why). Estado actual relevante:

- `BatchCircuit` (`pipeline/process_retries.py`) persiste `matlab_process_circuit.json` (`schema_version: 1`, `limit`, `consecutive_failures`, `open`, `resolutions`). Hoy solo se reinicia con `force_reset`, derivado de `resume_if_possible=false` + `overwrite_existing=true`. En ese caso renombra el archivo anterior a `matlab_process_circuit.<uuid>.json`.
- `_preflight_render_artifacts` y `RenderCoordinator.prepare` (`pipeline/render_pipeline.py`) abortan si el circuito está abierto y no se forzó.
- `RetryChain.resolve_attempt` registra `retry_decision=circuit_open` en el outcome y en la cadena para **todo** intento resuelto con el corte abierto, aunque haya sido aceptado. `preflight_chain` reutiliza las cadenas aceptadas con salidas reutilizables antes de mirar el estado, pero trata como terminal una cadena `circuit_open` con fallo.
- `archive_attempt` exige `state=scheduled` y un outcome con `retry_decision=scheduled` y terminación observada, salvo `confirmed_interruption`.
- `interruption_recovery.py` ya implementa el patrón de intervención auditada: inspección por defecto, `--apply/--confirm-stopped/--reason`, `active_render_writers()`, `static_writer_lock`, registro con preimágenes y hashes publicado antes de proyectar outcome y cadena, y verificación del registro al reanudar.

Evidencia en `outputs/directivity_experiment103`: hay 5 cadenas `circuit_open`. Cuatro tienen el último intento `accepted` con salidas presentes (invocaciones que ya estaban en curso cuando se abrió el corte). Solo una tiene `unknown_process_failure` en el ordinal 1. Hay además una cadena `scheduled` normal.

## Goals / Non-Goals

**Goals:**
- Reanudar un lote cortado sin re-renderizar variantes aceptadas.
- Que el reinicio sea una intervención explícita, auditable, idempotente y recuperable, con las mismas garantías que `reconcile-static`.
- Que el render sin reinicio siga bloqueado y el mensaje indique la salida.

**Non-Goals:**
- Diagnosticar o reparar la causa del corte (licencia, `matlabprefs.mat`). El operador lo hace antes del reinicio.
- Renovar presupuesto por cadena o reabrir cadenas `exhausted`/`not_retryable`.
- Normalizar las cadenas aceptadas que registraron `circuit_open`. Se dejan intactas porque `preflight_chain` ya las reutiliza.
- Cambiar el modo forzado existente o añadir flags a `render-static`.

## Decisions

1. **Comando separado `reset-circuit`, no un flag en `render-static`.** Sigue el precedente de `reconcile-static` y la regla de contratos de que las intervenciones son explícitas y no lanzan MATLAB. *Alternativa:* `render-static --reset-circuit --reason`. Se descarta porque mezcla la intervención con un render, y un script que lo deje fijo anula la protección del circuito.

2. **Nuevo módulo `pipeline/circuit_reset.py`** con `reset_batch_circuit(run_root, *, apply, confirm_stopped, reason, process_probe=None)`. Reutiliza de `interruption_recovery.py` los helpers `active_render_writers`, `_encoded`, `_exclusive` y la forma del flujo inspeccionar → verificar → publicar. Si hace falta compartirlos, se extraen a un helper común sin cambiar su comportamiento. *Alternativa:* extender `reconcile_interrupted_chain`. Se descarta porque su contrato prohíbe explícitamente reiniciar el circuito y su unidad es una sola cadena.

3. **Cadenas elegibles.** Son elegibles las cadenas activas (no `superseded_by`) con `state=circuit_open` cuyo último intento está `resolved`, tiene `classification=unknown_process_failure`, `retry_decision=circuit_open` y terminación observada con `exit_code` entero. Su transición es `scheduled` si `ordinal < max_attempts`, o `exhausted` si no. Las cadenas con el último intento `accepted` no se tocan. Cualquier `running`/`reserved` bloquea todo el reinicio (hay que usar `reconcile-static` primero).

4. **Publicación en el orden del patrón de reconciliación.** Bajo `static_writer_lock(root)`:
   1. Revalidar el inventario de escritores y la evidencia.
   2. Publicar `metadata/indexes/circuit_resets/<reset_id>/record.json` de forma exclusiva, con `schema_version`, `action=reset_batch_circuit`, operador, fecha, motivo, la preimagen y el sha256 del circuito, y por cada cadena afectada la preimagen y el sha256 de la cadena y el outcome, más su estado siguiente.
   3. Copiar el circuito anterior a `matlab_process_circuit.<reset_id>.json`.
   4. Proyectar cada outcome (`retry_decision` → `scheduled|exhausted`, `circuit_reset_id`) y cada cadena (último intento `retry_decision`, `state`, `not_before=now`, `circuit_reset_id`).
   5. Publicar el circuito nuevo `{schema_version:1, limit, consecutive_failures:0, open:false, resolutions:[], reset_id}` al final.

   El circuito se cierra al final para que una interrupción a mitad de camino deje el lote bloqueado. Repetir el comando con el mismo `record.json` completa la publicación de forma idempotente. *Alternativa:* cerrar primero el circuito. Se descarta porque abriría una ventana en que el render podría despachar sin las cadenas proyectadas.

5. **Verificación al reanudar.** `_resolved_outcome`/`archive_attempt` aceptan un outcome con `circuit_reset_id` solo si el registro existe, sus preimágenes coinciden por hash con la proyección esperada y la cadena enlaza el mismo `reset_id` (equivalente a `verify_interruption_resolution`). `archive_attempt` acepta `retry_decision=scheduled` proyectado por un reinicio con terminación observada (la regla actual ya lo cubre una vez proyectado). El intento original se conserva en el registro.

6. **`BatchCircuit` acepta un campo opcional `reset_id`** sin subir `schema_version`. Los lectores actuales solo validan `schema_version`, así que la compatibilidad se mantiene.

7. **Mensaje de bloqueo.** `RetryBlockedError("Corte de procesos MATLAB persistido: <ruta>")` añade una nota que sugiere `reset-circuit <run_root>`. El texto actual no cambia.

## Risks / Trade-offs

- [Se reinicia sin haber corregido el entorno y el lote vuelve a cortarse] → La inspección muestra la causa del corte (clasificación de los últimos fallos y rutas de `console.log`). El nuevo corte se registra igual que el primero, y el presupuesto por cadena no se renueva.
- [Proyectar outcomes cambia estados persistidos (riesgo de datos)] → Preimágenes y hashes en el registro, publicación ordenada, idempotencia, y tests de interrupción en cada paso de la publicación.
- [Divergencia entre la lógica de elegibilidad y `preflight_chain`] → Tests con el escenario real (4 aceptadas + 1 fallida + 1 `scheduled`) que comprueban que `render-static` despacha solo la fallida reprogramada, la `scheduled` y las variantes sin cadena.
- [Las cadenas aceptadas siguen mostrando `circuit_open` en el resumen] → Es aceptable y queda documentado. El resumen ya cuenta las aceptaciones por outcome.

## Migration Plan

No requiere migración: los lotes existentes con corte abierto se reanudan ejecutando `reset-circuit <run_root> --apply --confirm-stopped --reason "..."` y luego `render-static`. Para revertir basta con no usar el comando. Los archivos de auditoría y el circuito archivado se conservan.
