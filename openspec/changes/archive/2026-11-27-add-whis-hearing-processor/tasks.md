# Tasks

## 1. Orquestador: configuración y catálogo

- [x] 1.1 Agregar a `HearingDegradationConfig` (`config/models.py`) los campos `processors` (por defecto `[msbg]`), `calibration` (`mode`, `digital_rms1_spl_db`, `presentation_level_db_spl`, `presentation_level_range_db_spl`), `outer_middle_ear`, `msbg.level_handling` y `whis` (`install_root`, `matlab_executable` = `matlab`, `synth_method` = `dtvf`); verificar con tests de `test_validator.py` que los valores desconocidos y las listas repetidas fallan nombrando el campo.
- [x] 1.2 Implementar en `config/validator.py` las reglas cruzadas: modos de calibración y exclusión entre nivel fijo y rango, calibración obligatoria con `whis`, `outer_middle_ear` por defecto u obligatorio, y bloques `msbg`/`whis` exigidos o rechazados según la lista; verificar con un test por escenario de `hearing-level-calibration` y `hearing-processor-selection`.
- [x] 1.3 Extender `load_hearing_profile_catalog` con la banda `125` y `compression_health` (escalar o por banda en [0, 1]) y las reglas de WHIS (CH obligatorio, sin umbrales negativos); verificar con tests que nombran perfil, oído y banda, y que los catálogos existentes siguen siendo válidos con `[msbg]`.
- [x] 1.4 Agregar el perfil de control `nh_reference` (0 dB HL en 125–8000 Hz, `compression_health` 1) a `configs/hearing/hearing_profiles.yaml` y a `apps/orchestrator/examples/hearing_profiles.yaml`, más un ejemplo de configuración con `[msbg, whis]`; verificar que ambos pasan la validación (salvo la existencia de `install_root`).

## 2. Orquestador: calibración por render, layout y jobs

- [x] 2.1 Crear `pipeline/level_calibration.py` con el cálculo de ms por canal, P (fijo o muestreado con SHA-256 de semilla + `variant_id`), K, canales silenciosos y el registro de `level_calibration.jsonl`; verificar con tests de las fórmulas, del determinismo entre ejecuciones, del cambio de semilla y de los casos silenciosos.
- [x] 2.2 Cambiar `build_clarity_job_id` y `resolve_clarity_job_paths` (`pipeline/output_paths.py`) al formato con procesador; verificar con tests de `test_output_paths.py` que dos procesadores producen rutas e IDs distintos con el mismo basename.
- [x] 2.3 Integrar en `pipeline/clarity_handoff.py` el cálculo de K una vez por render (bloqueando con `calibration_undefined_silent_input` cuando corresponda), un job por procesador y los campos `processor`, `calibration`, `outer_middle_ear`, configuración del procesador y `processing_signature`; verificar con tests de `test_clarity_handoff.py` que los jobs de un render comparten K y que el manifiesto contiene todos los campos.
- [x] 2.4 Extender `pipeline/output_index.py`: validación del sidecar (procesador, firma, K igual al del job) y reanudación por firma; verificar con tests de cambio de calibración, de corrección de oído, de semilla con rango, de agregar `whis` a una corrida `[msbg]` y de un layout anterior no reutilizado.
- [x] 2.5 Agregar en `pipeline/clarity_runner.py` la verificación previa de WHIS por job (rutas bajo `install_root` y ejecutable MATLAB) para que bloquee solo los jobs `whis`; verificar con tests de `test_clarity_runner.py` que GCFB ausente bloquea WHIS y deja enviar MSBG.
- [x] 2.6 Actualizar `apps/orchestrator/README.md`, `docs/architecture/contracts.md` y `docs/architecture/data-flow.md` (configuración, layout, índice de calibración, job y sidecar, reanudación); verificar que no quedan referencias al layout anterior como vigente ni al nivel fijo de 70 dB como contrato.

## 3. Backend: contrato del job y MSBG

