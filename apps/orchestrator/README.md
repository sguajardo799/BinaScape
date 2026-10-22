# Acoustic Orchestrator

Python CLI for coordinating the static binaural acoustic pipeline. It loads an
experiment YAML file, validates and samples static scenes, writes scene
manifests, invokes the MATLAB/RAVEN backend, and prepares optional Clarity
hearing-loss degradation jobs.

The primary supported workflow is root-first execution from the monorepo root.

See the [documentation index](../../docs/README.md), [local architecture](ARCHITECTURE.md),
[shared contracts](../../docs/architecture/contracts.md), [setup](../../docs/development/setup.md),
and [testing guide](../../docs/development/testing.md). The sibling
[output analyzer](../output-analyzer/README.md) can inspect run metadata after
execution; it is an optional analysis application.

## Requirements

- Python `>=3.12,<3.13`
- `uv`
- MATLAB on `PATH` as `matlab` for `render-static`
- A local MATLAB/RAVEN backend at `apps/matlab`
- ITA Toolbox and RAVEN configured in MATLAB
- Local assets referenced by the selected config
- The sibling Clarity backend at `apps/clarity-backend` when submitting hearing
  degradation jobs

Install app dependencies from this directory:

```sh
uv sync --extra dev
```

Or from the repository root:

```sh
uv sync --project apps/orchestrator --extra dev
```

## Commands

Show CLI help:

```sh
uv run --project apps/orchestrator acoustic-orchestrator --help
```

Generate manifests only:

```sh
uv run --project apps/orchestrator acoustic-orchestrator generate-manifests configs/experiments/static_example.yml
```

Run the official opt-in MATLAB/RAVEN smoke directly through the orchestrator:

```sh
uv run --project apps/orchestrator acoustic-orchestrator render-static configs/experiments/static_example.yml
```

This command samples all configured room-shape candidates and requires the
referenced local assets, MATLAB, ITA Toolbox, and RAVEN.

Prepare Clarity handoff artifacts without submission:

```sh
uv run --project apps/orchestrator acoustic-orchestrator clarity-handoff configs/experiments/static_example.yml
```

Prepare and submit Clarity handoff artifacts to the sibling backend:

```sh
uv run --project apps/orchestrator acoustic-orchestrator clarity-handoff configs/experiments/static_example.yml --submit
```

From inside `apps/orchestrator`, the equivalent app-local example command is:

```sh
uv run acoustic-orchestrator generate-manifests .\examples\example_config.yml
```

The local `main.py` entrypoint can also be used for development:

```sh
uv run python .\main.py generate-manifests .\examples\example_config.yml
```

For development inspection of sampled room footprints, install the optional
`dev` extra (which includes matplotlib) and call
`acoustic_orchestrator.experiment.geometry_visualization.plot_room_geometry`.
The helper is intentionally not imported by the production pipeline.

## Config Examples

Root-level examples:

- `configs/experiments/static_example.yml`: root-first static
  pipeline example.
- `configs/experiments/speech_fixed_position_e2e.yml`: fixed-position experiment example.
- `configs/hearing/hearing_profiles.yaml`: hearing profile catalog for Clarity
  degradation.

App-local examples:

- `apps/orchestrator/examples/example_config.yml`
- `apps/orchestrator/examples/example_config_w_loss.yml`
- `apps/orchestrator/examples/example_config_clapping_background_noise.yml`
- `apps/orchestrator/examples/example_scene_static.json`
- `apps/orchestrator/examples/hearing_profiles.yaml`

The app-local examples are useful for tests and development. The root-level
config is the preferred starting point for end-to-end runs from the repository
root.

## Generate Manifests

```sh
uv run --project apps/orchestrator acoustic-orchestrator generate-manifests configs/experiments/static_example.yml
```

This command:

1. Reads and validates the YAML config.
2. Resolves relative paths from the config file location.
3. Samples static scenes according to `execution.num_simulations`.
4. Writes one scene manifest per generated scene under
   `{outputs.artifact_root}/{run_name}/metadata/manifests/scene/`.

Run artifacts are grouped under `metadata/manifests/`, `metadata/indexes/`,
and `output_audio/`. The optional `cropped_audio/` directory is created only when
source audio must be trimmed. Output directories are created when a stage writes
files, so a manifest-only run does not create empty render or Clarity directories.

Generated scene manifests use schema **3.0**. MATLAB accepts schemas 1.0, 2.0,
and 3.0; the examples illustrate contracts implemented by production validators.
The loader rejects legacy independent output-root keys and
`room_sampling.max_rt30_s`; use `outputs.artifact_root`, `outputs.run_name`,
and `room_sampling.reverberation`.

Current material contract in generated manifests:

- `room.materials` is preserved as a surface-to-`material_id` map.
- `room.material_files` is added as a surface-to-file map.
- Each material file path is absolute.
- Material entries do not emit `surface_id`.
- Schema 3.0 adds `room.acoustic_surfaces` with base, treatment and authoritative
  effective absorption/scattering vectors for every surface.

The required `room_sampling.reverberation` block defines a global uniform
distribution over equal-width bins. The sampler preassigns balanced quotas by
scene index, estimates all ten RAVEN octave bands with Sabine and classifies on
the seven-band mean from 125 Hz through 8 kHz. Walls and ceiling receive
independent synthetic treatments. A uniformly retained in-range candidate is
used as a soft fallback when the requested bin is not reached. Manifests contain
`reverberation_sampling`; their embedded `reverberation_batch` is a historical
snapshot of quotas, observations, deviations, fallbacks, failures and statistical
resolution. Current batch statistics live in
`metadata/indexes/static_run_summary.json`, not in rewritten reused manifests.

