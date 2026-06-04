# Binaural-Impared-Hear / MATLAB

Este directorio contiene un flujo en MATLAB para preparar y ejecutar renders binaurales a partir de escenas JSON. El proyecto sigue **en construcción**.

Actualmente, la ruta documentable como usable es la de **render estático** mediante `run_raven_static_render.m`. El flujo dinámico todavía **no está implementado ni soportado**: `run_raven_dynamic_render.m` existe como entrypoint, pero `dynamic/render_dynamic_scene.m` sigue en estado placeholder con `TODO`.

## Dependencias

- MATLAB
- ITA Toolbox
- RAVEN

## Estructura del repositorio

```text
matlab/
├─ assets/                      % audio de ejemplo y HRTF locales
├─ core/                        % carga, validación y preparación del proyecto
├─ dynamic/                     % trabajo preliminar del flujo dinámico
├─ example/                     % escenas JSON de ejemplo
├─ static/                      % implementación principal del flujo estático
├─ run_raven_static_render.m    % entrypoint del render estático
└─ run_raven_dynamic_render.m   % entrypoint presente, no soportado todavía
```

## Archivos clave

- `run_raven_static_render.m`: punto de entrada del render estático.
- `static/render_static_scene.m`: implementación principal del render estático.
- `core/load_scene_config.m`: carga la configuración JSON.
- `core/validate_static_scene_config.m`: validación básica para escenas estáticas.
- `core/prepare_raven_project.m`: prepara el proyecto RAVEN con supuestos de entorno local.
- `run_raven_dynamic_render.m`: entrypoint presente, pero no implementado como flujo soportado.
- `dynamic/render_dynamic_scene.m`: placeholder actual del render dinámico.

## Uso básico del flujo estático

Desde la raiz del monorepo, el uso recomendado es dejar que `apps/orchestrator` genere los manifests e invoque este backend por subprocess:

```sh
uv run --project apps/orchestrator acoustic-orchestrator render-static configs/experiments/static_example.yml
```

Para ese flujo, MATLAB debe estar disponible en `PATH` como `matlab`, ademas de tener ITA Toolbox y RAVEN configurados localmente.

```matlab
run_raven_static_render('example/scene_static_0001.json')
```

Antes de ejecutarlo, conviene revisar las rutas de entrada, HRTF, `.rpf` base y archivos de salida según el entorno local disponible.

## Contrato soportado del manifest estático

- El formato canónico del receiver es `receiver.hrtfs`, como lista no vacía de variantes con `hrtf_id` y `hrtf_path`.
- Por compatibilidad, también se acepta `receiver.hrtf` singular y se normaliza internamente como batch de una sola variante.
- La semilla soportada se declara preferentemente en `render.seed`. El campo heredado top-level `seed` sigue aceptándose si `render.seed` no existe.
- Cada entrada de `sources` puede declarar opcionalmente `directivity_path` para aplicar un patrón de directividad de fuente en RAVEN.
- `room.materials` y `room.material_files` forman ahora parte obligatoria del contrato del flujo estático.
- `room.material_files` debe declarar explícitamente `north_wall`, `south_wall`, `east_wall`, `west_wall`, `floor` y `ceiling`, cada uno con `material_id` y `material_path` absoluto.
- El validador exige consistencia entre `room.materials.<surface>` y `room.material_files.<surface>.material_id`.
- El flujo estático carga los coeficientes de absorción y scattering desde esos archivos explícitos y los aplica a RAVEN asumiendo el orden de paredes `north`, `south`, `east`, `west`.

### Salidas por HRTF

- `render.output_wav_path` y `render.output_metadata_path` pueden declararse como archivo explícito o como carpeta de salida.
- Si se declara una carpeta, el flujo deriva automáticamente el nombre del archivo desde `scene_id` usando `scene_id.wav` y `scene_id.json`.
- Si la escena declara una sola HRTF, el flujo mantiene `project_name` sin sufijos extra.
- Si la escena declara múltiples HRTFs, el flujo ejecuta una corrida secuencial por variante y deriva nombres seguros con el patrón `__<hrtf_id>` antes de la extensión del archivo ya resuelto.
- Cada variante genera su propio WAV y su propio metadata JSON.
- `render.trim_reverb_tail` es opcional y por defecto vale `false`. Si vale `true`, el flujo recorta de cada fuente renderizada la cola añadida por la convolución antes de hacer el mix.

### Ruido de fondo en el render estático

El manifest estático puede declarar `background_noise` como postprocesado aditivo sobre el audio final ya mezclado. Esta etapa ocurre **fuera de RAVEN**, después del mix de fuentes y antes de escribir el WAV de salida. No cambia BRIRs ni habilita el flujo dinámico.

- Si `background_noise` no existe o `background_noise.enabled` es `false`, no se agrega ruido.
- Con `enabled=true`, `layers` debe ser una lista no vacía. Cada capa se escala por `snr_db` usando la potencia RMS del mix final previo al ruido.
- `strategy="colored"` soporta `color`: `white`, `pink`, `brown`, `blue`, `violet`. La señal coloreada se genera mono de forma determinista con `render.seed` y el índice de capa, y se replica a todos los canales.
- `strategy="audio_file"` carga un WAV desde `path`; si es mono se replica, si tiene exactamente la misma cantidad de canales se usa canal a canal, y otros casos multicanal se rechazan.
- El alias heredado `strategy="audio_folder"` se acepta sólo cuando `path` apunta a un archivo WAV concreto; se normaliza como `audio_file`. No hay selección aleatoria desde carpetas.
- Si el WAV de ruido tiene otra frecuencia de muestreo, se intenta usar `resample`; si MATLAB no lo tiene disponible, el render falla con un error explícito.
- Si el ruido queda más corto que el render se repite en bucle; si queda más largo se recorta.
- Después de sumar todas las capas se aplica una guardia de pico a `0.999`. Si actúa, la metadata registra la ganancia y advierte que el SNR efectivo puede cambiar.

Ejemplo mínimo:

```json
"background_noise": {
  "enabled": true,
  "layers": [
    { "strategy": "colored", "color": "pink", "snr_db": 20.0 }
  ]
}
```

La metadata exportada en `summary.background_noise` registra las capas aplicadas, estrategia original y normalizada, color o archivo, SNR objetivo, semilla efectiva, resampling, loop/trim, adaptación de canales y guardia de clipping.

Ejemplo con carpetas de salida:

```json
"render": {
  "sample_rate_hz": 44100,
  "seed": 12345,
  "trim_reverb_tail": false,
  "output_wav_path": "../../data/raven_rendered/",
  "output_metadata_path": "../../data/raven_rendered/metadata/"
}
```

Para una escena con `scene_id = "scene_static_0001"`, el flujo genera:

- Single HRTF: `../../data/raven_rendered/scene_static_0001.wav` y `../../data/raven_rendered/metadata/scene_static_0001.json`.
- Multi HRTF: archivos por variante como `scene_static_0001__subject-001.wav` y `scene_static_0001__subject-001.json`.

### Alcance actual de reproducibilidad

- El valor efectivo de semilla se normaliza y queda reflejado en metadata (`effective_seed`, `seed_source`).
- En este repositorio sólo se aplica de forma verificada a `rng` de MATLAB.
- **No está verificado** en el código actual que la API de RAVEN exponga una semilla compatible para garantizar reproducibilidad completa del BRIR. La metadata documenta esta degradación de forma explícita.

## Render dinámico

**No implementado / no soportado aún**.
