# Design

## Context

Ver `proposal.md` para la motivación y los dos deltas para el contrato propuesto. El cambio cruza MATLAB, runner, índice, aceptación canónica T30, reanudación y Clarity, por lo que requiere diseño explícito.

Observado en el código actual:

- `matlab_runner.render_manifest_with_matlab` usa `subprocess.run(check=True)` y devuelve `None`; finaliza diagnósticos antes de propagar la excepción.
- `MatlabDiagnostics` genera el UUID y una copia del manifiesto dentro del directorio diagnóstico. Sus operaciones son de mejor esfuerzo y sus eventos no pueden autorizar aceptación.
- `run_raven_static_render.m` escribe cada WAV, exporta metadatos y emite `entrypoint_completed`; no existe constancia funcional.
- `inspect_variant` deriva `completed` principalmente de existencia. Las validaciones binaurales no decodifican WAV; las de arrays comprueban cabecera y procedencia parcial. Hay tests con texto como WAV.
- `render_static_scenes` aborta ante la excepción del intento canónico antes de validar T30. Solo después del control canónico planifica las demás variantes. Secuencial y paralelo comparten inspección, pero propagan errores por caminos distintos.
- La publicación del plan sobrescribe registros del índice; las comprobaciones de reanudación deben preservar evidencia previa antes de escribir un runtime nuevo. Ya existe una precaución específica para geometría de arrays.
- Los metadatos incluyen `summary.output_num_samples`, `summary.output_num_channels`, fuentes y T30. El renderer trabaja a 44100 Hz; la duración objetivo recorta o rellena, con redondeo MATLAB. Las fuentes se resumen en coordenadas RAVEN para schemas 2/3; no se pueden comparar directamente con poses públicas.
- El YAML permite metadatos opcionales, aunque el entrypoint actual los exporta; la variante canónica necesita sidecar para T30 independientemente de esa opción.

## Goals / Non-Goals

**Goals:**

- Separar evidencia inmutable, validación y política con funciones y registros simples, sin jerarquías nuevas.
- Hacer imposible recuperar un proceso fallido únicamente por log, eventos, existencia o un índice histórico optimista.
- Usar el mismo resultado de validación en todos los caminos funcionales, conservando el rechazo acústico como categoría distinta de corrupción.
- No depender de disponibilidad de diagnósticos para emitir, verificar o persistir la decisión funcional.

**Non-Goals:**

- Demostrar fidelidad física completa del WAV a partir de checksums o metadatos. La constancia acredita asociación e integridad de archivos, no corrección científica de la simulación.
- Identificar la causa raíz del cierre de MATLAB, incorporar listas de códigos recuperables o interpretar consola automáticamente.
- Añadir evaluación de LUFS, umbrales de silencio/clipping, igualdad entre canales, beamforming, métricas acústicas nuevas o reintentos genéricos.
- Soportar escritores independientes simultáneos sobre la misma variante pública. El paralelismo soportado sigue siendo entre variantes con rutas distintas.

## Decisions

### 1. La autoridad de recuperación es exclusivamente funcional

Crear el contexto del intento antes de inicializar diagnósticos. El UUID deja de ser propiedad exclusiva de `MatlabDiagnostics`: ambos contratos pueden compartir identidad, pero la disponibilidad de uno no depende del otro.

Extender el entrypoint conservando las llamadas existentes:

`run_raven_static_render(config_path, diagnostics, completion_context)`

El tercer argumento es opcional; `[]` permite contexto funcional sin diagnóstico. No se añaden campos de control al YAML ni al manifiesto acústico.

Alternativas descartadas: usar `entrypoint_completed` como permiso (viola aislamiento); buscar el mensaje de éxito (no estructurado); recuperar solo el código 1 (no informa sobre integridad ni fase).

### 2. Contrato de archivos por intento

Directorio funcional interno propuesto:

`{run_root}/metadata/render_attempts/{attempt_id}/`

Archivos:

- `input_manifest.json`: copia inmutable de los bytes exactos del runtime que se invoca; MATLAB recibe esta ruta, no una ruta susceptible de regeneración.
- `completion.json`: constancia publicada por MATLAB.
- `outcome.json`: registro del orquestador, inicialmente en ejecución y posteriormente con proceso observado, validación y decisión. Su creación previa al lanzamiento permite detectar intentos interrumpidos aunque el índice se regenere.

Para el helper directo sin layout de experimento, usar `render_attempts/` junto al manifiesto; no reutilizar `diagnostics_dir` para almacenamiento funcional. Los archivos sobreviven a descartes T30 y no contienen copias del audio completo.

Contenido de `completion.json`, versión 1:

