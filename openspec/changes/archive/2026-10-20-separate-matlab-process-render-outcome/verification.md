# Evidencia de verificación

## Entorno

- Windows; Python 3.12.12 mediante el workspace uv.
- MATLAB 25.2.0.3055257 (R2025b) Update 2, con ITA Toolbox cargado por el entorno.
- Las fixtures y salidas de verificación se crean en directorios nuevos bajo el temporal aprobado de OpenCode, sin modificar datasets existentes.

## Suite del orquestador

- Comando: `uv run --project apps/orchestrator pytest apps/orchestrator/tests -q --basetemp <directorio-temporal-nuevo>`.
- Resultado final tras la corrección de interoperabilidad y sus regresiones: **693 passed**, 163.70 s. La ejecución previa había pasado 685 pruebas.
- Incluye integridad WAV, constancias, persistencia fallida, recuperación secuencial/paralela, T30/reintentos, reinicio, legacy, revalidación de handoff y CLI.
- Mypy con `apps/orchestrator/pyproject.toml`: sin incidencias en los nueve archivos de producción afectados comprobados.

## MATLAB sin simulación RAVEN

- Suites: `test_emit_render_event`, `test_render_completion`, `test_render_diagnostics`, `test_export_render_metadata`.
- Resultado: **28 pruebas satisfactorias**, sin fallidas ni incompletas; `assertSuccess` completó y el proceso salió con código cero.
- Los avisos de shadowing de `fwrite`/`fclose` proceden de stubs locales para inyectar fallos y no representan cambios del path de producción.
- Fixtures de contrato emitidas por los escritores reales para schemas 1.0, 2.0 y 3.0, sin simular RAVEN: `render-contract-b8cf4da3-6588-45ff-ac1b-e4006cb383c2` bajo el temporal aprobado.
- Python verificó las tres fixtures finales con `validate_render(metadata_required=True, canonical=True)` e `inspect_completion`: todas `valid`, incluidos hashes, WAV PCM de 200 frames/2 canales/44100 Hz, fuentes/coordenadas, semilla, metadatos y T30.

## Integración HRTF de recuperación, reinicio y handoff

- `test_end_to_end_recovery_restart_handoff_and_tampered_input_rejection`: recuperación, reinicio sin nueva invocación, conservación del outcome/UUID, planificación de HRTF y bloqueo tras alterar el WAV. Solo el proceso externo está simulado; el pipeline, índices, persistencia, reanudación y handoff ejecutan código real.
- `test_clarity_revalidates_recovered_hrtf_and_blocks_stale_inputs` y `test_clarity_rechecks_input_after_planning_before_submission`: bloqueo por mutación de artefactos, manifiesto/escena, constancia, estado pendiente y cambios entre planificación y envío; no hay envío al backend en esos casos.
- `test_canonical_recovery_keeps_t30_gate_and_retry_history` y prueba de agotamiento: historial de procesos fallidos/T30 rechazado conservado y presupuesto respetado.
- Última ejecución conjunta de aceptación, historial y validación: **219 passed**, 53.79 s. Esto no sustituye el smoke físico de MATLAB/ITA/RAVEN.

## Incidencia detectada por el smoke físico inicial

- El primer render físico completó WAV, sidecar y constancia con proceso cero y artefactos válidos, pero se rechazó correctamente la aceptación ante una incompatibilidad del lector: MATLAB emitió `size_bytes: 1.411244E+6` y el lector exigía representación Python `int` sin normalizar números JSON integrales.
- Evidencia inicial conservada sin reescribir: `render-acceptance-smoke-bf18ae3c5614417f949718f9597aecbf`; intento `1c41f8e9250b4172b1815be5ea643cee`, decisión `abort`.
- Corrección: normalización decimal exacta de exponentes integrales seguros, sin redondear valores fraccionales o inseguros. Pruebas de regresión de tamaños y contadores grandes; no se relajan hashes, tamaños reales, inventario ni validación acústica.
- La nueva verificación física completó ambas fases satisfactoriamente en otro directorio nuevo; la evidencia inicial abortada permanece intacta.

## Smoke físico MATLAB/ITA/RAVEN

