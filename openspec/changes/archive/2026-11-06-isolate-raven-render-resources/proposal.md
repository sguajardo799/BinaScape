# Proposal

## Why

Los renders RAVEN paralelos pueden consumir geometría y absorción de otra escena porque el toolbox sobrescribe modelos y materiales compartidos, aunque los manifiestos de la tripleta sean idénticos. La investigación reprodujo la divergencia y la eliminó aislando esos recursos desde Python, sin modificar el toolbox ni MATLAB.

## What Changes

- Preparar desde el orquestador un RPF derivado y recursos de geometría/materiales privados por variante y realización, antes de lanzar MATLAB; conservar el paralelismo configurado.
- Registrar y verificar la procedencia del RPF derivado, sin alterar la configuración ni el manifiesto de escena que identifica el RPF original.
- Integrar el aislamiento con snapshots, reintentos, remuestreo T30 y reanudación no destructiva; no ignorar diferencias arbitrarias entre RPF.
- Preservar la unidad de muestreo RT por `scene_id`: las variantes comparten absorción, scattering, geometría, fuentes y pose; no se reparten cuotas por WAV.
- Incorporar regresiones Python y una verificación MATLAB de dos escenas con tripletas, comparando ejecución secuencial y paralela aislada.
- Documentar el riesgo de resultados históricos sin aislamiento y la regeneración explícita en un nuevo run; no modificar ni certificar retroactivamente sus audios.

No se cambian algoritmos acústicos, estimador Sabine, fallback de bins, límites T30, selección de sujetos, nombres públicos, contratos de Clarity ni backend Pyroomacoustics. La comparación numérica de T30 será una comprobación del harness, no un umbral nuevo de aceptación en producción.

## Capabilities

### New Capabilities

- `raven-render-isolation`: propiedad exclusiva de los archivos mutables de RAVEN, procedencia de sus entradas derivadas y preservación de la realización acústica compartida al ejecutar variantes concurrentes.

### Modified Capabilities

- `static-render-resumption`: comprobar RPF derivados mediante procedencia verificable y preservar también los recursos de variantes reutilizadas sin reescribirlos; mantener una ruta conservadora para resultados históricos sin aislamiento.

## Impact

- Componentes: exclusivamente código, pruebas y documentación de `apps/orchestrator`; harness de verificación que invoca el entrypoint MATLAB existente.
- Integración: `pipeline/matlab_runner.py`, `render_pipeline.py`, `output_paths.py`, validación/reanudación y cadenas de proceso cuando sea necesario, con un helper Python localizado para preparar recursos RAVEN.
- Archivos internos nuevos bajo el run: RPF derivado, directorio `RavenModels`, base privada de materiales y registro versionado de procedencia. No se añaden dependencias ni opciones YAML obligatorias.
- MATLAB/ITA Toolbox/RAVEN: consumidores existentes sin cambios de código. El despliegue mantiene los supuestos Windows actuales.
- Compatibilidad: preservar SHA-256 del snapshot realmente ejecutado y presupuesto de cada cadena. Un RPF privado ausente o alterado no se trata como equivalente por nombre de archivo.
- Evidencia local: `outputs/rt_pairing_investigation_cec8d2352d/results.json` contiene 15 renders exitosos; el paralelo compartido presentó diferencias de hasta 0,130771 s dentro de la muestra de dos escenas, frente a menos de 3,4e-8 s en el T30 medio con aislamiento. Esta evidencia local es referencia de investigación, no fixture portable obligatorio.