- `schema_version: 1`, `attempt_id`, `scene_id`, `variant_id`, `output_type`.
- `manifest_sha256`: SHA-256 hexadecimal minúscula de los bytes de `input_manifest.json`, no de JSON reserializado.
- `artifacts`: una entrada por archivo exportado, con `kind`, `path` absoluta normalizada, `size_bytes` entero no negativo y `sha256`. El inventario se contrasta exactamente con las salidas esperadas; no se admite descubrir arbitrariamente qué aceptar desde la constancia.

La prueba física confirmó que `jsonencode` puede representar tamaños y contadores
enteros grandes mediante exponentes, por ejemplo `1.411244E+6`. El lector normaliza
con precisión decimal esos números a enteros únicamente si son exactamente
integrales y están en el rango seguro `abs <= 2^53`. No convierte booleanos,
fracciones ni valores flotantes inseguros por redondeo. Esto conserva el contrato
numérico sin imponer una representación léxica distinta a la del productor.

Una invocación del orquestador selecciona una variante y exporta su WAV y sidecar. Las llamadas antiguas multivariante no exigen contexto nuevo; el escritor de constancia, si se usa en una llamada multivariante, debe inventariar todas sus salidas, no solo la primera.

El orquestador calcula por sí mismo la huella del manifiesto y las salidas esperadas. MATLAB calcula la huella del archivo que realmente carga, verifica la identidad recibida y calcula huellas de salidas cerradas. Usar SHA-256 interoperable por bloques: `hashlib` en Python y la API Java disponible en el MATLAB soportado, sin herramientas de shell ni paquetes nuevos. La disponibilidad de esa API debe verificarse en el smoke test del entorno real.

La huella no es firma contra un productor malicioso ni acredita contenidos de assets de entrada que cambien en disco sin cambiar el manifiesto; esa trazabilidad ampliada no forma parte del cambio.

### 3. Publicación como última operación funcional de MATLAB

Tras todas las escrituras y cierres comprobados:

1. Comprobar inventario esperado y calcular tamaños/huellas por bloques.
2. Escribir JSON UTF-8 en un temporal del mismo directorio de `completion.json`.
3. Comprobar escritura y cierre; publicar mediante renombrado del mismo volumen sin reemplazar una constancia previa del intento.
4. No ejecutar más trabajo funcional capaz de invalidar ese resultado. Los eventos finales y mensajes posteriores siguen siendo de mejor esfuerzo.

Comprobar también retornos de escritura/cierre de `export_render_metadata`, que hoy no los verifica. No es necesario hacer transaccional la publicación de WAV y sidecar: sin constancia válida e integridad completa nunca se recupera un proceso fallido.

Si falla la constancia solicitada, es un fallo funcional, no diagnóstico: propagarlo y no publicar finalización. Un temporal remanente no se acepta. La falta de constancia por sí sola no invalida un render con código cero y artefactos válidos (compatibilidad con productores anteriores); una constancia presente pero contradictoria sí señala un problema funcional.

### 4. Validación compartida, con perfiles explícitos

Introducir un módulo pequeño de validación de render reutilizado desde `output_index`, pipeline y handoff, en lugar de ampliar las responsabilidades diagnósticas. Entradas: manifiesto esperado, rutas y perfil de validación; salida: validez y comprobaciones estructuradas con códigos de motivo estables, más el resumen necesario para indexar. No convertir errores de lectura inesperados en éxito ni en excepción sin contexto.

Perfiles:

- **Normal:** WAV y correspondencia de runtime; metadatos según configuración y siempre para canónica.
- **Recuperación:** constancia válida y metadatos completos obligatorios, aunque el YAML los haga opcionales.
- **Reutilización/handoff:** las comprobaciones anteriores pertinentes, manifiesto vigente y evidencia previa de decisión; para Clarity se mantienen los controles HRTF existentes.

Validaciones concretas:

