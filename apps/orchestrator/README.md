# Orchestrator

## Qué hace

Orquesta la etapa Python del pipeline acústico: carga y valida un archivo YAML de experimento, muestrea escenas estáticas y genera manifiestos JSON listos para el renderer de MATLAB.

## Ejecución con `uv`

Desde la raiz del monorepo, el comando primario cross-platform para el flujo soportado es:

```sh
uv run --project apps/orchestrator acoustic-orchestrator render-static configs/experiments/static_example.yml
```

La config root-first canonica vive en `configs/experiments/static_example.yml`. Los ejemplos locales de esta carpeta siguen siendo la referencia para pruebas del app y no estan deprecated.

### Prerrequisitos

- Tener `uv` instalado.
- Usar Python 3.12.
- Desde esta carpeta (`apps/orchestrator`), instalar dependencias con:

```sh
uv sync --extra dev
```

- Para `render-static`, MATLAB debe estar disponible en `PATH` como `matlab`.
- El backend oficial de MATLAB en este monorepo es `apps/matlab`: desde `apps/orchestrator` se resuelve como `../matlab`, y el entrypoint esperado es `../matlab/run_raven_static_render.m`.
- El backend de Clarity de esta primera slice vive como sibling project en `apps/clarity-backend` y se invoca preferentemente con `uv run --project ../clarity-backend ...`.

### Config de ejemplo verificable en este repo

El repo incluye un ejemplo canónico en:

```text
examples/example_config.yml
```

Importante:
- `examples/example_config.yml` y `examples/example_scene_static.json` son la pareja canónica del contrato.
- El ejemplo principal de `examples/` es single-output con `binaural_hrtf`.
- El runtime sigue soportando múltiples receiver IR outputs cuando se habilitan salidas adicionales en `receiver_outputs`.

### Generar manifiestos

```sh
uv run acoustic-orchestrator generate-manifests .\examples\example_config.yml
```

Qué hace:

1. Lee y valida el YAML.
2. Resuelve rutas relativas respecto al archivo de configuración.
3. Genera escenas estáticas según `execution.num_simulations`.
4. Escribe un manifiesto JSON por escena dentro de `{outputs.artifact_root}/{run_name}/manifests/scene/`.

Contrato actual de materiales en el manifiesto:
- `room.materials` se conserva como mapa superficie -> `material_id`.
- `room.material_files` se agrega como mapa superficie -> `{ material_id, material_path }`.
- Cada `material_path` debe ser absoluto y no se emite `surface_id` dentro de cada entrada.

Contrato de ruido de fondo:
- La config opcional `background_noise` acepta `enabled`, `snr_db`, `allow_multiple_layers` y estrategias `colored` (`white|pink|brown`) o `audio_folder` (`noise_type`, `audio_dir`, `file_pattern`).
- Todo manifest incluye `background_noise`; si está deshabilitado, es exactamente `{ "enabled": false, "layers": [] }`.
- Cuando está habilitado, el orquestador elige capas concretas por escena de forma reproducible y balanceada, incluyendo rutas absolutas para archivos de carpeta. No hace DSP ni mezcla audio.

Nota de coordenadas para RAVEN: las posiciones de receiver y sources se serializan como `[x, y, -z]` porque RAVEN usa la esquina superior izquierda como origen.

### Render estático completo

```sh
uv run acoustic-orchestrator render-static .\examples\example_config.yml
```

Qué hace:

1. Repite la generación de manifiestos estáticos.
2. Crea manifiestos de entrada para MATLAB, uno por variante HRTF/HARTF habilitada, en `outputs.logs_dir/matlab_input_manifests/`.
3. Invoca `matlab -batch` usando el entrypoint fijo `../matlab/run_raven_static_render.m`.
4. Escribe WAVs y metadatos de render en las rutas configuradas.
5. Si `hearing_degradation.enabled=true`, prepara `manifests/runtime/clarity/clarity_jobs.jsonl`, actualiza `indexes/clarity_index.jsonl` y solo auto-envía al backend si `hearing_degradation.runner.auto_submit=true`.

