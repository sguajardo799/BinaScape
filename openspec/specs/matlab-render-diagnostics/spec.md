# matlab-render-diagnostics Specification

## Purpose

Definir el diagnóstico persistente propuesto para invocaciones del render estático entre el orquestador y MATLAB, separando evidencia del proceso y etapas del estado funcional de los artefactos. Estos requisitos añaden comportamiento todavía no implementado.

## Requirements

### Requirement: Identidad e historial por invocación
El orquestador SHALL asignar una identidad única a cada intento de invocar MATLAB y conservar su diagnóstico ligado a escena, variante y manifiesto de entrada, sin sobrescribir intentos anteriores. Las variantes reanudadas sin invocación SHALL NOT crear intentos nuevos.

#### Scenario: Reintento de la misma variante
- **WHEN** una variante se vuelve a invocar tras rechazo de T30 o en una ejecución posterior
- **THEN** cada intento conserva una identidad, evidencia de su entrada y archivos de diagnóstico distintos
- **AND** los diagnósticos anteriores no se borran al descartar salidas de render

#### Scenario: Variante reanudada
- **WHEN** las reglas actuales permiten reutilizar una variante sin ejecutar MATLAB
- **THEN** no se registra una invocación nueva ni un éxito de proceso ficticio

### Requirement: Registro del resultado del proceso
El orquestador SHALL conservar por intento el comando, tiempos de inicio y fin, duración no negativa, código de salida si existe, error de lanzamiento o ejecución y rutas de diagnóstico y artefactos esperados. SHALL conservar la salida de consola de MATLAB en un log identificable cuando el almacenamiento esté disponible. El resultado del proceso SHALL conservarse independientemente de que la política funcional propague el fallo o permita continuar.

#### Scenario: Proceso con salida no cero
- **WHEN** MATLAB termina con un código no cero
- **THEN** el resumen conserva ese código, la duración, la asociación al log y las rutas esperadas, tanto si el fallo se propaga como si la aceptación funcional permite continuar

#### Scenario: Ejecutable no disponible
- **WHEN** no se puede lanzar el ejecutable MATLAB
- **THEN** el resumen registra el error, duración y código de salida null sin afirmar que MATLAB empezó una etapa

### Requirement: Progreso explícito y persistente
MATLAB SHALL emitir eventos estructurados de inicio y fin para inicialización, simulación/procesado y escritura, y un evento de entrypoint completado. Los eventos SHALL persistirse al emitirse, identificar el intento y permitir obtener la última etapa iniciada y completada sin interpretar mensajes humanos.

#### Scenario: Caída durante simulación
- **WHEN** se conserva el inicio de simulación/procesado pero el proceso termina sin su evento de fin
- **THEN** el diagnóstico muestra esa etapa como última iniciada y conserva la última etapa completada, sin afirmar una causa interna

#### Scenario: Inicialización de MATLAB anterior al entrypoint
- **WHEN** el proceso termina sin eventos de MATLAB
- **THEN** el diagnóstico informa que no se observó entrada al flujo instrumentado y deja indeterminada la etapa interna

### Requirement: Terminación diferenciada del fin del render
El diagnóstico SHALL distinguir el fin del entrypoint de la terminación del proceso MATLAB. Un código no cero posterior al evento de entrypoint completado SHALL identificarse como fallo posterior al entrypoint, sin atribuir automáticamente una causa específica al cierre.

#### Scenario: Error posterior a escritura completa
- **WHEN** existe el evento de entrypoint completado y MATLAB devuelve un código no cero
- **THEN** el resumen conserva ambas evidencias y clasifica el fallo como posterior al entrypoint, con causa no confirmada

### Requirement: Independencia del progreso y resultado del proceso
El diagnóstico SHALL registrar entrypoint_completed independientemente de process_status. Solo una terminación observada con código cero SHALL establecer process_status=succeeded; un evento de fin del entrypoint SHALL NOT establecerlo ni reemplazar el resultado del proceso.

#### Scenario: Evento final sin éxito del proceso
- **WHEN** se observa entrypoint_completed y el proceso termina con código no cero
- **THEN** el resumen registra entrypoint_completed=true y process_status=failed con el código observado

