# Spec Delta

## Purpose

Evitar que renders RAVEN concurrentes de distintas variantes o escenas compartan archivos acústicos mutables. Este cambio propone aislamiento gestionado por el orquestador, preservando las condiciones comunes de cada escena y los contratos existentes del renderer.

## ADDED Requirements

### Requirement: Exclusividad de recursos mutables RAVEN
El orquestador SHALL preparar recursos privados de geometría y materiales para cada variante RAVEN y realización antes de su ejecución. Dos renders concurrentes SHALL NOT escribir ni consumir como propios los mismos archivos mutables, aunque tengan igual número de caras, nombres de superficies o identificadores locales en runs distintos.

#### Scenario: Escenas distintas con los mismos slots
- **WHEN** cuatro workers ejecutan variantes de dos escenas con seis caras y superficies llamadas `wall_001`, `floor` y `ceiling`
- **THEN** cada variante usa rutas privadas de modelo y base de materiales, sin consumir archivos generados por otra variante
- **AND** se conserva la concurrencia configurada sin serializar globalmente RAVEN

#### Scenario: Dos runs con identidades locales coincidentes
- **WHEN** dos runs en raíces diferentes ejecutan a la vez `scene_static_0001__binaural_hrtf`
- **THEN** sus recursos acústicos y proyectos de ejecución no colisionan

### Requirement: Procedencia verificable de entradas derivadas
El orquestador SHALL conservar la referencia al RPF original en configuración y escena, y registrar la relación verificable entre ese original, el RPF derivado y sus recursos privados. La derivación SHALL preservar los parámetros del original salvo rutas e identidad necesarias para aislamiento, y SHALL NOT modificar el original ni los recursos globales de modelos o materiales.

#### Scenario: Preparación de una variante
- **WHEN** se prepara una variante RAVEN desde una escena válida
- **THEN** el runtime apunta al RPF privado y existe evidencia versionada con rutas y huellas de las entradas originales y derivadas
- **AND** geometría, materiales globales y RPF original mantienen sus bytes previos

#### Scenario: RPF ambiguo o no aislable
- **WHEN** no puede resolverse de forma inequívoca la configuración necesaria para aislar un RPF
- **THEN** la preparación falla con ruta y motivo antes de lanzar MATLAB, sin volver silenciosamente a recursos compartidos

### Requirement: Realización acústica común a las variantes
El orquestador SHALL derivar las variantes de una escena de la misma realización de geometría, materiales, absorción, scattering, fuentes, tiempos, ganancias y pose. El aislamiento SHALL NOT volver a muestrear esas condiciones según tipo de salida, worker u orden de ejecución; solo cambia la respuesta de receptor prevista y los recursos técnicos privados.

#### Scenario: Tripleta del mismo individuo
- **WHEN** una escena produce HRTF binaural y HARTF frontal y posterior
- **THEN** las tres variantes conservan las mismas condiciones físicas y selección emparejada existentes, con recursos privados independientes

#### Scenario: Otras salidas soportadas
- **WHEN** una escena solicita varios sujetos, una única salida o un array circular RAVEN
- **THEN** cada variante obtiene aislamiento sin exigir una tripleta ni alterar sus cantidades, geometría de receptor u orden de canales

### Requirement: Cuotas RT por escena
El orquestador SHALL preservar la asignación y contabilización de bins RT por `scene_id`, no por variante ni WAV. El aislamiento SHALL conservar el estimador Sabine, el fallback permitido, los intentos de muestreo y el remuestreo de escena completa ante rechazo T30, sin introducir ajuste individual de absorción por salida.

#### Scenario: Treinta escenas con tres salidas
- **WHEN** se planifican treinta escenas entre seis bins equiprobables con una tripleta por escena
- **THEN** las cuotas objetivo son cinco escenas por bin y se esperan noventa variantes, sin contabilizarlas como noventa realizaciones acústicas
- **AND** los desvíos observados se explican mediante la política de fallback y fallos vigente

#### Scenario: Rechazo T30 canónico
- **WHEN** la validación canónica exige otra realización de una escena
- **THEN** sus variantes pendientes se derivan de la nueva realización compartida y no de absorciones ajustadas por separado

### Requirement: Evidencia y reintentos compatibles
El orquestador SHALL finalizar la preparación aislada antes de reservar y capturar el manifiesto del intento. La constancia SHALL seguir vinculada al SHA-256 de los bytes ejecutados. Un reintento de proceso SHALL conservar entrada y presupuesto de su cadena; una nueva realización acústica SHALL conservar evidencia previa y usar recursos de otra realización.

#### Scenario: Segundo intento del mismo proceso lógico
- **WHEN** una cadena autoriza reintentar una variante sin modificar la escena
- **THEN** el nuevo UUID no cambia el manifiesto funcional ni renueva el presupuesto, y no ejecuta en paralelo otro escritor de sus recursos

#### Scenario: Evidencia de una realización anterior
- **WHEN** se crea otra realización tras rechazo acústico
- **THEN** los archivos de procedencia anteriores se conservan y ninguna constancia anterior acredita la nueva entrada

### Requirement: Aislamiento sin cambios de backend ni formatos públicos
El aislamiento SHALL aplicarse automáticamente a nuevas ejecuciones RAVEN del orquestador sin modificar el toolbox ni el código MATLAB. SHALL preservar nombres públicos, canales, frecuencia, algoritmos y elegibilidad Clarity, y SHALL NOT introducir recursos RAVEN en ejecuciones Pyroomacoustics ni nuevos umbrales de T30 en producción.

#### Scenario: Configuración existente
- **WHEN** se ejecuta una configuración RAVEN válida sin nuevas opciones de aislamiento
- **THEN** se preparan recursos privados y los consumidores reciben los mismos nombres y formatos públicos

#### Scenario: Backend alternativo
- **WHEN** el backend seleccionado es Pyroomacoustics
- **THEN** no se preparan RPF ni bases de materiales RAVEN y se conserva su flujo anterior

#### Scenario: Verificación numérica de investigación
- **WHEN** el harness compara T30 de tripletas secuenciales y paralelas aisladas
- **THEN** registra concordancia por bandas con tolerancia numérica explícita, sin convertirla en una política nueva de aceptación o remuestreo en producción
