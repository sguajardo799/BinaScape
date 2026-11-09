# Spec Delta

## ADDED Requirements

### Requirement: Cierre coherente de cadenas conocidas antes de reutilizar
Para una variante con cadena conocida, el orquestador SHALL exigir una resolución persistida coherente de cadena, outcome e índice antes de reutilizarla o entregarla a Clarity. Un outcome `committed` o un índice completo SHALL NOT bastar mientras falte el cierre. Las salidas históricas sin cadena SHALL conservar las comprobaciones vigentes, sin inventarles una cadena.

#### Scenario: Aceptación confirmada pero cadena pendiente
- **WHEN** el outcome tiene terminación observada y aceptación confirmada, pero la cadena del mismo intento permanece `running`
- **THEN** no se permite su reutilización ni entrega directa a Clarity hasta completar la finalización auditable o informar su bloqueo

#### Scenario: Cadena cerrada normalmente
- **WHEN** el outcome aceptado, la cadena resuelta y el índice son coherentes y las validaciones aplicables siguen siendo satisfactorias
- **THEN** la variante puede reutilizarse sin crear una reparación innecesaria

#### Scenario: Resultado histórico sin cadena
- **WHEN** se considera una salida histórica sin cadena ni evidencia de un intento pendiente conocido
- **THEN** se aplica la política legacy vigente sin exigirle un cierre de cadena retroactivo

### Requirement: Auditoría explícita de finalización de cadena
La finalización reparada SHALL conservar preimágenes exactas y un registro versionado que identifique su acción y vincule intento, cadena, estados y evidencia verificada. SHALL preservar los hechos del proceso, entradas y presupuesto. Un fallo de almacenamiento no registrado previamente SHALL NOT inventarse como error histórico; se registrará el estado incompleto observado.

#### Scenario: No existe persistence_error en el outcome
- **WHEN** la aceptación quedó confirmada y el fallo posterior impidió guardar la clasificación o el cierre de la cadena sin registrar la excepción
- **THEN** la reparación conserva el outcome previo y documenta la finalización incompleta, sin exigir ni fabricar `persistence_error` o `storage_failure`

#### Scenario: Proceso no cero previamente recuperado
- **WHEN** una aceptación `continue_with_warning` conserva código no cero observado y vuelve a superar el contrato `post_completion`
- **THEN** completar su cadena conserva código, error y advertencia de proceso y no los sustituye por un éxito limpio

#### Scenario: Nueva interrupción durante el cierre
- **WHEN** el journal está publicado pero solo parte de los estados de finalización se guardó
- **THEN** no se habilita reutilización ni Clarity; la siguiente llamada completa solo las transiciones del mismo registro si la evidencia sigue siendo válida

### Requirement: Lectura segura de formatos de finalización
Los lectores actualizados SHALL admitir journals de reparación versión 1 válidos y la nueva acción de finalización en journals versión 2, con outcomes reparados versión 2. SHALL rechazar versiones o acciones desconocidas y registros incompletos o contradictorios. La nueva acción SHALL NOT reescribir journals anteriores completos ni cambiar el esquema de constancias o cadenas.

#### Scenario: Lote con reparaciones de ambas generaciones
- **WHEN** coexisten reparaciones de aceptación versión 1 y finalizaciones de cadena versión 2 completas y coherentes
- **THEN** el lector actualizado valida ambas sin migrar ni invalidar las reparaciones anteriores

#### Scenario: Rollback a un lector incompatible
- **WHEN** un lector que solo entiende outcomes versión 1 o journals de reparación versión 1 encuentra la nueva finalización, parcial o completa
- **THEN** rechaza el resultado por esquema no soportado en lugar de aceptarlo solo porque el índice muestra archivos completos

#### Scenario: Edición manual del estado
- **WHEN** se marca una cadena como resuelta sin un outcome coherente ni un registro de finalización válido cuando corresponde
- **THEN** el cambio de etiqueta por sí solo no autoriza reutilización ni entrega a Clarity
