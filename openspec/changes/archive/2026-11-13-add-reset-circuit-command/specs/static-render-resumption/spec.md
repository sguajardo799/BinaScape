# Spec Delta

## MODIFIED Requirements

### Requirement: Reanudación subordinada al historial persistente
El orquestador SHALL comprobar los vetos del historial y presupuesto antes de ejecutar variantes pendientes. La ausencia de salidas o la retirada del lock SHALL NOT resolver un intento sin terminación observada ni renovar una cadena agotada. Una reconciliación manual válida SHALL permitir continuar únicamente la misma cadena conforme a `matlab-process-retries`, sin convertir el intento anterior en completado. Un corte del lote persistido SHALL bloquear la reanudación hasta un reinicio explícito válido conforme a `matlab-process-retries`; tras él, SHALL reutilizar las variantes aceptadas y despachar solo las pendientes o reprogramadas.

#### Scenario: Lock retirado con intento pendiente
- **WHEN** se retiró `static_writer.lock` pero permanece un intento en ejecución o reservado sin terminación observada ni reconciliación válida
- **THEN** se bloquea la reanudación antes de modificar el plan, índice o runtime y no se lanza MATLAB, aunque no existan WAV ni metadatos

#### Scenario: Cadena reconciliada con intentos disponibles
- **WHEN** el intento interrumpido tiene una reconciliación válida y queda presupuesto en su cadena
- **THEN** se continúa esa cadena con el ordinal siguiente y otro UUID, conservando entrada, semilla, historial y rechazo del intento anterior

#### Scenario: Reinicio de una cadena agotada sin salidas
- **WHEN** se relanza normalmente una cadena agotada cuyos WAV y metadatos están ausentes
- **THEN** la ausencia de archivos no autoriza abrir otra cadena ni reiniciar el presupuesto consumido

#### Scenario: Corte persistido sin reinicio
- **WHEN** se relanza un lote cuyo circuito está abierto, con reanudación activa y sobrescritura desactivada
- **THEN** se bloquea antes de modificar el plan, índice o runtime, se informa la ruta del circuito y se indica `reset-circuit` como intervención

#### Scenario: Reanudación tras reinicio del corte
- **WHEN** se relanza un lote después de un reinicio válido del corte
- **THEN** las variantes aceptadas se reutilizan sin invocar MATLAB ni archivar sus salidas, y solo se despachan las variantes sin cadena, las `scheduled` y las reprogramadas por el reinicio
