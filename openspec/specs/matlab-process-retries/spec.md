# matlab-process-retries Specification

## Purpose

Definir la resiliencia ante fallos e interrupciones del proceso MATLAB mediante reintentos acotados y reconciliación manual explícita, sin confundir autorización de otro intento, recuperación funcional ni remuestreo acústico.

## Requirements

### Requirement: Presupuesto explícito de invocaciones
El render estático SHALL admitir una política de proceso configurable con `max_attempts`, espera inicial y máxima, y límite de fallos consecutivos del lote. Por defecto SHALL permitir tres invocaciones totales por cadena, esperas de 2 y 4 segundos con máximo de 30 segundos y corte a cinco fallos consecutivos no recuperados. `max_attempts=1` SHALL desactivar los reintentos.

#### Scenario: Configuración sin campos nuevos
- **WHEN** se carga una configuración anterior sin política de reintentos
- **THEN** se aplican los valores por defecto documentados sin exigir editar el YAML

#### Scenario: Límites inválidos
- **WHEN** el presupuesto no es entero positivo, la espera no es finita o no es no negativa, el máximo es menor que la inicial o el corte no es entero positivo
- **THEN** se rechaza la configuración antes de invocar MATLAB

### Requirement: Clasificación basada en evidencia funcional
El orquestador SHALL considerar automáticamente reintentable un fallo no cero con terminación observada, sin render aceptado ni error determinista funcional acreditado. SHALL excluir del reintento automático errores de entrada conocidos, dependencias ausentes acreditadas, fallos de lanzamiento, terminación no observada, integridad contradictoria y persistencia funcional fallida. Los textos de consola y eventos diagnósticos SHALL NOT decidir aceptación ni reintento.

#### Scenario: Fallo temprano de causa desconocida
- **WHEN** MATLAB termina con código no cero, faltan salidas y no hay evidencia funcional de un error determinista
- **THEN** se registra un candidato a reintento por fallo de proceso de causa indeterminada, aunque cambie o falte el mensaje de consola

#### Scenario: Error de entrada acreditado
- **WHEN** el preflight o un informe funcional válido del intento identifica una entrada inválida o una dependencia ausente
- **THEN** se aborta sin consumir invocaciones adicionales para ese error determinista

#### Scenario: Sin instrumentación diagnóstica
- **WHEN** no se conservan eventos o consola pero la evidencia funcional del proceso es la misma
- **THEN** la elegibilidad para reintento es la misma y no se afirma haber observado entrada ni una etapa interna

#### Scenario: Código cero con artefactos inválidos
- **WHEN** MATLAB termina con código cero pero la validación detecta un WAV truncado o metadatos incoherentes
- **THEN** se rechaza funcionalmente sin activar reintentos de proceso como atajo

#### Scenario: Reinicio sin terminación observada
- **WHEN** una cadena conserva un intento reservado o en ejecución sin resultado observado y no existe reconciliación manual válida
- **THEN** se bloquea su continuación automática aunque no aparezca un proceso activo o se haya retirado el lock

### Requirement: Informe funcional de fallo determinista
MATLAB SHALL poder publicar un informe de fallo versionado y vinculado al intento y hash del manifiesto cuando detecte una entrada inválida durante carga o validación. El orquestador SHALL verificar su identidad y categoría antes de usarlo. El informe SHALL NOT sustituir la excepción original, constituir finalización ni depender de archivos diagnósticos; las llamadas anteriores SHALL seguir soportadas.

#### Scenario: Fallo de validación dentro de MATLAB
- **WHEN** el validador rechaza la entrada y se proporcionó contexto funcional
- **THEN** se conserva la excepción original y se publica el informe funcional de entrada inválida cuando su almacenamiento está disponible

#### Scenario: Informe ajeno o contradictorio
- **WHEN** el informe corresponde a otro intento, usa otro hash o contradice la evidencia funcional
- **THEN** no se confía en su categoría y se aborta con motivo de integridad explícito

