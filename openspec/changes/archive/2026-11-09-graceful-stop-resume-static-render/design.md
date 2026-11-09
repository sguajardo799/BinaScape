# Design

## Context

Ver `proposal.md` para motivación y alcance. El usuario eligió explícitamente **parada controlada**, no cancelación inmediata.

### Estado observado, no hipótesis de aceptación

| Fuente inspeccionada | Hecho actual |
|---|---|
| Outcome `3ecc48aa2ada4a80b6ac6225ad2ba381` | Versión 1, código 1, `termination_observed=true`, estado de proceso failed y fin persistido; decisión/commit pendientes y validación inicial `process_not_observed`. |
| Cadena `78c977adbad74def8d4d69958f043bf4` | Running, ordinal 1 de 3, mismo hash/UUID. |
| Inventario del intento | Existe completion.json; no se verificaron sus hashes ni las validaciones de audio/T30 durante planificación. |
| Circuito | Cerrado, racha cero y ninguna resolución del UUID reportado. |
| `_run_render_process` | Persiste resultado del subprocess antes de devolverlo. `except Exception` no captura KeyboardInterrupt; un interrupt en el padre puede impedir llegar al coordinador. |
| Pools canónico y de variantes | Drenan ante errores ordinarios/circuito, pero no tienen control cooperativo de parada. Salir de ThreadPoolExecutor esperando workers no resuelve automáticamente sus resultados. |
| `_repair_action` | Finalize_chain exige aceptación committed. Rechaza correctamente este outcome pending, pero el mensaje no distingue bien el caso de evaluación pendiente. |
| Reconciliación manual | Exige resultado no observado y prohíbe constancia; no sirve para este incidente con resultado observado. |
| `_finish_round` | Con allow_resample=false conserva evidencia T30 y retorna halted; aún falta una continuación acústica durable consumible por el próximo preflight. |

Las specs principales todavía no contienen las deltas de los dos cambios implementados previos. Este cambio las complementa con nombres de requisitos distintos; solo modifica el escenario de interrupción de `pipeline-progress`, que actualmente no define parada cooperativa. No se amplía finalize_chain para aceptar pendientes ni se retira el veto sobre terminación desconocida.

## Goals / Non-Goals

**Goals:**
- Separar solicitud del operador, resultado del subprocess y publicación administrativa.
- Compartir una política entre caminos secuenciales, canónicas paralelas, otras variantes y ambos backends.
- Hacer recuperable el resultado observado pendiente sin ejecutar el backend para volver a conocer su código.
- Mantener una única autoridad de coordinación y preflight global antes de cambios activos.

**Non-Goals:**
- Kill/terminate automático, segundo Ctrl+C como escape forzado, SIGKILL, cierre de terminal o cortes eléctricos recuperables sin evidencia.
- Parar una ejecución Clarity que ya fue enviada; esta política se aplica a las fases de render/preparación y evita enviar Clarity después de una solicitud recibida allí.
- Alterar acústica, contratos de backend, límites de proceso/T30, formatos de constancia, permisos o locks ajenos.
- Optimizar el tiempo total del preflight o prometer recuperación del run real sin revalidarlo.

## Decisions

### 1. Solicitud cooperativa en la CLI, no captura indiscriminada de BaseException

Instalar un handler SIGINT acotado a `render-static`, en el hilo principal, y restaurar el anterior al salir. Su única operación será solicitar parada mediante una señal segura compartida; no hará I/O, logging, validación ni publicación desde el handler. Ctrl+C repetidos se coalescen; no existe modo forzado implícito. Las APIs programáticas admiten control opcional sin instalar handlers globales ni cambiar sus resultados normales.

Restaurar la política de señales anterior antes de entrar en una ejecución Clarity ya autorizada, para no extender esta semántica al backend de degradado. La comprobación final de stop y la transición al handoff forman una frontera explícita: una solicitud observada antes de ella impide el envío.

