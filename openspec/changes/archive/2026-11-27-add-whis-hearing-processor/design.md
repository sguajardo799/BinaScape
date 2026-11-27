# Design

## Context

Ver `proposal.md` (Why). Estado actual relevante:

- **Orquestador.** Planifica jobs `clarity__<variant>__<profile>` en `metadata/manifests/runtime/clarity/clarity_jobs.jsonl` (`pipeline/clarity_handoff.py`) con rutas `output_audio/degraded/<output_type>/<profile>/` (`pipeline/output_paths.py::resolve_clarity_job_paths`). Lanza `clarity-backend run-manifest` por subproceso (`pipeline/clarity_runner.py`). Valida el sidecar en `pipeline/output_index.py::_validate_clarity_result` y reanuda si `should_resume_clarity_job` encuentra un resultado `completed` válido; hoy no compara procesador ni nivel. Ya depende de `numpy`, `scipy` y `soundfile`.
- **Catálogo de perfiles.** Se valida en el orquestador (`config/validator.py::load_hearing_profile_catalog`) y en el backend (`cli.py`, `msbg.py`); ambos rechazan bandas fuera de 250–8000 Hz.
- **MSBG (pyclarity).** `Ear(equiv_0db_spl=70)` con `ahr=20` da RMS 1.0 ≡ 90 dB SPL. `Ear.process`:
  - mide con `measure_rms(signal[0], fs, -12)` el RMS de los tramos activos y multiplica la señal por `change_db = rms_completo_dB − rms_activo_dB` (≤ 0), sin deshacerlo a la salida;
  - aplica `src_to_cochlea_filt` hacia delante (`src_correction − MIDEAR`), el modelo coclear con `equiv_0db_spl + ahr` y el filtro inverso, todo con `lfilter` FIR de `n_wdw + 1` coeficientes (latencia sin compensar);
  - solo admite `src_pos` `ff`/`df`/`ITU`, pero `src_correction` es un atributo de la instancia.
- **WHIS v302.** `WHISv30_Batch` reescala cada entrada a `SrcSnd.SPLdB`. `WHISv302(SrcSnd, WHISparam)`:
  - exige 48/24 kHz, `HLoss.Type`, `HLoss.CompressionHealth`, `CalibTone.SPLdB` y `SrcSnd.SPLdB`;
  - usa `SrcSnd.SPLdB` para situar el análisis GCFB (`Eqlz2MeddisHCLevel`), y la síntesis `DTVF` aplica las ganancias sobre la señal recibida, compensando el retardo del banco (`GCFBv23_DelayCmpnst`);
  - ejecuta `StartupWHIS`, que agrega `../../gammachirp-filterbank/GCFBv234/`. GCFB analiza 100–12000 Hz.
- **Analizador.** `apps/output-analyzer/.../loader.py` agrupa los sidecars de `output_audio/degraded` por `(variant_id, hearing_profile_id)`, lo que colisiona si hay dos procesadores.

## Goals / Non-Goals

**Goals:**
- Que las versiones sin procesar, MSBG y WHIS de un render compartan K, dominio digital, referencia de oído, longitud y alineación temporal.
- Que un mismo K por render sirva para la simulación, el entrenamiento de redes y la reproducción calibrada.
- Que se pueda reproducir exactamente el comportamiento MSBG anterior (`legacy_active_rescale`) para comparar con corridas previas.
- Integrar WHIS sin modificar el código de WHIS/GCFB.

**Non-Goals:**
- Reutilizar salidas del layout anterior o migrarlas en disco.
- Compensar el limitador de pico del render, ecualizar audífonos (HpTF) o medir nivel de habla activa (ITU-T P.56).
- Igualar los modelos: MSBG no tiene equivalente a `compression_health`. La comparación se hace con varios perfiles WHIS (ver guía).
- Renombrar los artefactos `clarity_*`.

## Decisions

### D1. K se calcula una vez por render en el orquestador
Durante el handoff, para cada render elegible el orquestador lee el WAV, calcula ms_L, ms_R, P (si aplica) y K, y escribe un registro en `metadata/indexes/level_calibration.jsonl`. Ese registro viaja dentro de cada job. Un módulo `pipeline/level_calibration.py` concentra las fórmulas. **Por qué:** todos los procesadores y la exportación de la versión sin procesar ven el mismo K por construcción, y el backend no puede divergir. **Alternativa descartada:** calcular K en el backend. Es idéntico en teoría, pero deja la versión sin procesar sin K y permite divergencias entre implementaciones.

### D2. La calibración es una escala, no una reescritura
Nunca se reescala la señal para calibrar. Cada procesador recibe el SPL de cada canal (`K + 20·log10 rms_c`) y devuelve la señal en el dominio digital de la entrada. **Por qué:** es la propiedad que permite aplicar la misma ganancia a las tres versiones en la exportación y en el dataset. **Alternativa descartada:** normalizar al nivel de presentación, que rompería la comparación con el render original.

