# Spec Delta

## MODIFIED Requirements

### Requirement: Recuperación limitada a finalización acreditada
El orquestador SHALL aceptar el render de un intento con código no cero observado solo con constancia válida del intento actual, validación completa satisfactoria y sin contradicción funcional. SHALL rechazar recuperación por fallo de lanzamiento, terminación no observada o evidencia funcional insuficiente. Una recuperación SHALL indicar fallo posterior a finalización acreditada sin afirmar una causa de cierre. Reintentar el proceso SHALL NOT constituir recuperación ni aceptación de ese intento.

#### Scenario: Salida no cero recuperable
- **WHEN** MATLAB termina con código no cero y la constancia y todas las validaciones son satisfactorias
- **THEN** se permite continuar sin repetir el render por ese código y se conserva advertencia persistente asociada al intento

#### Scenario: Error genuino o evidencia insuficiente
- **WHEN** no se lanza MATLAB, no se observa su terminación, falta constancia válida o hay un error funcional contradictorio
- **THEN** no se declara recuperación y el fallo original permanece accesible en el resultado o excepción del experimento

#### Scenario: Proceso correcto sin constancia
- **WHEN** MATLAB devuelve cero y los artefactos pasan las validaciones aplicables pero la constancia no está disponible
- **THEN** la ausencia de constancia por sí sola no convierte el render en fallido ni se interpreta como recuperación

#### Scenario: Nuevo intento después de fallo no recuperable
- **WHEN** un intento no es aceptable pero satisface la política de `matlab-process-retries` y produce posteriormente otro intento
- **THEN** el primero permanece rechazado y solo la evidencia y validación del nuevo intento pueden acreditar su resultado

#### Scenario: Reconciliación manual sin terminación observada
- **WHEN** una reconciliación válida confirma ausencia de escritores para un intento cuyo resultado de proceso no se observó
- **THEN** no se declara finalización ni recuperación funcional de ese intento, se mantienen `exit_code=null` y `termination_observed=false` y su render permanece rechazado

## ADDED Requirements

### Requirement: Aceptación independiente de la programación de reintentos
El orquestador SHALL persistir la decisión funcional de cada intento antes de programar el siguiente. El presupuesto de proceso SHALL NOT alterar validación, recuperación `post_completion`, elegibilidad Clarity ni límites T30. La constancia o los archivos de otro intento SHALL NOT acreditar uno nuevo, aunque la variante y la semilla coincidan. Una reconciliación manual SHALL NOT acreditar finalización ni aceptación del render.

#### Scenario: Restos completos sin finalización acreditada
- **WHEN** un intento fallido deja WAV y metadatos aparentemente completos pero sin constancia válida
- **THEN** no se acepta ni entrega a Clarity ese intento; otro intento solo se ejecuta conforme a su política y aislamiento

#### Scenario: Recuperación y T30
- **WHEN** existe constancia íntegra pero el control acústico canónico rechaza T30
- **THEN** se conserva el proceso observado y se aplica remuestreo acústico, no reintento de proceso para eludir el rechazo

#### Scenario: Nuevo intento tras reconciliación
- **WHEN** una reconciliación permite programar otra invocación con un UUID nuevo
- **THEN** solo se acepta el nuevo render si satisface su propia evidencia de proceso y las validaciones funcionales aplicables, incluyendo T30 cuando corresponda; la intervención no exime ninguna comprobación

#### Scenario: Entrega a Clarity después de una intervención
- **WHEN** existen archivos de un intento reconciliado pero ningún intento posterior aceptado que acredite las salidas actuales
- **THEN** la reconciliación no habilita su entrega a Clarity ni convierte esos archivos en evidencia de un render aceptado
