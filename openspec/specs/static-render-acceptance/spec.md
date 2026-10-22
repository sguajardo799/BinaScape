# static-render-acceptance Specification

## Purpose

Definir la aceptación del render estático mediante evidencia funcional por intento y validación independiente de sus artefactos, sin equiparar la terminación del proceso MATLAB con el resultado acústico. Este contrato se introduce mediante el cambio de separación de resultados.

## Requirements

### Requirement: Resultados independientes y decisión explícita
El orquestador SHALL registrar por intento el resultado observado del proceso, la validez del render y la decisión funcional por separado. SHALL conservar código y error originales aunque permita continuar. La validez SHALL distinguir válido, incompleto, inválido y no verificable, con motivos de validación.

#### Scenario: Render válido con proceso fallido
- **WHEN** un intento satisface las condiciones de recuperación tras terminar MATLAB con código 1
- **THEN** el proceso permanece fallido con código 1, el render se registra válido y la decisión indica continuación con advertencia

#### Scenario: Proceso correcto con WAV inválido
- **WHEN** MATLAB devuelve cero pero el WAV está truncado
- **THEN** el resultado del proceso es satisfactorio pero el render no se acepta ni se contabiliza como variante completada válida

### Requirement: Constancia funcional vinculada al intento
MATLAB SHALL producir, cuando recibe contexto funcional, una constancia versionada con identidad única del intento, SHA-256 del manifiesto de ejecución e inventario completo de salidas con tipo, ruta, tamaño y SHA-256. El orquestador SHALL contrastarla con el intento y las salidas esperadas, sin confiar en consola o eventos diagnósticos.

#### Scenario: Constancia de otro intento
- **WHEN** los WAV y metadatos existen pero la constancia corresponde a otro intento o a otro manifiesto
- **THEN** no acredita la finalización del intento actual y no permite recuperar su proceso fallido

#### Scenario: Inventario incompleto o sustituido
- **WHEN** falta una salida esperada, hay entradas duplicadas o inesperadas, o no coincide ruta, tamaño o huella
- **THEN** la constancia se rechaza con motivo explícito y no autoriza recuperación

#### Scenario: Mensaje humano de éxito
- **WHEN** la consola imprime `Static render completed successfully` y hay eventos de fin pero no constancia funcional válida
- **THEN** esas evidencias no autorizan recuperación de un código no cero

### Requirement: Publicación final y atómica de la constancia
MATLAB SHALL publicar la constancia funcional únicamente después de escribir y cerrar todas las salidas inventariadas y calcular sus huellas, mediante publicación atómica. Una escritura parcial de constancia SHALL NOT constituir finalización. Los artefactos funcionales del intento SHALL conservarse separados de los diagnósticos de mejor esfuerzo.

#### Scenario: Fallo exportando metadatos
- **WHEN** se escribe el WAV y falla la exportación o cierre de sus metadatos
- **THEN** no se publica una constancia final válida para ese intento

#### Scenario: Constancia interrumpida
- **WHEN** el proceso cae durante la escritura temporal de la constancia
- **THEN** el archivo temporal no se interpreta como prueba de finalización

#### Scenario: Diagnóstico no disponible
- **WHEN** fallan directorio, log, eventos o resumen exclusivos de diagnóstico
- **THEN** la emisión y comprobación de la constancia funcional no dependen de esos archivos y la decisión se mantiene si la evidencia funcional no cambia

### Requirement: Integridad completa del WAV
El orquestador SHALL comprobar formato soportado, decodificación de toda la carga sin truncamiento, audio no vacío, muestras finitas, frecuencia y canales esperados. SHALL contrastar dimensiones con los metadatos requeridos y duración objetivo del manifiesto, cuando exista. Leer solo la cabecera SHALL NOT bastar para aceptar el WAV.

#### Scenario: Cabecera correcta y carga truncada
- **WHEN** la cabecera anuncia las dimensiones esperadas pero faltan bytes de audio
- **THEN** el WAV se rechaza aunque la cabecera se pueda abrir

#### Scenario: Tipo de receptor y frecuencia
- **WHEN** el manifiesto selecciona HRTF/HARTF o un array de N micrófonos
- **THEN** se exigen respectivamente dos o N canales, a los 44100 Hz soportados por el renderer actual

#### Scenario: Duración objetivo
- **WHEN** existe `target_duration_s`
- **THEN** las muestras coinciden con `max(1, round(target_duration_s * sample_rate_hz))`, usando el redondeo de MATLAB y su contrato de recorte o relleno

#### Scenario: Dimensiones o valores no válidos
- **WHEN** faltan muestras, hay dimensiones incompatibles o muestras no finitas
- **THEN** se registran motivos de rechazo sin aceptar el render por existencia del archivo

### Requirement: Metadatos estructurados y coherentes
El orquestador SHALL exigir metadatos JSON con esquema soportado y campos obligatorios de escena, variante, rutas, frecuencia, dimensiones y resumen de fuentes cuando sean requeridos. SHALL contrastar tipos y valores con WAV y manifiesto; la ausencia de campos obligatorios SHALL NOT pasar como coincidencia. Toda recuperación de código no cero SHALL requerir metadatos completos.

#### Scenario: JSON válido pero insuficiente
- **WHEN** el sidecar es `{}` o carece de identidad, dimensiones o fuentes obligatorias
- **THEN** se rechaza como metadato insuficiente, aunque el JSON sea sintácticamente válido

#### Scenario: Identidades contradictorias
- **WHEN** campos de identidad repetidos en distintas secciones discrepan entre sí o con el manifiesto
- **THEN** se rechaza el render sin resolver la contradicción eligiendo solamente el primer campo presente

