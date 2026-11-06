# Design

## Context

Ver `proposal.md` para la motivación. Actualmente `_sample_hrtfs` (`apps/orchestrator/src/acoustic_orchestrator/experiment/sampler.py:601`) enumera cada catálogo y usa `rng.choice` o `rng.sample` de manera independiente. `_validate_receiver_output` (`config/validator.py:558`) comprueba capacidad individual, no sujetos comunes.

El sampler genera las fuentes antes de seleccionar respuestas, pero puede continuar buscando otra realización si la candidata cae fuera del bin solicitado; cambiar el consumo del RNG también puede cambiar esas búsquedas posteriores. La especificación existente `source-identity-traceability` exige conservar las propiedades de escena al mantener semilla e inputs.

`pipeline/matlab_runner.py` copia cada descriptor a `receiver.hrtfs` en el runtime de una variante. MATLAB y Clarity no necesitan conocer cómo se seleccionó el sujeto. Las pruebas actuales cubren múltiples HRTF, cantidades distintas y mezcla con array; deben conservar esos usos, no eliminarlos para simplificar el emparejamiento.

## Goals / Non-Goals

**Goals:**
- Resolver identidad y capacidad con la misma lógica en validación y muestreo.
- Emparejar automáticamente las salidas cefálicas habilitadas sin nueva configuración.
- Mantener el contrato de manifiestos y el avance del RNG de escena para aislar el cambio a las respuestas seleccionadas.

**Non-Goals:**
- Comprobar que el nombre del archivo acredita biológicamente la identidad; se confía en la convención del catálogo.
- Agregar `subject_id` obligatorio, inspeccionar contenido DAFF o construir un registro externo de sujetos.
- Cambiar T30, semillas de RAVEN, BRIR, normalización, elegibilidad Clarity o el flujo dinámico.
- Reparar o regenerar runs históricos ni reinterpretar sus descriptores durante la reanudación.

## Decisions

### 1. Índice de identidad ligero y compartido

Agregar funciones pequeñas en un módulo de catálogo del orquestador, próximo a `experiment/sampler.py`, reutilizable desde el validador y sin dependencias de configuración/pipeline. No introducir clases de servicio ni otra jerarquía de modelos.

Para cada archivo descubierto con el glob actual, tomar `Path.stem` y quitar, si existe, exactamente uno de los sufijos terminales `_HARTF_front`, `_HARTF_rear` o `_HRTF`. El resto es el sujeto; sin sufijo, el stem completo es la identidad. Comparación exacta y orden lexicográfico estable. Rechazar identidad vacía o duplicada por catálogo participante, con los archivos en conflicto.

Esto admite los assets `idNN_*` existentes y catálogos con nombres idénticos sin sufijos. No normalizar `id01` a `id1` ni inferir IDs a partir de números internos: hacerlo podría emparejar sujetos distintos. Alternativas descartadas: emparejar por posición en la lista, elegir el primer archivo de cada carpeta o añadir un CSV de correspondencias no solicitado.

### 2. Seleccionar una secuencia común y repartir prefijos

Aplicar emparejamiento cuando al menos dos salidas cefálicas estén habilitadas. Crear la intersección ordenada de identidades y seleccionar sin repetición `max(num_hrtfs)` sujetos. Cada salida recibe el prefijo de longitud `num_hrtfs` y resuelve cada sujeto a su archivo de ese catálogo.

Mantener el orden de salidas actual y los IDs actuales, incluida la regla sin sufijo para una sola respuesta. Por ejemplo, con cantidades HRTF=2/front=1/rear=1, los IDs front y rear corresponden a `binaural_hrtf__1`; la segunda HRTF mantiene su propio sujeto distinto. Todos los sujetos elegibles deben existir en todos los catálogos participantes, incluso si una salida solicita menos variantes.

Se conserva así la cardinalidad que ya cubren las pruebas. Exigir cantidades iguales rompería una función existente sin necesidad; muestrear separadamente los sobrantes complicaría el significado de la población compartida. Las salidas deshabilitadas y circular_array no restringen la selección.

### 3. Validar antes de renderizar y no degradar a selección independiente

Conservar la validación individual de directorios, glob, cantidades y subdirectorios. Agregar validación cruzada solo para el grupo cefálico participante. Si el grupo no tiene suficientes sujetos comunes, reportar salidas, rutas, capacidad común y capacidad solicitada. Duplicados e identidades vacías son errores, no archivos para ignorar silenciosamente.

El sampler usa el mismo índice y conserva comprobaciones defensivas por si los catálogos cambian entre validación y muestreo. No persistir cachés ni introducir un índice compartido que pueda quedar obsoleto. Una sola salida conserva íntegramente su ruta anterior de muestreo y validación.

