# Arquitectura del orquestador

El orquestador transforma un espacio de experimentación YAML en escenas estáticas concretas, coordina MATLAB/RAVEN y prepara o ejecuta la degradación opcional con Clarity. El análisis posterior pertenece a `apps/output-analyzer`, que consume metadatos de un run y no es una etapa obligatoria de generación.

La arquitectura transversal se describe en [visión general](../../docs/architecture/overview.md), [flujo de datos](../../docs/architecture/data-flow.md) y [contratos](../../docs/architecture/contracts.md). La [guía local](README.md) contiene comandos y ejemplos; las guías comunes cubren [instalación](../../docs/development/setup.md) y [pruebas](../../docs/development/testing.md).

## Interfaces y límites

- La CLI `acoustic-orchestrator` expone `generate-manifests`, `render-static` y `clarity-handoff` con `--submit` opcional.
- El YAML describe el espacio de muestreo. Los modelos Pydantic y el validador semántico determinan la entrada aceptada; los ejemplos ilustran su uso.
- MATLAB se invoca mediante subprocess y manifiestos JSON; Clarity mediante trabajos JSONL y subprocess. No hay dependencia Python directa del orquestador sobre Clarity ni integración MATLAB Engine.
- El flujo soportado es estático. Aunque el modelo enumera `dynamic`, la validación lo rechaza y el renderer dinámico MATLAB sigue siendo un placeholder.
- Python planifica el ruido de fondo; MATLAB sintetiza o carga el ruido y lo mezcla después del render.

### Observación de progreso

`pipeline/progress.py` contiene `ProgressEvent`, el callback opcional y un
`ProgressReporter` por ejecución. Los eventos de presentación no son evidencia
de aceptación, reanudación ni autorización de reintentos. Un callback defectuoso
se desactiva sin sustituir errores funcionales; `KeyboardInterrupt` se propaga.
No se persiste este estado en manifiestos o índices.

`terminal_progress.py` pertenece al CLI: Rich escribe en stderr y un solo hilo
posee la presentación interactiva. Las llamadas programáticas no crean interfaz
sin solicitar observación. Un consumidor puede recoger eventos sin imprimir:

```python
from acoustic_orchestrator.pipeline.progress import ProgressReporter

events = []
report = ProgressReporter(events.append)
report.begin("evaluacion", 1, "escenas")
report.start("scene_0001")
report.resolve("scene_0001", "nuevas")
report.finish()
```

El ETA usa solo unidades nuevas aceptadas y necesita tres muestras por etapa.
Las reutilizaciones actualizan contadores pero no velocidad; el paralelismo se
estima por tiempo de reloj, sin multiplicar dos veces por workers.

## Módulos actuales

| Área | Módulo | Responsabilidad |
|---|---|---|
| Entrada | `cli.py` | Comandos, resúmenes y códigos de salida |
| Configuración | `config/models.py` | Modelos que reflejan las secciones YAML |
| Configuración | `config/loader.py` | Carga YAML, compatibilidad legacy y rutas relativas al archivo de configuración |
| Configuración | `config/validator.py` | Restricciones semánticas, assets, geometría, políticas y perfiles auditivos |
| Experimento | `experiment/sampler.py` | Muestreo reproducible de salas, receptores, fuentes y ruido |
| Experimento | `experiment/geometry.py`, `acoustic_geometry.py` | Geometría poligonal, áreas y restricciones espaciales |
| Experimento | `experiment/room_acoustics.py` | Coeficientes de materiales y estimación acústica |
| Experimento | `experiment/reverberation_sampling.py` | Bins, cuotas globales y diagnóstico RT30 |
| Experimento | `experiment/treatment_catalog.py` | Catálogo sintético de tratamientos v1 |
| Experimento | `experiment/scene_builder.py` | Construcción del manifiesto estático schema 3.0 |
| Experimento | `experiment/manifest_writer.py` | Escritura JSON y control de sobrescritura |
| Desarrollo | `experiment/geometry_visualization.py` | Inspección opcional de huellas; requiere matplotlib y no se importa en producción |
| Pipeline | `pipeline/render_pipeline.py` | Coordinación, validación T30 RAVEN, reintentos, fan-out e índices |
| Pipeline | `pipeline/matlab_runner.py` | Runtime por variante e invocación de `apps/matlab/run_raven_static_render.m` |
| Pipeline | `pipeline/runtime_audio.py` | Preparación y recorte de audio requerido por runtime |
| Pipeline | `pipeline/output_paths.py` | Layout, nombres y rutas de variantes y trabajos Clarity |
| Pipeline | `pipeline/output_index.py` | Inspección de artefactos, estados, reanudación e índices JSONL |
| Pipeline | `pipeline/clarity_handoff.py` | Elegibilidad variante/perfil y trabajos Clarity schema 1.0 |
| Pipeline | `pipeline/clarity_runner.py` | Invocación del backend hermano `apps/clarity-backend` |

