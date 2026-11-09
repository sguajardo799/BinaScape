# Design

## Context

Se propone reparar el commit de un intento terminado, no repetir su proceso. Motivación: [proposal.md](proposal.md).

Hallazgos en `apps/orchestrator`:

| Área | Comportamiento observado |
|---|---|
| `cli.py:render_static` | Captura `RenderStaticRunError`, imprime resumen y sale con 1 sin mostrar su causa. |
| `pipeline/render_acceptance.py:persist_acceptance` | Guarda outcome `pending`, publica índice y guarda `committed`. Si falla, registra `acceptance_persistence_failed`, `persistence_error`, `abort` y `failed`. |
| `pipeline/render_attempt.py:save` | Reemplaza `outcome.json` mediante un temporal; no acredita quién bloqueó un reemplazo en Windows. |
| `pipeline/process_retries.py` | Clasifica persistencia fallida como `storage_failure / not_retryable`; el preflight bloquea esa cadena. |
| `pipeline/render_history.py` | Exige outcome confirmado y decisión aceptada para reutilizar; borrar el índice no elimina el veto. |
| `pipeline/render_pipeline.py` | Ejecuta preflight antes de aplicar el plan. Ante el primer error paralelo deja de despachar y resuelve lo ya iniciado. |

La evidencia observada de `directivity2_0055` registra `exit_code=0`, terminación observada, render válido y un fallo de reemplazo de outcome. Los 58 registros de archivos completos frente a 57 escenas aceptadas reflejan publicación parcial, no 58 aceptaciones confirmadas. El contenido real del run es evidencia de diagnóstico: la implementación y los tests deben usar fixtures, no modificarlo.

Conflicto intencional con el contrato actual: `test_failed_index_commit_cannot_become_legacy_after_restart` exige bloqueo incluso con constancia válida. Las deltas introducen una excepción explícita y auditable, sin relajar los rechazos por evidencia incompleta. `storage_failure` sigue sin autorizar otro proceso.

## Goals / Non-Goals

**Goals:**
- Hacer legible la excepción sin depender del progreso ni de un traceback completo.
- Completar de forma recuperable outcome, índice y cadena del mismo intento bajo el lock existente.
- Soportar registros versión 1 verificables como el del incidente, sin exigir nuevos campos históricos.
- Mantener separadas observación de proceso, validez acústica, reparación de commit y contadores de la llamada.

**Non-Goals:**
- Reintentos indefinidos de escritura, detección del antivirus responsable o cambio de permisos.
- Reparación de procesos `running`/sin terminación observada, salida inválida, T30 rechazado o evidencia ilegible.
- Romper locks, resetear circuitos, renovar presupuestos, migrar automáticamente runtimes históricos incompatibles o repetir renders protegidos.
- Cambiar MATLAB/RAVEN, Pyroomacoustics, Clarity o la geometría/configuración de los experimentos.

## Decisions

### 1. Error terminal con contexto estructurado, no inferencias de consola

Extender el contexto transportado por el error de ejecución cuando se conoce la variante: escena, variante, UUID, cadena y rutas de evidencia. La CLI imprime, tras cerrar el progreso, el resumen disponible y una explicación en stderr con tipo/mensaje de la causa original, errores secundarios y notas relevantes. Los errores anteriores al resumen, incluidos preflight y reparación, reciben el mismo formato con el contexto que exista. Evitar un `except` que oculte errores de programación; no capturar `KeyboardInterrupt`/`SystemExit` como fallo de persistencia.

Ejemplo orientativo, no formato rígido:

```text
ERROR persistencia: PermissionError: [WinError 5] Acceso denegado: ...outcome.json.tmp -> ...outcome.json
escena=directivity2_0055 intento=96b9... cadena=fdf6...
Evidencia: .../outcome.json
Al repetir render-static se comprobará si puede repararse; no se repetirá un render válido.
```

La sugerencia de retomar se muestra solo para candidatos plausibles; un fallo inseguro explica su bloqueo. No afirmar que antivirus, otro proceso o permisos permanentes son la causa sin evidencia.