The bin divisibility check uses an absolute tolerance of `1e-9 s` (plus a
relative tolerance of `1e-12`) on the reconstructed time span. Bins follow
`[lower, upper)` semantics, except that the last bin includes its upper bound.
For a single-bin experiment, set its width to the complete range:

```yaml
room_sampling:
  reverberation:
    metric: estimated_rt30_s
    estimator: sabine
    estimator_version: sabine_polygon_octaves_v4
    aggregation: arithmetic_mean
    mean_bands_hz: [125, 250, 500, 1000, 2000, 4000, 8000]
    distribution:
      type: uniform
      scope: global
      range_s: {min: 0.4, max: 0.6}
      bin_width_s: 0.2
      quota_tolerance_fraction: 0.10
    treatment:
      catalog_version: 1
      mix_model: area_weighted_linear
      mix_model_version: 1
      eligible_surface_types: [wall, ceiling]
      max_treatments_per_surface: 1
      preserve_base_scattering: true
      allow_none: true
      wall_coverage: {min: 0.0, max: 1.0}
      ceiling_coverage: {min: 0.0, max: 1.0}
```

The v1 synthetic treatment catalog requires 31 finite absorption coefficients
in `[0, 1]` and limits the absolute step between adjacent frequency bands to
`0.08`. This is an acoustic-plausibility rule, not a claim of construction or
commercial-product realism. `quota_tolerance_fraction` is diagnostic: bins
above it are reported and a successful batch becomes
`complete_with_deviation`, but the tolerance never changes classification or
invalidates the batch. Small quotas also report
`insufficient_statistical_resolution` when their discrete resolution is coarser
than the requested tolerance.

Coordinate note for RAVEN: public manifests use `[x, y, z]` with the footprint
in `[x, z]`. For schemas 2.0 and 3.0, the MATLAB adapter applies the same explicit
`[x, y, z] -> [x, y, -z]` reflection to positions and orientation vectors.
Legacy schema 1.0 keeps its existing shoebox contract. Scene and runtime JSON
preserve public coordinates: the reflection takes place inside MATLAB. Render
metadata source positions and orientation vectors therefore use the adapted
RAVEN coordinates for schemas 2.0/3.0.

## Background Noise Planning

The orchestrator plans background noise metadata in the scene manifest. MATLAB
performs the downstream synthesis and mixing.

The optional `background_noise` config supports:

- `enabled`
- `snr_db`
- `allow_multiple_layers`
- `colored` strategies with `white`, `pink`, or `brown`
- `audio_folder` strategies with `noise_type`, `audio_dir`, and `file_pattern`

Every emitted manifest includes `background_noise`. When disabled, it is exactly:

```json
{ "enabled": false, "layers": [] }
```

When enabled, the orchestrator chooses concrete layers per scene in a
reproducible and balanced way, including absolute paths for selected audio
files. It does not perform DSP or audio mixing.

## Static Render

```sh
uv run --project apps/orchestrator acoustic-orchestrator render-static configs/experiments/static_example.yml
```

This command:

1. Inspects the complete static plan and existing manifests before applying changes.
2. Reuses compatible variants and creates missing scene/runtime inputs only for
   work that must run, one runtime per enabled receiver-output variant.
3. Invokes `matlab -batch` through the fixed backend entrypoint
   `apps/matlab/run_raven_static_render.m`.
4. Writes rendered WAV files and render metadata to configured paths.
5. Updates output indexes for planned, completed, resumed, failed, partial,
   blocked, skipped, or inconsistent variants.
6. If `hearing_degradation.enabled=true`, prepares Clarity handoff artifacts.
7. Submits Clarity jobs only when
   `hearing_degradation.runner.auto_submit=true`.

Parallel MATLAB/RAVEN execution is controlled by `execution.num_workers`.
`num_workers: 1` keeps execution sequential. Higher values may launch multiple
MATLAB subprocesses after runtime inputs are prepared. Do not configure more
workers than available MATLAB/RAVEN licenses, and return to `1` if the local
RAVEN setup shows hidden global state.

The canonical single-output example enables `binaural_hrtf`. MATLAB requires
`render.sample_rate_hz: 44100`. Before completing the remaining variants,
the pipeline checks that the canonical RAVEN T30 has exactly ten finite,
positive bands and can retry within the scene budget. This measured T30 is
distinct from the seven-band Sabine estimate used by sampling. The runtime also
supports additional receiver IR outputs when enabled under `receiver_outputs`.

## Diagnóstico por invocación MATLAB

Cada intento real (incluidos reintentos T30) tiene un UUID y archivos separados:
`{artifact_root}/{run_name}/metadata/diagnostics/matlab/{attempt_id}/`.
Las variantes reanudadas sin ejecutar MATLAB no crean intentos. El runner Python
directo acepta `diagnostics_dir=`; si se omite, usa `matlab_diagnostics/` junto al
manifiesto. No se añaden opciones obligatorias al YAML.

- `input_manifest.json`: copia de entrada; sobrevive a sobrescrituras o descartes T30.
- `console.log`: stdout y stderr combinados, escritos a disco sin reproducción en vivo.
- `events.jsonl`: eventos MATLAB incrementales, con versión, intento, secuencia y UTC.
- `invocation.json`: resumen inicial `running`, actualizado al observar la terminación.

El resumen versión 1 contiene identidades, comando como lista, rutas, tiempos UTC,
duración monotónica en segundos, código de salida (`null` sin resultado de proceso),
error, última etapa/subpaso iniciado y completado, y limitaciones en `diagnostic_errors`.
Ejemplo de campos independientes del resumen (extracto):