- Harness: `apps/orchestrator/tests/verify_render_acceptance.py`, configuración array-only local, sin cambios a datasets ni ejemplos versionados. Solo el harness añade un `error(...)` después de retornar el entrypoint para inducir la salida no cero.
- Evidencia: `render-acceptance-smoke-45b18b89f86942cd8f126a9c32b90d92/acceptance.json` bajo el temporal aprobado.
- Fase normal: intento `99353df83b034fc7a9359fddb6e2319f`, `process_status=succeeded`, `exit_code=0`, `render_validity=valid`, `decision=continue`.
- Fase inducida: intento `703a2fbbd52046c7835b8ec3d6332276`, `process_status=failed`, `exit_code=1`, `render_validity=valid`, `decision=continue_with_warning`.
- El harness verificó constancia/hashes, commit, advertencia durable, T30 aceptado, evento de fin y diagnóstico `post_entrypoint` para el fallo inducido. El código de proceso fallido no fue reescrito como éxito.
- El proceso del harness terminó con código cero después de verificar ambas fases. Esto acredita recuperación ante el fallo posterior controlado, no identifica la causa de otros cierres nativos de MATLAB.

## Trazabilidad de requisitos y escenarios

Los escenarios parametrizados y las pruebas integradas cubren los requisitos de ambos deltas:

| Requisito | Pruebas/evidencia |
|---|---|
| Resultados independientes y decisión explícita | `test_acceptance_table_preserves_process_evidence`, rechazo de render inválido con proceso cero, CLI y ambas fases físicas |
| Constancia vinculada al intento | `test_render_attempt.py`: identidad/schema, inventario exacto, duplicados/extras, constancia previa y hashes; regresiones de notación científica |
| Publicación final y atómica | `test_render_completion.m` y `test_render_diagnostics.m`: temporal, cierres/escrituras fallidas, hash fallido, publicación y diagnóstico no disponible |
| Integridad completa del WAV | `test_render_validation.py`: PCM soportado, cabecera legible con payload truncado, chunks, vacío, formato/canales/frecuencia y dimensiones/duración |
| Metadatos estructurados y coherentes | Versiones 1/2/3, campos ausentes, `{}`, tipos/booleanos, duplicados y valores contradictorios; fixtures reales MATLAB/Python |
| Correspondencia con manifiesto | Fuentes exactas/procedencia/rutas preparadas, ganancias/tiempos, poses y reflexión Z; receptor/IR, array, semilla y ruido |
| Aceptación acústica sin atajos | T30 válido, bandas rechazadas, frecuencias/derivados contradictorios, remuestreo canónico y agotamiento del presupuesto con procesos cero/no cero |
| Recuperación limitada a finalización acreditada | Matriz de aceptación, constancia ausente/contradictoria, lanzamiento/terminación no observada, error genuino y smoke físico inducido |
| Validación uniforme y reutilización | Reinicios después de fallo/commit incompleto, recuperación sin nueva invocación, mutación de hashes y entrada acústica, legacy estricto y handoff revalidado |
| Persistencia visible y aislamiento | Fallos de outcome/índice/commit final, error original encadenado, advertencia durable y contadores, workers 1/3 e historial T30 |
| Compatibilidad estática | Llamadas MATLAB con uno/dos/tres argumentos, metadatos opcionales según perfil, índices legacy sin proceso ficticio y rutas degradadas conservadas |
| Registro del proceso e independencia diagnóstica | `test_matlab_diagnostics.py`, `test_render_diagnostics_integration.py` y recuperación sin directorio/eventos/resumen; no se inventa `entrypoint_completed` |

Ruff sobre todos los archivos Python modificados/nuevos y `git diff --check` no señalaron problemas. Mypy final pasó en nueve archivos de producción; OpenSpec pasó `openspec validate separate-matlab-process-render-outcome --strict`. Los avisos Git de conversión LF/CRLF son informativos del entorno Windows y no son errores de whitespace.

No se archivó el cambio, no se sincronizaron las specs principales y no se hizo commit. Las evidencias físicas y sus fallos iniciales se conservan para revisión.