### Requirement: Cadena persistente y entradas estables
Cada invocación SHALL tener UUID y evidencia propios, vinculados a cadena, ordinal, intento previo, límite, motivo y programación del siguiente intento. Una cadena SHALL conservar contenido de entrada y semilla, sin remuestrear por fallos de proceso. El presupuesto SHALL persistirse antes de programar otro intento; una reanudación automática de cadena SHALL NOT reiniciarlo.

#### Scenario: Fallo seguido de render válido
- **WHEN** el primer proceso falla y el segundo produce un render aceptable
- **THEN** se conservan ambos resultados e identidades; el primero sigue fallido y el segundo no se registra como recuperación del primero

#### Scenario: Interrupción entre intentos
- **WHEN** se reinicia el orquestador con una cadena pendiente identificable y sin conflictos de archivos
- **THEN** se respeta el presupuesto consumido y la espera pendiente, sin inventar el resultado de una invocación interrumpida

#### Scenario: Cadena agotada
- **WHEN** se alcanza `max_attempts` sin render aceptado
- **THEN** se aborta conservando errores y evidencia de todos los intentos, y un reinicio no abre silenciosamente otro presupuesto para esa cadena

### Requirement: Aislamiento y conservación de restos
Antes de repetir una invocación, el orquestador SHALL exigir terminación observada o reconciliación manual válida que confirme ausencia de escritores, sin equipararlas. SHALL conservar los restos propios en almacenamiento por intento con ruta original, tamaño y hash, e impedir su uso como salidas o constancias nuevas. No SHALL mover ni sobrescribir archivos preexistentes protegidos; si no puede aislar los restos SHALL abortar.

#### Scenario: Fallo durante escritura
- **WHEN** un proceso fallido reintentable deja solo WAV o metadatos parciales creados en su cadena
- **THEN** se conserva esa evidencia y se dejan libres las rutas de salida antes del siguiente intento, sin aceptar el resultado parcial

#### Scenario: No se puede conservar evidencia
- **WHEN** falla el archivo o registro de los restos o la persistencia de la cadena
- **THEN** no se invoca MATLAB de nuevo y se conserva accesible el error original junto al error de almacenamiento

### Requirement: Corte del lote y concurrencia acotada
El orquestador SHALL contar fallos consecutivos de proceso no recuperados en orden de resolución persistida y detener nuevos despachos al alcanzar el límite. Un render aceptado SHALL reiniciar la racha. En paralelo SHALL permitir terminar y registrar invocaciones ya iniciadas, sin iniciar reintentos adicionales tras el corte ni contaminar evidencia entre variantes.

#### Scenario: Problema general del entorno
- **WHEN** se alcanza el límite de fallos consecutivos mientras quedan trabajos pendientes
- **THEN** no se lanzan nuevos procesos, se registra el corte y el lote finaliza con error aunque algunos trabajos no hayan agotado su límite individual

#### Scenario: Recuperación antes del corte
- **WHEN** un nuevo intento produce un render aceptado antes de alcanzar el límite global
- **THEN** la racha vuelve a cero y el historial mantiene los procesos fallidos anteriores

### Requirement: Resumen sin éxitos ficticios
La CLI y el resumen SHALL distinguir trabajos renderizados, invocaciones reales, reintentos, procesos fallidos, variantes aceptadas tras reintento, recuperaciones `post_completion` y variantes reanudadas. Los fallos anteriores SHALL seguir visibles después de un éxito posterior y el error terminal SHALL enlazar la cadena y su evidencia.

#### Scenario: Trabajo aceptado en el tercer intento
- **WHEN** una variante se acepta después de dos fallos de proceso
- **THEN** se informa un trabajo, tres invocaciones, dos reintentos y dos procesos fallidos, sin presentarlo como éxito limpio ni recuperación `post_completion`