**Alternativas:** traceback crudo como única salida (ruidoso y no asegura contexto); imprimir solo `str(exc)` (pierde causas/notas y ubicación del intento).

### 2. Dos fases antes de aplicar el plan

Dentro de `static_writer_lock`, después de inspeccionar el plan en memoria:

1. Preflight de lectura de todo el lote: compatibilidad, circuitos, locks privados, cadenas y huellas. Reconocer candidatos reparables como una excepción explícita al veto terminal, sin marcarlos reutilizables todavía. Un conflicto en cualquier escena impide toda reparación.
2. Publicar las reparaciones verificadas, recargar índice e historial y repetir el preflight normal. Solo entonces aplicar el plan y despachar pendientes.

Mantener funciones explícitas de inspección/publicación, preferiblemente en un módulo pequeño `pipeline/persistence_recovery.py`; no crear un framework de recuperación. Compartir las verificaciones existentes de constancia, validación de artefactos, equivalencia de manifiestos e inventario RAVEN. La reparación nunca prepara audio ni materializa un runtime.

**Alternativas:** ignorar `commit_status` en `resume_evidence` (aceptaría publicación incompleta); reparar dentro de un preflight supuestamente de lectura (viola detección de conflictos antes de escrituras); invocar `reconcile-static` (ese comando prohíbe constancias/salidas completas y atiende terminaciones no observadas).

### 3. Elegibilidad conservadora y backend compartido

Aceptar como candidato solo el último intento inequívoco de la cadena activa, resuelto y vinculado a sus entradas. Exigir `termination_observed=true`, código entero y resultado de proceso coherente; constancia íntegra; ausencia de informe de fallo funcional; hashes e identidades compatibles; audio preparado y recursos privados verificables; validación completa incluyendo T30 si es canónico. Un código no cero debe satisfacer además toda la recuperación `post_completion` existente.

El estado previo debe acreditar un fallo exclusivo de publicación (`acceptance_persistence_failed`/`persistence_error`, clasificación de almacenamiento o reparación ya iniciada con registro válido). `pending` sin evidencia del fallo o del journal no basta. Outcome ausente/corrupto, ambigüedad de intentos, cadena no resuelta o referencia del índice a otro intento bloquean. El índice puede faltar o estar rezagado, pero no contradecir identidades.

Para el incidente registrado se verifican los archivos reales en la futura ejecución; el plan no presume que sigan intactos. El mismo mecanismo se aplica a ambos backends del runner compartido, sin necesitar un proceso nuevo ni usar textos diagnósticos para decidir.

**Alternativas:** aceptar cualquier código cero con WAV existente (no acredita procedencia); repetir el backend con sobrescritura (destructivo y costoso); convertir todo `PermissionError` en recuperable (demasiado amplio).

### 4. Journal por intento y publicación repetible

Los outcomes reparados usarán `schema_version=2`, tanto en publicación parcial como final. Los lectores nuevos admitirán versiones 1 y 2, exigiendo un journal válido para la versión 2. Los lectores anteriores rechazan la versión 2 en lugar de ignorar las guardas nuevas. Esto no cambia el esquema de constancias ni el de cadenas y fue autorizado por el usuario; solo los outcomes reparados son incompatibles hacia atrás.

Proponer `metadata/render_attempts/<attempt_id>/persistence_repair/` con preimágenes exactas de outcome y cadena, y del registro del índice o una marca de ausencia. Un `record.json` versión 1 enlaza sus hashes, identidad, error original, fecha de reparación, inventario/huellas de evidencia y estados esperados de publicación. No es una nueva constancia ni una invocación. Mantenerlo separado de diagnósticos de mejor esfuerzo.

Orden de publicación:

1. Guardar preimágenes y journal verificable antes de cambiar estados activos.
2. Publicar estado de reparación en curso y el registro de índice revalidado.
3. Confirmar outcome con decisión recalculada y cadena con resolución aceptada del mismo ordinal.
4. Marcar journal completado tras contrastar los tres estados.

