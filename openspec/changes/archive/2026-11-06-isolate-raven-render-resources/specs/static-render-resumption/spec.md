# Spec Delta

## ADDED Requirements

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