### D3. Rango de nivel determinista
P = min + u·(max − min), con u uniforme a partir de un generador sembrado con `hash(random_seed, variant_id)` (SHA-256, sin depender del orden de iteración). **Por qué:** el dataset de entrenamiento necesita variación de nivel reproducible y reanudable. La semilla entra en la firma (D9).

### D4. MSBG con nivel real
Con `true_level`, el backend reproduce el cálculo de pyclarity: `change_db` con el mismo `measure_rms` sobre el mismo canal. Luego construye `Ear` con `equiv_0db_spl = K − ahr − change_db`, procesa y multiplica la salida por `10^(−change_db/20)`. Tras el reescalado interno, el tramo activo se simula a `K + rms_activo_dB`, su nivel real, y la salida vuelve al dominio de la entrada. Con `legacy_active_rescale` se usa `equiv_0db_spl = K − 20` sin compensar. La versión de `pyclarity` se fija en `pyproject.toml`, y un test detecta cambios en `measure_rms` o en `process`. **Alternativa descartada:** reimplementar el pipeline de MSBG. Es más control, pero un fork difícil de mantener.

### D5. Corrección de oído compartida
`outer_middle_ear` se traduce así:
- MSBG: `free_field` → `ff`, `diffuse_field` → `df`, `itu` → `ITU`; `eardrum` asigna `ear.src_correction = 0` en la grilla `HZ`, de modo que la corrección queda solo como `−MIDEAR`.
- WHIS: `FreeField`, `DiffuseField`, `ITU`, `EarDrum`.

Para HRTF medidas con el canal bloqueado, la guía recomienda `itu`, que existe de forma nativa en ambos. **Por qué:** una misma referencia es requisito para comparar umbrales absolutos entre simuladores.

### D6. Layout e identificadores por procesador
`resolve_clarity_job_paths` y `build_clarity_job_id` reciben el procesador: `degraded/<procesador>/<output_type>/<perfil>/` y `clarity__<variant>__<perfil>__<procesador>`. El índice `clarity_index.jsonl` queda indexado por el job_id nuevo. Los registros con IDs antiguos se ignoran para reanudar; al no coincidir el job_id, sus salidas no se consideran. **Alternativa descartada:** conservar las rutas de MSBG y agregar solo las de WHIS, que deja un layout asimétrico (decisión del usuario).

### D7. WHIS se ejecuta desde el backend con una sesión `matlab -batch` por manifiesto
`run-manifest` agrupa los jobs por procesador. Los jobs `msbg` se procesan como hoy y los `whis` en una sola sesión MATLAB que ejecuta `apps/clarity-backend/matlab/binascape_whis_run.m`, con `try/catch` por tarea. **Alternativas descartadas:**
- El pool MATLAB del render: acoplado a su protocolo de intentos.
- MATLAB Engine para Python: acopla versiones de Python y MATLAB.
- Portar WHIS a Python: alto costo.

### D8. Protocolo de intercambio y finalización incremental
Por job y canal no silencioso, en `<dir. manifiesto>/whis_work/<sha1(job_id)[:12]>/`, Python escribe (nombre corto porque los `job_id` son largos y Windows limita las rutas a 260 caracteres):
- `in_<c>.wav`: float32 a 48 kHz (remuestreo `resample_poly` 160/147).
- `task.json`: `srcsnd_spl_db`, `calib_tone_spl_db = K − 26`, los 7 umbrales (125 Hz copia de 250 si falta), los 7 valores de `compression_health`, `synth_method` y `out_mid_crct`.

El wrapper llama a `WHISv302` con `HLoss.Type='HL0'` y `AllowDownSampling=0`. Escribe `out_<c>.wav`, `result_<c>.json` (CH aplicada, `PinLossdB_ACT`/`PAS`, versión, release de MATLAB) o `error_<c>.json`. Python vigila los resultados y finaliza cada job en cuanto tiene sus dos canales: vuelta a 44.1 kHz (147/160), recorte a longitud, latencia, chequeo de recorte, sidecar y evento de progreso. Al terminar la sesión, los jobs sin resultado quedan `failed` de sesión. El directorio de trabajo se borra si el job se completa.

### D9. Firma de procesamiento
`processing_signature = {processor, calibration: {mode, valores configurados, default}, random_seed (solo con rango), outer_middle_ear, msbg.level_handling | whis.synth_method}`, serializada en JSON canónico. K no entra: se deriva del audio y de la firma, y el audio ya lo protege la revalidación del render. Los commits de WHIS/GCFB e `install_root` se registran pero no están en la firma.

