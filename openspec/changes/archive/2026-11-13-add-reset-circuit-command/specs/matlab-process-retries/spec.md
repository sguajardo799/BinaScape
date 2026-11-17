# Spec Delta

## ADDED Requirements

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
