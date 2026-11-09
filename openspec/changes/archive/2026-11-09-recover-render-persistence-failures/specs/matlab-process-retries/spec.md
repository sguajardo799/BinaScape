# Spec Delta

## ADDED Requirements

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

## MODIFIED Requirements

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
