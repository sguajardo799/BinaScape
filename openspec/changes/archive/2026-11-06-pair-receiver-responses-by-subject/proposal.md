# Proposal

## Why

El muestreo actual elige respuestas HRTF y HARTF de forma independiente: las 30 escenas de `outputs/e2e_4_worker_hartf_s42` conservan sus condiciones de escena, pero ninguna usa el mismo ID en las tres respuestas. Para comparar ubicaciones de recepción sin mezclar sujetos, las variantes deben compartir identidad.

## What Changes

- Muestrear sujetos comunes a los catálogos habilitados de `binaural_hrtf`, `bte_front_hartf` y `bte_rear_hartf`, en lugar de elegir cada archivo independientemente.
- Emparejar por identidad y ordinal de selección, manteniendo `num_hrtfs`, los identificadores de salida y las rutas públicas existentes. Con una respuesta por salida, las tres pertenecerán al mismo sujeto; con cantidades distintas, las salidas compartirán el prefijo de la selección común.
- Validar que cada identidad tenga una respuesta inequívoca por catálogo y que existan suficientes sujetos comunes; informar errores antes del render, sin sustituciones independientes.
- **BREAKING**: los experimentos con varias salidas cefálicas y catálogos sin suficientes identidades comunes, o con identidades ambiguas, dejarán de aceptarse. Las selecciones de respuestas multi-output cambiarán incluso conservando la semilla.
- Mantener salas, fuentes, poses, ganancias y tiempos; conservar el comportamiento de experimentos con una sola salida cefálica y del array circular.
- Actualizar pruebas y documentación del contrato. T30, reproducibilidad de RAVEN, normalización de audio y reprocesamiento de resultados anteriores quedan fuera del cambio.

## Capabilities

### New Capabilities

- `receiver-subject-pairing`: identidad común y selección reproducible de respuestas cefálicas entre salidas, con validación de catálogos y conservación del contrato de manifiestos.

### Modified Capabilities

Ninguna. `source-identity-traceability` describe la identidad de los estímulos, no la del sujeto receptor; su contrato se conserva.

## Impact

- `apps/orchestrator`: validación semántica de catálogos, `_sample_hrtfs` en `experiment/sampler.py` y pruebas de generación de manifiestos.
- Ejemplos y `docs/architecture/contracts.md`: convención de identidad, emparejamiento y errores de configuración.
- Sin nuevas dependencias, claves YAML ni campos obligatorios de manifiesto. MATLAB y Clarity siguen consumiendo `hrtf_id` y `hrtf_path` sin cambios de interfaz.
- Los resultados existentes, incluido el run inspeccionado, no se reescriben ni se consideran retroactivamente emparejados.