```json
{
  "schema_version": 1,
  "process_status": "failed",
  "exit_code": 7,
  "entrypoint_completed": true,
  "failure_location": "post_entrypoint"
}
```

`entrypoint_completed` no implica éxito del proceso: solo una terminación observada
con código cero establece `process_status=succeeded`. Sin evento final se conserva
`entrypoint_completed=false`, aunque el proceso termine con cero. `running` tras una
caída del orquestador no confirma que MATLAB siga vivo. No hay reconciliación automática.

Las etapas son `initialization`, `simulation` y `artifact_writing`; dentro de
`simulation` se distinguen `raven_run` y `audio_postprocessing`, sin instrumentar
internamente RAVEN. Sin eventos, un fallo queda `indeterminate`; un fallo posterior
al evento final es `post_entrypoint`, sin atribuir una causa concreta al cierre.
Cada artefacto registra ruta, existencia anterior/posterior y `write_confirmed`
solo por evento del intento actual. Existencia no equivale a escritura ni validación.

Estos archivos **no cambian el índice de render, la reanudación ni las excepciones**.
Los fallos exclusivos del diagnóstico advierten y conservan la evidencia posible;
si no se puede abrir el log se hereda la consola y si no se puede preparar contexto
se ejecuta sin eventos. La persistencia depende del almacenamiento disponible.
Una línea final incompleta de eventos se ignora con advertencia.

La retención es manual: no se borran diagnósticos al descartar WAV/metadatos.
Los logs y copias pueden crecer y contener rutas locales; revísalos antes de compartirlos.

Comprobación opt-in con MATLAB/ITA/RAVEN reales (el destino debe ser nuevo):

```sh
uv run --project apps/orchestrator python apps/orchestrator/tests/verify_matlab_diagnostics.py --output-root <directorio-nuevo>
```

Verifica un render array-only y un manifiesto inválido, conserva la evidencia y
escribe `acceptance.json`. No simula fallos nativos de cierre ni modifica ejemplos versionados.

## Aceptación funcional y fallos del proceso MATLAB

El pipeline registra tres resultados independientes: **proceso**, **validez del
render** y **decisión de continuación**. Un código no cero no equivale por sí solo
a render incompleto; un mensaje de éxito o un archivo existente tampoco acreditan
un render válido.

Cada invocación prepara evidencia funcional fuera de los diagnósticos:

`{artifact_root}/{run_name}/metadata/render_attempts/{attempt_id}/`

- `input_manifest.json`: snapshot de bytes exactos que se pasa a MATLAB.
- `completion.json`: constancia funcional versión 1, con identidad del intento,
  SHA-256 del manifiesto e inventario exacto de WAV/metadatos con tamaño y SHA-256.
- `failure.json`: informe funcional opcional versión 1 de entrada inválida durante
  carga/validación MATLAB; no es una constancia de finalización ni un diagnóstico.
- `outcome.json`: resultado observado del proceso, validaciones, decisión, error
  original y estado de persistencia. Una decisión solo queda confirmada cuando
  `commit_status=committed` y se ha publicado también el índice correspondiente.

MATLAB publica la constancia atómicamente después de cerrar todas las salidas.
La comprobación de hashes **no sustituye** la validación completa del WAV PCM,
frecuencia/canales/duración, estructura del sidecar, fuentes y procedencia,
receptor/IR o geometría del array, semilla, ruido y resultados acústicos requeridos.
La variante canónica mantiene su puerta T30 de diez bandas finitas positivas y el
presupuesto existente de remuestreo. No se añaden umbrales de calidad de señal ni
un requisito de coincidencia entre T30 RAVEN y los bins Sabine.

| Evidencia | Decisión |
|---|---|
| Código cero y artefactos válidos | Continuar; no exigir constancia a productores anteriores |
| Código no cero observado, constancia válida y todas las validaciones satisfactorias | `continue_with_warning`, conservando `process_status=failed` y el código original |
| Constancia presente pero contradictoria | Abortar, incluso con código cero |
| Artefactos incompletos, corruptos o no verificables | No aceptar; conservar el error del proceso si existe |
| Artefactos íntegros y T30 canónico rechazado | Política T30 existente, sin aceptar la escena ni eludir límites; con código no cero también se exige constancia válida |
| Fallo de lanzamiento o terminación no observada | No recuperar |

El índice conserva los campos anteriores y añade identidad/rutas del intento,
`process_status`, `exit_code`, `render_validity`, `decision` y `recovery_reason`.
Por ello, el estado de archivos no debe interpretarse sin la decisión funcional.
El outcome recuperado conserva `error` y una advertencia durable con código
`matlab_process_failed_after_completion`. La consola también advierte con el UUID y
la ruta de evidencia; la CLI muestra:

```text
MATLAB procesos_fallidos=1 recuperados=1 recuperaciones_reanudadas=0
```

`procesos_fallidos` cuenta intentos de esta ejecución, incluidos los descartados
por T30; `recuperados` cuenta los aceptados con advertencia y persistencia confirmada.
`recuperaciones_reanudadas` es un contador separado para variantes reutilizadas
con recuperación previa. La recuperación por sí sola no cambia el exit de CLI a
fallo si el experimento fue aceptado; los códigos por fallo o muestreo se mantienen.

`recovery_reason=post_completion` significa finalización funcional acreditada. No
confirma la causa del cierre. El diagnóstico solo usa `post_entrypoint` si observó
su evento final; si no lo observó, no lo inventa aunque el render sea recuperable.
Ni eventos ni consola autorizan aceptación. Sus fallos siguen siendo de mejor
esfuerzo; los fallos de almacenamiento funcional impiden continuar y conservan
accesibles ambos errores cuando coinciden con un proceso fallido.

