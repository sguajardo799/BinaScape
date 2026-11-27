# Design

## Context

Ver `proposal.md` para la motivación y `specs/matlab-render-diagnostics/spec.md` para el contrato propuesto. Este diseño aplica porque cruza el límite Python/MATLAB.

Actualmente `matlab_runner.py` llama a `subprocess.run(..., check=True)` y no persiste salida ni resultado. `render_pipeline.py` captura el error por tarea y actualiza el índice mediante inspección de archivos; esa inspección no utiliza el código de salida. La variante canónica puede repetirse tras rechazo T30, con el mismo identificador y manifiesto sobrescrito. Las tareas restantes pueden ejecutarse en paralelo.

En MATLAB, `run_raven_static_render.m` carga y valida el manifiesto, llama a `render_static_scene`, escribe WAV y metadatos y relanza errores. Dentro del renderer se prepara RAVEN, se ejecuta `rpf.run()` y se calculan respuestas, convolución, mezcla y ruido. Los mensajes actuales no constituyen un protocolo de eventos.

## Goals / Non-Goals

**Goals:** mantener un contrato pequeño basado en archivos, evidencia incremental y responsabilidades explícitas: Python conoce el proceso; MATLAB conoce las etapas. Permitir consultar diagnósticos incluso después de borrar salidas rechazadas.

**Non-Goals:** no usar los diagnósticos como autoridad para aceptar variantes; no cambiar `output_index.py`, Clarity, validación acústica o reanudación. No instrumentar el interior de RAVEN ni prometer recuperación tras caída del sistema operativo. No introducir timeouts, servicios, una base de datos ni soporte dinámico.

## Decisions

### 1. Directorio independiente e inmutable por identidad de intento

Añadir al layout `metadata/diagnostics/matlab/<attempt_id>/`, con `attempt_id` UUID generado antes del lanzamiento. Cada directorio contiene:

- `invocation.json`: resumen versionado escrito inicialmente como `running` y actualizado al terminar.
- `console.log`: stdout y stderr del proceso en un mismo archivo, sin cargarlos enteros en memoria.
- `events.jsonl`: eventos emitidos únicamente por MATLAB para ese intento.
- `input_manifest.json`: copia de la entrada usada para conservar evidencia frente a sobrescrituras de manifiestos por reintentos.

`render_pipeline.py` pasa el directorio base de diagnóstico al runner mediante un argumento opcional; el runner conserva el contrato de retorno y excepciones. Para llamadas Python directas sin ese argumento, usar un directorio `matlab_diagnostics` junto al manifiesto. MATLAB recibe un contexto de diagnóstico opcional separado del manifiesto acústico. No añadir campos obligatorios al YAML ni al esquema de escena.

Alternativas descartadas: un archivo por `variant_id` pierde reintentos; un JSONL compartido necesita coordinación entre procesos; añadir evidencia al índice funcional acopla diagnóstico y reanudación.

### 2. Resumen propiedad de Python

Campos de `invocation.json` (versión inicial `1`): `attempt_id`, `scene_id`, `variant_id`, `output_type`, `command`, `manifest_path`, `manifest_snapshot_path`, `started_at`, `finished_at`, `duration_s`, `process_status`, `exit_code`, `error`, `last_started_stage`, `last_completed_stage`, `entrypoint_completed`, `failure_location`, rutas de logs y `artifacts`.

Usar UTC para tiempos y reloj monotónico para duración. `exit_code` permanece null cuando no existe resultado del proceso. `process_status` distingue `running`, `succeeded`, `failed` y `launch_failed`. No confundirlo con los estados del índice de render.

`entrypoint_completed` se deriva exclusivamente de eventos válidos del intento; `process_status` se deriva exclusivamente del resultado observado del proceso. Ningún evento MATLAB puede establecer `succeeded`: ese estado requiere observar terminación con código cero. Un evento de fin del entrypoint seguido de código no cero produce `entrypoint_completed=true` y `process_status=failed`. Código cero sin evento produce `succeeded` con `entrypoint_completed=false` (fin del entrypoint no confirmado, no prueba de fallo del render). Si no se observa terminación, el resumen no puede declarar éxito aunque exista el evento.

Mantener `check=True`: capturar la información de `CalledProcessError` o de lanzamiento, finalizar el resumen en un bloque protegido y relanzar el error original. Guardar el comando como lista, manteniendo el escape de rutas MATLAB y evitando shell. El log se vacía/cierra antes de finalizar el resumen. La consola de la CLI puede indicar la ruta del log; la reproducción en vivo no forma parte del contrato.

La escritura del resumen usa archivo temporal y reemplazo dentro del directorio del intento. Si Python muere, puede quedar `running` con eventos parciales; no inventar un código o una duración final. No se añade reconciliación automática.

