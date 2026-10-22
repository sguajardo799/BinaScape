# Proposal

## Why

MATLAB puede completar las escrituras del render estático y después terminar con código no cero; hoy el orquestador aborta incluso en ese caso. Es necesario separar el resultado del proceso de la validez del render, sin convertir archivos existentes, mensajes de éxito o diagnósticos de mejor esfuerzo en pruebas de integridad.

## What Changes

- Introducir una constancia funcional de finalización por intento, independiente del diagnóstico, publicada atómicamente por MATLAB después de cerrar todas las salidas. Identifica intento, manifiesto e inventario de artefactos mediante SHA-256.
- Validar contenido completo del WAV, estructura y coherencia de metadatos, correspondencia con el manifiesto de ejecución y resultados acústicos requeridos antes de aceptar una variante.
- Registrar separadamente resultado del proceso, validación del render y decisión del pipeline. Un código no cero con constancia válida y todas las validaciones satisfactorias permite continuar con advertencia persistente, conservando el proceso como fallido.
- Mantener el rechazo T30 y sus límites de remuestreo como política acústica distinta del fallo del proceso; impedir que la recuperación los eluda.
- Reutilizar la validación en ejecución, reanudación y entrega a Clarity, evitando aceptar archivos antiguos o salidas inválidas mediante otro punto de entrada.
- Cambiar explícitamente la obligación anterior de propagar todo código no cero: solo se recuperan los casos acreditados por el contrato funcional. Eventos y consola continúan siendo exclusivamente observacionales.
- Preservar rutas públicas de WAV/metadatos, comportamiento acústico, elegibilidad HRTF de Clarity y llamadas MATLAB antiguas sin contexto funcional. La validación más estricta puede rechazar salidas históricas anteriormente consideradas completas por su mera existencia.

## Capabilities

### New Capabilities

- `static-render-acceptance`: constancia funcional por intento, validación integral de artefactos y decisión segura de aceptación, recuperación y reutilización del render estático.

### Modified Capabilities

- `matlab-render-diagnostics`: conservar evidencia del proceso independientemente de la decisión funcional y permitir recuperación acreditada sin que los diagnósticos autoricen aceptación ni oculten errores.

## Impact

- `apps/orchestrator`: runner MATLAB, coordinación secuencial/paralela y variante canónica, validación/índice de salidas, reanudación, resumen/CLI y controles de entrega a Clarity.
- `apps/matlab`: contexto funcional opcional del entrypoint estático y publicación de constancia después de WAV y metadatos. No cambia el algoritmo RAVEN/ITA.
- Contrato de archivos: nuevos artefactos funcionales internos por intento; campos aditivos en índice y resumen. No se modifica el YAML público ni los nombres de audio y sidecars.
- Pruebas: sustituir WAV ficticios por fixtures decodificables cuando corresponda; cubrir integridad, procedencia, códigos no cero, rechazo T30, reanudación y concurrencia.
- Documentación de ambos componentes y del contrato compartido. Preferir bibliotecas existentes y estándar; comprobar interoperabilidad SHA-256 MATLAB/Python en Windows sin añadir servicios ni dependencias de diagnóstico.

Este cambio describe comportamiento propuesto, no una capacidad ya implementada. No incluye render dinámico, reintentos genéricos, conversión de receptores, nuevas métricas de calidad acústica ni implementación durante la planificación.