El helper directo `render_manifest_with_matlab(...)` acepta `attempts_dir=`
(fallback `render_attempts/` junto al manifiesto), además del `diagnostics_dir=`
independiente. Devuelve `MatlabProcessResult` con `exit_code`, `error`, estado del
proceso e intento; **ya no propaga automáticamente `CalledProcessError`**. El caller
debe comprobar el resultado; la política de aceptación y su persistencia pertenecen
al pipeline. Errores de preparación/persistencia funcional sí se propagan. El helper
por sí solo no acepta artefactos ni convierte una terminación desconocida en éxito.

Smoke opt-in con MATLAB/ITA/RAVEN reales, en un destino que no exista:

```sh
uv run --project apps/orchestrator python apps/orchestrator/tests/verify_render_acceptance.py --output-root <directorio-nuevo>
```

Ejecuta un render array-only normal y otro cuyo proceso falla deliberadamente
después de retornar el entrypoint. La inyección existe solo en el harness; no cambia
el renderer ni el audio de producción. Conserva outcome, constancia y diagnóstico,
y escribe `acceptance.json` solo si ambas fases superan sus comprobaciones.

### Plan y reanudación protegida

`resume_if_possible: true` junto con `overwrite_existing: false` es la combinación
de reanudación protegida soportada (véase `examples/example_config.yml`). El
preflight inspecciona el lote completo antes de escribir: JSON corrupto, claves
duplicadas, valores no finitos, identidades/poses/superficies inválidas, entradas
incompatibles o salidas parciales protegidas producen conflicto con ruta y motivo.
No se arregla ese conflicto activando sobrescritura de forma automática. Una
variante sin salidas puede ejecutarse; una salida preexistente no reutilizable no
se borra, mueve ni reemplaza con sobrescritura desactivada.

`metadata/indexes/static_plan.json` (versión 1) registra configuración funcional
normalizada, hash del contrato de generación, identidad/número de escenas,
versiones y catálogo ordenado, con rutas y hashes de entradas seleccionadas.
Workers, reanudación, sobrescritura y política de reintentos no cambian la identidad
acústica; cambios acústicos, de catálogo, versiones o número de escenas sí pueden
impedir compatibilidad. El plan no sustituye la validación de cada manifiesto.

Un lote histórico sin plan requiere replay en memoria de su realización persistida:
índice, semilla entera exacta, intento de muestreo y versiones disponibles. Se
conserva el intento seleccionado después de T30, no se vuelve a la primera escena.
Solo tras verificar todo el lote se publica adopción; las huellas observadas ahora
no se presentan como huellas originales desconocidas. Si el replay no acredita
compatibilidad, se informa conflicto. Se completan huecos con IDs estables y se
conserva el journal de fallos/revisiones de muestreo y rechazos T30.

La reutilización conserva **bytes y fechas de modificación** de escena, runtime,
audio preparado, WAV y sidecar. Primero se construye la entrada esperada en memoria;
no se llama al writer, al preparador de audio con efectos ni a MATLAB para variantes
válidas. Audio preparado alterado es conflicto, no regeneración silenciosa. Índices
y resúmenes actuales se publican por separado. Incluso con ambas opciones en `true`
se prefieren resultados compatibles; `false/false` no autoriza sustituir existentes.

`metadata/indexes/static_run_summary.json` (versión 1) separa `generated_scenes`,
`acoustically_accepted_scenes` y variantes completadas: generar una escena no acredita
T30 ni render completo. El resumen refleja también lotes interrumpidos; los campos
embebidos históricos no se refrescan al reanudar ni borran rechazos conocidos.

Una realización nueva por rechazo T30 se guarda, junto con la revisión del plan,
antes de invocarla. No se remuestrea una escena si eso sustituiría variantes válidas
protegidas por `overwrite_existing=false`. Los restos y recortes de realizaciones
descartadas se archivan con sus huellas; un presupuesto T30 agotado se conserva como
estado terminal, no como un manifiesto ausente que deba regenerarse al reiniciar.
Los runtimes compatibles conservan sus bytes exactos, incluidos finales CRLF legacy:
la identidad de una cadena no se calcula reserializando un JSON existente.

Solo puede haber un orquestador escritor por lote. La exclusión de escritor se
obtiene antes de aplicar acciones y se revalidan precondiciones de archivos. Un
lock de estado incierto **no se rompe automáticamente**: investigar y confirmar que
no quedan escritores antes de intervenir. No sustituye protección ante cambios
externos; el paralelismo interno por variantes sigue permitido.

### Reanudación, índices históricos y entrega a Clarity

Con `resume_if_possible=true`, el pipeline compara cada runtime existente con la
entrada que espera generar **antes de sobrescribirlo**. Considera fuentes y rutas
preparadas, ganancias, tiempos, sala, receptor/IR o geometría y semilla; no solo
los nombres de archivos. Cambios de cuotas/resumen de lote o de trazabilidad de
muestreo no invalidan por sí solos el audio si la entrada acústica sigue igual.

La reanudación consulta el historial funcional más reciente por variante. Un
intento `running`, una decisión abortada/rechazada, una publicación `pending` o
`failed`, o una referencia a evidencia desaparecida no se convierten en éxito por
reiniciar. Esto también se comprueba si falta el índice: los archivos del intento
no se consideran legacy solo porque se haya perdido una fila JSONL. Publicar un
plan no borra las decisiones anteriores. Si el historial no puede leerse o
atribuirse con seguridad, se bloquea su reutilización; no se borra evidencia
automáticamente ni se afirma que el proceso siga vivo.

