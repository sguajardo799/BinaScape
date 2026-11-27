# Proposal

## Why

La degradación auditiva usa hoy solo Clarity MSBG, con una relación nivel digital↔dB SPL fija en el código (`Ear(equiv_0db_spl=70)` + `ahr=20`, es decir RMS 1.0 ≡ 90 dB SPL en `apps/clarity-backend/src/clarity_backend/msbg.py`) y sin registrar. Además, MSBG reescala cada canal para que el RMS de sus tramos activos iguale al del archivo completo: simula a menor nivel que el real, atenúa la salida y, al procesar los canales por separado, altera ligeramente la ILD.

El objetivo es primero validar comparativamente MSBG y WHIS (Wadai Hearing Impairment Simulator v302 + GCFBv234, MATLAB), también frente a oyentes normoyentes (NH) e hipoacúsicos (IH), y después generar cientos de horas de estímulos para entrenar redes neuronales. Ambos usos exigen que, dado un render, las versiones sin procesar, MSBG y WHIS compartan una misma escala de nivel conocida, el mismo dominio digital, la misma referencia de oído y la misma alineación temporal, y que ese nivel pueda llevarse a una reproducción calibrada.

## What Changes

- `hearing_degradation.processors`: lista con `msbg` y/o `whis` (por defecto `[msbg]`). Todos los procesadores de la lista se ejecutan sobre cada render y perfil en la misma corrida.
- Calibración `hearing_degradation.calibration` con modos `fixed_scale` (`digital_rms1_spl_db`) y `presentation_level` (`presentation_level_db_spl` fijo o `presentation_level_range_db_spl` muestreado de forma determinista por render). La escala efectiva K se calcula **una vez por render** en el orquestador, se registra en `metadata/indexes/level_calibration.jsonl` y se comparte entre todos los procesadores. Sin bloque y solo con MSBG, se usa `fixed_scale` 90 dB. Con WHIS el bloque es obligatorio.
- MSBG: `msbg.level_handling` `true_level` (por defecto) compensa el reescalado por nivel activo para simular el nivel real y devolver la señal en su dominio digital; `legacy_active_rescale` reproduce el comportamiento anterior.
- `hearing_degradation.outer_middle_ear` compartido por ambos procesadores (`free_field`, `diffuse_field`, `itu`, `eardrum`); por defecto `free_field` solo con MSBG y obligatorio si se incluye WHIS.
- Procesador WHIS en `apps/clarity-backend`: Python prepara los canales (remuestreo 44.1↔48 kHz, nivel SPL por canal a partir de K) y ejecuta una sesión `matlab -batch` por manifiesto que llama directamente a `WHISv302`.
- Cada procesador compensa su latencia constante: la salida queda alineada muestra a muestra con la entrada.
- Catálogo de perfiles: banda opcional `125` y `compression_health` por oído (obligatorio con WHIS).
- **BREAKING (layout y reanudación)**: salidas en `output_audio/degraded/<procesador>/<output_type>/<perfil>/`, con job_id `clarity__<variant>__<perfil>__<procesador>`. Las salidas del layout anterior no se reutilizan. Un resultado se reutiliza solo si coincide su firma de procesamiento (procesador, calibración, corrección de oído y ajustes del procesador).
- Nuevo comando `export-presentation`: dada una calibración de reproducción `K_play` por canal, exporta las versiones sin procesar, MSBG y WHIS de cada render con la misma ganancia, con un SPL máximo y sin recorte.
- Output analyzer: identifica los registros degradados por procesador, separa sus gráficos y lee los sidecars del layout anterior como `msbg` heredado.
- Documentación: guías de calibración para generar datos de entrenamiento de redes y para la validación comparativa de simuladores (NH vs IH), y un perfil de control `nh_reference` (0 dB HL, `compression_health` 1).
- Fuera de alcance: compensar el limitador de pico del render, la ecualización de audífonos (HpTF), portar WHIS a Python y el nivel de habla activa ITU-T P.56.

## Capabilities

### New Capabilities
- `hearing-level-calibration`: escala digital↔SPL por render compartida por todos los procesadores (modos, rango determinista, índice de calibración, nivel real en MSBG, registro en metadata).
- `hearing-processor-selection`: lista de procesadores, validación de configuración y catálogo, corrección de oído compartida, layout e identificadores por procesador, alineación temporal, firma de procesamiento y reanudación.
- `whis-hearing-processor`: ejecución de WHIS v302 + GCFBv234 vía MATLAB desde el backend.
- `presentation-export`: exportación calibrada de las versiones sin procesar y degradadas de cada render para reproducción en experimentos de escucha.

### Modified Capabilities
- `clarity-input-eligibility`: el requisito de conservar las rutas de salida degradadas pasa a admitir el layout por procesador.
- `output-analyzer-report`: los registros y gráficos degradados se distinguen por procesador y se leen los sidecars del layout anterior.

## Impact

- `apps/orchestrator`: `config/models.py`, `config/validator.py`, `pipeline/output_paths.py` (layout e IDs), `pipeline/clarity_handoff.py` (cálculo de K, campos del job), `pipeline/clarity_runner.py`, `pipeline/output_index.py`, `cli.py` (`export-presentation`), nuevo módulo de calibración y de exportación, tests.
- `apps/clarity-backend`: `cli.py`, `msbg.py` (nivel real, corrección compartida, latencia), nuevo módulo WHIS y wrapper MATLAB, tests.
- `apps/output-analyzer`: `loader.py` (agrupación por procesador, compatibilidad con el layout anterior), gráficos degradados.
- Dependencias externas: checkouts locales de `AMLAB-Wakayama/WHIS` y `AMLAB-Wakayama/gammachirp-filterbank`, y MATLAB solo cuando se incluye `whis`. La versión de `pyclarity` queda fijada porque `eardrum` y `true_level` dependen de su comportamiento interno.
- Documentación: READMEs de `orchestrator`, `clarity-backend` y `output-analyzer`, `docs/architecture/contracts.md` y `data-flow.md`, nuevas guías en `docs/guides/`, ejemplos de configuración y de perfiles.