### 3. Eventos explícitos en puntos existentes

Un helper MATLAB pequeño recibe contexto opcional; sin él es una operación vacía. Cada evento incluye versión, intento, secuencia monotónica, tiempo UTC, etapa, tipo y, cuando corresponda, identidad de salida, ruta o error MATLAB. Abrir, anexar y cerrar por evento permite conservar líneas completas sin esperar al final del render. Una última línea incompleta se ignora con advertencia al leer; otras anomalías se registran como limitación de evidencia.

Etapas propuestas:

| Etapa | Límite |
| --- | --- |
| `initialization` | Entrada al entrypoint, carga/validación y preparación de proyecto, sala, receptor y fuentes |
| `simulation` | Desde justo antes de `rpf.run()` hasta respuestas, convolución, mezcla y ruido completados |
| `artifact_writing` | Escritura de WAV y metadatos en el entrypoint |

Emitir `stage_started` y `stage_completed`; después de cada escritor exitoso, emitir `artifact_written` para WAV y metadatos por separado. Emitir `entrypoint_completed` solo después de todas las escrituras. El catch existente emite `error` con identificador, mensaje y stack disponibles, y conserva `rethrow(ME)`.

Se adoptan además cuatro eventos ligeros dentro de `simulation`: `step_started` y `step_completed` con `step=raven_run` inmediatamente alrededor de `rpf.run()`, y con `step=audio_postprocessing` alrededor del bloque posterior de extracción de respuestas, reverberación, convolución, mezcla y ruido. El segundo subpaso comienza tras retornar `rpf.run()` y termina al completar el audio del receptor. Guardar `last_started_step` y `last_completed_step` como evidencia adicional del resumen, sin reemplazar las etapas principales ni cambiar `failure_location=simulation`. No añadir eventos por partícula, reflexión, fuente o muestra ni modificar internamente RAVEN.

Estos cuatro eventos por variante permiten distinguir una interrupción en la llamada RAVEN de otra en el procesamiento posterior con coste acotado. Mantener solo la etapa global sería más simple, pero perdería precisamente esa distinción; instrumentar internamente RAVEN ampliaría el alcance y queda descartado.

El pipeline usa una variante por invocación; las llamadas MATLAB multi-variante existentes siguen funcionando. En ellas los eventos incluyen la identidad de salida y las etapas repetidas no se colapsan en el archivo; el resumen muestra el último evento de cada tipo de progreso.

Alternativa descartada: analizar `[INFO]` o usar solamente `diary` no distingue de forma estable etapas ni confirma escrituras.

### 4. Clasificación conservadora y procedencia de archivos

Python deriva progreso solo de eventos válidos del intento. `failure_location` distingue `launch`, la etapa activa observada, `post_entrypoint` e `indeterminate`; si el proceso es exitoso queda null. Sin eventos, no afirmar un fallo de inicialización: pudo ocurrir antes de entrar al flujo instrumentado. Fin de entrypoint más salida no cero significa fallo posterior al entrypoint, no prueba una causa concreta del cierre.

Para cada WAV/metadato guardar ruta esperada, existencia anterior, existencia posterior y confirmación por evento del intento. Esos datos son evidencia, no validación del contenido. La copia de entrada conserva rutas y parámetros del intento aunque las salidas sean eliminadas o sobrescritas. No borrar diagnósticos en los descartes T30.

### 5. Diagnóstico de mejor esfuerzo, separado del resultado funcional

Errores de almacenamiento, parsing o emisión se convierten en advertencias identificables y nunca sustituyen errores originales ni generan errores funcionales nuevos. Si no se puede abrir el log, ejecutar con la salida heredada actual; si no se puede preparar contexto, ejecutar sin eventos. La garantía de persistencia está condicionada a almacenamiento disponible y a que el productor siga vivo. No esconder el fallo de diagnóstico: conservarlo en el resumen cuando sea posible y advertir por consola.

Proteger también el vaciado/cierre del log y el propio mecanismo de advertencias: una advertencia promovida a excepción o una consola no escribible no puede ocultar el error original. No reintentar el render por un fallo de diagnóstico. En Python, comprobar que se relanza la misma instancia de excepción del proceso; en MATLAB, conservar identificador, mensaje y stack del error original mediante `rethrow`, incluso si falla el helper dentro del catch. Comparar estados, validación, reanudación y recuentos del pipeline contra el mismo escenario sin fallos de diagnóstico; no comparar literalmente timestamps variables.

Alternativa descartada: abortar si falla la observabilidad cambiaría la compatibilidad acordada.

## Test Matrix

