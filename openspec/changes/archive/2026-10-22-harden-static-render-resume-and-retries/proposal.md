# Proposal

## Why

El render estático no puede reanudarse con `resume_if_possible=true` y `overwrite_existing=false`: intenta regenerar manifiestos antes de validar las salidas existentes. Además, un fallo del proceso MATLAB sin finalización acreditada aborta el lote sin un presupuesto de nuevos intentos, aunque pueda ser transitorio; hacen falta reanudación no destructiva y resiliencia general sin aceptar resultados incompletos.

## What Changes

- Validar el conjunto de manifiestos existente antes de escribir, conservar realizaciones compatibles y generar solo las ausentes. Una incompatibilidad con sobrescritura desactivada produce un conflicto explícito.
- Reutilizar renders estrictamente válidos sin reescribir manifiestos de escena/runtime, audio preparado ni salidas públicas, y sin crear invocaciones ficticias. Conservar realizaciones aceptadas después de remuestreo T30.
- Separar estadísticas mutables del lote de las entradas de escena conservadas y registrar la procedencia/configuración del plan para futuras reanudaciones; admitir una validación conservadora de lotes anteriores sin ese registro.
- Añadir una política configurable de reintentos de proceso independiente de T30 y de recuperación `post_completion`. Propuesta inicial: tres invocaciones totales por trabajo, espera creciente y corte del lote tras cinco fallos consecutivos de proceso no recuperados. `max_attempts=1` conserva el comportamiento sin reintentos.
- Decidir por categorías y evidencia funcional, no por un texto de consola ni por ausencia de eventos: un código no cero observado puede justificar otro intento, no declarar una causa ni aceptar su render. Errores deterministas conocidos y fallos de persistencia no se reintentan.
- Persistir cadena, presupuesto, razón y resultado de cada invocación; aislar los restos de intentos fallidos antes de otro intento sin borrar evidencia ni sobrescribir salidas preexistentes protegidas.
- Incorporar `reconcile-static`, con inspección sin escrituras por defecto y aplicación explícita sobre un intento identificado. La operación exige confirmar ausencia de escritores, conserva evidencia anterior y presupuesto consumido, y permite programar otro intento sin inventar código de salida ni aceptar el render interrumpido.
- Cubrir combinaciones de opciones, interrupciones, salidas parciales, recuperación, límites, concurrencia y conservación de bytes/fechas de archivos reutilizados; actualizar documentación y ejemplos.

## Capabilities

### New Capabilities

- `static-render-resumption`: compatibilidad del plan, conservación de entradas y salidas, y reanudación no destructiva del lote estático.
- `matlab-process-retries`: clasificación conservadora, presupuestos persistentes, aislamiento y límites generales de nuevos intentos de proceso.

### Modified Capabilities

- `static-render-acceptance`: distinguir aborto de un intento de la decisión de programar otro, y garantizar que intentos anteriores no autoricen ni contaminen un resultado nuevo.

## Impact

- `apps/orchestrator`: modelos/validación de configuración, preparación de manifiestos, audio runtime, planificación secuencial/paralela, runner MATLAB, aceptación, historial, índice, rutas y resumen CLI.
- `apps/matlab`: exposición de errores de entrada deterministas mediante evidencia funcional compatible con llamadas anteriores; no cambiar el algoritmo acústico ni introducir MATLAB Engine.
- Contratos/documentación y pruebas de ambos componentes. Se reutiliza el almacenamiento por archivos existente, sin servicios ni dependencias nuevas previstas.
- Los nombres públicos de WAV y metadatos se conservan. Cambios intencionados de comportamiento: reintentos automáticos acotados por defecto y protección efectiva de salidas incompatibles cuando `overwrite_existing=false`.
- El incidente `directivity2_0476` es evidencia motivadora, no un caso especial: su JSON, geometría, coeficientes, referencias existentes y correspondencia con el snapshot pasaron las comprobaciones de solo lectura; la causa MATLAB sigue indeterminada y no se ejecutó una validación RAVEN completa.
