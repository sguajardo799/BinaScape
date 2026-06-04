# Clarity Backend

Backend mínimo para la slice actual del handoff de Clarity en el workspace.

## Propósito

Este proyecto consume el manifiesto JSONL generado por `apps/orchestrator`, valida los inputs requeridos por job, resuelve el `hearing_profile_id` en un catálogo YAML y escribe los artifacts mínimos del backend.

Hoy el backend aplica degradación auditiva real con MSBG (`clarity.evaluator.msbg.msbg`) y preserva el boundary explícito de archivos + subprocess para que el orchestrator pueda inspeccionar resultados.

## Relación con `orchestrator` y `matlab`

- `apps/orchestrator` decide qué jobs de Clarity existen y escribe `manifests/runtime/clarity/clarity_jobs.jsonl`.
- `apps/matlab` produce el WAV renderizado y su metadata upstream.
- `apps/clarity-backend` consume esos paths explícitos, genera el WAV degradado y su sidecar exactamente en los paths esperados del manifest, y no importa código interno del orchestrator.

La forma preferida de invocación desde el sibling orchestrator sigue siendo:

```sh
uv run --project ../clarity-backend clarity-backend run-manifest .\manifests\runtime\clarity\clarity_jobs.jsonl
```

Desde la raiz del monorepo, el backend tambien se verifica como proyecto sibling con Python 3.12:

```sh
uv run --project apps/clarity-backend clarity-backend --help
```

## Instalación local

```sh
uv sync
```

## CLI

### Ejecutar un manifiesto

```sh
uv run --project . clarity-backend run-manifest path/to/clarity_jobs.jsonl
```

Qué hace el comando:

1. Lee el manifiesto JSONL línea por línea.
2. Valida que existan `input_wav_path`, `input_render_metadata_path` y `hearing_profiles_path`.
3. Resuelve el `hearing_profile_id` en el catálogo YAML.
4. Convierte el perfil auditivo a audiogramas de Clarity y procesa el WAV con MSBG.
5. Escribe el WAV degradado y la metadata sidecar en los paths pedidos por cada job.
6. Continúa con los siguientes jobs aunque uno falle.
7. Termina con código `1` si hubo al menos un job fallido.

## Contrato actual del manifiesto

El backend consume el contrato escrito por `apps/orchestrator/tests/test_clarity_handoff.py`.

Cada línea del JSONL representa un job con esta forma general:

```json
{
  "schema_version": "1.0",
  "run_id": "sim_test",
  "job_id": "clarity__scene_static_0001__binaural_hrtf__mild_loss",
  "variant_id": "scene_static_0001__binaural_hrtf",
  "scene_id": "scene_static_0001",
  "output_type": "binaural_hrtf",
  "hearing_profile_id": "mild_loss",
  "hearing_profiles_path": ".../hearing_profiles.yaml",
  "backend_invocation": {
    "mode": "uv_project",
    "entrypoint": "clarity-backend",
    "use_uv": true,
    "backend_project_path": null
  },
  "input_wav_path": ".../scene_static_0001__binaural_hrtf.wav",
  "input_render_metadata_path": ".../scene_static_0001__binaural_hrtf__render.json",
  "output_dir": ".../outputs/degraded/binaural_hrtf/mild_loss",
  "expected_output_wav_path": ".../scene_static_0001__binaural_hrtf.wav",
  "expected_output_metadata_path": ".../scene_static_0001__binaural_hrtf.json"
}
```

Notas:

- `schema_version` actual: `1.0`.
- `backend_invocation` se preserva como parte del contrato, pero este repo solo consume el manifiesto ya escrito.
- `expected_output_wav_path` y `expected_output_metadata_path` son autoritativos; el backend no debe asumir nombres fijos.

## Catálogo de perfiles auditivos

El backend espera un YAML con raíz `profiles` y perfiles únicos por `hearing_profile_id`.

Ejemplo mínimo válido:

```yaml
profiles:
  - hearing_profile_id: mild_loss
    ears:
      left:
        loss_db_by_band:
          250: 10
      right:
        loss_db_by_band:
          250: 12
```

Reglas actuales validadas por el backend:

- `profiles` debe ser una lista no vacía.
- cada perfil debe existir una sola vez.
- cada oído (`left`, `right`) debe existir.
- `loss_db_by_band` debe ser un mapa no vacío.
- las bandas soportadas por MSBG en este backend son `250, 500, 1000, 2000, 4000, 8000` para ambos oídos.
- cada banda debe tener valor numérico finito.

## Outputs generados

Por cada job, el backend escribe en `output_dir`:

- el WAV degradado con el mismo basename que `input_wav_path`
- el sidecar JSON con el mismo stem (`{wav_stem}.json`)

### Caso exitoso

- el WAV degradado es el resultado procesado por MSBG a partir de `input_wav_path`.
- la metadata sidecar incluye identidad del job, paths resueltos, `status: "completed"`, `processor` y `degradation_applied` con pérdidas por banda y frecuencias del audiograma.

### Caso fallido

- la metadata sidecar se escribe igual.
- `status` pasa a `"failed"`.
- `output_wav_path` queda en `null`.
- se incluye `error.type` y `error.message` para debugging.

## Limitaciones actuales

- MSBG en esta integración soporta WAV estéreo a 44.1 kHz.
- el backend asume que el orchestrator ya produjo un manifiesto válido y rutas explícitas.
- hoy solo existe un CLI simple con `run-manifest`.
- la validación está centrada en inputs y perfiles auditivos, no en una capa de modelos más abstracta.

## Referencias upstream relevantes

Para entender el contrato vigente, revisar primero:

- `apps/orchestrator/README.md`
- `apps/orchestrator/tests/test_clarity_handoff.py`
- `apps/orchestrator/tests/test_output_index.py`

Esos archivos describen mejor que este README cómo se preparan los jobs, cómo se reservan los outputs y qué estados observa el orchestrator alrededor del handoff.