- WAV PCM producido por `audiowrite` actualmente: recorrer todos los frames mediante `wave` y comprobar número real de bytes, alineación de frames, tamaño de muestra, frecuencia, canales y número positivo de frames. Decodificar PCM soportado sin cargar todo en memoria; sus muestras enteras no admiten NaN/Inf. Otros formatos se rechazan con motivo de formato no soportado, no se aceptan por cabecera. No es necesario añadir soporte IEEE-float para este productor.
- Exigir 44100 Hz y dos canales HRTF/HARTF o N del array. Comparar con dimensiones declaradas. Para duración objetivo positiva usar el equivalente de redondeo MATLAB (no `round` de Python en empates); sin objetivo exigir coherencia entre frames reales y resumen, sin inventar una duración física exacta.
- Parsear JSON de objeto y validar campos obligatorios por schema soportado 1.0/2.0/3.0. No tratar booleanos como números; no admitir NaN/Inf JSON. Comprobar campos repetidos, no ocultar contradicciones con selección del primer valor.
- Metadatos requeridos: versión, escena/job/tipo, selección de receptor/IR, rutas de salida, frecuencia, dimensiones, número y resumen de fuentes, semilla efectiva y resultados acústicos aplicables. Campos legacy opcionales se normalizan como el productor: procedencia ausente a strings vacíos; sin inventar semilla cero ni poses omitidas.
- Comparar conjunto de `source_id` sin duplicados, rutas preparadas y procedencia, ganancias y tiempos. Transformar poses/vectores al marco de exportación según schema; tolerancia geométrica existente `abs_tol=1e-6`, `rel_tol=1e-9`. Validar geometría completa del array y orden de canales. Normalizar rutas de manera consistente con Windows, no comparar strings arbitrarios como rutas.
- Contrastar semilla según la normalización real del productor y ruido habilitado/capas/políticas aplicadas. Un motivo de omisión soportado no implica automáticamente corrupción; no exigir SNR acústico medido exacto.
- T30 canónico: conservar las diez frecuencias actuales, valores finitos positivos, y verificar frecuencia/contador/estado/media declarados cuando sean obligatorios. Separar `artifact_issues` de `acoustic_rejection`; no reclasificar JSON corrupto como simple rechazo T30.

### 5. Resultado del proceso primero; política fuera del runner

El runner devuelve un registro ligero del proceso y del intento con excepción original retenida. Un código no cero observado no se descarta ni se transforma en éxito. Mantener lanzamiento fallido, código no cero y ausencia de terminación como estados distintos; no capturar interrupciones del usuario como recuperaciones.

La política se aplica después de observar terminación y validar, no dentro del bloque de diagnóstico ni en un `finally` que pueda sustituir el error original.

| Proceso | Constancia | Artefactos/acústica | Decisión |
|---|---|---|---|
| cero | ausente o válida | válidos | `continue` |
| cero | contradictoria | cualquiera | `abort` |
| cero | cualquiera | incompletos/inválidos/no verificables | `abort` |
| no cero observado | válida | válidos | `continue_with_warning` |
| cero o no cero observado | válida si no cero | íntegros, rechazo T30 canónico | `retry_acoustic` o agotamiento existente |
| no cero observado | ausente/inválida | cualquiera | `abort` |
| lanzamiento fallido o terminación no observada | cualquiera | cualquiera | `abort` |

Para código cero, el rechazo T30 de artefactos estructuralmente íntegros mantiene el remuestreo aun sin constancia. No se consumen reintentos T30 por WAV corrupto, JSON incompleto o un fallo de lanzamiento.

No hay lista de códigos recuperables: la evidencia funcional acredita finalización; el registro funcional la llama `post_completion`, sin atribuir causa. Los eventos diagnósticos pueden seguir indicando `post_entrypoint` solo si realmente lo observaron. Un evento diagnóstico ausente o de error no cambia por sí solo la decisión; contradicción funcional significa discrepancia de identidad, contenido, contrato o fallo funcional comunicado, no interpretar el log.

### 6. Integración sin debilitar el control canónico

Aplicar la misma evaluación a canónica, tareas secuenciales y tareas paralelas. Una canónica recuperada todavía pasa por T30 antes de habilitar sus variantes hermanas. Un T30 rechazado conserva resultado de proceso y constancia del intento rechazado, descarta salidas mediante el mecanismo existente y usa un UUID nuevo en el siguiente intento.

Para no aceptar una variante corrupta con exit cero, `completed` nuevo exige validación satisfactoria. Mantener los estados públicos `planned/completed/partial/failed`: un conjunto presente pero inválido es `partial`, ausencia de salidas producidas es `failed`; la mera existencia del runtime de entrada no demuestra render parcial. La validez detallada permite distinguir incompleto/ inválido/no verificable sin cambiar esos estados.

Mantener la cadena de `RenderStaticRunError` para abortos. Cuando también falla la persistencia funcional, conservar ambos errores y dejar accesible como causa el error original del proceso; no continuar sin advertencia durable.

### 7. Persistencia, reanudación y handoff

Añadir al índice referencias funcionales como `attempt_id`, `attempt_outcome_path`, `completion_path`, `process_status`, `exit_code`, `render_validity`, `decision` y `recovery_reason`. Conservar `validation.ok/issues` para consumidores actuales; ampliar el resultado detallado sin exigir que los lectores antiguos conozcan los campos nuevos.

Persistir `outcome.json` final y actualizar índice atómicamente antes de continuar tras recuperación. El resumen y CLI muestran intentos de proceso fallido, recuperaciones de esta ejecución y variantes reutilizadas con recuperación previa en contadores diferenciados. Si todo fue aceptado, la recuperación sola no obliga a exit CLI no cero; los exits por fallo/muestreo actuales permanecen.