El coordinador comprueba la señal antes de cada autorización de despacho, reserva, retry, ronda acústica y handoff. Una reserva/despacho ya autorizada al entrar en su sección crítica pertenece al conjunto en vuelo y debe concluir su preparación/lanzamiento o persistir un fallo real de preparación: nunca dejar un reservado como si hubiera sido cancelado sin prueba. La solicitud se hace efectiva en la siguiente frontera segura; no autorizar otro despacho después de observarla. Probar explícitamente la carrera reserva/señal/lanzamiento.

En preflight y preparación sin procesos activos, comprobar entre unidades y antes de publicación. Si una transacción administrativa ya comenzó, completar su frontera durable o dejar su journal retomable, sin iniciar otro plan ni nuevos procesos. No se promete abortar una llamada de filesystem bloqueada.

**Alternativas descartadas:** dejar que KeyboardInterrupt desarme el pool (pierde resolución); catch BaseException y afirmar éxito (oculta cancelaciones y errores); handlers globales en imports (afecta APIs y otros comandos).

### 2. Aislar la señal del padre del proceso de render

Mantener el command y los escritores de backend actuales. En la frontera compartida `_run_render_process`, crear grupo nuevo en Windows y sesión separada en POSIX para impedir que Ctrl+C enviado al grupo del padre termine el hijo. Las opciones de lanzamiento serán específicas de plataforma, sin shell intermedio, sin dependencia nueva y sin usar ausencia de PID para declarar finalización.

Usar el resultado real del subprocess como hoy. Verificar tanto la construcción de opciones como un arnés de procesos Python temporales que emule un render lento y reciba una señal real en el padre. El arnés no ejecutará MATLAB ni resultados reales. Si se interrumpe externamente el hijo, conservar su código real y aplicar aceptación/clasificación normales; no inventar que fue Ctrl+C.

**Alternativa descartada:** solo cambiar el handler del padre, porque Windows/POSIX pueden entregar también la señal a los hijos del mismo grupo.

### 3. Drenaje funcional con continuaciones suspendidas

Reutilizar `coordinator.resolve` y la evaluación habitual; el stop impide despachar, no decidir. Mantener el writer lock hasta drenar todos los resultados y publicar el resumen. Los fallos de publicación no deben abandonar la observación de otros workers; se acumulan con sus contextos y la salida será error 1.

Un fallo de proceso reintentable se cierra normalmente como scheduled, manteniendo not_before, pero `execute` y ambos pools no lo lanzan hasta otro comando. Una aceptación se conserva y reutiliza. Los procesos ya fallidos siguen contándose como fallidos; pedir parar no cuenta como fallo, invocación ni retry.

Para rechazo T30, guardar una intención explícita por intento en `acoustic_continuation/record.json` (versión 1) con identidad de escena/cadena/intento, diagnóstico verificado, hashes de realización y plan observado, siguiente ordinal acústico permitido y fases pendientes. Se publica durante el drenaje sin remuestrear ni trasladar archivos. Es evidencia para planificar una continuación, no permiso de escritura independiente sobre `static_plan.json`. La ausencia de este registro no autoriza inferir una continuación acústica por existencia de WAV.

### 3.1. Transacción acústica de lote y preflight proyectado

**Refinamiento autorizado tras la pausa de implementación:** varios rechazos T30 drenados pueden compartir la misma preimagen de `static_plan.json`. No ejecutar journals por intento que reemplacen ese archivo por separado: la segunda escritura podría perder la revisión de la primera. Tampoco reparar el plan antes de comprobar el resto del lote.

En el relanzamiento, descubrir intenciones y transacciones pendientes sin escribir. Permitir una única transacción acústica activa por run, en `metadata/acoustic_transactions/{transaction_id}/`, con plan inmutable versionado y estado de publicación separado. Ordenar participantes por índice de escena y UUID; una escena solo puede tener una continuación activa inequívoca. Nuevas intenciones no se incorporan a un plan ya publicado: terminarlo antes de planificar otro, siempre después del preflight de todo el lote.

