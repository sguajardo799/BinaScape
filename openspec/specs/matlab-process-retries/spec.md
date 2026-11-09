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
La CLI y el resumen SHALL distinguir trabajos renderizados, invocaciones reales, reintentos, procesos fallidos, variantes aceptadas tras reintento, recuperaciones `post_completion`, variantes reanudadas y reparaciones de persistencia confirmadas en la llamada actual. Los fallos anteriores SHALL seguir visibles después de un éxito posterior y el error terminal SHALL enlazar la cadena y su evidencia disponible.

#### Scenario: Trabajo aceptado en el tercer intento
- **WHEN** una variante se acepta después de dos fallos de proceso
- **THEN** se informa un trabajo, tres invocaciones, dos reintentos y dos procesos fallidos, sin presentarlo como éxito limpio ni recuperación `post_completion`

#### Scenario: Reanudación completa después de reparación
- **WHEN** una llamada repara un intento válido y reutiliza todas las variantes del lote
- **THEN** muestra cero invocaciones y reintentos nuevos, una reparación de persistencia y las variantes reanudadas; el error histórico sigue en la auditoría

#### Scenario: Llamada posterior a reparación ya confirmada
- **WHEN** otra llamada reutiliza el mismo lote sin nuevas reparaciones
- **THEN** el contador de reparaciones de esa llamada es cero y no vuelve a contabilizarse el intento como proceso ejecutado

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

### Requirement: Error terminal explícito en render-static
La CLI del orquestador SHALL mostrar en stderr el tipo y mensaje de la causa terminal, rutas afectadas y contexto de escena, variante, intento, cadena y evidencia disponible. SHALL preservar causas originales y errores secundarios sin atribuir una causa ambiental no demostrada. SHALL mantener código de salida no cero y esta explicación con `--no-progress`, incluso si el fallo sucede durante preflight.

#### Scenario: Fallo de guardado después de proceso satisfactorio
- **WHEN** Windows deniega reemplazar `outcome.json.tmp` por `outcome.json`
- **THEN** la CLI muestra `PermissionError`, el mensaje `WinError 5`, las rutas y el intento implicado, informa que falló la persistencia y no lo presenta como fallo MATLAB

#### Scenario: Error de proceso seguido de error de almacenamiento
- **WHEN** existe un error original del backend y además falla la publicación funcional
- **THEN** se muestran ambas causas y las rutas de evidencia disponibles sin ocultarlas detrás del resumen

#### Scenario: Fallo antes de tener un intento
- **WHEN** el preflight bloquea por incompatibilidad o historial no reparable y no existe un intento nuevo
- **THEN** se muestra tipo, mensaje y contexto conocido sin inventar identificadores, imprimir solo un resumen de éxito ni requerir barra de progreso

### Requirement: Reparación de persistencia sin reintento de proceso
Una reparación confirmada SHALL conservar UUID, ordinal y presupuesto consumido del intento. SHALL NOT incrementar invocaciones, reintentos ni variantes aceptadas tras reintento. La clasificación `storage_failure` SHALL continuar excluida de nuevos reintentos de proceso; solo una reparación verificada conforme a `static-render-acceptance` podrá resolver su veto de publicación.

#### Scenario: Reparación con presupuesto de una invocación
- **WHEN** el único intento permitido produjo un render válido con fallo exclusivo de publicación y es reparable
- **THEN** puede confirmarse y reutilizarse sin renovar presupuesto ni lanzar otro proceso

#### Scenario: Fallo de proceso no recuperable
- **WHEN** un intento dejó salidas inválidas, evidencia contradictoria o una cadena agotada por fallos de proceso
- **THEN** la reparación de persistencia no lo habilita ni crea otra cadena para repetirlo

### Requirement: Reemplazo acotado de metadatos ante bloqueos Windows
El orquestador SHALL reintentar únicamente el reemplazo de temporales completos y cerrados de outcome, cadena, circuito, journal de reparación y resumen estático ante `winerror` 5, 32 o 33. SHALL realizar como máximo cinco intentos con esperas de 50, 100, 200 y 400 ms. Otros errores SHALL propagarse inmediatamente. La política existente del índice SHALL conservarse sin reintentos anidados.

#### Scenario: Bloqueo transitorio liberado
- **WHEN** el reemplazo de outcome falla dos veces con `winerror=5` y el tercer intento tiene éxito
- **THEN** se publica el mismo contenido preparado, se continúa el flujo normal y no se repite la simulación ni la serialización por esa espera

#### Scenario: Error no elegible
- **WHEN** el reemplazo falla por disco lleno, ruta inexistente o un `PermissionError` sin código Windows elegible
- **THEN** se propaga el error sin espera ni repetición de la operación

#### Scenario: Escritura del temporal fallida
- **WHEN** falla la creación, serialización, escritura, cierre o sincronización del temporal antes del reemplazo
- **THEN** esta política no repite esa fase ni interpreta un temporal parcial como publicación satisfactoria

#### Scenario: Índice con tolerancia existente
- **WHEN** se actualiza el índice JSONL bajo su política de reemplazo actual
- **THEN** no se le aplica un segundo bucle de reintentos ni se amplía implícitamente su presupuesto

### Requirement: Error visible al agotar reemplazos
Al agotar los reintentos de reemplazo, el orquestador SHALL conservar tipo, código, mensaje y rutas de la excepción de almacenamiento y añadir el número de intentos y el límite de espera aplicado. SHALL abortar la publicación sin declarar éxito ni atribuir una causa ambiental no acreditada; el resto de la evidencia funcional SHALL permanecer disponible.