Antes de sobrescribir runtimes o publicar `planned`, cargar decisiones previas y revalidar contra el manifiesto esperado. Un intento funcional `running`, abortado o rechazado no se vuelve legacy por perder un registro del índice: consultar los registros funcionales del run. Un fallo del índice tras guardar `outcome` no pierde su veto al reiniciar. Si falta evidencia que permita resolver el historial, no presumir aceptación.

Para salidas históricas sin registros funcionales y sin fallo no recuperado conocido, permitir reutilización solo tras validación estricta y correspondencia con la entrada vigente; no fabricar resultado de proceso ni constancia. Si falta información obligatoria, renderizar de nuevo según la política de reanudación existente. No reinterpretar antiguos diagnósticos como autorización.

Una variante recuperada se reanuda preservando vínculo y advertencia, sin crear un intento nuevo. Verificar las huellas de su constancia conservada frente a las salidas actuales; comparar la entrada vigente semánticamente normalizada con el snapshot original, porque campos agregados posteriores de cuotas/lote no deben invalidar por sí solos el audio. La huella exacta del snapshot sigue siendo obligatoria para verificar su constancia.

El handoff revalida archivos en disco y procedencia antes de crear/enviar jobs; no confía únicamente en `completed` y `validation.ok` almacenados. Una variante HARTF/array sigue siendo inelegible aunque tenga dos canales o se recupere.

### 8. Compatibilidad y dependencias entre componentes

- MATLAB y orquestador deben compartir versión 1 de constancia, identidad, bytes hash y normalización de rutas; tests comunes fijarán ese contrato.
- No se modifica el schema acústico 3.0 ni el contrato público de salidas. Contexto funcional es argumento independiente, no una mezcla con diagnóstico.
- Actualizar los tests existentes que afirman propagación incondicional: mantener casos no recuperables y añadir recuperación acreditada. Los fixtures de texto dejan de representar renders válidos.
- `matlab-render-diagnostics` modifica explícitamente propagación incondicional, pero conserva identidad/progreso/aislamiento. Las specs de array, procedencia y elegibilidad no cambian: se cumplen más estrictamente en la validación compartida.

## Risks / Trade-offs

- [Coste de lectura/hash de WAV grandes] -> Procesar por bloques y reutilizar resultados dentro de una evaluación, sin eliminar la revalidación ante reutilización posterior.
- [Diferencias de redondeo, coordenadas y seeds] -> Fixtures del productor real para schemas soportados, empates de duración y manifiestos preparados; no comparar representaciones sin normalizar.
- [Renombrado bloqueado en Windows o cierre no comprobado] -> Temporal en mismo directorio, comprobar retornos, ensayar publicación interrumpida y bloqueo. No afirmar garantía de durabilidad ante pérdida eléctrica ni soporte de semánticas de red no ensayadas.
- [Constancia íntegra sobre audio científicamente incorrecto] -> Validación de estructura/procedencia/T30; no presentar hashes como demostración de fidelidad física.
- [Reutilización de archivos de intento anterior] -> UUID y snapshot inmutable, inventario exacto, hashes, historial funcional y prohibición de que `planned` borre vetos.
- [Mutación concurrente después de validar] -> Un único escritor por variante dentro del run; comprobar huellas al reusar/entregar. Escritores externos hostiles o dos ejecuciones simultáneas sobre las mismas rutas no quedan soportados.
- [Regresión sobre archivos históricos débiles] -> Documentar rechazo y necesidad de rerender; no rebajar comprobaciones para conservar aceptación falsa.
- [Entorno MATLAB no disponible en CI] -> Tests MATLAB sin RAVEN para emisión/hash y tests Python con proceso simulado; smoke test real separado antes de declarar interoperabilidad verificada.

## Migration Plan

1. Incorporar contrato funcional y validación con fixtures interoperables; preservar llamadas antiguas.
2. Integrar evaluación y persistencia en todas las rutas; actualizar índices de manera aditiva al leer/escribir, sin reescribir historial masivamente.
3. Activar recuperación únicamente al completar todos los controles de constancia y validación. Productores anteriores con salida no cero siguen fallando de forma conservadora.
4. Verificar tests Python, MATLAB y smoke test Windows/RAVEN con salidas nuevas, sin modificar ni borrar datasets existentes.
5. Publicar documentación de advertencias, perfiles, compatibilidad y limitaciones; no presentar esto como arreglo de la causa del cierre.

Rollback: volver a la política conservadora de propagar códigos no cero y conservar archivos funcionales como evidencia. No borrar constancias ni reescribir `process_status` de intentos recuperados; herramientas antiguas pueden ignorar campos aditivos, pero no deben usarse para validar datasets nuevos sin los controles descritos.
