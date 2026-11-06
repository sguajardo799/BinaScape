# Tasks

## 1. Orquestador: identidad y validación de catálogos

- [x] 1.1 Agregar helpers compartidos para descubrir archivos con el glob existente, extraer identidad por sufijo terminal e indexar por sujeto; verificar con pruebas unitarias `idNN`, nombres sin sufijo, comparación exacta, orden estable, identidad vacía y duplicados.
- [x] 1.2 Integrar la validación cruzada de las salidas cefálicas habilitadas en `config/validator.py`, conservando las comprobaciones individuales; verificar intersección parcial, catálogos disjuntos, capacidad insuficiente para el máximo `num_hrtfs` y errores con salidas, rutas y cantidades.
- [x] 1.3 Adaptar las fixtures multi-output a sujetos comunes sin eliminar casos negativos ni el caso de cantidades distintas; verificar que las pruebas de validación existentes siguen distinguiendo errores individuales de errores de emparejamiento.
- [x] 1.4 Documentar en `docs/architecture/contracts.md` la convención de identidad y las nuevas condiciones de error; verificar que los ejemplos de nombres producen las identidades esperadas en las pruebas del helper.

## 2. Orquestador: muestreo emparejado y compatibilidad de semilla

- [x] 2.1 Sustituir la selección independiente multi-output de `_sample_hrtfs` por una secuencia de sujetos comunes sin repetición y prefijos por salida; verificar tripletas 1/1/1, selección múltiple, cantidades 2/1/1, solo front/rear y exclusión de salidas deshabilitadas y del array.
- [x] 2.2 Aislar la selección emparejada en un RNG clonado y conservar en el RNG original las operaciones legacy; verificar igualdad del estado final frente al algoritmo previo con múltiples semillas y cardinalidades, incluido el camino de fallback de muestreo de escenas.
- [x] 2.3 Conservar la ruta legacy para una sola salida cefálica y la ausencia de selección cefálica para array-only; verificar selección exacta con la misma semilla y catálogos arbitrarios, sin exigir identidades comunes.
- [x] 2.4 Extender `tests/test_generate_manifests.py` con una comparación del muestreo previo y nuevo para entradas válidas, incluyendo una candidata descartada antes de aceptar la escena; verificar igualdad de sala, estímulos, directividades, poses, ganancias y tiempos, excluyendo únicamente las respuestas.
- [x] 2.5 Actualizar la explicación multi-output de `apps/orchestrator/examples/example_config.yml` y la documentación del contrato para describir ordinales, cantidades distintas y selección por semilla; verificar coherencia con los casos 1/1/1 y 2/1/1 y con los nombres públicos existentes.

## 3. Contratos de integración: runtime, MATLAB y Clarity

- [x] 3.1 Extender las pruebas de manifiestos runtime para comprobar que cada variante conserva su archivo emparejado, su ID y las condiciones compartidas de escena; verificar también rutas de salida sin cambios para respuestas simples y múltiples.
- [x] 3.2 Actualizar la prueba de mezcla HRTF/HARTF/array con varias HRTF usando catálogos compatibles; verificar que siguen generándose las cantidades solicitadas y que Clarity recibe únicamente los targets admitidos, sin nuevos campos obligatorios.
- [x] 3.3 Añadir una regresión de conservación de manifiestos existentes durante reanudación; verificar que no se reescriben descriptores históricos no emparejados ni WAV previos para imponer la nueva selección, usando archivos temporales de prueba.
- [x] 3.4 Documentar la distinción entre generaciones nuevas y runs históricos, y una comprobación de aceptación sin RAVEN en un run nuevo; verificar que la guía no promete corregir T30, no pide sobrescribir resultados anteriores y no requiere modificar ajustes del usuario en `static_example.yml`.

## 4. Verificación integral

- [x] 4.1 Ejecutar la suite de `apps/orchestrator/tests` con el entorno del proyecto; verificar que pasan las pruebas de catálogos, generación, reanudación, array y handoff Clarity sin necesitar un render MATLAB real.
- [x] 4.2 Ejecutar una comprobación de aceptación de 30 manifiestos con los catálogos reales y semilla 42, en un run nuevo y sin render; verificar igualdad de sujeto en cada tripleta, existencia de los archivos seleccionados y conservación de condiciones entre variantes, sin tocar `outputs/e2e_4_worker_hartf_s42`.
- [x] 4.3 Ejecutar `openspec validate pair-receiver-responses-by-subject --strict` y revisar el diff; verificar alineación con la especificación, ausencia de cambios de T30/RAVEN y conservación de modificaciones preexistentes del usuario.

## 5. Compatibilidad del soporte de pruebas (autorizada tras la primera suite)

- [x] 5.1 Mantener el hash histórico de fuentes proyectando únicamente los nombres de las fixtures receptoras intencionalmente cambiadas; verificar `test_flat_sampling_matches_pre_identity_baseline` sin modificar el hash esperado ni los estímulos muestreados.
- [x] 5.2 Aislar el input histórico estático de los goldens del YAML editable y normalizar representaciones de rutas por plataforma solo en el soporte de pruebas; verificar todos los tests de goldens y backend explícito/implícito sin regenerar snapshots esperados ni editar la configuración del usuario.
- [x] 5.3 Corregir el lanzador de la prueba E2E Pyroomacoustics para detectar el console script Windows y ejecutar una CLI real en su fallback; verificar el caso E2E y una prueba del fallback sin cambios de producción en Pyroomacoustics o T30.