Matriz de pruebas previstas, no resultados ya verificados. Las pruebas del runner usan resultados y eventos simulados; las pruebas MATLAB usan stubs/mocks para inyectar el fallo en el límite real de cada etapa. Cada fila de fallo verifica además que se conserva la excepción original y que el índice aplica sus reglas existentes.

| Caso | Inyección / evidencia | Resultado diagnóstico esperado |
| --- | --- | --- |
| Lanzamiento | Ejecutable ausente o permiso denegado antes de crear proceso | `launch_failed`, código null, `failure_location=launch`, sin etapas MATLAB confirmadas |
| Antes del entrypoint | Proceso devuelve no cero sin eventos | `failed`, código real, ubicación `indeterminate`; no atribuir inicialización interna |
| Inicialización | Error al cargar/validar manifiesto o preparar proyecto tras `initialization` iniciada | `failed`, última etapa iniciada `initialization`, sin su fin ni escrituras confirmadas |
| Llamada RAVEN | Error/caída tras inicio de `raven_run` sin su fin | `failed`, ubicación `simulation`, último subpaso iniciado `raven_run`, inicialización completada |
| Audio posterior | `raven_run` completado; fallo al extraer respuestas, convolucionar, mezclar o aplicar ruido | `failed`, ubicación `simulation`, `audio_postprocessing` iniciado pero no completado |
| Escritura WAV | Error en escritor WAV tras inicio de escritura | `failed`, ubicación `artifact_writing`, sin confirmación WAV ni metadatos del intento |
| Escritura metadatos | WAV confirmado y error al exportar metadatos | `failed`, ubicación `artifact_writing`, solo WAV confirmado; escritura no completada |
| Posterior al entrypoint | Evento `entrypoint_completed`, seguido de código no cero simulado | `entrypoint_completed=true`, `process_status=failed`, ubicación `post_entrypoint`; causa no confirmada |
| Éxito observado | Secuencia completa y código cero | `entrypoint_completed=true`, `process_status=succeeded`, ubicación null |
| Éxito sin evento final | Código cero, evento final ausente | `entrypoint_completed=false`, `process_status=succeeded`, sin inventar evento |
| Terminación no observada | Resumen inicial y evento final, sin finalización Python | Resumen permanece `running`; el evento no demuestra terminación exitosa |
| Fallo exclusivo de diagnóstico | Fallos de directorio, snapshot, eventos, parsing, resumen, apertura/cierre de log y advertencias | Con proceso exitoso y fallido por separado: resultado funcional intacto, misma excepción original si existe y evidencia disponible conservada |
| Concurrencia | Dos o más invocaciones solapadas mediante barrera, con marcas distintas | Directorios e identidades diferentes; logs, snapshots, resúmenes, eventos y temporales exclusivos de su intento |
| Concurrencia con fallo aislado | Un intento falla al escribir diagnóstico mientras otro completa | El segundo conserva su evidencia completa, salida y estado; no hay sobrescrituras, contaminación ni repetición de invocaciones |

Las pruebas concurrentes fuerzan solapamiento con sincronización explícita, no con esperas temporizadas. Comprobar conjuntos exactos de identidades/rutas y ausencia de las marcas del otro intento en cada archivo; repetir también dos llamadas concurrentes del runner con la misma identidad de variante y la misma entrada inmutable para demostrar que el aislamiento depende del intento, no de la variante. Esto no añade soporte para renders acústicos simultáneos sobre las mismas rutas de salida.

## Risks / Trade-offs

- [Caída nativa o terminación abrupta] --> Puede faltar el último evento; informar último progreso confirmado y no una causa inventada.
- [Fallo del orquestador] --> El resumen inicial y eventos sobreviven cuando el filesystem lo permite; `running` no equivale a éxito ni a proceso aún vivo.
- [Salida redirigida en Windows] --> Verificar con MATLAB real que el ejecutable esperado termina con el batch y conserva stdout/stderr; no asumir propiedades de otros launchers.
- [Sobrecoste y crecimiento de logs/copias] --> Emitir pocos eventos por etapa, escribir consola a disco y documentar retención manual; sin limpieza automática.
- [Rutas o errores con información local] --> Mantener archivos en el árbol local de artefactos y advertir que deben revisarse antes de compartirlos.
- [Cambios de instrumentación que afecten errores] --> Proteger el helper y probar que los errores originales y los estados del índice permanecen intactos.

## Migration Plan

No se migran índices ni artefactos previos. Desplegar juntos runner e instrumentación MATLAB; las firmas MATLAB anteriores continúan aceptadas. Validar primero con procesos simulados y pruebas MATLAB sin RAVEN, después con una comprobación opt-in del entorno instalado. Para rollback, retirar la instrumentación y el contexto opcional; los archivos de diagnóstico existentes pueden permanecer y no participan en la reanudación.