### Requirement: Reconciliación explícita de un intento interrumpido
El orquestador SHALL ofrecer `reconcile-static <run_root> --chain <UUID>` como inspección sin escrituras por defecto. Aplicar SHALL exigir `--apply`, `--attempt <UUID>`, `--confirm-stopped` y un `--reason` no vacío. La autorización SHALL limitarse al intento indicado y SHALL NOT lanzar MATLAB, aceptar su render ni retirar locks.

#### Scenario: Inspección previa a la intervención
- **WHEN** se inspecciona una cadena interrumpida sin `--apply`
- **THEN** se muestran identidad del intento, presupuesto consumido y restante y estado siguiente, sin modificar archivos ni crear un lock

#### Scenario: Confirmaciones insuficientes
- **WHEN** se solicita aplicar sin identificar el intento exacto, confirmar ausencia de escritores o aportar un motivo
- **THEN** se rechaza la operación sin publicar una reconciliación

#### Scenario: Intento distinto del autorizado
- **WHEN** el último intento de la cadena ya no coincide con `--attempt`
- **THEN** se bloquea la operación sin autorizar ni modificar el intento nuevo

### Requirement: Guardas de seguridad para la intervención
Aplicar una reconciliación SHALL requerir exclusión de escritor, consulta local satisfactoria de posibles escritores sin candidatos activos y evidencia coherente de cadena, intento, snapshot y runtime. Un lock existente, consulta fallida o no verificable, evidencia contradictoria, constancia o informe funcional presente, salidas completas verificables o circuito abierto SHALL bloquearla. La ausencia de PID SHALL NOT acreditar la terminación pasada.

#### Scenario: Escritor activo o consulta incierta
- **WHEN** se detecta MATLAB/RAVEN u otro orquestador de render/generación potencialmente escritor, o no puede obtenerse un inventario de procesos válido
- **THEN** no se aplica la reconciliación, incluso con `--confirm-stopped`

#### Scenario: Lock pendiente de intervención
- **WHEN** existe `static_writer.lock`
- **THEN** se bloquea la reconciliación sin romperlo por antigüedad ni asumir que su propietario terminó

#### Scenario: Evidencia incompatible o resultado completo
- **WHEN** cambian las identidades o huellas, existe constancia/informe funcional o las salidas resultan funcionalmente completas
- **THEN** se exige revisión independiente sin descartar esas salidas ni autorizar su repetición mediante esta operación

#### Scenario: Circuito abierto
- **WHEN** el lote alcanzó su corte persistente de procesos
- **THEN** la reconciliación no lo reinicia ni habilita nuevos despachos

### Requirement: Evidencia auditable sin resultado de proceso ficticio
La reconciliación SHALL conservar preimágenes exactas de cadena y outcome y un registro versionado vinculado por identidad y huellas, con operador, fecha, motivo e inventario de salidas. SHALL registrar `process_status=interrupted`, `decision=abort`, `exit_code=null` y `termination_observed=false`, preservando el error original y sin inventar fecha de finalización. Esa evidencia SHALL NOT acreditar finalización ni aceptación del render.

#### Scenario: Interrupción sin resultado observado
- **WHEN** se reconcilia un intento cuyo código y finalización no se registraron
- **THEN** se conserva ese desconocimiento y se registra la fecha de la intervención por separado, sin fabricar éxito, código no cero ni recuperación `post_completion`

#### Scenario: Edición de estados sin evidencia vinculada
- **WHEN** se alteran estados, preimágenes o huellas sin un registro de reconciliación coherente
- **THEN** no se autoriza continuar mediante la mera apariencia de una cadena resuelta

### Requirement: Publicación recuperable e idempotente de la reconciliación
El registro y sus preimágenes SHALL publicarse antes de modificar outcome y cadena. La continuación SHALL requerir concordancia entre ambas publicaciones y la evidencia vinculada. Repetir la operación sobre el mismo intento SHALL ser idempotente; una publicación incompleta SHALL poder completarse solo con el mismo registro y confirmaciones, sin sobrescribir estados ajenos ni renovar presupuesto.

