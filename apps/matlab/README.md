# MATLAB Backend

This app contains the MATLAB/RAVEN backend for the static binaural rendering
stage. It consumes scene JSON manifests produced by the orchestrator or written
manually for backend development.

The supported flow is static rendering through `run_raven_static_render.m`.
Dynamic rendering is not supported yet: `run_raven_dynamic_render.m` exists as
an entrypoint, but `dynamic/render_dynamic_scene.m` is still a placeholder.

See the [documentation index](../../docs/README.md),
[shared contracts](../../docs/architecture/contracts.md),
[data flow](../../docs/architecture/data-flow.md),
[setup](../../docs/development/setup.md), and
[testing guide](../../docs/development/testing.md).

## Requirements

- MATLAB configured for a 44100 Hz static render
- ITA Toolbox
- RAVEN
- Local RAVEN project files, HRTF/DAFF files, source audio, directivity files,
  and material files referenced by the input scene manifest

For root-first end-to-end runs, `matlab` must also be available on `PATH` so the
orchestrator can invoke it by subprocess. `core/prepare_raven_project.m`
currently writes RPF projects under the fixed local Windows path
`C:\ITASoftware\Raven\RavenInput\`; verify that this installation path is
available before a full render. Portability to other installations is not
established by the unit tests.

## Directory Layout

```text
apps/matlab/
|-- assets/                     # Local example audio and HRTF assets, when present
|-- core/                       # Loading, validation, audio, metadata, and helpers
|-- dynamic/                    # Preliminary dynamic-rendering work
|-- example/                    # Scene JSON examples
|-- static/                     # Static rendering implementation
|-- tests/                      # MATLAB tests
|-- run_raven_static_render.m   # Supported static rendering entrypoint
`-- run_raven_dynamic_render.m  # Present, but not supported yet
```

## Recommended Use Through The Orchestrator

From the repository root, prefer the orchestrator for normal runs. It generates
the scene manifests, resolves output paths, and invokes this backend:

```sh
uv run --project apps/orchestrator acoustic-orchestrator render-static configs/experiments/static_example.yml
```

The root-level config must point to assets and a RAVEN `.rpf` file that exist in
the local environment.

## Direct MATLAB Use

For backend development, call the static entrypoint with a scene JSON path:

```matlab
run_raven_static_render('example/scene_static_0001.json')
```

Before running directly, review the scene manifest paths for source WAV files,
HRTFs, material files, the base `.rpf`, output WAV paths, and metadata paths.

Los catálogos DAFF deben usar una versión compatible con el RAVEN instalado;
el nombre/extensión por sí solo no garantiza compatibilidad. El adaptador pasa
las rutas IR como `char` y comprueba que el RPF conserva el archivo seleccionado,
evitando una sustitución silenciosa por el HRTF predeterminado al serializar.
Los archivos v1.7 rechazados por el backend deben reemplazarse o convertirse
fuera de esta ruta; no se convierten automáticamente ni se omiten errores.

## Important Files

- `run_raven_static_render.m`: entrypoint for supported static rendering.
- `static/render_static_scene.m`: main static rendering implementation.
- `core/load_scene_config.m`: loads scene JSON.
- `core/validate_static_scene_config.m`: validates the static scene contract.
- `core/prepare_raven_project.m`: prepares the RAVEN project for a local setup.
- `core/apply_background_noise.m`: applies post-render additive background
  noise.
- `core/export_render_metadata.m`: writes render metadata.
- `core/transform_schema2_pose_to_raven.m`: shared position/vector reflection
  for schema 2.0/3.0.
- `core/build_room_reverberation_summary.m`: measured T30 summary and validity.
- `run_raven_dynamic_render.m`: dynamic entrypoint placeholder.
- `dynamic/render_dynamic_scene.m`: dynamic implementation placeholder.

## Static Scene Contract

The current static manifest contract includes these fields and behaviors:

- `receiver.hrtfs` describes catalog-backed variants with `hrtf_id` and
  `hrtf_path`. It may be empty when `receiver.circular_array` is present.
- The legacy singular `receiver.hrtf` field is accepted and normalized as a
  one-variant batch.
- `render.seed` is the preferred seed location. A legacy top-level `seed` is
  accepted when `render.seed` is absent.
- Each source may optionally define `directivity_path`.
- `schema_version` accepts `1.0`, `2.0`, and `3.0`; missing version defaults
  to `1.0`. The orchestrator currently generates schema `3.0`.
- Schema 1.0 keeps the legacy shoebox dimensions and six named material
  surfaces: `north_wall`, `south_wall`, `east_wall`, `west_wall`, `floor`,
  and `ceiling`. Explicit material files supply absorption and scattering.
  Their RAVEN slots are `matShoebox1=floor`, `2=ceiling`, `3=south`,
  `4=west`, `5=north`, and `6=east`, independent of API enumeration order.