### D10. Latencia
Cada procesador mide su latencia una vez por configuración (procesador, frecuencia, corrección de oído), procesando un impulso con un perfil de 0 dB HL. La compensa desplazando la salida y rellenando con ceros al final, y registra las muestras desplazadas. MSBG con `legacy_active_rescale` no compensa (registra `latency_compensated: false`), para que su salida siga siendo idéntica a la anterior (decisión del usuario durante la implementación). **Por qué:** la latencia de MSBG (filtros FIR sin compensar) y la residual de WHIS dependen de la configuración y no de la señal; medirla evita depender de constantes internas.

### D11. Exportación para presentación
Nuevo comando `export-presentation` en el orquestador. Lee la calibración de reproducción (YAML), `level_calibration.jsonl` y el índice degradado, calcula `g_c = K − k_play_db_c` y verifica por render el SPL máximo y el recorte de todas sus versiones antes de escribir ninguna. Escribe float32 y un sidecar por versión. **Por qué:** float32 evita cuantizar a bajo nivel y desacopla el subtipo de la reproducción. Omitir el render entero evita presentar versiones de un mismo render a niveles distintos.

### D12. Analizador
`loader.py` agrupa por `(variant_id, hearing_profile_id, processor)`, toma el procesador de `processor.name` (o `msbg` con marca `legacy` si falta), recorre `output_audio/degraded` recursivamente (ya lo hace) y desglosa por procesador los gráficos y resúmenes degradados cuando hay más de uno.

### D13. Subtipo y recorte en la degradación
Las salidas degradadas se escriben en float32: el filtrado de MSBG y WHIS puede superar 1.0 cuando el render llega cerca de escala completa (observado: pico 1.02 con `nh_reference` sobre un render con pico 0.99), y float32 conserva ese exceso sin recortar ni cuantizar a bajo nivel. La única excepción es MSBG con `legacy_active_rescale`, que conserva el subtipo de entrada para seguir siendo idéntico a la salida anterior y falla si con subtipo entero el pico supera 1.0. La exportación también escribe float32 (decisión del usuario durante la implementación).

## Risks / Trade-offs

- [`true_level` y `eardrum` dependen de detalles internos de pyclarity] → Versión fijada y tests que fallan si cambian `measure_rms`, `process` o `src_correction`.
- [Llamar a `WHISv302` directamente puede depender de campos que fija `WHISv30_Batch`] → Test de identidad con `nh_reference` y test de equivalencia contra `WHISv30_Batch` a igual nivel.
- [Ancho de banda distinto (GCFB 100–12000 Hz, pasa-bajos propio de MSBG)] → Medir la respuesta de ambos con `nh_reference`. La guía de validación indica aplicar el mismo pasa-bajos a las tres versiones al comparar.
- [WHIS puede ser mucho más lento que MSBG para cientos de horas] → Medir el factor de tiempo real en la verificación, documentarlo y estimar el costo en la guía de dataset. La sesión única por manifiesto evita el arranque repetido de MATLAB.
- [La cola de reverberación y los silencios entran en el Leq de `presentation_level`] → Medida declarada como `rms_unweighted_full_file`. Una medida de habla activa queda para otro cambio.
- [Una actualización de WHIS/GCFB en el mismo `install_root` no fuerza reprocesar] → Commits registrados en el sidecar; `force_rerun` disponible.
- [El limitador de pico del render altera la escala en `fixed_scale`] → Fuera de alcance. `presentation_level` no se ve afectado.

## Migration Plan

1. Las corridas existentes no se reanudan en el layout nuevo: sus jobs se reprocesan en `degraded/msbg/...`. Para reproducir exactamente las salidas anteriores se usa `msbg.level_handling: legacy_active_rescale`, `outer_middle_ear: free_field` y calibración por defecto.
2. El analizador sigue leyendo corridas antiguas (marca `legacy`).
3. Para usar WHIS: clonar ambos repositorios en un `install_root`, agregar `compression_health` al catálogo y configurar `processors`, `calibration`, `outer_middle_ear` y `whis.install_root`.
4. Rollback: revertir el cambio. Las corridas hechas con el layout nuevo quedan legibles por el analizador nuevo, no por el anterior.

## Open Questions

- ~~Toolboxes de MATLAB que requieren WHISv302 y GCFBv234~~: resuelto en la verificación; MATLAB y Signal Processing Toolbox.
- ~~Tolerancias numéricas de los tests de identidad y alineación~~: fijadas tras observar el comportamiento real (`tests/test_integration_matlab.py`). Observación relevante: MSBG con 0 dB HL adelanta las bajas frecuencias respecto de las altas (dispersión del banco gammatone), de modo que la alineación por impulso no alinea la voz; está documentado en las guías y en el README del backend.