#### Scenario: Código cero sin evento final
- **WHEN** el proceso termina con código cero pero no se conserva el evento de fin del entrypoint
- **THEN** el resumen registra process_status=succeeded y entrypoint_completed=false, sin inferir el evento ausente

#### Scenario: Terminación no observada
- **WHEN** existe el evento de fin del entrypoint pero el orquestador no alcanza a registrar la terminación del proceso
- **THEN** el resumen persistido no declara process_status=succeeded a partir de ese evento

### Requirement: Evidencia de artefactos del intento
El diagnóstico SHALL distinguir rutas esperadas, archivos observados antes y después de la invocación, y escrituras confirmadas por eventos del intento actual. La mera existencia de un WAV o metadato SHALL NOT constituir confirmación de escritura por ese intento.

#### Scenario: Salidas de un intento anterior
- **WHEN** ya existen WAV y metadatos y la nueva invocación falla antes de escribir
- **THEN** se registran como observados pero no como escrituras confirmadas del nuevo intento

#### Scenario: WAV escrito y exportación de metadatos fallida
- **WHEN** MATLAB confirma escritura del WAV y falla exportando sus metadatos
- **THEN** se conserva la confirmación del WAV, escritura como etapa no completada y ausencia de confirmación para los metadatos

### Requirement: Compatibilidad funcional y aislamiento del diagnóstico
El diagnóstico SHALL NOT autorizar aceptación ni cambiar estados del índice, validación, reanudación, reintentos T30, planificación, salidas acústicas o decisión funcional. La recuperación y propagación de errores SHALL regirse por el contrato funcional independiente. Las llamadas estáticas existentes sin contexto de diagnóstico SHALL seguir aceptándose. Los fallos exclusivos de diagnóstico SHALL advertirse sin sustituir errores originales ni convertir un render correcto en fallido.

#### Scenario: Artefactos completos con proceso fallido
- **WHEN** existen artefactos completos pero MATLAB termina con error
- **THEN** el diagnóstico conserva el fallo del proceso y no decide su recuperación
- **AND** solo una constancia funcional válida del intento y validación completa satisfactoria permiten continuar con advertencia; en ausencia de esas condiciones el fallo no se recupera

#### Scenario: Diagnóstico no escribible
- **WHEN** falla la creación o actualización de un archivo exclusivo de diagnóstico
- **THEN** se emite una advertencia, se conserva la evidencia disponible y el resultado funcional y error original no cambian

#### Scenario: Fallo de limpieza o advertencia durante otro error
- **WHEN** el renderer falla y también falla el cierre de un log, la emisión de eventos, la finalización del resumen o el mecanismo de advertencias
- **THEN** el diagnóstico no sustituye la excepción original ni altera el estado funcional del pipeline

#### Scenario: Fallo de diagnóstico con render correcto
- **WHEN** el renderer termina correctamente pero falla una operación exclusiva de diagnóstico o su advertencia
- **THEN** no se introduce un error funcional ni se repite la invocación por ese fallo

#### Scenario: Invocaciones paralelas
- **WHEN** se ejecutan variantes simultáneamente
- **THEN** cada log, resumen y secuencia de eventos queda asociado solo a su intento sin mezclar evidencia de otros procesos

#### Scenario: Fallo de diagnóstico aislado entre intentos concurrentes
- **WHEN** dos invocaciones se solapan y una falla al persistir diagnóstico
- **THEN** sus directorios de intento son distintos y el fallo no modifica los archivos, el resultado del proceso ni el estado funcional del otro intento

#### Scenario: Llamada MATLAB anterior
- **WHEN** se invoca el entrypoint estático con solo el manifiesto, sin contexto de diagnóstico
- **THEN** la llamada mantiene su contrato funcional anterior

#### Scenario: Evento final sin constancia funcional
- **WHEN** se conserva `entrypoint_completed` pero falta una constancia funcional válida y el proceso termina con error
- **THEN** el evento mantiene su significado diagnóstico y no autoriza recuperación

#### Scenario: Recuperación sin eventos diagnósticos
- **WHEN** la constancia funcional y todas las validaciones permiten recuperar un proceso fallido pero no se conservaron eventos
- **THEN** la decisión funcional no cambia, `entrypoint_completed` no se inventa y el diagnóstico no atribuye una causa de cierre no confirmada
