# pipeline-progress Specification

## Purpose

Definir el progreso de terminal propuesto para planificación, render estático y degradado Clarity, con estimaciones temporales por etapa y sin otorgar autoridad funcional a la presentación. Esta capacidad añade comportamiento visible todavía no implementado.

## Requirements

### Requirement: Progreso por etapas reales
Los CLI del orquestador SHALL presentar planificación, render canónico y variantes restantes como etapas diferenciadas, con unidad, cantidad resuelta, total y tiempo transcurrido. Las operaciones sin total medible SHALL mostrar actividad indeterminada. El fin de una etapa SHALL distinguir éxito, resultado con fallos e interrupción.

#### Scenario: Render con varias salidas
- **WHEN** se ejecuta un lote con canónicas y variantes adicionales
- **THEN** se muestra primero el avance canónico por escenas y después el avance de las variantes restantes por trabajos
- **AND** ninguna etapa se presenta como porcentaje global del pipeline

#### Scenario: Abortado con trabajo pendiente
- **WHEN** un error o el corte de procesos impide despachar parte del trabajo
- **THEN** se conserva el avance observado y se indica interrupción con pendientes, sin forzar el contador al total

### Requirement: Planificación sin publicación ficticia
La planificación SHALL contar cada escena evaluada una sola vez y distinguir nuevas, conservadas y fallidas. Completar la evaluación SHALL NOT afirmar publicación del plan ni aceptación acústica. El CLI SHALL identificar por separado la verificación previa y la publicación mientras ocurran.

#### Scenario: Fallo de muestreo
- **WHEN** se evalúan diez escenas y dos agotan el muestreo
- **THEN** el avance de evaluación alcanza diez con dos fallidas, sin declarar diez manifiestos generados

#### Scenario: Conflicto de preflight
- **WHEN** la evaluación termina pero la verificación detecta un conflicto protegido
- **THEN** se informa el conflicto sin declarar el plan publicado ni alterar las protecciones de reanudación

### Requirement: Contabilidad funcional del render
El progreso del render SHALL contar unidades resueltas tras su decisión funcional persistida, separando aceptadas, reutilizadas y fallidas. Los reintentos de proceso y remuestreos T30 SHALL mostrarse aparte y SHALL NOT incrementar el total ni completar de nuevo una unidad. Un rechazo T30 provisional SHALL mantener pendiente la escena canónica.

#### Scenario: Reintento de proceso exitoso
- **WHEN** una variante se acepta en la tercera invocación
- **THEN** el progreso resuelve una unidad y conserva visibles las tres invocaciones y dos reintentos

#### Scenario: Rechazo T30
- **WHEN** una canónica se rechaza por T30 y se vuelve a muestrear su escena
- **THEN** la escena sigue pendiente hasta su aceptación o fallo terminal y no se incrementa el total por la nueva realización

#### Scenario: Resultados paralelos fuera de orden
- **WHEN** varias invocaciones terminan en un orden distinto al de envío
- **THEN** cada trabajo se cuenta una sola vez tras persistir su decisión, incluso si otros permanecen activos

### Requirement: Actividad MATLAB observada
Durante las invocaciones activas, el orquestador SHALL mostrar la identidad del trabajo y la última etapa o paso observado en eventos diagnósticos válidos del intento actual. Sin eventos SHALL indicar actividad interna desconocida. Esta información SHALL NOT asignar porcentajes internos a RAVEN ni acreditar finalización funcional.

#### Scenario: Simulación larga
- **WHEN** el último evento válido del intento identifica simulación y no aparece otro
- **THEN** la consola conserva esa última actividad observada y actualiza el tiempo transcurrido sin fabricar avance interno

#### Scenario: Eventos ausentes o incompletos
- **WHEN** falta el archivo diagnóstico, contiene una línea parcial o aparecen eventos de otro intento
- **THEN** el render continúa sin usar esos datos para aceptar, reintentar o declarar completado el trabajo

### Requirement: Ejecución de degradado diferenciada del handoff
El orquestador SHALL distinguir degradado deshabilitado, jobs preparados sin envío, ausencia de pendientes, envío bloqueado y backend en ejecución. Solo una ejecución real del backend SHALL presentar progreso de procesamiento. Los jobs reutilizados, bloqueados u omitidos antes del envío SHALL permanecer fuera del total ejecutable del backend.

#### Scenario: Preparación sin envío
- **WHEN** el degradado está habilitado y el envío está desactivado
- **THEN** se informa que los jobs están preparados y no se muestra una barra de degradado completada

