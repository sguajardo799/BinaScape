# Flujo de artefactos

[Índice](../README.md) · [Arquitectura](overview.md) · [Contratos](contracts.md)

El layout de generación es común a develop y HEAD. Su productor principal es `pipeline/output_paths.py:resolve_artifact_layout` del orquestador. La raíz efectiva es `{outputs.artifact_root}/{outputs.run_name o experiment.experiment_id}`.

```text
<run>/
  metadata/
    manifests/
      scene/                         # escenas públicas
      runtime/render/                # entradas de cada variante MATLAB
      runtime/clarity/clarity_jobs.jsonl
    indexes/
      render_index.jsonl
      clarity_index.jsonl
      scene_sampling_failures.jsonl
  output_audio/
    render/<output_subdir>/          # WAV y sidecar de render
    degraded/<output_type>/<hearing_profile_id>/
  cropped_audio/                    # únicamente si se prepara audio
  analysis/                         # destino predeterminado del analizador
```

Es una descripción de paths resueltos, no garantía de que todos existan. Se crean al escribir. `analysis` también puede ubicarse fuera del run mediante `--output-dir`.

## 1. Configuración a escenas

`generate-manifests CONFIG` carga YAML, resuelve paths relativos respecto de su directorio, valida entradas y muestrea. `scene_builder.build_static_manifest` materializa geometría, superficies, fuentes, poses públicas, HRTF, render, muestreo y tratamientos en schema 3.0. Se escriben escenas bajo `metadata/manifests/scene` y fallos bajo el índice de muestreo cuando corresponde.

Un run puede ser parcial: no se convierte automáticamente un fallo de escena en éxito. La CLI distingue muestreo parcial (2), fallido (1) y completo (0). La documentación histórica de un PRD no modifica esos resultados.

## 2. Escenas a render

`render-static CONFIG` prepara audio de runtime cuando procede y manifiestos por variante. Estos conservan geometría y poses públicas; la adaptación a RAVEN ocurre en MATLAB. El orquestador invoca `matlab -batch` y `run_raven_static_render` y registra resultados en `render_index.jsonl`.

Los paths WAV/JSON se resuelven según `output_subdir` y patrones configurados; variantes HRTF reciben identidad propia. No reconstruir nombres arbitrariamente desde un glob. MATLAB exporta sidecars render (habitualmente `*__render.json`) con `summary` de la variante y `batch.other_variants` para hermanas.

## 3. Render a Clarity opcional

`clarity-handoff CONFIG` selecciona variantes render completadas/validadas, con WAV, sidecar y perfil disponibles y tipo incluido en `input_targets`. Produce un job schema 1.0 por combinación elegible variante/perfil y actualiza el índice Clarity. `--submit` invoca el backend hermano; el render también puede enviar automáticamente según `runner.auto_submit`.

Clarity consume `input_wav_path`, `input_render_metadata_path`, `hearing_profiles_path`, `expected_output_wav_path` y `expected_output_metadata_path`. Preserva el basename del WAV render; el JSON usa su stem. Su directorio es `output_audio/degraded/{output_type}/{hearing_profile_id}`. Un `hearing_degradation.output_dir` explícito puede sustituir la raíz degradada. Estos paths son autoritativos, no nombres fijos inferidos.

El backend verifica la existencia del sidecar de entrada, pero no interpreta su contenido. Escribe sidecar degradado 1.0 con estado completed/failed cuando el error se captura. El orquestador inspecciona artefactos y mantiene estados/resume; no equivale a validar el contenido acústico del WAV.

## 4. Análisis posterior de metadatos

`output-analyzer analyze RUN [--output-dir DEST] [--quiet]` lee metadatos de escena/render/degradación e índices presentes, sin WAV ni assets. El run puede ser una copia que contenga solo metadatos. No se necesita ejecutar generación o Clarity para iniciar el análisis.

En develop exporta summary, render_scenes JSON, joined_render_sources y degraded_metadata JSONL/CSV, SVG y HTML con imágenes relativas. En HEAD añade escenas/fuentes únicas, incidencias, cobertura, materiales, muestreo y RT30 auditables en JSON/JSONL/CSV, general_parameters y metrics, con SVG inline. Consulte la [matriz de revisiones](../maintenance/documentation-audit.md#diferencias-del-analizador-entre-revisiones) y la [guía del analizador](../../apps/output-analyzer/README.md).

Los índices ausentes no prueban cobertura completa ni cero fallos. Las cifras históricas de runs E2E son referencias de aquellos datasets, no valores universales de estas etapas.