La planificación acepta control cooperativo opcional, propagado también desde el preflight de recuperación observada. Comprobarlo al entrar y antes de calcular cada participante, además de la frontera de publicación existente. Una solicitud durante un muestreo permite terminar esa unidad, incluso si agota su presupuesto, pero impide empezar la siguiente; descartar el plan solo en memoria y conservar intactas las intenciones y archivos protegidos. Las llamadas sin control mantienen el comportamiento actual.

El plan común captura:
- Identidades y hashes de las intenciones originales, configuración/contrato de generación y dependencias seleccionadas.
- Preimágenes exactas del plan compartido, manifiestos participantes y registros de índice afectados; inventarios de artefactos/runtime/audio preparado con rutas originales, destinos de conservación, tamaños y hashes. Los destinos se fijan antes de mover archivos, nunca se regeneran al retomar.
- Resultados de remuestreo calculados solo en memoria al relanzar, con semillas, ordinales e historial conservados; por escena, manifiesto de destino o fallo terminal por presupuesto acústico agotado.
- Un solo `static_plan.json` de destino que combine todas las revisiones y preserve escenas no participantes, dependencias previas e historial. Incluir el destino del registro de fallos de muestreo cuando corresponda, no dejarlo fuera de la transacción.
- Secuencia de publicaciones y todos los estados intermedios admisibles, con hashes verificables y enlaces a participantes. Los hashes de las intenciones excluyen los vínculos/estados de publicación posteriores para evitar dependencias circulares entre digests.

Antes de publicar siquiera el plan de transacción, validar la configuración y todo el lote sobre una **vista lógica de solo lectura**, explícitamente acotada a las rutas inventariadas. Para una transacción nueva, la vista se construye de las preimágenes verificadas y destinos calculados en memoria; para una interrumpida, se deriva del mismo plan durable. Resolver cada archivo como original o como destino de conservación únicamente cuando su identidad, posición de publicación y hash coincidan. Pasar esa vista a la inspección del plan/manifiestos y al preflight de render; no parchear globalmente el filesystem, restaurar temporales sobre rutas reales ni omitir escenas participantes para eludir validación. Las escenas no participantes se inspeccionan directamente sin excepciones.

La autorización es de la transacción y sus participantes exactos, no un `ignore_conflicts` genérico. Un conflicto en otra escena, lock ajeno, intento sin terminación observada, archivo no inventariado o estado que no pertenezca a un prefijo previsto bloquea con cero nuevas escrituras y cero despachos, incluso si ya había una publicación parcial anterior. El circuito abierto conserva su veto; esta transacción acústica no lo resuelve ni lo reinicia. Revalidar el estado real bajo el writer lock inmediatamente antes de cada publicación.

Después del preflight satisfactorio, publicar preimágenes y plan común antes de cualquier modificación activa y enlazar las intenciones participantes. Conservar archivos según el inventario, publicar manifiestos o estados terminales previstos y finalmente el plan compartido/registro de fallos de destino. Una caída entre estas escrituras se reconoce mediante la misma vista y secuencia; no necesita que disco contenga por un instante un plan global coherente. No repetir remuestreo ni generar otra transacción para completar la existente. La transacción conserva también los prefijos válidos de movimientos cuando origen/destino ya cambió, rechazando duplicación o bytes ajenos.

Confirmar los journals participantes y la transacción solo cuando todos sus destinos y conservaciones coincidan. Hasta entonces no despachar procesos, aceptar salidas del rechazo ni entregar esas variantes a Clarity. Una nueva señal de parada no inicia esta publicación; si llegó durante una transacción activa, termina una frontera durable y conserva el resto pendiente. Tras completar, recargar desde disco y repetir el preflight ordinario antes de programar únicamente el trabajo autorizado. Repetir el comando no añade rechazos, revisiones ni ordinales ya consumidos.

La integración requiere una entrada de inspección/proyección explícita en `static_plan.py` y su uso desde `render_pipeline.py`, además del registro de continuaciones. No cambiar los contratos acústicos ni adoptar una discrepancia histórica sin transacción verificable; los estados desalineados ajenos al protocolo siguen siendo conflictos. Los lectores antiguos pueden rechazar una transacción parcial: para retomarla se requiere una versión compatible, no una rebaja de formato.