## De configuración a variantes

`generate-manifests` carga y valida la configuración, muestrea escenas y escribe un JSON de escena por índice aceptado. Las escenas fallidas se registran en `scene_sampling_failures.jsonl`; el resumen distingue lote completo, parcial y fallido. La geometría puede ser shoebox, trapezoidal o en L.

`render-static` genera los manifiestos y expande `receiver.hrtfs` en variantes. Cada variante tiene identidad `{scene_id}__{output_type}`, un manifiesto runtime y rutas WAV/JSON resueltas. Varias IR de un mismo tipo reciben identificadores distintos. Las salidas habilitadas incluyen `binaural_hrtf`, `bte_rear_hartf` y `bte_front_hartf`.

Los manifiestos de escena y runtime conservan posiciones públicas `[x, y, z]`, con `y` vertical y huella `[x, z]`. El runtime sustituye las rutas de salida, el nombre de proyecto, la lista IR por una variante y las rutas de audio preparadas cuando corresponde. **La reflexión Z de schemas 2.0/3.0 ocurre dentro del adaptador MATLAB**, no al serializar el runtime Python. Las posiciones y vectores de fuentes exportados en el resumen de render provienen de ese sistema adaptado RAVEN.

El pipeline valida las diez bandas T30 del render canónico antes de completar las variantes restantes. Si el resultado no cumple las condiciones finitas/positivas, reintenta dentro del presupuesto compartido de escena y registra el diagnóstico. La estimación Sabine usada para clasificar bins es una métrica distinta, agregada sobre siete bandas de 125 a 8000 Hz.

## Layout y estado

El run se ubica bajo `{outputs.artifact_root}/{outputs.run_name}`; si falta `run_name`, se usa el identificador del experimento. `resolve_artifact_layout` define:

```text
metadata/manifests/scene/
metadata/manifests/runtime/render/
metadata/manifests/runtime/clarity/clarity_jobs.jsonl
metadata/indexes/render_index.jsonl
metadata/indexes/scene_sampling_failures.jsonl
metadata/indexes/clarity_index.jsonl
output_audio/render/{receiver_output.output_subdir}/
output_audio/degraded/{output_type}/{hearing_profile_id}/
cropped_audio/    # solo cuando se prepara audio recortado
```

Los nombres WAV y JSON se obtienen de `outputs.naming.wav_pattern` y `metadata_pattern`; solamente `{scene_id}` y `{output_type}` son placeholders admitidos. Las claves antiguas de directorios independientes no son aceptadas por el loader. Los directorios se crean cuando una etapa escribe archivos.

Los índices JSONL registran planificación, resultados, validación e intentos de reanudación; la existencia de un WAV aislado no basta para dar una variante por completada. Las opciones `overwrite_existing` y `resume_if_possible` gobiernan estos comportamientos. `execution.num_workers` controla el paralelismo MATLAB; requiere licencias y un entorno local que tolere procesos concurrentes.

## Handoff Clarity opcional

`hearing_degradation.enabled` habilita la preparación tras render. `runner.auto_submit` controla el envío automático; `clarity-handoff --submit` solicita envío explícito. Sin `--submit`, la CLI de handoff prepara únicamente los trabajos.

El handoff combina variantes elegibles con perfiles del catálogo: exige un render completado y validado, tipo incluido en `input_targets`, WAV y metadatos de entrada presentes. El trabajo incluye identificadores, catálogo, `backend_invocation` y paths de entrada/salida. Los trabajos no elegibles quedan omitidos o bloqueados con su diagnóstico.

La salida degradada por defecto es `output_audio/degraded/{output_type}/{hearing_profile_id}/`. Conserva el basename del WAV renderizado y escribe un sidecar `{render_wav_stem}.json`. Los campos `expected_output_wav_path` y `expected_output_metadata_path` de cada trabajo son autoritativos para el backend y la reanudación. Un `hearing_degradation.output_dir` explícito puede cambiar la raíz.

## Límites del entorno

El backend MATLAB requiere 44100 Hz, ITA Toolbox, RAVEN y assets locales. Su preparación de proyecto tiene una ruta Windows fija; una instalación en otro entorno requiere comprobarla. Se aplica semilla MATLAB, pero no se ha comprobado un mecanismo equivalente para todas las operaciones RAVEN. Las pruebas unitarias con mocks no sustituyen una verificación acústica completa.
