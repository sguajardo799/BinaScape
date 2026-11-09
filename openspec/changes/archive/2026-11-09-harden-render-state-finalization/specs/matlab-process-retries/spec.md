# Spec Delta

## ADDED Requirements

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
