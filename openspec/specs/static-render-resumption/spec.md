# static-render-resumption Specification

## Purpose

Definir la reanudación no destructiva propuesta del render estático, preservando las realizaciones existentes compatibles y validando su evidencia antes de reutilizar resultados.

## Requirements

### Requirement: Preflight compatible y no destructivo
El orquestador SHALL validar la compatibilidad de los manifiestos existentes y del plan antes de modificar artefactos del lote. Con `resume_if_possible=true` y `overwrite_existing=false`, SHALL conservar los manifiestos compatibles y generar solo los ausentes; un manifiesto corrupto o incompatible SHALL producir un conflicto explícito sin sobrescribirlo.

#### Scenario: Combinación de opciones de reanudación protegida
- **WHEN** se relanza un lote interrumpido con reanudación activa, sobrescritura desactivada y manifiestos compatibles existentes
- **THEN** la preparación no falla por su mera existencia y conserva sus bytes y fechas de modificación

#### Scenario: Conflicto detectado antes de escrituras
- **WHEN** un manifiesto existente es ilegible, contiene claves duplicadas, valores no finitos, identidad incorrecta o entradas incompatibles
- **THEN** se informa la ruta y el motivo antes de modificar el plan, índice, audio preparado o salidas existentes

### Requirement: Compatibilidad verificable de realizaciones
El orquestador SHALL contrastar configuración efectiva, identidad, geometría, fuentes, receptor y parámetros funcionales de cada realización conservada. SHALL respetar el intento de muestreo persistido tras rechazo T30 y no equiparar diferencias de contabilidad del lote a cambios acústicos. Los lotes anteriores sin registro de configuración SHALL requerir verificación conservadora, no confianza por existencia.

#### Scenario: Realización posterior a rechazo acústico
- **WHEN** la escena aceptada corresponde a un intento de muestreo posterior compatible con la configuración
- **THEN** se conserva esa realización y su historial, sin sustituirla por la primera propuesta determinista

#### Scenario: Configuración acústica modificada
- **WHEN** cambia una entrada funcional o no puede acreditarse compatibilidad bajo sobrescritura desactivada
- **THEN** se bloquea la reutilización con un conflicto y no se mezclan realizaciones de configuraciones distintas

#### Scenario: Lote histórico sin registro del plan
- **WHEN** faltan los nuevos registros del plan pero los manifiestos y realizaciones pueden verificarse contra la configuración vigente
- **THEN** se permite reanudar después de esa verificación sin alterar los manifiestos existentes ni inventar procedencia histórica

### Requirement: Reutilización estricta sin escrituras sobre entradas
La reanudación SHALL aplicar la validación funcional e historial de `static-render-acceptance` antes de reutilizar cada variante. Una variante válida SHALL reutilizarse sin invocar MATLAB ni reescribir su manifiesto de escena, runtime, audio preparado, WAV o metadatos. La actualización de índices y resúmenes SHALL permanecer separada de esos archivos protegidos.

#### Scenario: Reinicio de un lote parcialmente completado
- **WHEN** existen variantes válidas y otras sin salidas
- **THEN** solo se invoca MATLAB para las pendientes y los archivos protegidos de las reutilizadas mantienen bytes y fechas de modificación

#### Scenario: Reanudación completa
- **WHEN** todas las variantes siguen siendo válidas y compatibles
- **THEN** se registran como reutilizadas, no se crean intentos nuevos y el número de invocaciones es cero

#### Scenario: Audio preparado alterado
- **WHEN** una fuente preparada existente no corresponde a la entrada esperada
- **THEN** se informa el conflicto sin regenerarla silenciosamente antes de validar el render

### Requirement: Protección efectiva frente a sobrescritura
Con `overwrite_existing=false`, el orquestador SHALL bloquear la sustitución de salidas preexistentes no reutilizables y SHALL permitir ejecutar una variante sin salidas funcionales solo si su historial lo autoriza. El aislamiento de restos propios, incluidos los de un intento reconciliado, SHALL regirse por `matlab-process-retries` y su inventario verificado, sin autorizar cambios sobre archivos protegidos anteriores a la cadena.

