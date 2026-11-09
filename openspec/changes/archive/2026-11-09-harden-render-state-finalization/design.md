# Design

## Context

Completar el cierre administrativo de un render ya aceptado, sin ejecutar otro proceso. Ver [proposal.md](proposal.md) para motivación y alcance. Este cambio parte de la implementación de `recover-render-persistence-failures` en la misma rama; no reabre ni borra sus tareas completadas.

### Evidencia del incidente y brecha observada

| Evidencia / componente | Hecho observado |
|---|---|
| Intento `cbb504fb91b2491896c9d1331635e0d0`, escena `directivity2_0540` | Outcome versión 1, proceso satisfactorio, código 0, terminación observada, render válido y `commit_status=committed`; no contiene `persistence_error` ni clasificación de reintentos. |
| `outcome.json.tmp` | Contiene `retry_classification=accepted` y `retry_decision=not_retryable`, pero no se publicó. Es diagnóstico del punto de fallo, nunca evidencia autorizante. |
| Cadena `74e93107d010471b91ae8220ed48d197` | Cadena e intento siguen etiquetados `running`. |
| Circuito del run | Cerrado; ya contiene una resolución `accepted`, `process_failed=false` para ese UUID. |
| `RetryChain.resolve_attempt` | Escribe clasificación/decisión en outcome antes de publicar el cierre de cadena. |
| Coordinador | Persiste la resolución del circuito antes de llamar al cierre de cadena; repetir esa resolución provoca `attempt already counted by circuit`. |
| Preflight / reparación actual | El veto global de `running` ocurre antes de inspeccionar candidatos; el reparador exige cadena resuelta y fallo de publicación de aceptación. |
| Reemplazo de índice | Ya tiene 10 intentos y esperas de 10 ms bajo lock local y temporales únicos. Outcome, cadena/circuito, journal y resumen usan reemplazo directo. |

La constancia, WAV, sidecar y recursos del run no se vuelven a validar durante esta planificación: se verificará su elegibilidad real al aplicar/ejecutar la versión futura. Los tests usarán fixtures equivalentes, no el run real.

Los contratos principales de OpenSpec aún no incluyen las deltas del cambio anterior. Las nuevas deltas añaden requisitos con nombres distintos y deben sincronizarse/archivarse después del anterior. Se amplía la exclusión previa por etiqueta `running`, no el veto por terminación desconocida.

## Goals / Non-Goals

**Goals:**
- Tolerar bloqueos breves del reemplazo en la misma llamada y recuperar cierres incompletos en la siguiente.
- Distinguir aceptación, resolución de circuito y cierre de cadena como publicaciones separadas verificables.
- Mantener un único flujo de preflight, auditoría, reutilización y elegibilidad Clarity para RAVEN y Pyroomacoustics.
- Leer reparaciones anteriores sin migrarlas ni recalcular presupuestos.

**Non-Goals:**
- Detectar qué programa bloqueó Windows, modificar permisos o garantizar progreso ante almacenamiento permanentemente inaccesible.
- Recuperar automáticamente un proceso sin resultado observado, un rechazo T30 o una aceptación no acreditada mediante la nueva acción.
- Reconstruir resoluciones ausentes del circuito, repetirlas o reinterpretar su orden.
- Cambiar reintentos de MATLAB, publicación de constancias del backend, audio, manifiestos protegidos, política del índice o diagnósticos de mejor esfuerzo.
- Añadir dependencias o campos de configuración obligatorios; ejecutar la E2E real durante desarrollo.

## Decisions

### 1. Reintentar solo la operación de reemplazo

Introducir un helper pequeño, por ejemplo `pipeline/atomic_replace.py`, para publicar un temporal ya completo y cerrado mediante el reemplazo existente. Política fija propuesta: cinco intentos totales, esperas de 0.05, 0.10, 0.20 y 0.40 segundos (máximo 0.75 s de espera programada por operación; no incluye el tiempo del filesystem). Es una decisión de este cambio, no un comportamiento ya implementado.

Solo son elegibles `OSError` con `winerror` 5, 32 o 33; no basta la clase `PermissionError` ni buscar texto en el mensaje. Una denegación permanente también puede usar código 5: el límite acota el daño de tratarla como potencialmente transitoria. No hay esperas ante errores distintos, ni reintentos de apertura, serialización, escritura o fsync. No capturar señales de interrupción como fallos transitorios.

Aplicar el helper en `RenderAttempt.save`, `_save` de cadenas/circuito, `persistence_journal.atomic_write` y `_publish_run_summary`. Conservar cierres/fsync existentes, locks y ownership por UUID; no regenerar el temporal en cada espera. Un temporal desaparecido, error distinto o fallo de resultado incierto no se transforma en éxito por inspeccionar el destino. Propagar causas con contexto, añadiendo presupuesto agotado cuando corresponda.

