# Spec Delta

## MODIFIED Requirements

### Requirement: Registro del resultado del proceso
El orquestador SHALL conservar por intento el comando, tiempos de inicio y fin, duración no negativa, código de salida si existe, error de lanzamiento o ejecución y rutas de diagnóstico y artefactos esperados. SHALL conservar la salida de consola de MATLAB en un log identificable cuando el almacenamiento esté disponible. El resultado del proceso SHALL conservarse independientemente de que la política funcional propague el fallo o permita continuar.

#### Scenario: Proceso con salida no cero
- **WHEN** MATLAB termina con un código no cero
- **THEN** el resumen conserva ese código, la duración, la asociación al log y las rutas esperadas, tanto si el fallo se propaga como si la aceptación funcional permite continuar

#### Scenario: Ejecutable no disponible
- **WHEN** no se puede lanzar el ejecutable MATLAB
- **THEN** el resumen registra el error, duración y código de salida null sin afirmar que MATLAB empezó una etapa

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
