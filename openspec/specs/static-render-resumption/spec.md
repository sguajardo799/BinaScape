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

### Requirement: Compatibilidad de RPF derivados aislados
Para variantes con aislamiento RAVEN registrado, la reanudación SHALL verificar la procedencia y las huellas del RPF original, el derivado y sus entradas privadas antes de declararlos compatibles. SHALL aceptar solo transformaciones técnicas autorizadas, sin ignorar globalmente `base_rpf_file` en la comparación de manifiestos ni modificar la entrada capturada por la cadena.

#### Scenario: Runtime aislado compatible
- **WHEN** el runtime contiene un RPF privado cuya procedencia acredita el original vigente y exclusivamente las transformaciones de aislamiento previstas
- **THEN** se permite continuar la validación de reutilización sin confundir la diferencia de ruta con un cambio de escena

#### Scenario: Recurso ausente o alterado
- **WHEN** falta el RPF privado, su registro de procedencia o una entrada privada requerida, o no coincide una huella protegida
- **THEN** se informa un conflicto con ruta y motivo antes de escribir sobre artefactos protegidos o lanzar MATLAB
- **AND** no se regenera silenciosamente el archivo ni se acepta por equivalencia de nombres

#### Scenario: Cambio de RPF original
- **WHEN** cambia el contenido del RPF original sin cambiar su nombre
- **THEN** el runtime derivado anterior no se considera compatible por su mera existencia ni por conservar la misma semilla

### Requirement: Recursos reutilizados no destructivos
Una variante aislada reutilizable SHALL conservar bytes y fechas de modificación de su RPF derivado, entradas privadas y registro de procedencia, además de los archivos protegidos por el contrato existente. La preparación SHALL comprobar antes de materializar recursos y SHALL NOT crear otro intento o reconstruir archivos como efecto de una reanudación completa.

#### Scenario: Reanudación completa aislada
- **WHEN** todas las variantes y sus recursos privados son compatibles y válidos
- **THEN** se producen cero invocaciones MATLAB y se mantienen intactos los manifiestos, fuentes preparadas, recursos privados, WAV y sidecars existentes

#### Scenario: Reanudación parcial
- **WHEN** algunas variantes aisladas son reutilizables y otras están pendientes con historial que permite ejecutarlas
- **THEN** solo las pendientes materializan recursos faltantes autorizados y ejecutan MATLAB, sin alterar los recursos de las reutilizadas

### Requirement: Procedencia histórica sin certificación retroactiva
Las salidas históricas sin registro de aislamiento SHALL conservar las comprobaciones estrictas de compatibilidad, artefactos e historial ya existentes. La reanudación SHALL distinguir esa procedencia histórica de una ejecución aislada verificada, sin crear evidencia de aislamiento ficticia ni regenerar resultados automáticamente; una regeneración SHALL requerir un nuevo run o la autorización de sobrescritura existente.

#### Scenario: Resultado histórico reutilizable
- **WHEN** un resultado histórico sin aislamiento supera las validaciones vigentes y se solicita reanudar sin sobrescribir
- **THEN** se conserva sin invocar MATLAB ni reescribir su runtime, y el resumen identifica su aislamiento como histórico no verificado
- **AND** no se afirma que sus materiales efectivos hayan sido exclusivos durante la ejecución original

#### Scenario: Lote mixto
- **WHEN** se reutilizan variantes históricas y se ejecutan variantes pendientes con aislamiento
- **THEN** las nuevas usan recursos privados y el resumen distingue ambas procedencias sin certificar como aislada toda la tripleta

#### Scenario: Regeneración explícita
- **WHEN** el usuario solicita repetir un resultado histórico en otro run o autoriza su sustitución conforme al contrato existente
- **THEN** la nueva ejecución usa aislamiento y conserva la evidencia histórica según las reglas de archivo y sobrescritura vigentes