**Alternativas descartadas:** dejar únicamente halted en memoria (pierde autorización durable); publicar revisiones independientes por intento (pierde actualizaciones del plan compartido); reparar disco antes del preflight (viola la garantía global); reducir concurrencia o excluir rechazos simultáneos (incumple el comportamiento acordado).

### 4. Nueva acción para evaluación de resultados observados

Separar descubrimiento, evaluación sin escrituras y publicación. Mantener las dos acciones anteriores intactas y añadir `evaluate_observed_attempt`:

| Estado | Tratamiento |
|---|---|
| Resultado coherente observado, outcome v1 pending, último intento running y clasificación ausente | Candidato nuevo, sea el código cero o no cero. |
| Aceptación fallida por almacenamiento con cadena terminal | Reparación de aceptación previa. |
| Outcome committed aceptado y circuito ya resuelto, cadena running | Finalize_chain previo. |
| Evaluación nueva parcialmente publicada | Retomar el mismo journal y sus destinos exactos. |
| Terminación no observada, identidad ambigua, evidencia ajena o contradictoria | Bloquear; no reinterpretar como esta acción. |

Revalidar UUID/ordinal/predecesor/política, runtime y snapshot exactos, entradas esperadas, fuentes preparadas, recursos privados, artefactos, constancia e informe funcional. Reconstruir un resultado observado en memoria y usar `evaluate_render`/clasificación normal sin llamar al runner ni guardar otra fecha de fin. La validación inicial `process_not_observed` del outcome pendiente no sustituye los campos posteriores de proceso; tampoco acredita un render.

Admitir los resultados de la evaluación normal, no solo aceptación: accepted, unknown_process_failure, acoustic_rejection y rechazos terminales. Los errores de integridad que impidan atribuir evidencia bloquean el preflight sin escrituras; un fallo funcional determinista íntegro puede cerrarse como rechazo y se informa antes de despachar. Solo scheduled autoriza retry de proceso; acoustic_rejection se enlaza a la continuación acústica de la decisión 3. Código no cero sin constancia nunca se acepta por tener WAV; código cero mantiene la política actual, que no exige constancia cuando las demás comprobaciones normales bastan.

**Alternativas descartadas:** usar reconcile-static (falsearía el tipo de evidencia); ampliar finalize_chain (mezcla aceptación no evaluada y confirmada); ejecutar de nuevo para obtener un resultado ya persistido.

### 5. Journal/outcome versión 3 y circuito exactamente una vez

Extender los módulos existentes, no introducir otro framework. La nueva acción usa journal versión 3 y outcome reparado versión 3; los lectores admiten explícitamente versiones 1/2/3 y rechazan acciones desconocidas. Se conservan preimágenes outcome/cadena/registro de índice/circuito, evidencia vinculada, destinos, fecha de evaluación y orden administrativo de recuperación. No fabricar persistence_error ni atribuir a Ctrl+C un código 1 histórico.

Planificar todo el lote antes de escribir. Si faltan resoluciones de varios candidatos, fijar un orden administrativo determinista por finished_at válido y aware, con UUID como desempate, y registrar que es orden de recuperación, no reconstrucción de la secuencia original. Validar el prefijo actual y proyectar todas las resoluciones aplicando las reglas existentes de racha/corte. Si hay exactamente una resolución coherente, enlazarla sin repetirla; duplicación o contradicción bloquean. Un corte alcanzado durante esta proyección permanece enclavado, aunque se terminen de registrar éxitos ya observados.

Antes de estados activos, publicar un plan de recuperación de lote inmutable vinculado a los journals de intento, con prefijo y secuencia proyectada. Los journals por intento lo referencian por hash y posición. Esto permite reconocer cada prefijo intermedio exacto del circuito tras una caída y evita que otro candidato capture como nueva una resolución ya aplicada. Tras reiniciar, terminar ese mismo plan antes de construir otro; no congelar resoluciones legítimas añadidas después de su finalización.