- [x] 3.1 Extender el parseo del catálogo en `cli.py` como en 1.3 y hacer que `_process_job` use `processor`, `calibration`, `outer_middle_ear` y la firma del job sin recalcular K, y escriba en el sidecar `calibration`, `processing_signature`, `outer_middle_ear` e `ignored_profile_fields`; verificar con tests de `test_cli.py` sobre el sidecar.
- [x] 3.2 Implementar en `msbg.py` `level_handling` (`true_level` según D4 y `legacy_active_rescale`) y la traducción de `outer_middle_ear` (incluido `eardrum` con `src_correction` nula); fijar la versión de `pyclarity` en `pyproject.toml`; verificar con tests que `legacy_active_rescale` + `free_field` + 90 dB es idéntico muestra a muestra a una referencia generada antes del cambio, que `true_level` no atenúa por nivel activo con `nh_reference`, y que un test centinela falla si cambia la firma interna de `measure_rms`/`process`.
- [x] 3.3 Implementar la medición y compensación de latencia (D10) compartida por los procesadores del backend y aplicarla a MSBG; verificar con un test de impulso que la salida MSBG queda alineada en ±1 muestra y que el sidecar registra las muestras compensadas.
- [x] 3.4 Actualizar `apps/clarity-backend/README.md` (contrato del job y del sidecar, `level_handling`, corrección de oído, latencia); verificar que los ejemplos JSON del README coinciden con los que producen los tests.

## 4. Backend: procesador WHIS

- [x] 4.1 Escribir `apps/clarity-backend/matlab/binascape_whis_run.m` según D8 (rutas desde `install_root`, `WHISv302` directo, `try/catch` por tarea, `out`/`result`/`error` por canal); verificar con una ejecución manual sobre `Snd_Hello123.wav` que produce salida y resultado.
- [x] 4.2 Crear `apps/clarity-backend/src/clarity_backend/whis.py`: validación de entrada, remuestreo 160/147 de ida y vuelta, mapeo a 7 bandas con extrapolación registrada de 125 Hz, `srcsnd_spl_db` a partir de K, omisión de canales silenciosos, ensamblado estéreo y chequeo de recorte; verificar con tests unitarios sin MATLAB (mapeo, ida y vuelta con longitud exacta, frecuencia no soportada, recorte en PCM16).
- [x] 4.3 Implementar la sesión `matlab -batch` por manifiesto con finalización incremental y progreso (D8), y aplicar la compensación de latencia de 3.3; verificar con tests que sustituyen MATLAB por un ejecutable falso (éxito, fallo de una tarea, muerte de la sesión) y que un manifiesto mixto procesa MSBG y WHIS.
- [x] 4.4 Escribir el sidecar WHIS (versiones, commits o `null`, release de MATLAB, `synth_method`, `processing_sample_rate_hz`, `resampled`, CH pedida y aplicada, pérdida activa y pasiva, `extrapolated_bands`, `output_peak`, latencia); verificar con tests sobre resultados simulados que el sidecar pasa la validación de 2.4.
- [x] 4.5 Documentar en `apps/clarity-backend/README.md` la instalación de WHIS (comandos de clonado, layout de `install_root`) y sus limitaciones; verificar siguiendo la guía en un checkout limpio.

## 5. Orquestador: exportación para presentación

- [x] 5.1 Implementar `export-presentation <run_root> --playback <archivo>` (`cli.py` y nuevo módulo de exportación) según D11: validación de la calibración de reproducción, ganancia por canal, layout, sidecars en float32, y verificación por render de SPL máximo y recorte antes de escribir; verificar con tests de cada escenario de `presentation-export`.
- [x] 5.2 Agregar un ejemplo de calibración de reproducción en `apps/orchestrator/examples/` y documentar el comando en `apps/orchestrator/README.md`; verificar ejecutando el ejemplo sobre una corrida de prueba.

## 6. Output analyzer

- [x] 6.1 Cambiar `loader.py` para agrupar los registros degradados por `(variant_id, hearing_profile_id, processor)`, tratar los sidecars sin `processor.name` como `msbg` con marca `legacy` y exportar procesador, K, P y Leq por oído; verificar con tests sobre una corrida con ambos procesadores y una corrida con el layout anterior.
- [x] 6.2 Desglosar por procesador los gráficos y resúmenes degradados cuando hay más de uno; verificar con tests del reporte que el estado de degradación separa `msbg` y `whis` y que una corrida de un solo procesador genera los mismos gráficos que antes.
- [x] 6.3 Actualizar `apps/output-analyzer/README.md` con los campos nuevos y la compatibilidad con corridas antiguas; verificar que el reporte de ejemplo documentado se regenera sin errores.

