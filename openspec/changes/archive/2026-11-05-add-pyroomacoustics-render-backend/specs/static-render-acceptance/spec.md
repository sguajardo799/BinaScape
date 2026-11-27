## MODIFIED Requirements

### Requirement: Constancia funcional vinculada al intento
El proceso de render del backend seleccionado (MATLAB/RAVEN o el worker Pyroomacoustics) SHALL producir, cuando recibe contexto funcional, una constancia versionada con identidad única del intento, SHA-256 del manifiesto de ejecución e inventario completo de salidas con tipo, ruta, tamaño y SHA-256. Ambos backends SHALL usar el mismo formato de constancia. El orquestador SHALL contrastarla con el intento y las salidas esperadas, sin confiar en consola o eventos diagnósticos.

#### Scenario: Constancia de otro intento
- **WHEN** los WAV y metadatos existen pero la constancia corresponde a otro intento o a otro manifiesto
- **THEN** no acredita la finalización del intento actual y no permite recuperar su proceso fallido

#### Scenario: Inventario incompleto o sustituido
- **WHEN** falta una salida esperada, hay entradas duplicadas o inesperadas, o no coincide ruta, tamaño o huella
- **THEN** la constancia se rechaza con motivo explícito y no autoriza recuperación

#### Scenario: Mensaje humano de éxito
- **WHEN** la consola imprime `Static render completed successfully` y hay eventos de fin pero no constancia funcional válida
- **THEN** esas evidencias no autorizan recuperación de un código no cero

#### Scenario: Constancia del worker Pyroomacoustics
- **WHEN** el worker Pyroomacoustics termina un intento con contexto funcional
- **THEN** publica una constancia con `schema_version` 1, la identidad del intento y el inventario WAV y metadatos, que el orquestador verifica con las mismas reglas que una constancia MATLAB

### Requirement: Publicación final y atómica de la constancia
El proceso de render del backend seleccionado SHALL publicar la constancia funcional únicamente después de escribir y cerrar todas las salidas inventariadas y calcular sus huellas, mediante publicación atómica sin sobrescribir una constancia existente. Una escritura parcial de constancia SHALL NOT constituir finalización. Los artefactos funcionales del intento SHALL conservarse separados de los diagnósticos de mejor esfuerzo.

#### Scenario: Fallo exportando metadatos
- **WHEN** se escribe el WAV y falla la exportación o cierre de sus metadatos
- **THEN** no se publica una constancia final válida para ese intento

#### Scenario: Constancia interrumpida
- **WHEN** el proceso cae durante la escritura temporal de la constancia
- **THEN** el archivo temporal no se interpreta como prueba de finalización

#### Scenario: Diagnóstico no disponible
- **WHEN** fallan directorio, log, eventos o resumen exclusivos de diagnóstico
- **THEN** la emisión y comprobación de la constancia funcional no dependen de esos archivos y la decisión se mantiene si la evidencia funcional no cambia

#### Scenario: Constancia ya existente
- **WHEN** el worker Pyroomacoustics encuentra que la ruta de constancia del intento ya existe
- **THEN** no la reemplaza, termina con código no cero y el intento no se acredita como finalizado

### Requirement: Integridad completa del WAV
El orquestador SHALL comprobar formato soportado, decodificación de toda la carga sin truncamiento, audio no vacío, muestras finitas, frecuencia y canales esperados. La frecuencia esperada SHALL pertenecer al conjunto de frecuencias declarado por el backend de la variante y coincidir con la del manifiesto de ejecución. SHALL contrastar dimensiones con los metadatos requeridos y duración objetivo del manifiesto, cuando exista. Leer solo la cabecera SHALL NOT bastar para aceptar el WAV.

#### Scenario: Cabecera correcta y carga truncada
- **WHEN** la cabecera anuncia las dimensiones esperadas pero faltan bytes de audio
- **THEN** el WAV se rechaza aunque la cabecera se pueda abrir

#### Scenario: Tipo de receptor y frecuencia
- **WHEN** el manifiesto selecciona HRTF/HARTF o un array de N micrófonos
- **THEN** se exigen respectivamente dos o N canales, a una frecuencia declarada por el backend de la variante, que en esta versión es 44100 Hz tanto para RAVEN como para Pyroomacoustics

#### Scenario: Duración objetivo
- **WHEN** existe `target_duration_s`
- **THEN** las muestras coinciden con `max(1, round(target_duration_s * sample_rate_hz))`, usando el redondeo de MATLAB (mitades alejándose de cero) y su contrato de recorte o relleno, con cualquier backend

#### Scenario: Dimensiones o valores no válidos
- **WHEN** faltan muestras, hay dimensiones incompatibles o muestras no finitas
- **THEN** se registran motivos de rechazo sin aceptar el render por existencia del archivo

### Requirement: Aceptación acústica sin atajos de recuperación
La aceptación SHALL exigir los resultados acústicos requeridos por el flujo, incluyendo las diez bandas T30 finitas y positivas de la variante canónica. SHALL verificar su correspondencia de frecuencias y coherencia de campos derivados obligatorios. El método de medición esperado SHALL ser el declarado por el backend de la variante: `rpf.getT30` estimado desde `room` para RAVEN, y `schroeder_backward_integration_v1` estimado desde `rir` para Pyroomacoustics. Un método distinto del declarado SHALL rechazarse. La recuperación de proceso SHALL NOT eludir el rechazo acústico ni sus límites de remuestreo existentes.

#### Scenario: T30 inválido con constancia íntegra
- **WHEN** la variante canónica tiene artefactos íntegros y constancia válida pero T30 rechazado, con código cero o no cero
- **THEN** no se acepta la escena y se sigue la política existente de rechazo y remuestreo T30, conservando el resultado del proceso de ese intento

#### Scenario: T30 válido
- **WHEN** están presentes las diez bandas esperadas con valores finitos positivos y campos derivados coherentes
- **THEN** se satisface el control acústico existente sin imponer coincidencia nueva con el intervalo del estimador Sabine

#### Scenario: Método T30 del backend Pyroomacoustics
- **WHEN** una variante canónica Pyroomacoustics declara `method: schroeder_backward_integration_v1` y `estimated_from: rir` con diez bandas finitas y positivas
- **THEN** se satisface el control acústico con la misma política de rechazo y remuestreo que RAVEN

#### Scenario: Método T30 ajeno al backend
- **WHEN** una variante RAVEN declara el método Pyroomacoustics, o una variante Pyroomacoustics declara `rpf.getT30`
- **THEN** la validación registra una incoherencia del campo T30 y la variante no se acepta