Una variante recuperada se reutiliza solo si siguen coincidiendo las huellas de
su snapshot y salidas con la constancia original, la entrada vigente es compatible
y sus artefactos vuelven a superar la validación. No crea otro intento: conserva
UUID, outcome, código no cero, `process_status=failed` y advertencia. En una
reanudación sin MATLAB, por ejemplo, la CLI puede mostrar:

```text
MATLAB procesos_fallidos=0 recuperados=0 recuperaciones_reanudadas=1
```

La lectura de índices anteriores es aditiva; no hay migración masiva ni se fabrican
constancias o éxitos de proceso. Una salida legacy sin evidencia funcional y sin
fallo conocido puede reutilizarse si tiene runtime y artefactos estrictamente
válidos. Un `.wav` de texto, sidecar `{}`, datos truncados o identidad insuficiente
requieren un render nuevo aunque antes fueran clasificados como completos por
existencia. Una recuperación declarada sin constancia verificable no es legacy.
Con `resume_if_possible=false` se vuelve a invocar MATLAB incluso si las salidas
anteriores son válidas, siempre que la sobrescritura necesaria esté autorizada;
los intentos anteriores permanecen como historial.

Clarity vuelve a comprobar archivos, manifiesto/procedencia e historial antes de
crear jobs y otra vez al enviarlos; no confía solo en `completed` y `validation.ok`
guardados. Si existe manifiesto de escena, también contrasta la entrada runtime
con esa escena. Un archivo alterado o intento pendiente posterior bloquea el job y
no llega al backend. Solo las HRTF seleccionadas son elegibles, incluidas sus
variantes numeradas; HARTF y array siguen siendo render-only aunque tengan dos
canales. Se mantienen basename WAV, directorio por perfil y sidecar adyacente.

La validación acredita el contrato y la integridad disponibles, no fidelidad física
completa. No firma archivos contra productores maliciosos ni demuestra el contenido
original de assets históricos sin huellas propias. El lock del lote excluye otros
orquestadores, no escritores externos; cada variante debe tener un único escritor
durante render/entrega.

### Reintentos de proceso y operación

`execution.matlab_process_retry` es opcional y aplica estos defaults:

```yaml
execution:
  matlab_process_retry:
    max_attempts: 3
    initial_delay_s: 2.0
    max_delay_s: 30.0
    max_consecutive_failures: 5
```

Los presupuestos/corte son enteros positivos estrictos (no booleanos); las esperas
son números finitos no negativos y el máximo no puede ser menor que la inicial.
`max_attempts` incluye la primera invocación; `1` desactiva repetición. Tras el fallo
ordinal `n`, la espera es `min(max_delay_s, initial_delay_s * 2**(n-1))`: 2/4 segundos
por defecto. No cambia el presupuesto de muestreo T30 ni la aceptación funcional.

Se evalúa aceptación primero: una recuperación `post_completion` válida no se
repite. Un código no cero observado sin render aceptado ni error determinista
acreditado puede reintentarse, sin afirmar causa transitoria. No se reintentan
lanzamiento fallido, terminación no observada, almacenamiento funcional fallido,
entrada/dependencia inválida acreditada, integridad contradictoria o código cero
con render inválido. Consola y eventos no deciden esta política, aunque falten.

El contexto funcional añade `failure_path`, separado de `completion_path`. Python
verifica estrictamente `failure.json`: `schema_version=1`, identidad de intento y
variante, `manifest_sha256`, categoría `invalid_input` y `error` original
(`identifier`, `message`). Un informe corrupto/ajeno o contradictorio bloquea por
integridad. Su ausencia no acredita transitoriedad ni una etapa interna. MATLAB
solo lo emite por fallo de carga/validación, no por cualquier fallo de RAVEN;
si no puede escribirlo, conserva y relanza la excepción original. Las llamadas sin
contexto o con contexto antiguo siguen soportadas; no se fabrica `completion.json`.

Cada cadena persistente fija realización, runtime/hash, semilla y política. Reserva
el ordinal antes del lanzamiento; cada invocación tiene UUID, snapshot y constancia
propios, enlazados al intento previo. `decision=abort` sigue rechazando ese intento;
`retry_decision=scheduled|not_retryable|exhausted|circuit_open` decide programación.
Un reinicio respeta presupuesto y espera pendientes. Un `running` sin terminación
observada bloquea hasta intervención, sin suponer que MATLAB haya muerto.

#### Reconciliar manualmente una interrupción

`reconcile-static` permite autorizar otro intento después de confirmar que no quedan
escritores. **No acepta el render anterior, no inventa su código de salida, no retira
locks y no lanza MATLAB.** Use la raíz del lote, no el YAML ni una carpeta de escena.

1. Confirme que el orquestador y sus procesos MATLAB/RAVEN anteriores están detenidos.
   Un `static_writer.lock` existente sigue bloqueando: investigue su propietario antes
   de retirarlo manualmente. La operación nunca lo elimina por antigüedad.
2. Inspeccione sin escribir (desde la raíz del repositorio):

   ```powershell
   uv run acoustic-orchestrator reconcile-static .\outputs\mi_lote --chain <chain_id>
   ```

3. Aplique únicamente al UUID del intento mostrado por la inspección:

   ```powershell
   uv run acoustic-orchestrator reconcile-static .\outputs\mi_lote --chain <chain_id> --attempt <attempt_id> --apply --confirm-stopped --reason "Interrupción; se verificó que no quedan escritores"
   ```