#### Scenario: Interrupción durante publicación
- **WHEN** queda publicado el registro de intervención pero solo parte de los estados posteriores
- **THEN** el render permanece bloqueado y repetir la reconciliación completa únicamente esa misma publicación si la evidencia sigue siendo coherente

#### Scenario: Repetición de una reconciliación completada
- **WHEN** se repite la autorización sobre el mismo intento ya reconciliado sin cambios contradictorios
- **THEN** se conserva el registro original y no se crean invocaciones, nuevas intervenciones ni ordinales adicionales

### Requirement: Continuación con presupuesto conservado tras intervención
Una reconciliación válida SHALL permitir la clasificación `confirmed_interruption` sin convertirla en terminación observada. El intento SHALL conservar su ordinal consumido; solo si queda presupuesto SHALL programarse otro UUID en la misma cadena, con entrada y semilla estables. La intervención SHALL NOT reiniciar el circuito ni contarse como invocación nueva, proceso fallido observado o recuperación funcional.

#### Scenario: Presupuesto disponible
- **WHEN** se reconcilia el primer intento de una cadena con límite de tres invocaciones
- **THEN** quedan dos intentos disponibles y la siguiente invocación usa ordinal dos y otro UUID; el intento reconciliado permanece rechazado

#### Scenario: Presupuesto agotado tras interrupción
- **WHEN** el intento interrumpido ya consumió el último ordinal permitido
- **THEN** se conserva la intervención pero la cadena queda agotada sin autorizar otra invocación

#### Scenario: Archivo de restos confirmado
- **WHEN** se va a ejecutar el siguiente intento y existen restos parciales propios incluidos en el inventario de la intervención
- **THEN** se conservan y verifican mediante el journal de archivo antes del lanzamiento, sin aceptarlos como resultado válido

#### Scenario: Salidas posteriores a la confirmación
- **WHEN** aparecen o cambian archivos después de la intervención, o su archivo contradice el inventario confirmado
- **THEN** se bloquea su atribución y traslado automáticos sin sobrescribirlos ni lanzar otro proceso

### Requirement: Reinicio explícito del corte del lote
El orquestador SHALL ofrecer `reset-circuit <run_root>` como inspección sin escrituras por defecto. Aplicar SHALL exigir `--apply`, `--confirm-stopped` y un `--reason` no vacío. La operación SHALL limitarse al circuito y a las cadenas detenidas por él, y SHALL NOT lanzar MATLAB, aceptar renders, modificar salidas públicas ni retirar locks.

#### Scenario: Inspección de un circuito abierto
- **WHEN** se ejecuta `reset-circuit` sin `--apply` sobre un lote con el corte persistido
- **THEN** se muestran el estado del circuito, el límite, la racha, y cada cadena afectada con su presupuesto consumido y restante y su estado siguiente, sin modificar archivos ni crear un lock

#### Scenario: Confirmaciones insuficientes
- **WHEN** se solicita aplicar sin `--confirm-stopped` o sin un motivo no vacío
- **THEN** se rechaza la operación sin publicar registro ni modificar el circuito o las cadenas

#### Scenario: Circuito cerrado
- **WHEN** se ejecuta el comando sobre un lote cuyo circuito no está abierto o no existe
- **THEN** se informa que no hay corte que reiniciar y no se escribe ningún archivo, con o sin `--apply`

### Requirement: Guardas de seguridad del reinicio del corte
Aplicar el reinicio SHALL requerir exclusión de escritor y una consulta local satisfactoria de posibles escritores sin candidatos activos. Un `static_writer.lock` existente, una consulta fallida o no verificable, una cadena en ejecución o con intento reservado sin terminación observada, o evidencia de circuito, cadena u outcome contradictoria SHALL bloquearlo sin escrituras.

#### Scenario: Escritor activo o consulta incierta
- **WHEN** se detecta MATLAB/RAVEN u otro orquestador potencialmente escritor, o no puede obtenerse un inventario de procesos válido
- **THEN** no se aplica el reinicio, incluso con `--confirm-stopped`