El índice conserva `_replace_index`, sus 10 intentos, temporales únicos y lock. No envolverlo con otro retry ni convertir este trabajo en una migración global de todos los `.replace()` del repositorio. Quedan fuera snapshots, audio preparado, manifiestos, constancias y archivos diagnósticos.

**Alternativas:** repetir `persist_acceptance` o `resolve_attempt` completos (puede duplicar circuito/transiciones); retry ilimitado (oculta permisos permanentes); unificar también el índice (cambia una política existente sin necesidad para este fallo).

### 2. Matriz explícita de elegibilidad

Construir la vista esperada del lote sin escribir y reconocer estas situaciones:

| Outcome publicado / cadena | Tratamiento |
|---|---|
| Aceptación fallida, `storage_failure`, cadena terminal resuelta | Reparación de aceptación anterior, sin cambiar su contrato. |
| Versión 1, `committed`, aceptación válida, terminación observada; cadena/intento `running`; clasificación ausente | Nueva acción `finalize_chain`, si supera todas las guardas. |
| Igual, con clasificación `accepted` y decisión `not_retryable` ya publicadas | La misma acción, cerrando solo lo pendiente. |
| Outcome y cadena ya coherentes/resueltos | Reutilización normal; no crear auditoría adicional. |
| Journal de finalización pendiente y estados dentro de su plan | Completar ese mismo registro, sin otro UUID. |
| Outcome pendiente, ilegible, terminación no observada, clasificación contradictoria, rechazo funcional/acústico | No elegible para la nueva acción; mantener el bloqueo/política aplicable. |
| Reparación anterior completa cuyo outcome versión 2 contradice ahora su cadena | Conflicto de integridad; no encadenar una reparación que oculte esa alteración. |

Exigir último intento inequívoco de la única cadena activa compatible, UUID/ordinal/política/semilla estables, rutas canónicas de evidencia, hashes de runtime/snapshot, índice no contradictorio, constancia íntegra y ausencia de informe de fallo funcional. Revalidar artefactos, fuentes preparadas, T30 canónico y recursos RAVEN cuando correspondan. Para código no cero, exigir además todos los controles `post_completion` y conservar su error/advertencia. La ausencia de campos de clasificación permite derivarlos de evidencia verificada, no del temporal.

### 3. Preflight positivo antes de levantar el veto running

Reutilizar la separación de lectura/publicación del cambio anterior. El preflight global obtiene candidatos de las dos acciones y valida íntegramente el lote bajo `static_writer_lock`. Las cadenas `running` solo pueden diferirse para comprobar su elegibilidad contra la vista esperada; antes de cualquier escritura, todas deben ser candidatos acreditados o producir bloqueo. No añadir un `ignore_running` genérico.

El veto de ancestría, identidades ambiguas, procesos no observados, locks y circuito abierto sigue aplicándose. Una cadena no mapeable a una variante prevista no recibe excepción. Pasar al preflight de cadenas únicamente la autorización del candidato exacto, válida para publicar estado, nunca para reservar otra invocación. Publicar las reparaciones, recargar índice/historial y repetir el preflight normal antes de aplicar plan y despachar pendientes.

**Alternativas:** quitar el veto global (permite coexistir con procesos realmente activos); inferir terminación por PID ausente (no acredita resultado pasado); usar `reconcile-static` (prohíbe constancias/salidas completas y sirve para terminación desconocida).

### 4. Circuito como evidencia a preservar, no como operación a repetir

Exigir exactamente una resolución ya persistida del UUID con `classification=accepted` y `process_failed=false`, y circuito cerrado en el preflight. Si falta, está duplicada o contradice el resultado, bloquear: no se puede insertar retroactivamente la aceptación sin alterar potencialmente la racha y el orden.

Capturar preimagen exacta del circuito y enlazar la resolución/posición verificada al journal. No llamar a `BatchCircuit.resolve` durante reparación ni escribir en el circuito para completarla. Al verificar una reparación posteriormente, permitir resoluciones nuevas legítimas añadidas después del prefijo capturado, pero no cambios de las resoluciones previas; no congelar el archivo entero frente al progreso normal del lote.

**Alternativas:** capturar `attempt already counted` e ignorarlo (no verifica la clasificación); resetear el circuito (pierde historial/corte); insertar la resolución si falta (orden original desconocido).

### 5. Journal versión 2 con acción de finalización