4. Reanude `render-static` por separado con `resume_if_possible=true` y
   `overwrite_existing=false`. Se reutilizan las otras variantes válidas y el nuevo
   intento tiene otro UUID, pero conserva cadena, entrada, semilla y presupuesto.

La consulta de procesos es conservadora: bloquea cualquier MATLAB/RAVEN local y otros
orquestadores de render/generación. Si la consulta falla o hay un posible escritor no
verificable, no permite aplicar; `--confirm-stopped` no omite esa comprobación. Es una
confirmación operativa, no prueba de que se haya observado la terminación pasada.

La evidencia queda en `metadata/render_attempts/<attempt_id>/interruption_reconciliation/`:
`chain.before.json` y `outcome.before.json` preservan bytes exactos; `record.json`
conserva motivo, operador, fecha, identidad, huellas e inventario de salidas. El outcome
queda `process_status=interrupted`, `decision=abort`, `termination_observed=false` y
`exit_code=null`; no se modifica la fecha de finalización desconocida ni el error original.
La programación usa `confirmed_interruption`, acreditada por ese registro, no por logs.

El intento interrumpido **sigue consumiendo su ordinal**. Sin presupuesto restante,
la cadena queda agotada; tampoco se reinicia el circuito del lote. Los restos parciales
se conservan mediante el journal existente antes de otro lanzamiento. Si aparecen o
cambian archivos después de la confirmación, se bloquea su archivo automático.
Constancias/informes funcionales presentes, salidas completas verificables o evidencia
incoherente requieren revisión independiente y no se descartan con este comando.
Repetir la misma reconciliación es idempotente; una publicación interrumpida puede
completarse con el mismo comando, pero nunca autoriza otro intento distinto del indicado.

Las rutas públicas WAV/sidecar no cambian. Antes de repetir se archivan solo restos
propios del proceso terminado, bajo su intento, con journal de rutas originales,
destinos, tamaños y SHA-256. Snapshot y constancia permanecen intactos. Un archivo
interrumpido se reconcilia antes de lanzar; ambigüedad o persistencia fallida bloquea
y conserva ambos errores. No se mueven salidas protegidas anteriores a la cadena.

La coordinación limita despachos a `num_workers`. Cuenta fallos no recuperados en
orden de resolución persistida; aceptación reinicia la racha, T30 por sí solo no.
Al alcanzar el umbral el corte queda enclavado y persistido: no hay nuevos lanzamientos
ni reintentos. Se espera y registra trabajo ya iniciado, sin matarlo; un éxito tardío
no reabre el lote. El error terminal conserva el original y enlaces a toda la cadena.

`render_jobs` cuenta trabajos/variantes intentados, no procesos. Por ejemplo, una
variante aceptada al tercer intento produce este extracto ilustrativo del resumen:

```text
render_jobs=1 matlab_invocations=3 process_retries=2 variants_completed_after_retry=1
MATLAB procesos_fallidos=2 recuperados=0 recuperaciones_reanudadas=0
```

Los contadores de proceso incluyen todas las invocaciones reales; reutilizar no
crea ninguna. Éxito tras reintento no es recuperación del primer proceso fallido;
las advertencias históricas se conservan y el resumen incluye la razón del corte.

Un restart normal no renueva una cadena agotada/cortada. Solo una ejecución nueva
intencional con `resume_if_possible=false` y `overwrite_existing=true` abre nuevas
cadenas, preservando las anteriores; no es un arreglo rutinario para conflictos.
Outcomes antiguos sin cadena siguen legibles y pueden preceder una nueva cadena
si no existen salidas protegidas, sin inventar política retroactiva. Para rollback,
usar `max_attempts=1`; versiones antiguas requieren YAML compatible y no deben
reanudar cadenas nuevas pendientes que no puedan verificar. Preferir otro run y
conservar toda evidencia: no borrar registros para forzar reanudación o rollback.

## Circular Array Configuration

Configuration loading/validation accepts an optional `receiver_outputs.circular_array`.
The static pipeline supports array-only and mixed receiver scene generation,
runtime selection, rendering, indexing, and resume provenance checks.

```yaml
receiver_outputs:
  circular_array:
    enabled: true
    num_microphones: 8
    radius_m: 0.05
    output_subdir: circular_array
    required: true
hearing_degradation:
  enabled: false
  input_targets: []
  hearing_profiles_path: path/to/hearing_profiles.yml
```

This is a section-level excerpt: retain the other required experiment sections.
Array-only config does not require a `binaural_hrtf` block or receiver IR catalog.
`num_microphones` is a strict integer >= 2; `radius_m` is a finite positive
distance in meters. Channels are derived from the microphone count, so do not
configure `num_channels`, `num_hrtfs`, `file_pattern`, or `ir_catalog_path` for an
array. The defaults are `enabled: false`, `required: false`, and
`output_subdir: circular_array`; a disabled array can omit its geometry.
Output subdirectories must be relative, nonempty, and contain no traversal
segments or Windows drive/root/UNC prefixes. At least one output must be enabled.

The array geometry is a horizontal ring in the public XZ plane, centered on
the sampled receiver and rotated by yaw only. HRTF/HARTF outputs remain
independently configurable alongside the array.

Array WAVs and sidecars use the existing artifact layout:

```text
{artifact_root}/{run_name}/output_audio/render/{output_subdir}/
  {scene_id}__circular_array.wav
  {scene_id}__circular_array__render.json
```