Los consumidores de evidencia deben rechazar una reparación en curso aunque lean un outcome `committed`. `render_history`, preflight de cadenas y validación de reutilización/entrega comparten esa guarda. Una nueva llamada solo acepta los bytes anteriores o las transiciones previstas por ese mismo journal; valores ajenos bloquean. Si el índice completo cambió legítimamente por otras variantes, contrastar el registro afectado, no restaurar todo el JSONL ni perder registros ajenos.

El error original permanece en el journal/preimagen; el outcome activo enlaza la reparación y conserva el resultado original del proceso. No dejar un `persistence_error` activo que vuelva a clasificar el resultado como fallo: trasladar su interpretación histórica al registro de reparación, verificable al leerlo. Mantener intactos los errores y advertencias `post_completion` cuando existan. No resetear el circuito ni volver a resolver su racha como si hubiera terminado un proceso nuevo.

Si otra escritura falla, detener nuevos despachos y comunicar el error. La siguiente llamada puede completar el journal sin nuevas invocaciones. Si no pudo publicarse un journal completo, conservar el estado anterior como no aceptado; nunca inferir éxito a partir de temporales. No se añade una política de reintentos de filesystem en esta propuesta.

**Alternativas:** editar manualmente `failed` a `committed` (sin procedencia); conservar solo el mensaje del fallo (no permite distinguir intervención de manipulación); transacción de base de datos (innecesaria para los contratos file-based existentes).

### 5. Contabilidad aditiva y compatibilidad

Añadir `persistence_repairs` al resumen Python y `persistencias_reparadas` a la salida CLI, con cero por defecto y conteo por UUID confirmado en la llamada actual. Las variantes reparadas reutilizadas también cuentan como reanudadas; no inflan `matlab_invocations`, `process_retries`, `variants_completed_after_retry` ni `recovered_matlab_processes`. La advertencia previa `post_completion` sigue su contabilidad de reutilización existente.

No cambiar nombres de archivos públicos, códigos de salida de fallo ni campos YAML obligatorios. Un resumen del índice sigue describiendo archivos, mientras que la aceptación exige evidencia confirmada; el mensaje de fallo explica esa diferencia sin reinterpretar `completed` como confirmación funcional.

**Alternativas:** sumar reparaciones como reintentos (falsea coste e historial); reutilizar `recuperados` (confunde reparación de almacenamiento con recuperación de proceso no cero).

## Risks / Trade-offs

- [Manipulación o cambio de archivos entre comprobaciones] → Lock existente, revalidación de huellas antes de cada publicación y bloqueo ante estados no previstos; no se protege frente a modificaciones externas maliciosas concurrentes.
- [Nueva interrupción a mitad del commit multiarchivo] → Journal durable, guardas en consumidores e inyección de fallos en cada frontera de publicación.
- [La misma denegación impide guardar incluso el journal] → Abortar sin declarar éxito, mantener causa visible y permitir nueva llamada; no hay garantía de progreso si el almacenamiento sigue inaccesible.
- [Costo de validar WAV/hashes al reanudar] → Inspeccionar a fondo solo candidatos y reutilizar resultados de lectura dentro del mismo preflight, sin relajar validación.
- [Expansión accidental a fallos de proceso o T30] → Casos negativos explícitos y regresiones de reconciliación manual, aislamiento y presupuestos.

## Migration Plan

1. Implementar y probar con fixtures de `outcome` versión 1 equivalentes al incidente; no modificar el run real durante desarrollo.
2. No migrar lotes en masa: la próxima llamada compatible con reanudación activa verifica y repara solo candidatos elegibles.
3. Documentar el mensaje visible, el contador y la distinción con `reconcile-static`; recomendar conservar evidencia ante bloqueos inseguros.
4. Rollback: conservar journal y preimágenes, no restaurar estados manualmente ni borrar evidencia. Lectores anteriores rechazan los outcomes versión 2 como no reutilizables; para continuar usar la versión compatible o un run nuevo, no forzar un presupuesto nuevo. Verificar el rechazo con las comprobaciones de esquema de la versión previa.
