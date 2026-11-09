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

### Requirement: Reanudación automática tras fallo de publicación
Con `resume_if_possible=true`, `render-static` SHALL detectar y reparar aceptaciones no persistidas recuperables según `static-render-acceptance` antes de despachar nuevas variantes. SHALL hacerlo sin flags adicionales ni exigir `overwrite_existing=true`. Con reanudación desactivada SHALL NOT aplicar esa reparación automática.

#### Scenario: Segunda llamada sobre lote detenido por WinError 5
- **WHEN** se repite el mismo comando con entradas compatibles, el bloqueo de almacenamiento ya no existe y el intento cumple el contrato de reparación
- **THEN** se confirma y reutiliza ese intento, se reutilizan las variantes ya aceptadas y solo las pendientes autorizadas invocan el backend

#### Scenario: Recuperación de evidencia anterior a este cambio
- **WHEN** un outcome versión 1 registra `storage_failure` y `acceptance_persistence_failed` con resultado de proceso observado, cadena coherente y constancia íntegra, pero no tiene campos nuevos de reparación
- **THEN** se permite verificarlo y repararlo mediante la misma auditoría sin fabricar evidencia de ejecución pasada

#### Scenario: Índice ausente o rezagado
- **WHEN** la evidencia recuperable identifica inequívocamente el intento y sus salidas y el índice falta o conserva el estado anterior sin referencias contradictorias
- **THEN** se reconstruye su registro desde evidencia verificada, sin tratarlo como resultado legacy ni reemplazar referencias a otro intento

#### Scenario: Reanudación desactivada
- **WHEN** se invoca `render-static` con `resume_if_possible=false`
- **THEN** no se ejecuta reparación automática y se aplican las reglas vigentes de conflicto o sobrescritura explícita

### Requirement: Reparación subordinada al preflight y exclusión de escritor
La reparación SHALL ejecutarse bajo exclusión de escritor, después de comprobar compatibilidad del lote y ausencia de vetos de integridad o procesos no resueltos. SHALL NOT retirar locks, resolver terminaciones no observadas, reiniciar circuitos ni renovar presupuestos. Si no puede verificarse su seguridad SHALL bloquearse con motivo explícito.

#### Scenario: Otra cadena conserva un proceso no resuelto
- **WHEN** hay un candidato reparable pero el lote contiene un intento reservado o en ejecución sin terminación observada
- **THEN** no se publica la reparación ni se despachan renders; sigue siendo necesaria la intervención prevista por el contrato existente

#### Scenario: Conflicto en otra escena del lote
- **WHEN** el preflight detecta entradas incompatibles o evidencia contradictoria antes de reparar un candidato
- **THEN** se informa el conflicto sin modificar plan, índice, outcomes ni archivos protegidos

#### Scenario: Circuito o lock persistente
- **WHEN** el circuito del lote está abierto o no puede adquirirse el lock de escritor
- **THEN** la reparación no rompe ni reinicia ese estado y no permite nuevos despachos

### Requirement: Reparación no destructiva y fallo recurrente visible
La reparación SHALL conservar bytes y fechas de modificación de manifiestos, fuentes preparadas, recursos privados, constancias, WAV y sidecars. Si vuelve a fallar la publicación SHALL detener nuevos despachos, conservar una reparación retomable y explicar el nuevo error sin declarar la variante aceptada.

#### Scenario: Comparación de archivos antes y después
- **WHEN** se repara una aceptación válida y se completa la reanudación
- **THEN** solo cambian metadatos de estado, auditoría y resumen; no se regeneran ni archivan entradas o salidas protegidas

#### Scenario: Persisten los permisos denegados
- **WHEN** el almacenamiento vuelve a impedir completar la reparación
- **THEN** la CLI finaliza con error explícito, no lanza el backend y la siguiente llamada puede retomar la misma reparación si la evidencia continúa siendo válida

### Requirement: Finalización automática con terminación acreditada
Con `resume_if_possible=true`, `render-static` SHALL detectar el último intento inequívoco de una cadena con cierre pendiente y completar su finalización si existe aceptación confirmada, terminación observada, constancia íntegra y validación funcional completa compatible. SHALL permitirlo sin sobrescritura, sin flags nuevos y sin invocar el backend para ese intento.

#### Scenario: Segunda llamada tras fallo al guardar clasificación
- **WHEN** una cadena permanece `running`, su outcome versión 1 está `committed` y aceptado con código cero observado, y la clasificación no llegó a publicarse
- **THEN** el preflight revalida el intento y, si cumple todas las guardas, publica su finalización auditable antes de reutilizarlo y continuar las pendientes

#### Scenario: Clasificación publicada pero cadena sin cerrar
- **WHEN** el outcome ya contiene clasificación aceptada y decisión terminal coherentes pero la escritura de la cadena no se completó
- **THEN** se permite completar el mismo cierre sin requerir repetir el proceso ni atribuir éxito a un temporal

#### Scenario: Resultado no aceptable
- **WHEN** falta constancia, el outcome no está confirmado, T30 canónico es rechazado o la evidencia contradice identidad, entradas o artefactos
- **THEN** esta finalización automática no acepta el intento ni programa otro proceso para eludir el rechazo

### Requirement: Excepción acreditada al veto de cadena running
El preflight SHALL levantar el veto de `running` únicamente para candidatos de finalización verificados positivamente. SHALL comprobar todo el lote bajo exclusión de escritor antes de publicar reparaciones. Un lock, circuito abierto, conflicto o intento sin terminación observada SHALL bloquear la operación sin cambiar estados protegidos ni inferir terminación por ausencia de PID.

#### Scenario: Otro intento tiene terminación desconocida
- **WHEN** un candidato es verificable pero otra cadena conserva una invocación no resuelta sin resultado observado
- **THEN** no se publican reparaciones ni se lanzan pendientes; el contrato de reconciliación manual de esa invocación se conserva

#### Scenario: Running sin acreditación suficiente
- **WHEN** una cadena está `running` y solo existen WAV, mensajes de éxito, temporales o ausencia de procesos en el inventario local
- **THEN** se mantiene el bloqueo y no se interpreta ese estado como un cierre reparable

#### Scenario: Conflicto en otra escena o lock persistente
- **WHEN** el preflight global detecta incompatibilidad en otra escena o no puede verificar exclusión de escritor
- **THEN** no modifica outcomes, cadenas, índices ni evidencia de reparación antes de informar el conflicto

### Requirement: Finalización no destructiva y contabilidad conservada
La finalización SHALL conservar bytes y fechas de modificación de entradas, recursos privados, constancias, WAV y sidecars. SHALL contabilizarse una vez en `persistence_repairs` y `persistencias_reparadas` de la llamada que la confirma, además de la reutilización pertinente, sin aumentar invocaciones, reintentos de proceso ni presupuesto consumido.

#### Scenario: Run mixto después del cierre
- **WHEN** una llamada completa un cierre reparable, reutiliza resultados ya aceptados y ejecuta variantes pendientes autorizadas
- **THEN** cuenta una reparación de persistencia y solo las pendientes aumentan las invocaciones; los archivos protegidos anteriores conservan contenido y mtime

#### Scenario: Repetición del comando tras cierre completo
- **WHEN** se repite el comando sobre el mismo intento ya finalizado sin cambios de evidencia
- **THEN** no se crea otra reparación ni un UUID nuevo y su contador de reparaciones en esa llamada es cero

#### Scenario: Reanudación desactivada
- **WHEN** `resume_if_possible=false`
- **THEN** no se ejecuta esta finalización automática y se conservan las reglas vigentes de conflicto y sobrescritura explícita

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
