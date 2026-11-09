# Spec Delta

## ADDED Requirements

### Requirement: Versionado seguro de outcomes reparados
El orquestador SHALL publicar outcomes reparados con `schema_version=2` y exigir su registro de reparación válido antes de aceptarlos. SHALL seguir leyendo outcomes versión 1 y mantener versión 1 en intentos no reparados y constancias. Los lectores que solo admiten versión 1 SHALL rechazar outcomes reparados, sin degradarlos a resultados históricos reutilizables.

#### Scenario: Rollback durante reparación
- **WHEN** un lector anterior inspecciona un outcome reparado versión 2, parcial o confirmado
- **THEN** lo rechaza por esquema no soportado y no acepta el render mediante su registro del índice

#### Scenario: Lectura de lote mixto
- **WHEN** un lote combina outcomes sin reparar versión 1 con outcomes reparados versión 2
- **THEN** el lector actualizado valida ambas versiones con las guardas pertinentes y no exige una migración de los intentos versión 1

### Requirement: Confirmación posterior de aceptación no persistida
El orquestador SHALL permitir reparar una aceptación no persistida solo con terminación observada, código entero, constancia válida del mismo intento, entradas compatibles y validación funcional completa satisfactoria. SHALL exigir que el único impedimento sea la publicación del estado. Esta reparación SHALL NOT crear una invocación ni inferir éxito por existencia de archivos.

#### Scenario: Error de reemplazo de outcome después de render válido
- **WHEN** un intento con código cero observado y constancia íntegra conserva un fallo de persistencia y supera otra vez todas las comprobaciones, incluyendo T30 canónico y recursos RAVEN cuando apliquen
- **THEN** se permite confirmar posteriormente la aceptación del mismo UUID sin cambiar su resultado de proceso ni repetir el render

#### Scenario: Proceso no cero con aceptación recuperable
- **WHEN** el proceso observado terminó con código no cero, la publicación falló y se satisfacen las condiciones vigentes de recuperación `post_completion`
- **THEN** la reparación conserva código, error de proceso y advertencia `post_completion`, sin convertirlos en éxito limpio

#### Scenario: Evidencia insuficiente o contradictoria
- **WHEN** falta constancia o terminación observada, existe informe funcional contradictorio, cambia una huella protegida o falla la validación acústica
- **THEN** no se repara ni acepta el intento, no se reescriben salidas protegidas y se informa el motivo

### Requirement: Auditoría durable de reparación de persistencia
Antes de cambiar estados funcionales, el orquestador SHALL conservar preimágenes y un registro versionado de reparación vinculado al intento, cadena y huellas verificadas. SHALL preservar el error de persistencia original y el resultado del proceso. SHALL habilitar reutilización y entrega a Clarity solo tras confirmar la publicación coherente de outcome, índice y cadena.

#### Scenario: Índice publicado pero outcome no confirmado
- **WHEN** el índice muestra archivos completos y el outcome registra aceptación no persistida
- **THEN** el índice por sí solo no acredita aceptación; la reparación auditable debe completar los estados antes de reutilizar o entregar a Clarity

#### Scenario: Nueva interrupción durante la reparación
- **WHEN** la reparación se interrumpe después de publicar su registro y solo parte de los estados
- **THEN** la siguiente llamada puede completar únicamente esa misma reparación si las huellas y estados son compatibles; hasta entonces no se acepta el intento

#### Scenario: Reparación ya completada
- **WHEN** se repite la llamada después de una reparación coherente sin cambios en las entradas ni salidas
- **THEN** se reutiliza el intento sin otra reparación, UUID, invocación ni alteración de las preimágenes

## MODIFIED Requirements

### Requirement: Validación uniforme en ejecución y reutilización
Ejecución nueva, reanudación y entrega a Clarity SHALL aplicar la misma validación de artefactos pertinente al manifiesto vigente. Un rechazo o fallo no recuperado conocido SHALL NOT convertirse en completado por reiniciar, salvo la confirmación posterior auditable de aceptación no persistida definida en esta capacidad. Reanudar SHALL NOT inventar una invocación ni un éxito de proceso y SHALL preservar la advertencia de un intento recuperado.

#### Scenario: Reinicio después de recuperación
- **WHEN** se reanuda una variante recuperada previamente y sus artefactos siguen válidos
- **THEN** se reutiliza sin lanzar MATLAB y se conserva la asociación al proceso fallido recuperado

#### Scenario: Reinicio después de fallo no recuperado
- **WHEN** un intento fallido dejó archivos aparentemente completos sin constancia válida y se reinicia el experimento
- **THEN** no se acepta ese intento mediante la mera inspección de archivos ni sobrescribiendo su decisión con un registro planificado

#### Scenario: Salida histórica sin constancia
- **WHEN** una salida legacy no tiene fallo no recuperado conocido y se considera para reanudación
- **THEN** solo se reutiliza si supera la validación estricta y la correspondencia con el manifiesto vigente; no se inventa constancia ni resultado del proceso

#### Scenario: Archivo alterado antes del handoff
- **WHEN** un WAV o sidecar cambia después de haber sido indexado como válido
- **THEN** la revalidación bloquea su entrega a Clarity si ya no satisface el contrato, manteniendo la elegibilidad exclusiva de HRTF

#### Scenario: Reinicio después de fallo exclusivamente de publicación
- **WHEN** el historial conserva un fallo de persistencia que satisface la confirmación posterior auditable
- **THEN** solo esa reparación confirmada levanta el veto del historial; reiniciar o borrar el índice no lo levanta por sí mismo
