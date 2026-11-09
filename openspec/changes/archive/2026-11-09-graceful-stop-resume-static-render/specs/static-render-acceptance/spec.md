# Spec Delta

## ADDED Requirements

### Requirement: Auditoría de evaluación retomada
La evaluación retomada SHALL conservar preimágenes exactas, identidad, evidencia verificada y plan versionado antes de modificar estados activos. SHALL preservar código, error y tiempos observados, diferenciando cuándo se evaluó del momento en que terminó el proceso. Una reparación parcial SHALL NOT habilitar reutilización ni Clarity.

#### Scenario: Resultado observado con validación inicial pendiente
- **WHEN** el outcome conserva validación inicial no verificada aunque ya tenga código y fecha de fin
- **THEN** se conserva ese estado original como preimagen y se publica una evaluación nueva, sin fabricar un error histórico de almacenamiento ni sustituir el fallo del proceso

#### Scenario: Interrupción durante reparación
- **WHEN** se interrumpe cualquiera de las publicaciones de outcome, índice, circuito o cadena
- **THEN** se retoma el mismo registro y solo sus transiciones previstas, sin contar otra evaluación ni habilitar una reparación incompleta

### Requirement: Resolución auditable sin doble contabilidad
Una evaluación retomada SHALL verificar cualquier resolución existente del UUID y preservarla si coincide. Si falta, SHALL añadirla una sola vez en el orden de recuperación registrado, conservando el prefijo histórico y sin presentarla como resolución histórica reconstruida. Contradicciones o duplicados SHALL bloquear; ni circuito ni presupuesto SHALL reiniciarse.

#### Scenario: Circuito sin el intento observado
- **WHEN** un resultado observado pendiente supera preflight y aún no figura en el circuito
- **THEN** se audita y aplica su resolución como nueva resolución administrativa en recuperación, sin inventar una posición anterior ni eliminar resoluciones posteriores de otros UUID

#### Scenario: Resolución ya publicada antes de otra caída
- **WHEN** el circuito ya tiene exactamente la resolución prevista por el registro de recuperación
- **THEN** solo se completan las publicaciones restantes, sin incrementar rachas, reiniciarlas de nuevo ni duplicar el UUID

#### Scenario: Corte durante recuperación de varios resultados
- **WHEN** una resolución administrativa abre el circuito mientras se cierran resultados ya observados
- **THEN** se termina de registrar esos resultados sin habilitar despachos y el corte permanece enclavado aunque llegue después una aceptación

### Requirement: Compatibilidad explícita de evaluaciones retomadas
Los lectores actualizados SHALL distinguir reparaciones anteriores de aceptación, finalización de cadena y evaluación retomada, admitiendo sus formatos válidos sin migrarlos. Los formatos nuevos SHALL ser rechazables por lectores incompatibles y las acciones desconocidas SHALL bloquear. El rollback SHALL conservar auditoría y no rebajar versiones ni editar running manualmente.

#### Scenario: Lote con tres generaciones
- **WHEN** coexisten journals válidos de aceptación, finalize_chain y evaluación retomada
- **THEN** se validan con sus reglas respectivas y no se reescriben registros completos anteriores

#### Scenario: Lector anterior frente a evaluación nueva
- **WHEN** un lector que solo admite outcomes hasta versión 2 o journals hasta versión 2 encuentra una evaluación retomada
- **THEN** la rechaza por esquema no soportado, aunque el índice parezca completo