#### Scenario: Metadatos desactivados
- **WHEN** una variante no canónica se ejecuta con metadatos opcionales y no tiene sidecar
- **THEN** un código cero puede aceptarse mediante las comprobaciones aplicables, pero un código no cero no se recupera sin metadatos completos

### Requirement: Correspondencia con el manifiesto de ejecución
La validación SHALL contrastar receptor, IR o geometría y orden de canales, conjunto exacto de fuentes, procedencia, rutas preparadas, ganancias, tiempos y semilla efectiva con el manifiesto del intento, respetando normalizaciones y marcos de coordenadas soportados. SHALL comprobar la coherencia del tratamiento de ruido cuando esté configurado.

#### Scenario: Fuente omitida o duplicada
- **WHEN** el resumen no contiene exactamente las fuentes del manifiesto o altera su procedencia, ganancia o tiempo
- **THEN** se rechaza la correspondencia y no se permite recuperar el proceso

#### Scenario: Audio preparado y procedencia original
- **WHEN** el runtime usa una fuente recortada
- **THEN** los metadatos coinciden con su ruta preparada y preservan `identity_id` y `audio_relpath` originales, usando valores vacíos para manifiestos legacy que los omiten

#### Scenario: Array incompatible
- **WHEN** el número de canales coincide pero cambian geometría, orden de micrófonos o marco de coordenadas
- **THEN** la variante se rechaza por procedencia incompatible

### Requirement: Aceptación acústica sin atajos de recuperación
La aceptación SHALL exigir los resultados acústicos requeridos por el flujo, incluyendo las diez bandas T30 finitas y positivas de la variante canónica. SHALL verificar su correspondencia de frecuencias y coherencia de campos derivados obligatorios. La recuperación de proceso SHALL NOT eludir el rechazo acústico ni sus límites de remuestreo existentes.

#### Scenario: T30 inválido con constancia íntegra
- **WHEN** la variante canónica tiene artefactos íntegros y constancia válida pero T30 rechazado, con código cero o no cero
- **THEN** no se acepta la escena y se sigue la política existente de rechazo y remuestreo T30, conservando el resultado del proceso de ese intento

#### Scenario: T30 válido
- **WHEN** están presentes las diez bandas esperadas con valores finitos positivos y campos derivados coherentes
- **THEN** se satisface el control acústico existente sin imponer coincidencia nueva con el intervalo del estimador Sabine

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

### Requirement: Validación uniforme en ejecución y reutilización
Ejecución nueva, reanudación y entrega a Clarity SHALL aplicar la misma validación de artefactos pertinente al manifiesto vigente. Un rechazo o fallo no recuperado conocido SHALL NOT convertirse en completado por reiniciar. Reanudar SHALL NOT inventar una invocación ni un éxito de proceso y SHALL preservar la advertencia de un intento recuperado.

#### Scenario: Reinicio después de recuperación
- **WHEN** se reanuda una variante recuperada previamente y sus artefactos siguen válidos
- **THEN** se reutiliza sin lanzar MATLAB y se conserva la asociación al proceso fallido recuperado

#### Scenario: Reinicio después de fallo no recuperado
- **WHEN** un intento fallido dejó archivos aparentemente completos sin constancia válida y se reinicia el experimento
- **THEN** no se acepta ese intento mediante la mera inspección de archivos ni sobrescribiendo su decisión con un registro planificado

#### Scenario: Salida histórica sin constancia
- **WHEN** una salida legacy no tiene fallo no recuperado conocido y se considera para reanudación
- **THEN** solo se reutiliza si supera la validación estricta y la correspondencia con el manifiesto vigente; no se inventa constancia ni resultado del proceso

#### Scenario: Archivo alterado antes del handoff
- **WHEN** un WAV o sidecar cambia después de haber sido indexado como válido
- **THEN** la revalidación bloquea su entrega a Clarity si ya no satisface el contrato, manteniendo la elegibilidad exclusiva de HRTF

### Requirement: Persistencia visible y aislamiento de intentos
El orquestador SHALL persistir la decisión, motivos, identidad del intento y vínculo a constancia y resultado del proceso antes de continuar tras recuperación. SHALL mostrar la recuperación en el resumen y CLI sin presentarla como éxito limpio. Cada intento SHALL conservar su evidencia sin mezclarse con otros ni perderse al descartar salidas por T30.

#### Scenario: No se puede persistir la recuperación
- **WHEN** se validó un render de proceso fallido pero no puede guardarse su decisión funcional
- **THEN** no se continúa silenciosamente y se conserva accesible el fallo del proceso junto al error de persistencia

#### Scenario: Intentos paralelos y reintentos
- **WHEN** se solapan variantes o se vuelve a renderizar tras rechazo T30
- **THEN** identidades y archivos funcionales son distintos, las evidencias anteriores permanecen y ninguna constancia autoriza otra variante o intento

### Requirement: Compatibilidad del render estático existente
Las llamadas MATLAB existentes con solo manifiesto o con contexto diagnóstico SHALL seguir aceptándose sin exigir contexto funcional. SHALL preservarse nombres públicos de salidas y algoritmo acústico. La validación más estricta SHALL documentar los rechazos de salidas históricas antes admitidas por existencia; no añade soporte dinámico ni umbrales nuevos de calidad de señal.

#### Scenario: Llamada MATLAB anterior
- **WHEN** se invoca el entrypoint estático sin contexto funcional
- **THEN** mantiene el render y exportación anteriores sin emitir una constancia ficticia

#### Scenario: Fixture o archivo no acústico
- **WHEN** un archivo llamado `.wav` contiene texto u otros bytes no decodificables
- **THEN** la nueva validación lo rechaza en lugar de mantener la aceptación histórica por existencia

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