#### Scenario: Denegación persistente
- **WHEN** el reemplazo falla en los cinco intentos elegibles
- **THEN** la CLI informa el error original y el agotamiento incluso con `--no-progress`, sin iniciar procesos adicionales por ese fallo

#### Scenario: Resultado desconocido después de una interrupción
- **WHEN** el orquestador termina antes de observar el resultado de una publicación
- **THEN** la siguiente llamada inspecciona estados y evidencia mediante el protocolo de reparación, sin suponer éxito por el número de esperas ni por la presencia del temporal

### Requirement: Cierre sin recontabilizar el circuito
Completar una cadena aceptada SHALL exigir exactamente una resolución persistida del mismo intento en el circuito, con clasificación `accepted` y `process_failed=false`. SHALL preservar la resolución y el historial del circuito sin volver a aplicarlos, cambiar su orden, reiniciar rachas o renovar presupuesto. La ausencia, duplicación o contradicción de esa evidencia SHALL bloquear este cierre automático.

#### Scenario: Resolución publicada antes del fallo de cadena
- **WHEN** el circuito ya contiene la aceptación del intento y solo falta publicar su clasificación o cierre de cadena
- **THEN** se completa el cierre sin añadir otra resolución ni provocar el error de intento ya contado

#### Scenario: Resolución ausente o contradictoria
- **WHEN** el circuito no registra ese intento, lo duplica o registra una clasificación distinta
- **THEN** se informa el conflicto sin reconstruir el orden de resolución ni insertar retroactivamente una aceptación

#### Scenario: Presupuesto de una invocación
- **WHEN** el intento que debe cerrarse consumió la única invocación permitida y cumple las guardas de finalización
- **THEN** se permite cerrar y reutilizar el mismo UUID sin abrir otra cadena o consumir un ordinal nuevo

### Requirement: Separación entre espera de escritura y reintento de proceso
Los reintentos de reemplazo SHALL NOT incrementar invocaciones, reintentos de proceso, fallos de proceso ni variantes aceptadas tras reintento. Una escritura que finalmente se publica en la misma llamada SHALL NOT contarse como reparación entre llamadas; solo una finalización auditable confirmada durante la reanudación incrementará el contador de reparaciones correspondiente.

#### Scenario: Reemplazo recuperado dentro de la ejecución
- **WHEN** el render se acepta y un reemplazo elegible termina publicándose después de una espera acotada
- **THEN** conserva una invocación y cero reintentos de proceso por ese bloqueo, sin incrementar `persistencias_reparadas`

#### Scenario: Cierre completado en una llamada posterior
- **WHEN** se confirma una finalización auditable de un intento ejecutado en una llamada anterior
- **THEN** se cuenta una reparación y la reutilización pertinente, pero ninguna invocación ni recuperación `post_completion` nueva por el mero cierre

### Requirement: Parada cooperativa del render estático
El orquestador SHALL interpretar `Ctrl+C` durante `render-static` como solicitud de parada controlada: no despachar nuevas invocaciones y permitir terminar las ya iniciadas. SHALL evaluar y persistir todos los resultados observados antes de salir, manteniendo exclusión de escritor durante el drenaje. La solicitud SHALL NOT matar procesos de render ni acreditar su terminación.

#### Scenario: Canónicas paralelas activas
- **WHEN** se solicita parar con dos canónicas en ejecución y otras pendientes
- **THEN** las dos terminan y se resuelven sin despachar una tercera, aun si sus resultados llegan fuera de orden

#### Scenario: Render secuencial y variantes adicionales
- **WHEN** se solicita parar durante una invocación secuencial o una variante no canónica
- **THEN** se persiste ese resultado y no se inicia la siguiente variante ni se envía Clarity

#### Scenario: Solicitudes repetidas
- **WHEN** el operador vuelve a pulsar Ctrl+C durante el drenaje
- **THEN** la solicitud sigue siendo idempotente y no interrumpe publicaciones ni se convierte en cancelación inmediata

#### Scenario: Señal de consola y backend
- **WHEN** el padre recibe Ctrl+C en Windows o POSIX mientras ejecuta RAVEN o Pyroomacoustics
- **THEN** la parada no se propaga incidentalmente al render activo y el padre observa y conserva su resultado real

### Requirement: Continuaciones diferidas tras parada
Una solicitud de parada SHALL impedir lanzar reintentos de proceso y nuevas realizaciones T30 en esa llamada. El orquestador SHALL conservar la continuación autorizada para el próximo relanzamiento, con presupuesto, espera, semilla e historial existentes. SHALL NOT registrar los trabajos no iniciados como invocaciones ni abrir otro presupuesto por haber parado.

#### Scenario: Fallo reintentable durante drenaje
- **WHEN** un proceso drenado falla y su política permite otro intento
- **THEN** queda programado para la siguiente llamada, sin ejecutar el nuevo UUID durante la parada ni omitir su espera pendiente

#### Scenario: Rechazo acústico durante drenaje
- **WHEN** una canónica drenada recibe rechazo T30 con remuestreo aún permitido
- **THEN** se conserva su rechazo y la continuación acústica para el próximo comando, sin aceptarla ni remuestrear durante el drenaje

#### Scenario: Presupuesto o circuito agotado
- **WHEN** el resultado drenado agota la cadena o abre el circuito
- **THEN** el relanzamiento mantiene ese bloqueo y la parada no renueva límites ni reinicia rachas