Un circuito abierto ajeno al plan bloquea. Si el propio plan publicado y verificado ya aplicó su corte, autorizar exclusivamente completar sus resultados observados y sus cierres; no levantar el corte ni permitir nuevos despachos. No usar este caso como bypass general de circuito abierto.

Publicación por candidato: outcome v3 pending → índice → resolución de circuito si falta → outcome definitivo → cierre de cadena → confirmación del journal. Los pasos exactos y el plan de lote preceden las modificaciones; revalidar evidencia/transiciones antes de cada escritura. Marcar el plan de lote completo solo después de todos sus journals. Aceptación, reuse y Clarity requieren la auditoría completa; lectores de cadenas deben admitir también outcomes v3 de rechazo sin tratarlos como aceptación.

La evaluación retomada confirmada suma una persistence_repair por UUID en esa llamada, incluso si cierra un rechazo; solo una aceptación realmente reutilizada suma resumed_variants. No sumar una invocación ni fallo de proceso nuevo por reevaluar; los resúmenes preservan el hecho histórico y separan un nuevo retry realmente ejecutado. Conservar intactas las políticas de reemplazo del cambio previo y del índice.

### 6. Presentación y resultado de parada

Emitir aviso corto en stderr aun con --no-progress; la animación sigue siendo opcional. Mostrar procesos en vuelo, no ETA garantizado. Introducir un resultado de parada distinguible (excepción tipada con resumen parcial para la CLI) sin reutilizar errores de aceptación como señal de stop. CLI: 130 si se completó el cierre administrativo de la parada, aun si se informan fallos de proceso duraderos; 1 si hay fallo de integridad/persistencia que deja el cierre incompleto. No enviar Clarity tras la solicitud. Las llamadas normales y otros comandos conservan sus códigos.

Documentar que una parada cooperativa puede durar lo que falte al render activo; Ctrl+C repetidos no fuerzan salida. Un kill externo queda fuera de esta garantía. La evidencia desconocida conserva reconcile-static; la evidencia observada pendiente recibe diagnóstico específico en lugar de «aceptación no confirmada» como única explicación.

## Risks / Trade-offs

- [Render que no termina] → Parada cooperativa no tiene plazo ni mata hijos; mostrar actividad y no prometer recuperación tras kill externo.
- [Diferencias de señales en Windows/POSIX] → Arnés real con hijos Python y pruebas específicas de plataforma, además de tests de opciones; no declarar garantizada una plataforma no verificada.
- [Señal entre reserva y lanzamiento] → Sección de autorización explícita, checkpoint seguro y test de cada frontera; no devolver ordinal consumido como si nunca se hubiera invocado.
- [Circuito sin resolución histórica] → Anexar resolución administrativa auditable, conservar prefijo y enclavamiento; no inventar el orden original.
- [T30 simultáneo o publicación interrumpida de nuevo] → Una transacción acústica con destino común, vista read-only para el preflight global y pruebas de reentrada en cada frontera, incluyendo rechazo terminal y conflictos en escenas ajenas.
- [Formatos mezclados/rollback] → V3 rechazada por lectores anteriores; conservar evidencia y usar lector compatible o run nuevo, nunca bajar versión a mano.

## Migration Plan

1. Implementar después de los dos cambios previos, sin alterar sus tareas ni las deltas principales durante apply.
2. Probar todo con fixtures temporales y subprocesses Python; incluir fixture equivalente al incidente sin copiar ni reparar resultados reales.
3. Validar la suite completa y documentar cobertura efectiva de señales por plataforma. No ejecutar la E2E de speech_directivity_experiment durante desarrollo.
4. No migrar resultados correctos. El próximo comando operativo del usuario, con versión nueva, decidirá elegibilidad tras preflight; no se garantiza la aceptación del intento reportado.
5. Antes de rollback conservar planes/journals/preimágenes. Finalizar con versión compatible o usar run nuevo; no borrar evidencia ni resolver running manualmente. Archivar/sincronizar primero recover-render-persistence-failures, luego harden-render-state-finalization y por último este cambio.