#### Scenario: Lock pendiente
- **WHEN** existe `static_writer.lock`
- **THEN** se bloquea el reinicio sin romper el lock por antigüedad

#### Scenario: Terminación no observada
- **WHEN** alguna cadena del lote está `running` o su último intento no está resuelto
- **THEN** se bloquea el reinicio y se indica usar primero `reconcile-static` para esa cadena

### Requirement: Reprogramación acotada de cadenas detenidas por el corte
Al aplicar el reinicio, cada cadena cuyo último intento sea un fallo de proceso no recuperado resuelto con `retry_decision=circuit_open` SHALL pasar a `scheduled` si su ordinal es menor que `max_attempts`, o a `exhausted` en caso contrario. El reinicio SHALL NOT renovar presupuesto, crear ordinales ni modificar cadenas cuyo último intento fue aceptado o que estén en otro estado terminal.

#### Scenario: Fallo con presupuesto disponible
- **WHEN** el último intento de una cadena falló en el ordinal uno con límite de tres invocaciones y se resolvió como `circuit_open`
- **THEN** la cadena queda `scheduled` y la siguiente invocación usa el ordinal dos y otro UUID; el intento fallido sigue rechazado y visible

#### Scenario: Fallo en el último ordinal
- **WHEN** el último intento fallido consumió el ordinal máximo permitido y se resolvió como `circuit_open`
- **THEN** la cadena queda `exhausted` sin autorizar otra invocación

#### Scenario: Intento aceptado resuelto durante el corte
- **WHEN** una invocación ya iniciada fue aceptada después de abrirse el corte y su cadena registró `circuit_open`
- **THEN** su cadena, su outcome y sus salidas no se modifican y la reanudación la reutiliza conforme a `static-render-resumption`

### Requirement: Evidencia auditable del reinicio del corte
El reinicio SHALL publicar, antes de cualquier otra escritura, un registro versionado con operador, fecha, motivo, preimágenes exactas y huellas del circuito y de cada cadena y outcome que modifica, y su estado siguiente. El circuito anterior SHALL archivarse sin borrarlo. El nuevo circuito SHALL iniciar con racha cero y `open: false`, y el historial anterior SHALL seguir visible.

#### Scenario: Reinicio aplicado
- **WHEN** se aplica el reinicio sobre un corte persistido
- **THEN** existen el registro de intervención, una copia archivada del circuito anterior con sus resoluciones y un circuito nuevo cerrado, y cada cadena modificada enlaza el registro

#### Scenario: Edición manual sin registro
- **WHEN** el circuito o una cadena `circuit_open` se editan a mano sin un registro de reinicio coherente
- **THEN** la reanudación no autoriza despachar esa cadena por la mera apariencia de su estado

#### Scenario: Corte posterior al reinicio
- **WHEN** tras un reinicio el lote vuelve a acumular el límite de fallos consecutivos
- **THEN** el circuito se abre de nuevo con el límite registrado en el circuito reiniciado, sin contar los fallos previos al reinicio

### Requirement: Publicación recuperable e idempotente del reinicio
El registro SHALL publicarse antes de modificar circuito, cadenas y outcomes; la reanudación SHALL exigir concordancia entre esas publicaciones y el registro. Repetir el reinicio con la misma evidencia SHALL ser idempotente, y una publicación incompleta SHALL poder completarse solo con el mismo registro, sin sobrescribir estados ajenos.

#### Scenario: Interrupción durante la publicación
- **WHEN** queda publicado el registro pero solo parte del circuito y de las cadenas
- **THEN** el render permanece bloqueado y repetir el reinicio completa únicamente esa misma publicación si la evidencia sigue siendo coherente

#### Scenario: Repetición de un reinicio completado
- **WHEN** se repite el reinicio sobre un lote ya reiniciado y sin un corte nuevo
- **THEN** se informa que no hay corte abierto y no se crean registros, archivos de circuito ni transiciones adicionales