These filenames follow the default naming templates; custom templates still
use `{scene_id}` and `{output_type}`. One array has one variant identity
`{scene_id}__circular_array`, regardless of microphone count. Its index entry
uses `receiver_ir_path: null`, not a fake HRTF path. Metadata records the
public center/yaw, radius, ordered microphone positions and WAV channel mapping.
Microphone 0 corresponds to WAV channel 1; yaw zero starts at +X, with ordering
toward +Z. Array source WAVs are downmixed by channel mean before normalization
and gain, while HRTF/HARTF source handling is unchanged.

Resume checks compare planned array geometry with the sidecar/runtime provenance
and verify actual WAV channel count/sample rate. Wrong radius/yaw/order or channel
count makes the output invalid for resume. If sidecars are disabled, saved runtime
geometry is required; newly written manifests must not validate old stale WAVs.
Canonical receiver rendering still needs a T30 sidecar for room acceptance.
For resuming an existing experiment, use `resume_if_possible: true` together
with `overwrite_existing: false`: compatible inputs and valid outputs are reused
without rewriting them. Incompatible protected artifacts produce an explicit conflict.

Full experiment examples: `configs/experiments/circular_array_only.yml` and
`configs/experiments/circular_array_mixed.yml`. Receiver-section examples are
in `apps/orchestrator/examples/circular_array_scenes.md`.

Opt-in acceptance checks require installed MATLAB/RAVEN, compatible subject-01
DAFF files in the HRTF/front-HARTF/rear-HARTF catalogs, and the Clarity backend.
Run from the repository root using a fresh writable artifact directory:

```sh
uv run --project apps/orchestrator python apps/orchestrator/tests/verify_circular_array_e2e.py render --output-root PATH
uv run --project apps/orchestrator python apps/orchestrator/tests/verify_circular_array_e2e.py clarity --output-root PATH
```

The render phase verifies array-only and four-output mixed runs, actual IR paths
stored in RAVEN projects, channel/coordinate provenance, T30, and resume. The
Clarity phase verifies HRTF-only degraded artifacts and rejection of HARTF/array
targets. Reports are `render_verification.json` and `clarity_verification.json`
under `PATH`. These real-backend checks are separate from mocked unit tests.

## Clarity Handoff

Only `binaural_hrtf` is accepted in `hearing_degradation.input_targets`.
This base target covers numbered sampled HRTFs such as `binaural_hrtf__1`;
do not list numbered variants in the experiment config.
HARTF rear/front and `circular_array` targets are rejected explicitly, even
when degradation is disabled and even for two microphones. To migrate a
previous HARTF-degradation config, remove HARTF from `input_targets`; keep
its receiver output enabled to continue rendering it. Array-only or
HARTF-only runs must disable degradation and use `input_targets: []`.
Enabled degradation requires an enabled binaural HRTF output and a nonempty
eligible target list. Handoff creation and submission independently enforce
the same HRTF-only policy, including stale or externally supplied job records.

Prepare artifacts only:

```sh
uv run --project apps/orchestrator acoustic-orchestrator clarity-handoff configs/experiments/static_example.yml
```

Prepare and submit:

```sh
uv run --project apps/orchestrator acoustic-orchestrator clarity-handoff configs/experiments/static_example.yml --submit
```

Expected artifacts:

- `metadata/manifests/runtime/clarity/clarity_jobs.jsonl`: one job per eligible rendered
  variant, including `run_id` and `backend_invocation`.
- `metadata/indexes/clarity_index.jsonl`: status tracking for planned, submitted,
  completed, partial, failed, blocked, or skipped jobs.
- `output_audio/degraded/{output_type}/{hearing_profile_id}/{render_wav_name}.wav`:
  degraded output from the backend.
- `output_audio/degraded/{output_type}/{hearing_profile_id}/{render_wav_stem}.json`:
  metadata sidecar next to the degraded WAV.

Current degradation output contract:

- The default root is `{artifact_root}/{run_name}/output_audio/degraded/`; an explicit `hearing_degradation.output_dir` overrides it.
- Subdirectories are `{output_type}/{hearing_profile_id}/`.
- `expected_output_wav_path` and `expected_output_metadata_path` in each JSONL
  job are authoritative for the backend.
- Resume checks validate the explicit paths reserved in the handoff manifest.
- Legacy layouts under `outputs/clarity/...` are not enough to satisfy new runs
  by themselves.

The orchestrator does not add the Clarity backend as a direct Python dependency.
The current design keeps a file-and-subprocess boundary between the two apps.

## Project Structure

```text
apps/orchestrator/
|-- main.py
|-- pyproject.toml
|-- ARCHITECTURE.md
|-- examples/
|-- src/
|   `-- acoustic_orchestrator/
|       |-- cli.py
|       |-- config/
|       |-- experiment/
|       `-- pipeline/
`-- tests/
```

Key modules:

- `config/loader.py`: reads config files and resolves paths.
- `config/models.py`: Pydantic models for the experiment config.
- `config/validator.py`: config validation rules.
- `experiment/sampler.py`: scene sampling.
- `experiment/scene_builder.py`: scene manifest construction.
- `experiment/manifest_writer.py`: manifest output.
- `pipeline/render_pipeline.py`: high-level workflow orchestration.
- `pipeline/matlab_runner.py`: MATLAB subprocess integration.
- `pipeline/clarity_handoff.py`: Clarity JSONL job preparation.
- `pipeline/clarity_runner.py`: Clarity subprocess integration.
- `pipeline/output_index.py`: output status tracking.
- `pipeline/output_paths.py`: output path reservation.
- `pipeline/runtime_audio.py`: runtime audio preparation.

## Source audio directories

`source_sampling.source_types[].audio_dir` accepts WAVs directly in the configured
directory and directly in its immediate child folders. Each child folder name is
treated as an opaque database identity, not necessarily a speaker:

```text
speech/                  <-- audio_dir
+-- flat.wav
+-- ID01/
|   +-- recording.wav
+-- ID02/
    +-- recording.wav