- Schemas 2.0/3.0 use `room.geometry` with `height_m`, ordered footprint
  vertices in the public XZ plane and `wall_ids`. Supported generated shapes
  are shoebox, trapezoid and L-shaped rooms. Material maps contain every
  `wall_id`, plus `floor` and `ceiling`, with no extra surfaces.
- `room.materials` and `room.material_files` remain required. Every file entry
  carries `material_id` and absolute `material_path`, consistent with the
  semantic material map.
- Schema 3.0 additionally requires `room.acoustic_surfaces` and
  `reverberation_sampling`. Effective absorption/scattering vectors are
  authoritative for rendering; treatment v1 preserves base scattering and
  leaves the floor untreated. See the shared contract for treatment metadata.
- Scene and runtime JSON retain public `[x, y, z]` coordinates, with Y as
  height. The MATLAB adapter reflects Z for positions and orientation vectors
  in schemas 2.0/3.0. Render metadata source positions/vectors are exported
  from this adapted RAVEN frame, not copied from the public manifest.
- `render.sample_rate_hz` must equal `44100`; the renderer rejects other rates.
- Render metadata reports `t30_s` from `rpf.getT30` with octave centers
  `[31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]` Hz when the
  returned vector has ten values. `mean_t30_s` is available only when all ten
  bands are finite and positive; otherwise it is empty and status is
  `unavailable`. This ten-band measured mean differs from the seven-band
  Sabine sampling estimate over 125–8000 Hz.

## Render de arrays circulares estáticos

El entrypoint MATLAB soporta `receiver.circular_array` además de HRTF/HARTF.
El orquestador genera variantes de array y comprueba su indexación/procedencia.
El descriptor contiene `output_type: circular_array`, `num_microphones` entero
>=2, `radius_m` finito positivo, `center_m`, `yaw_deg` y `microphones` ordenados.
Cada micrófono lleva `microphone_index` (desde cero), `channel_index` (desde uno)
y `position_m`. El centro/yaw debe coincidir con la pose del receptor.

La geometría permanece horizontal en XZ (Y vertical), sin cabeza ni carcasa.
Con yaw cero, el canal 1 está en +X; los siguientes avanzan hacia +Z. Pitch/roll
no inclinan el array. Las posiciones públicas se reflejan una vez en Z al
construir los receptores RAVEN. El runtime puede seleccionar exclusivamente
un descriptor mediante `render.active_output_type`; sin selección se procesan
todas las variantes declaradas. Un array no requiere `active_hrtf` ni un
catálogo IR propio.

El WAV contiene N canales sincronizados a 44100 Hz. Cada fuente multicanal se
reduce a mono por media aritmética antes de normalización/gain; HRTF/HARTF
conserva su tratamiento anterior. No se alinean inicios ni se normalizan
canales de micrófonos individualmente. El sidecar identifica `output_type`,
incluye geometría/canales en `receiver.circular_array`, marca coordenadas como
`public_xyz_y_up` y conserva T30 y política de downmix en los resúmenes.

El ruido posterior a la mezcla admite capas mono replicadas o exactamente
N canales. Un WAV de ruido estéreo no se convierte automáticamente a ocho
canales: se rechaza. La replicación mono es ruido correlacionado, no un campo
difuso espacial. El array nunca es entrada Clarity, incluso con N=2.
El render dinámico sigue sin soporte.

Tras generar un manifiesto completo de array con rutas válidas, se usa el
mismo entrypoint estático:

```matlab
run_raven_static_render('ruta/al/scene_array.json');
```

Los ejemplos de configuración están en
`configs/experiments/circular_array_only.yml` y `circular_array_mixed.yml`;
los extractos de receptor y el orden de canales se documentan en
`apps/orchestrator/examples/circular_array_scenes.md`.

## Verificación del contrato RIR para arrays

La verificación del contrato RIR se mantiene separada de los tests de
orquestación/indexación y requiere un backend real.
La adaptación seleccionada usa receptores en lote y
`rpf.getImpulseResponseItaAudio()`: devuelve una matriz `itaAudio`
`[n_fuentes, n_micrófonos]`, con una respuesta mono a 44100 Hz por par.
El orden de columnas corresponde al orden de `setReceiverPositions` y
`setReceiverNames`. No se deben alinear ni normalizar los canales por separado.

La verificación real con dos fuentes asimétricas y cuatro micrófonos comprueba
orden, tiempo absoluto de llegada y diferencias de nivel del sonido directo:

```matlab
addpath('tests');
verify_raven_array_response_contract( ...
    'C:/ITASoftware/Raven/RavenInput/Classroom/Classroom.rpf', ...
    fullfile(tempdir, 'binascape_array_contract'));
```

El fixture copia el RPF base, conserva resultados y escribe
`array_contract_report.json` en el directorio indicado; no modifica el RPF base.
Requiere un modelo shoebox de 4.2 x 3.6 x 2.8 m con materiales y el backend
RAVEN/ITA instalado, no solamente mocks. No es un test unitario automático.
En esta versión, `getT30()` devuelve una celda por receptor;
`getT30(true, false)` devuelve el promedio entre receptores en diez bandas,
sin promediar frecuencias. El RPF base puede cargar su HRIR heredado incluso
con BRIR desactivado; las respuestas recuperadas por este fixture son RIR mono,
no BRIR ni canales filtrados por cabeza.

## Outputs Per HRTF

- `render.output_wav_path` and `render.output_metadata_path` can be explicit
  files or output directories.
- If an output path is a directory, filenames are derived from `scene_id`.
- A single HRTF run keeps the configured `project_name` without extra suffixes.
- Multiple HRTFs are rendered sequentially, one variant at a time.
- Multi-HRTF output filenames receive a safe `__<hrtf_id>` suffix before the
  file extension.
- Each HRTF variant writes its own WAV file and metadata JSON.
- Each JSON keeps scene and output identifiers at the top level. `summary`
  contains the simulation results for that variant, such as source details and
  room reverberation. `batch` contains `n_variants` and, when applicable,
  `other_variants` with the HRTF ID and output paths of each sibling variant.
  The current variant is identified by the top-level fields and is not repeated
  in `batch`.
- `render.trim_reverb_tail` is optional and defaults to `false`. When `true`,
  each rendered source is trimmed before mixing.

Example output-directory config:

```json
"render": {
  "sample_rate_hz": 44100,
  "seed": 12345,
  "trim_reverb_tail": false,
  "output_wav_path": "../../data/raven_rendered/",
  "output_metadata_path": "../../data/raven_rendered/metadata/"
}
```

For `scene_id = "scene_static_0001"`, the backend writes:

- Single HRTF: `scene_static_0001.wav` and `metadata/scene_static_0001.json`.
- Multiple HRTFs: names such as `scene_static_0001__subject-001.wav` and
  `metadata/scene_static_0001__subject-001.json`.

## Background Noise

The static manifest may include `background_noise` as additive post-processing
on the final mixed audio. This happens outside RAVEN, after source mixing and
before the output WAV is written. It does not modify BRIRs and does not enable
the dynamic pipeline.

- If `background_noise` is absent or `background_noise.enabled` is `false`, no
  noise is added.
- With `enabled=true`, `layers` must be a non-empty list.
- Each layer is scaled by `snr_db` using the RMS power of the final pre-noise
  mix.
- `strategy="colored"` supports `white`, `pink`, `brown`, `blue`, and `violet`.
- Colored noise is generated deterministically from `render.seed` and the layer
  index, then replicated to all channels.
- `strategy="audio_file"` loads a WAV from `path`.
- Mono noise is replicated to all channels. Noise with the same channel count is
  used channel-by-channel. Other multichannel layouts are rejected.
- The legacy alias `strategy="audio_folder"` is accepted only when `path` points
  to a concrete WAV file, and is normalized as `audio_file`.
- If a noise WAV has a different sample rate, the backend attempts to resample
  it. If the required MATLAB function is unavailable, rendering fails with an
  explicit error.
- Short noise is looped; long noise is trimmed.
- After summing all layers, a peak guard limits audio to `0.999`. If it acts,
  metadata records the gain and warns that the effective SNR may change.

Minimal example:

```json
"background_noise": {
  "enabled": true,
  "layers": [
    { "strategy": "colored", "color": "pink", "snr_db": 20.0 }
  ]
}
```

The exported metadata records applied layers, original and normalized strategy,
noise color or file path, target SNR, effective seed, resampling, loop/trim,
channel adaptation, and clipping-guard information.

## Reproducibility Notes

- The effective seed is normalized and written to metadata as `effective_seed`
  and `seed_source`.
- In this repository, the seed is verified for MATLAB `rng` usage.
- The code does not currently verify that the RAVEN API exposes a compatible
  seed mechanism for complete BRIR reproducibility. Metadata documents that
  limitation explicitly.

## Tests

Run MATLAB tests from this directory in an environment with the required
toolboxes:

```matlab
runtests('tests')
```

Some tests use local mocks; full rendering still depends on the local
MATLAB/RAVEN setup and assets.

## Dynamic Rendering

Dynamic rendering is not implemented or supported yet.