### 4. Conservar el estado aleatorio de escena

Antes de seleccionar respuestas emparejadas, clonar el estado recibido en un RNG local para elegir la secuencia común. En el RNG de escena original, reproducir las mismas operaciones legacy de `choice`/`sample`, con los mismos catálogos ordenados y cardinalidades, descartando únicamente sus selecciones independientes.

El RNG original queda exactamente en el estado anterior esperado, también en búsquedas con fallback, mientras el RNG local decide las respuestas emparejadas. Es una compatibilidad explícita, no una garantía sobre el RNG interno de RAVEN. Encapsularlo y explicar por qué existen ambos caminos; una prueba debe comparar estados finales además de manifiestos.

Alternativa descartada: sustituir directamente todos los draws por un solo `sample`, porque alteraría intentos posteriores y podría cambiar salas o estímulos fuera del alcance solicitado. La reproducción legacy no requiere renderizar ni leer el contenido de los archivos.

### 5. Trazabilidad por el contrato existente

No agregar campos obligatorios ni cambiar schema_version. El sujeto puede verificarse aplicando la convención a `hrtf_path` en escena, runtime y sidecar. Las rutas e identificadores siguen basados en tipo de salida y ordinal; no usar el ID de sujeto como `hrtf_id`, pues este último identifica la variante, no la persona.

Dependencias entre componentes: el orquestador produce el emparejamiento; MATLAB recibe las rutas ya resueltas y conserva su interfaz; Clarity continúa procesando solo los targets admitidos actualmente; el analizador no requiere cambios. Las pruebas de integración deben confirmar que la separación de variantes no pierde el archivo seleccionado.

### 6. Conservar la frontera histórica de pruebas

La primera suite completa detectó ocho fallos; el usuario autorizó reparar el soporte de pruebas sin cambiar su YAML ni T30. El hash histórico de fuentes se conserva proyectando solo los nombres de las fixtures receptoras renombradas. Los goldens estáticos usan ahora una copia independiente del input histórico single-HRTF, semánticamente idéntica al ejemplo original versionado; sus snapshots JSON esperados no se regeneran. Los placeholders normalizan separadores Windows y la representación histórica POSIX de la ruta fija RAVEN solo en las pruebas, sin cambiar la resolución de rutas de producción.

La comprobación del ejemplo editable actual se mantiene separada mediante la aceptación de 30 escenas y 90 runtimes con assets reales. El lanzador E2E reconoce el console script `.exe` y llama explícitamente a `main()` en su fallback: evita un proceso exitoso que no ejecutaba la CLI. Estos ajustes no modifican Pyroomacoustics ni el render acústico de producción.

## Risks / Trade-offs

- [Convención de nombres incorrecta en los assets] → Documentar que el emparejamiento garantiza identidad nominal y no certifica el contenido DAFF.
- [Catálogos antes válidos ahora incompatibles] → Fallar de forma explícita y documentar la migración; nunca elegir otro sujeto para completar una salida.
- [Menor población al intersectar catálogos incompletos] → Informar capacidad común; no exigir que todos los catálogos tengan exactamente los mismos miembros.
- [Costo de enumerar catálogos y reproducir draws legacy] → Funciones simples, sin leer DAFF; mantener esta compatibilidad acotada al muestreo cefálico.
- [Fixtures sintéticas con nombres distintos por salida] → Darles sujetos comunes reales y conservar pruebas negativas separadas; no debilitar la validación para acomodarlas.
- [Confundir runs antiguos con nuevos] → Documentar que no se corrigen resultados ni se reemplazan manifiestos existentes al reanudar; usar un run nuevo para verificar el nuevo comportamiento.

## Migration Plan

1. Incorporar helpers, validación y muestreo junto con pruebas; no editar ni borrar artefactos generados anteriores.
2. Actualizar fixtures y ejemplos que describen HARTF para reflejar identidades comunes. Preservar los ajustes del usuario en `configs/experiments/static_example.yml`; cualquier edición futura debe ser puntual, no sustituir el archivo por su versión de Git.
3. Documentar la convención y el cambio de selección multi-output en `docs/architecture/contracts.md` y ejemplos locales relevantes, sin afirmar que T30 o RAVEN quedaron corregidos.
4. Verificar con pruebas automatizadas sin MATLAB. Para aceptación local con los assets reales, generar manifiestos en una ubicación de run nueva; no sobrescribir `outputs/e2e_4_worker_hartf_s42` ni lanzar RAVEN como requisito de esta corrección de muestreo.
5. Si se revierte la funcionalidad, volver al código previo restaura selección independiente para nuevas generaciones; conservar los artefactos ya generados y no convertir el rollback en una migración de resultados.