Paralelización MATLAB/RAVEN: `execution.num_workers: 1` mantiene la ejecución secuencial. Valores mayores a 1 pueden ejecutar subprocesses de MATLAB en paralelo después de preparar todos los manifiestos/audio runtime; no configure más workers que licencias MATLAB/RAVEN disponibles, y vuelva a `1` si RAVEN presenta estado global oculto en su entorno.

### Preparar o enviar el handoff de Clarity

Solo preparar artifacts:

```sh
uv run acoustic-orchestrator clarity-handoff .\examples\example_config.yml
```

Preparar y enviar al backend sibling:

```sh
uv run acoustic-orchestrator clarity-handoff .\examples\example_config.yml --submit
```

Artifacts esperados del slice inicial:
- `manifests/runtime/clarity/clarity_jobs.jsonl` — un job por variant elegible, incluyendo `run_id` y `backend_invocation` para preservar el contrato con el backend sibling.
- `indexes/clarity_index.jsonl` — estados `planned|submitted|completed|partial|failed|blocked|skipped`.
- `outputs/degraded/{output_type}/{hearing_profile_id}/{render_wav_name}.wav` — salida degradada producida por el backend, preservando exactamente el basename del WAV renderizado upstream.
- `outputs/degraded/{output_type}/{hearing_profile_id}/{render_wav_stem}.json` — metadata sidecar del mismo archivo, escrita en el mismo directorio con el stem del WAV degradado.

Regla de contrato actual para degradación auditiva:
- el root por defecto es `{artifact_root}/{run_name}/outputs/degraded/`
- los subdirectorios son `{output_type}/{hearing_profile_id}/`
- `expected_output_wav_path` y `expected_output_metadata_path` son autoritativos para el backend sibling
- el resume del orquestador valida solo esos paths explícitos; layouts legacy bajo `outputs/clarity/...` con nombres fijos no satisfacen runs nuevos por sí solos
- `execution.resume_if_possible: false` deshabilita el resume también para Clarity; `hearing_degradation.runner.force_rerun: true` fuerza reenvío de jobs de Clarity aunque ya existan salidas completas

### Notas prácticas

- `render-static` asume la estructura actual del monorepo (`apps/orchestrator` y `apps/matlab` como carpetas hermanas). Si `apps/matlab` no existe, el comando falla antes de invocar MATLAB.
- El orquestador no hace `uv add` del backend de Clarity como dependencia directa: la decisión actual es preservar el boundary de subprocess + archivos y dejar el lockfile del backend en su propio proyecto.
- La metadata `backend_invocation` en el manifest describe cómo invocar el backend, pero la ejecución real sigue ocurriendo por `uv run --project <sibling-backend>` o por el entrypoint configurado; no se importa código del backend desde el orchestrator.
- El ejemplo canónico tiene `execution.overwrite_existing: true`, por lo que volver a ejecutar los comandos reemplaza los manifiestos/salidas del ejemplo.
- También puede usarse el entrypoint local `uv run python .\main.py <comando> <config>`, pero la forma recomendada es `uv run acoustic-orchestrator ...`.

## Estructura actual

```text
orchestrator/
├─ main.py
├─ pyproject.toml
├─ README.md
├─ ARCHITECTURE.md
├─ AGENTS.md
├─ examples/
│  ├─ example_config.yml
│  └─ example_scene_static.json
├─ src/
│  └─ acoustic_orchestrator/
│     ├─ cli.py
│     ├─ config/
│     │  ├─ loader.py
│     │  ├─ models.py
│     │  └─ validator.py
│     ├─ experiment/
│     │  ├─ manifest_writer.py
│     │  ├─ sampler.py
│     │  └─ scene_builder.py
│     ├─ pipeline/
│     │  ├─ clarity_handoff.py
│     │  ├─ clarity_runner.py
│     │  ├─ matlab_runner.py
│     │  ├─ output_index.py
│     │  └─ render_pipeline.py
│     ├─ matlab/        # actualmente vacío
│     └─ utils/         # actualmente vacío
└─ tests/
   ├─ test_clarity_handoff.py
   ├─ test_clarity_runner.py
   ├─ test_generate_manifests.py
   ├─ test_output_index.py
   ├─ test_output_paths.py
   └─ test_render_pipeline_clarity.py
```