## 7. Guías de calibración

- [x] 7.1 Escribir `docs/guides/calibration-simulator-validation.md` para la validación comparativa (MSBG vs WHIS, oyentes NH vs IH):
  - medición de `k_play_db` con acoplador u oído artificial y el tono de 1 kHz a −26 dBFS RMS;
  - elección de `outer_middle_ear` según el punto de medición de las HRTF (`itu` para canal bloqueado);
  - `fixed_scale` o `presentation_level` fijo con P audible para el oyente IH y bajo `max_spl_db`;
  - perfiles por participante y barrido de `compression_health` en WHIS (por ejemplo 0, 0.5 y 1);
  - control con `nh_reference` y pasa-bajos común;
  - flujo con `export-presentation`, qué escucha cada grupo (IH: sin procesar; NH: simulado y sin procesar como control) y qué registrar.
  
  Verificar recorriendo la guía de punta a punta sobre la corrida de 8.4.
- [x] 7.2 Escribir `docs/guides/calibration-nn-dataset.md` para generar cientos de horas de entrenamiento:
  - `presentation_level_range_db_spl` y semilla;
  - uso del índice de calibración para llevar el dataset a una escala absoluta común (`× 10^((K − K_ref)/20)`) o al nivel como condicionamiento;
  - prohibición de normalizar archivo por archivo después de degradar;
  - tripletas alineadas (sin procesar, MSBG, WHIS) y control `nh_reference`;
  - firma de procesamiento para reanudar lotes largos;
  - costo estimado con el factor de tiempo real medido en 8.4 y particionado de corridas.
  
  Verificar que los fragmentos de configuración de la guía pasan la validación.
- [x] 7.3 Enlazar ambas guías desde `docs/README.md` y desde los READMEs de `orchestrator` y `clarity-backend`; verificar que los enlaces resuelven.

## 8. Verificación de integración (requiere MATLAB y checkout de WHIS)

- [x] 8.1 Identidad y equivalencia WHIS: `nh_reference` produce salida ≈ entrada (fijar la tolerancia observada) y el wrapper coincide con `WHISv30_Batch` a igual `SrcSnd.SPLdB` con una entrada mono.
- [x] 8.2 Equivalencia entre procesadores: con el mismo render, MSBG y WHIS con `nh_reference` quedan alineados en ±1 muestra, con la misma longitud y con diferencia de nivel de banda ancha documentada. Se miden y documentan sus anchos de banda efectivos.
- [x] 8.3 ILD y dependencia del nivel: 10 dB de ILD con perfil simétrico conservan el orden de niveles en ambos procesadores, y la misma señal con K y K+20 produce degradaciones distintas.
- [x] 8.4 Extremo a extremo: una corrida pequeña con `[msbg, whis]`, `itu` y `presentation_level_range_db_spl` completa los jobs; al reanudarla los reutiliza; agregar o quitar un procesador solo afecta a sus jobs; `export-presentation` produce las tres versiones con la misma ganancia; y el analizador reporta ambos procesadores. Registrar el factor de tiempo real de WHIS y los toolboxes requeridos en el README.
- [x] 8.5 Ejecutar las suites completas (`uv run pytest` en `apps/orchestrator`, `apps/clarity-backend` y `apps/output-analyzer`) y `openspec validate add-whis-hearing-processor --strict` sin fallos.
  Nota: ejecutado el 2026-11-27. clarity-backend 57 passed (4 skipped: integración MATLAB opcional), output-analyzer 153 passed, orchestrator 1401 passed y 7 fallos previos del entorno (goldens RAVEN con ruta absoluta, `test_raven_only_code_paths_never_import_pyroomacoustics` y un golden dependiente del orden) que fallan igual en `HEAD` 15e123f sin este cambio; `openspec validate --strict` válido. Aceptado por el usuario.

## Workflow follow-up

- Archivar el change cuando se cumplan los requisitos de revisión del proyecto y verificar que las specs principales quedan creadas o actualizadas (`clarity-input-eligibility`, `output-analyzer-report`).