#### Scenario: Fallo histórico sin salidas
- **WHEN** un intento histórico anterior a las cadenas persistentes falló, no existen WAV ni metadatos y el manifiesto es compatible
- **THEN** puede abrirse una cadena nueva conservando el fallo histórico, sin atribuirle retroactivamente un presupuesto de cadena

#### Scenario: Salida parcial preexistente
- **WHEN** existe un WAV truncado o un sidecar incompleto al iniciar la cadena y la sobrescritura está desactivada
- **THEN** se bloquea esa variante sin borrar, mover ni sobrescribir esos archivos

#### Scenario: Restos de una cadena reconciliada
- **WHEN** se retoma una cadena con reconciliación manual válida, presupuesto disponible y restos parciales propios incluidos en el inventario confirmado
- **THEN** solo pueden archivarse esos restos conforme a `matlab-process-retries` antes del nuevo intento, sin sustituir archivos protegidos ni aceptar las salidas interrumpidas

#### Scenario: Restos alterados después de la confirmación
- **WHEN** los archivos presentes o su archivo de conservación contradicen el inventario de la reconciliación
- **THEN** se bloquea la continuación sin trasladar ni sobrescribir los archivos desconocidos

#### Scenario: Reanudación desactivada
- **WHEN** `resume_if_possible=false` y existen artefactos que requerirían sustitución con `overwrite_existing=false`
- **THEN** se informa el conflicto sin presentar los artefactos como reutilizados ni sobrescribirlos

#### Scenario: Sobrescritura permitida y reanudación activa
- **WHEN** ambas opciones están activadas y una variante sigue siendo válida y compatible
- **THEN** se reutiliza, pues permitir sobrescritura no obliga a repetir resultados válidos

### Requirement: Reanudación subordinada al historial persistente
El orquestador SHALL comprobar los vetos del historial y presupuesto antes de ejecutar variantes pendientes. La ausencia de salidas o la retirada del lock SHALL NOT resolver un intento sin terminación observada ni renovar una cadena agotada. Una reconciliación manual válida SHALL permitir continuar únicamente la misma cadena conforme a `matlab-process-retries`, sin convertir el intento anterior en completado.

#### Scenario: Lock retirado con intento pendiente
- **WHEN** se retiró `static_writer.lock` pero permanece un intento en ejecución o reservado sin terminación observada ni reconciliación válida
- **THEN** se bloquea la reanudación antes de modificar el plan, índice o runtime y no se lanza MATLAB, aunque no existan WAV ni metadatos

#### Scenario: Cadena reconciliada con intentos disponibles
- **WHEN** el intento interrumpido tiene una reconciliación válida y queda presupuesto en su cadena
- **THEN** se continúa esa cadena con el ordinal siguiente y otro UUID, conservando entrada, semilla, historial y rechazo del intento anterior

#### Scenario: Reinicio de una cadena agotada sin salidas
- **WHEN** se relanza normalmente una cadena agotada cuyos WAV y metadatos están ausentes
- **THEN** la ausencia de archivos no autoriza abrir otra cadena ni reiniciar el presupuesto consumido

### Requirement: Contabilidad del lote independiente
El orquestador SHALL publicar estadísticas actuales de muestreo y render fuera de los manifiestos conservados, diferenciando escenas generadas de renders aceptados. SHALL preservar rechazos y fallos de muestreo conocidos y mantener identificadores deterministas al completar manifiestos ausentes.

#### Scenario: Actualización del resumen después de interrupción
- **WHEN** se recalcula el estado de un lote parcialmente renderizado
- **THEN** el resumen distingue generación y aceptación funcional sin reescribir manifiestos reutilizados ni borrar fallos históricos