Extender el protocolo de `persistence_repair/record.json`, sin crear otro framework:

- Journal versión 1: seguir interpretando la reparación de aceptación anterior con sus tres preimágenes y reglas existentes.
- Journal versión 2: acción obligatoria `finalize_chain`, preimágenes de outcome/cadena/registro de índice y circuito, hashes, evidencia vinculada y estados de destino.
- Outcome de la nueva acción: versión 2 y vínculo al journal; constancia y cadena conservan sus esquemas. Los lectores originales rechazan el outcome y la versión intermedia rechaza el journal 2 desconocido. No migrar registros anteriores completos.
- No exigir `persistence_error` ni `storage_failure` en esta acción. Guardar el estado incompleto observado como motivo. Si la excepción original no se persistió, representarla como desconocida, sin copiarla del temporal o fabricar un WinError histórico.

Orden: guardar preimágenes y plan verificable; publicar outcome versión 2 pendiente como primera modificación activa; publicar índice/outcome final y cierre terminal aceptado de la misma cadena; verificar resolución conservada del circuito; marcar journal completo al final. El circuito no forma parte de las escrituras. Cada paso admite solo preimagen o transiciones exactas previstas, con nueva verificación antes de publicar. Una interrupción permite completar ese mismo journal, pero no autoriza reutilización parcial.

Reutilizar la validación común del cambio anterior sin convertir la nueva acción en una excepción a sus invariantes. Mantener auditable toda modificación de estado y no borrar el `.tmp` del incidente para acreditar éxito. Los temporales sobrantes no son entrada funcional del reparador.

### 6. Guardas de lectura y contabilidad

Actualmente un outcome versión 1 confirmado puede superar la validación de historial aunque la cadena no haya cerrado, lo que requiere una guarda explícita también en la entrega directa a Clarity. Para outcomes con `chain_id`, verificar el cierre de la cadena conocida y el vínculo al intento antes de devolver evidencia reutilizable. Mantener el camino legacy sin cadena. Compartir una comprobación de solo lectura sin introducir ciclos entre `process_retries`, `render_history` y los helpers de reparación.

Ampliar la lectura del journal por versión/acción en las mismas entradas que hoy verifican reparación. No aceptar un journal completo si sus estados o preimágenes contradicen el resultado actual.

Una finalización confirmada incrementa el contador existente `persistence_repairs`/`persistencias_reparadas` una vez por UUID en esa llamada; la variante reutilizada cuenta también como reanudada. No sumar invocaciones, reintentos, fallos de proceso o recuperaciones `post_completion` nuevas por el cierre. Los retries del reemplazo que se resuelven dentro de una llamada no cuentan como reparación entre llamadas. No se añade un contador global mutable ni otro campo YAML para medirlos; al agotarse se informa el límite en las notas de la excepción.

## Risks / Trade-offs

- [WinError 5 también puede ser permanente] → Cinco intentos como máximo y error original visible; no prometer eliminar la causa ambiental.
- [0.75 s no cubre todos los bloqueos] → La recuperación entre llamadas complementa el retry; no ampliar silenciosamente la espera ni repetir el backend.
- [Confundir running con proceso realmente activo] → Excepción solo con terminación persistida y validación positiva de todo el lote; no quitar locks ni usar ausencia de PID.
- [Duplicar o alterar la racha del circuito] → Exigir resolución existente coherente, conservar prefijo y no llamar a su resolución otra vez.
- [Otra caída durante el cierre] → Journal antes de estados activos, outcome reparado versión 2, guardas comunes e inyección de fallos en cada frontera.
- [Lectores de distintas generaciones] → Rechazo por versiones desconocidas, tests de rollback y no operar el mismo run con versiones mezcladas concurrentemente.
- [Regresión en reparación anterior o índice] → Fixtures de journal 1, política del índice sin cambios y suite completa del orquestador.

## Migration Plan

1. Implementar sobre la misma rama y conservar la implementación anterior; documentar que el nuevo trabajo depende de ella.
2. Probar ambos backends con procesos simulados, incluyendo el estado exacto observado sin clasificaciones publicadas ni `persistence_error`; probar relojes/sleeps falsos para los retries.
3. No migrar runs ni reparar el incidente como efecto del desarrollo. La futura llamada normal con reanudación activa decidirá si la evidencia sigue siendo elegible.
4. En rollback, conservar preimágenes/journals y usar una versión compatible o un run nuevo. No rebajar esquemas, borrar registros o convertir `running` a resuelto a mano.
5. Revisar y sincronizar/archivar primero `recover-render-persistence-failures` y después este cambio; este workflow no ejecuta ninguna de esas operaciones ni la implementación.