#### Scenario: Reanudación sin pendientes
- **WHEN** todos los jobs elegibles se reutilizan
- **THEN** se informa ausencia de pendientes y no se simula una ejecución del backend

#### Scenario: Backend bloqueado
- **WHEN** el entorno impide enviar los jobs preparados
- **THEN** se muestra el bloqueo sin presentar esos jobs como procesados

### Requirement: Progreso local de Clarity
El CLI Clarity SHALL mostrar avance por job de variante y perfil auditivo, con total de registros no vacíos del manifiesto y contadores separados de completados y fallidos. Solo SHALL resolver un job después de procesarlo y persistir su resultado. Un error que interrumpe el recorrido SHALL dejar visibles los pendientes y conservar la semántica de salida existente.

#### Scenario: Fallo procesable entre éxitos
- **WHEN** el backend persiste un resultado fallido y continúa con otros jobs
- **THEN** avanza una unidad fallida, no un éxito, y conserva su salida no cero al finalizar el lote

#### Scenario: Manifiesto vacío
- **WHEN** el manifiesto no contiene registros no vacíos
- **THEN** se informa ausencia de trabajo sin división por cero ni ETA de procesamiento

### Requirement: ETA aproximado por etapa
El progreso SHALL mostrar ETA aproximado solo tras al menos tres unidades ejecutadas y aceptadas en esa etapa de la ejecución actual. SHALL excluir reutilizaciones de las muestras, incorporar tiempo de reintentos y esperas, y considerar concurrencia mediante tiempo de reloj. Sin muestras suficientes SHALL indicar ETA no disponible o calculando. SHALL NOT mostrar ETA global ni valores negativos, infinitos o inválidos.

#### Scenario: Arranque y reanudación
- **WHEN** una etapa tiene veinte reutilizaciones y solo dos unidades recién ejecutadas y aceptadas
- **THEN** se muestran las reutilizaciones pero el ETA sigue sin estar disponible

#### Scenario: Trabajo paralelo
- **WHEN** se ejecutan variantes con varios workers
- **THEN** el ETA se basa en el ritmo de resolución observado durante el intervalo de ejecución, sin multiplicar de nuevo la velocidad por el número de workers

#### Scenario: Reintentos y trabajo pendiente
- **WHEN** una unidad pendiente acumula reintentos o espera antes de resolverse
- **THEN** ese tiempo se incluye en la estimación posterior y no se presenta ETA cero como finalización mientras queden pendientes

### Requirement: Control de consola y salida compatible
Los comandos `generate-manifests`, `render-static`, `clarity-handoff` y `run-manifest` SHALL aceptar `--no-progress`. En modo automático SHALL animar únicamente cuando stderr sea un terminal; sin terminal SHALL emitir solo transiciones breves sin controles de cursor. El progreso SHALL usar stderr y preservar resúmenes existentes en stdout. La desactivación del orquestador SHALL propagarse al backend enviado.

#### Scenario: Salida redirigida
- **WHEN** stderr no es un terminal
- **THEN** se conservan mensajes breves de transición y no se emiten animaciones ni secuencias de control del progreso

#### Scenario: Desactivación de extremo a extremo
- **WHEN** se ejecuta render con envío automático y `--no-progress`
- **THEN** ni el orquestador ni su backend Clarity emiten presentación de progreso y los diagnósticos y resúmenes funcionales se conservan

### Requirement: Progreso sin autoridad funcional
Activar o desactivar progreso SHALL preservar generación, aceptación, reanudación, políticas de reintento, elegibilidad Clarity, artefactos funcionales y códigos de salida. Fallos exclusivos de observación o presentación SHALL NOT sustituir errores funcionales ni provocar reintentos. Las llamadas programáticas existentes SHALL permanecer silenciosas por defecto. Los recursos de presentación SHALL cerrarse al salir o interrumpir.

#### Scenario: Presentación defectuosa
- **WHEN** falla la actualización de consola durante un render correcto o fallido
- **THEN** se desactiva la presentación afectada sin cambiar el resultado funcional ni sustituir su error original

#### Scenario: APIs sin observador
- **WHEN** un consumidor invoca las funciones programáticas existentes sin solicitar progreso
- **THEN** no se abre una interfaz de progreso ni se modifica el valor devuelto

#### Scenario: Interrupción del operador
- **WHEN** el operador interrumpe una ejecución activa
- **THEN** se cierran la presentación y su observación sin declarar completados los pendientes ni alterar la política existente de interrupción del proceso