```

Both layouts can coexist; no new YAML keys are needed. Extensions are
case-insensitive (`.wav`, `.WAV`). Sidecars, empty folders, and deeper recordings
such as `ID01/session/clip.wav` are ignored. At least one eligible WAV is required.
Point `audio_dir` at the parent of the ID folders, not an individual ID folder.
Selection remains file-based: IDs do not impose uniqueness or balancing, and two
recordings of the same identity can occur in one scene.

New scene manifests include two provenance strings per source:

| Original recording relative to `audio_dir` | `identity_id` | `audio_relpath` |
| --- | --- | --- |
| `ID02/recording.wav` | `"ID02"` | `"ID02/recording.wav"` |
| `clip_ID13.wav` | `""` | `"clip_ID13.wav"` |

The scene-local `source_id` (for example `src_0001`) identifies an instance, not
the database identity. Flat recordings have an empty identity; IDs are not parsed
from filenames or the configured root's own name. IDs are scoped to that source
database, not globally unique person identifiers. `audio_relpath` uses forward
slashes and always describes the original recording. Runtime trimming can change
`audio_path` but leaves both provenance strings unchanged.

When render metadata is enabled, inspect `summary.sources` in each rendered JSON
sidecar for these fields. Existing MATLAB manifests missing either field remain
accepted and export an empty string for the missing value. Provenance is not
duplicated into the compact output-index snapshot or Clarity degraded sidecars;
the latter retain their link to the input render metadata. Existing schema
versions, output names, and acoustic processing are unchanged.

## Fixed source positions

`fixed_position` places a required source type at receiver-relative points
defined by the Cartesian product of three non-empty lists:

```yaml
source_sampling:
  min_sources: 1
  max_sources: 1
  source_types:
    - event_type: speech
      role: base
      min_count: 1
      max_count: 1
      probability: 1.0
      audio_dir: ../../../assets/events/speech/
      spatial_policy:
        type: fixed_position
        azimuths_deg: [-90, 0, 90]
        elevations_deg: [0, 15]
        distances_m: [1.0]
```

The example defines six cases. `execution.num_simulations` must be at least
the largest case count of any configured `fixed_position` policy. For each
policy, every block of six scenes uses a seed-reproducible shuffle and covers
each case exactly once. A final partial block uses the prefix of a fresh
shuffle. The scheduled case is preferred. If scheduled cases from simultaneous
`fixed_position` policies conflict, the sampler tries deterministic local
alternatives; this exceptional substitution can reduce that block's coverage.
WAV selection remains independent and may reuse the same file.

Angles follow MATLAB/RAVEN: at receiver yaw/pitch zero, azimuth/elevation zero
points along `+X`; positive azimuth rotates toward RAVEN `+Z`; positive
elevation rotates toward `+Y`. Azimuth is added to receiver yaw, elevation is
added to receiver pitch, and receiver roll is ignored. Python preserves the
public coordinates in the manifest; the MATLAB schema 2.0/3.0 adapter reflects Z
consistently for room geometry, poses, and orientation vectors.

List values must be finite and unique. Azimuth is limited to `[-180, 180]`,
elevation to `[-90, 90]`, and distance must be greater than zero. A fixed
source type must have `min_count >= 1`. Multiple fixed sources in one scene use
distinct points; the sampler searches alternate assignments when necessary to
meet source-to-source separation. It aborts if there are too few unique points
or no valid assignment.

Assigned fixed points always enforce room bounds, the 0.5 m wall clearance,
the configured receiver clearance, and source separation, including when
`require_sources_inside_room` is false. An assigned point that violates a room,
wall, or receiver constraint aborts the experiment instead of being resampled.
Monophonic scenes are supported with `source_sampling.min_sources: 1`.

## Development

Run tests:

```sh
uv run --project apps/orchestrator pytest apps/orchestrator/tests
```

Run static checks when the tools are installed:

```sh
uv run --project apps/orchestrator ruff check apps/orchestrator
uv run --project apps/orchestrator mypy apps/orchestrator/src
```

The canonical `configs/experiments/static_example.yml` smoke config uses
`execution.overwrite_existing: false`. To intentionally perform a fresh smoke run,
choose a fresh `outputs.run_name`, then execute `render-static` above directly.
To resume a compatible run, keep its name and enable `resume_if_possible`; a fresh
run name is not required for protected resume. Other examples may choose overwrite
behavior independently. Real-backend smoke checks remain opt-in, not a claim that
MATLAB/ITA/RAVEN was verified in this environment.

## Practical Notes

- `generate-manifests` and `render-static` return exit code 2 for partial
  sampling and 1 for failed sampling; render errors also return 1. Failed scene
  indices and diagnostics remain in `metadata/indexes/scene_sampling_failures.jsonl`.
- A Clarity job requires a completed, validated render variant targeted by
  `input_targets`, with both input WAV and render metadata present.
- `render-static` assumes the monorepo layout where `apps/orchestrator`,
  `apps/matlab`, and `apps/clarity-backend` are sibling directories.
- If `apps/matlab` is missing, `render-static` fails before invoking MATLAB.
- The orchestrator writes `backend_invocation` metadata into Clarity jobs, but
  actual execution still happens via `uv run --project <sibling-backend>` or the
  configured entrypoint.
- The supported pipeline is static. Dynamic rendering remains outside the
  supported orchestrator workflow.
