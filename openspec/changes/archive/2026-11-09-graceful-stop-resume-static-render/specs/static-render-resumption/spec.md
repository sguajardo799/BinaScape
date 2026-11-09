# Spec Delta

## ADDED Requirements

### Requirement: Evaluación retomada de procesos observados
Con reanudación activa, el orquestador SHALL reconocer el último intento inequívoco con cadena `running`, resultado observado coherente y decisión/publicación pendiente. SHALL reevaluarlo con las reglas normales antes de reutilizar o reintentar, sin exigir aceptación previa ni confundirlo con terminación desconocida o cierre de aceptación ya confirmada.

#### Scenario: Caso reportado con código uno
- **WHEN** el outcome versión 1 registra código 1, terminación observada, decisión y commit pendientes y existe constancia del intento
- **THEN** se verifica la evidencia completa y solo se conserva el render como `continue_with_warning` si satisface el contrato post_completion; la constancia por existir no basta

#### Scenario: Fallo observado sin finalización acreditada
- **WHEN** el resultado pendiente tiene código no cero, falta constancia y la política funcional lo clasifica como fallo de proceso reintentable
- **THEN** se conserva ese fallo y se continúa únicamente con el siguiente ordinal autorizado, después de aislar sus restos propios

#### Scenario: Evaluación rechazada
- **WHEN** la validación encuentra integridad contradictoria, error determinista o rechazo acústico
- **THEN** no se declara aceptación y se conserva el bloqueo o continuación acústica que corresponda a la política normal, sin usar un reintento de proceso para eludirlo

### Requirement: Preflight global de continuaciones observadas
El orquestador SHALL validar todo el lote bajo exclusión de escritor antes de publicar evaluaciones retomadas. Solo un candidato acreditado SHALL autorizar levantar su veto `running`. Terminación desconocida, lock ajeno, identidad ambigua, circuito abierto ajeno al plan retomable o conflicto protegido SHALL bloquear sin reparaciones parciales ni despachos.

#### Scenario: Candidato válido y otra terminación desconocida
- **WHEN** existe un resultado observado recuperable y otro intento reservado o running sin terminación observada
- **THEN** no se publica ninguna recuperación ni se lanza trabajo; se explica el bloqueo y la vía de reconciliación manual para el intento desconocido

#### Scenario: Otra escena incompatible
- **WHEN** otra escena contradice entradas o procedencia protegida
- **THEN** se informa el conflicto antes de cambiar outcomes, cadenas, índices o circuito del candidato

#### Scenario: Reanudación desactivada
- **WHEN** resume_if_possible es false
- **THEN** no se aplica la evaluación retomada automáticamente ni se usa como autorización implícita de sobrescritura

#### Scenario: Plan interrumpido después del corte
- **WHEN** un plan válido quedó parcial tras publicar el corte que él mismo proyectaba
- **THEN** se permite terminar exclusivamente sus cierres auditados, manteniendo el circuito abierto y sin autorizar invocaciones nuevas

### Requirement: Relanzamiento tras parada sin repetir renders válidos
El relanzamiento normal después de una parada controlada SHALL reutilizar resultados aceptados y continuar solo trabajo autorizado pendiente, sin flags nuevos ni reconciliación manual para resultados observados coherentes. SHALL conservar entradas, recursos y salidas reutilizadas por bytes y mtime. Una caída forzada sin resultado observado SHALL mantener la reconciliación explícita existente.

#### Scenario: Segunda llamada tras parada limpia
- **WHEN** se relanza el mismo comando con reanudación activa y sin sobrescritura
- **THEN** se reutilizan los UUID aceptados, solo pendientes consumen nuevas invocaciones y no se reescriben sus artefactos protegidos

#### Scenario: Ventana cerrada o terminación forzada
- **WHEN** se fuerza la salida antes de observar o persistir el resultado de un proceso
- **THEN** no se promete reanudación automática ni se infiere finalización por ausencia de PID, logs, WAV o temporales
