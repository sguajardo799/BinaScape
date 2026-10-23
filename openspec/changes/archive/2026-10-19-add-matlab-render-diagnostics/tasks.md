# Tasks

## 1. Orquestador: persistencia y resultado del proceso

- [x] 1.1 Añadir el directorio base de diagnóstico al layout y contexto opcional del runner, con identidad UUID y snapshot de entrada por intento; verificar mediante pruebas de rutas, llamadas directas y dos intentos de la misma variante sin sobrescritura.
- [x] 1.2 Persistir resumen inicial/final versionado, tiempos UTC, duración monotónica, comando, código o error y salida stdout/stderr a disco manteniendo `check=True` y el relanzamiento original; verificar éxito, salida no cero, ejecutable ausente, error previo al lanzamiento y rutas con espacios/apóstrofos en pruebas del runner.
- [x] 1.3 Añadir lectura tolerante de eventos y derivación de progreso, ubicación del fallo y confirmaciones de escritura, con observaciones de archivos anteriores/posteriores; verificar eventos truncados, intento incorrecto, ausencia de eventos, cada etapa interrumpida y fallo posterior al entrypoint mediante fixtures.
- [x] 1.4 Aislar fallos exclusivos de diagnóstico con advertencias y fallback a salida heredada; inyectar fallos al crear directorio, copiar entrada, abrir/vaciar/cerrar log, leer eventos, reemplazar resumen y emitir advertencias (incluidas advertencias promovidas a excepción). Verificar para render correcto y fallido que no cambia el resultado ni se repite la invocación, y que se relanza la misma instancia de excepción original cuando existe.
- [x] 1.5 Documentar en `apps/orchestrator/README.md` layout, campos, evidencia incompleta, diferencias respecto al índice, fallback y retención manual; verificar que los ejemplos de resumen son JSON válido y coinciden con las pruebas.
- [x] 1.6 Implementar pruebas parametrizadas del runner correspondientes a la matriz de `design.md`: lanzamiento, ausencia de eventos, inicialización, llamada RAVEN, audio posterior, WAV, metadatos y fallo posterior al entrypoint; verificar código, ubicación, último progreso y confirmaciones esperadas de cada fila.
- [x] 1.7 Probar la independencia entre `entrypoint_completed` y `process_status` con evento final/código no cero, código cero/sin evento y resumen inicial sin terminación observada; verificar que ningún evento establece éxito del proceso y que no se inventan eventos ni códigos.

## 2. MATLAB: eventos de etapas y escrituras

- [x] 2.1 Crear un helper de eventos JSONL con contexto opcional, versión, intento, secuencia y UTC, cierre por evento y advertencias protegidas; verificar con pruebas MATLAB sin RAVEN que genera líneas válidas, no hace nada sin contexto y no falla funcionalmente ante un destino no escribible.
- [x] 2.2 Instrumentar `run_raven_static_render.m` y `static/render_static_scene.m` en los límites de inicialización, simulación/procesado, escritura y fin del entrypoint, propagando contexto opcional; añadir inicio/fin de subpasos `raven_run` y `audio_postprocessing` sin instrumentación interna de RAVEN. Verificar orden y cuatro eventos adicionales por variante, ausencia de fin tras fallo y secuencias con errores de carga, preparación, llamada RAVEN y audio posterior mediante mocks/stubs.
- [x] 2.3 Emitir confirmación individual después de cada WAV/metadato escrito y error estructurado antes del `rethrow` existente; verificar con pruebas de escritores simulados el caso WAV exitoso/metadatos fallidos y la conservación del identificador original de excepción.
- [x] 2.4 Mantener aceptadas las llamadas estáticas sin contexto y multi-variante; verificar con las pruebas MATLAB actuales y una prueba multi-variante que las identidades de salida permanecen separadas en eventos y no cambian los resultados acústicos simulados.
- [x] 2.5 Documentar en `apps/matlab/README.md` el contexto opcional, etapas y límite entre fin del entrypoint y salida del proceso, manteniendo explícito que el flujo dinámico no está soportado; verificar la documentación contra los eventos de las pruebas.
- [x] 2.6 Inyectar fallo del helper dentro del catch del renderer y fallo del mecanismo de advertencia; verificar con pruebas MATLAB que el error original mantiene identificador, mensaje y stack relevante mediante `rethrow`, y que un render correcto no falla por diagnóstico.

## 3. Orquestación: integración sin cambios funcionales

- [x] 3.1 Pasar el directorio base a todas las invocaciones desde `render_pipeline.py`, incluida variante canónica y tareas secuenciales/paralelas, adaptando mocks de pruebas al argumento opcional; verificar que cada invocación crea un intento y que una variante reanudada no lo crea.
- [x] 3.2 Añadir regresiones para reintentos T30, snapshots y aislamiento paralelo; verificar que se conserva evidencia de intentos descartados, no se mezclan logs y los recuentos y decisiones de planificación permanecen iguales.
- [x] 3.3 Añadir regresión de artefactos completos con proceso no cero y de salidas preexistentes sin confirmación del intento; verificar que el índice mantiene sus estados actuales y el mismo fallo se propaga aunque el diagnóstico señale fallo del proceso.
- [x] 3.4 Añadir pruebas con barrera de sincronización para forzar invocaciones solapadas, con marcas exclusivas en consola, entrada y eventos; verificar directorios/UUID distintos, rutas y temporales propios y ausencia de marcas cruzadas en todos los archivos. Repetir a nivel runner con la misma variante y entrada inmutable, sin afirmar soporte de renders acústicos simultáneos sobre las mismas salidas.
- [x] 3.5 Inyectar un fallo exclusivo de diagnóstico en uno de los intentos solapados; verificar que el otro conserva íntegros su directorio, logs, resumen, eventos, snapshot y resultado, y que ninguno se vuelve a invocar por ese fallo.
- [x] 3.6 Comparar escenarios con y sin fallos exclusivos de diagnóstico usando los mismos resultados del renderer; verificar igualdad de estados, validación, decisiones de reanudación y recuentos funcionales (excluidos timestamps), tanto con éxito como con excepción original conservada.

## 4. Verificación transversal

- [x] 4.1 Ejecutar la suite del orquestador y las pruebas MATLAB pertinentes; verificar ausencia de regresiones en índices, reanudación, metadatos y receptores soportados, registrando comandos y resultados y vinculando cada fila de la matriz de `design.md` a su prueba y resultado (o limitación explícita).
- [x] 4.2 Ejecutar una comprobación opt-in en Windows con MATLAB/ITA/RAVEN instalados para un render correcto y un manifiesto inválido, contrastando código de salida, captura de consola y eventos; si el entorno no está disponible, documentar explícitamente qué aceptación real queda pendiente sin sustituirla por resultados simulados.

## Verification evidence

- `uv run --extra dev pytest -q` desde `apps/orchestrator`: **403 passed**, 93.01 s.
- `matlab -wait -batch "addpath(genpath('apps/matlab')); results=runtests('apps/matlab/tests'); assertSuccess(results);"` desde la raíz: suite MATLAB completa aprobada en R2025b. Los nuevos casos usan stubs; no prueban simulación RAVEN real.
- `uv run --extra dev ruff check src/acoustic_orchestrator/pipeline/matlab_diagnostics.py src/acoustic_orchestrator/pipeline/matlab_runner.py tests/test_matlab_diagnostics.py tests/test_render_diagnostics_integration.py`: aprobado.
- `git diff --check`: sin errores de espacios; Git advierte conversión LF/CRLF según configuración local.

Correspondencia de la matriz de diseño con pruebas aprobadas:

| Fila de matriz | Prueba Python / MATLAB |
| --- | --- |
| Lanzamiento | `test_runner_failure_matrix[launch]`, `test_process_result_and_console`, `test_prelaunch_failure_and_matlab_path_escaping` |
| Antes del entrypoint | `test_runner_failure_matrix[before_entrypoint]` |
| Inicialización | `test_runner_failure_matrix[initialization]`, `testFailuresDoNotCompleteActiveStage` (load/prepare) |
| Llamada RAVEN | `test_runner_failure_matrix[raven_run]`, `testFailuresDoNotCompleteActiveStage` (run) |
| Audio posterior | `test_runner_failure_matrix[audio_postprocessing]`, `testFailuresDoNotCompleteActiveStage` (audio) |
| Escritura WAV / metadatos | `test_runner_failure_matrix[wav/metadata]`, `testWavAndMetadataFailuresKeepOriginalError` |
| Posterior al entrypoint | `test_runner_failure_matrix[post_entrypoint]`, `test_completed_artifacts_and_failed_process_remain_independent` |
| Éxito observado / sin evento | `test_entrypoint_and_process_status_are_independent` |
| Terminación no observada | `test_unobserved_termination_does_not_invent_success` (interrupción simulada, no caída física del sistema) |
| Fallo exclusivo de diagnóstico | `test_diagnostic_failures_never_replace_process_result`, `testDiagnosticAndWarningFailuresPreserveOriginalException`, `test_diagnostic_failure_does_not_change_pipeline_state` |
| Concurrencia | `test_concurrent_attempt_directories_are_isolated` (barrera, entradas distintas y entrada compartida) |
| Concurrencia con fallo aislado | `test_concurrent_diagnostic_failure_does_not_affect_other_attempt` |

Los fallos internos y posteriores al entrypoint se inyectan: no se provocan caídas nativas de RAVEN/MATLAB ni fallos físicos de disco. La aceptación real del proceso y render se registra por separado en 4.2.

### Aceptación real 4.2

- Comando desde la raíz: `uv run --project apps/orchestrator python apps/orchestrator/tests/verify_matlab_diagnostics.py --output-root "C:/Users/sguaj/AppData/Local/Temp/opencode/matlab-diagnostics-acceptance-20261019-01"`.
- Windows, MATLAB R2025b, ITA Toolbox y RAVEN instalados; **aprobado** sin mocks del proceso ni del renderer. Configuración array-only con un bin amplio para limitar esta comprobación a proceso/diagnóstico, sin modificar los ejemplos versionados.
- Render real: una variante completada, una invocación, código **0**, duración **71.672 s**, **13 eventos**, fin del entrypoint y ambas escrituras confirmadas; consola capturada y cero errores de diagnóstico.
- Manifiesto inválido: código **1**, `process_status=failed`, `entrypoint_completed=false`, `failure_location=initialization`, error MATLAB y consola conservados.
- Reporte local: `C:/Users/sguaj/AppData/Local/Temp/opencode/matlab-diagnostics-acceptance-20261019-01/acceptance.json`. Cada intento conserva su `invocation.json`, `events.jsonl`, `console.log` y snapshot en ese árbol; no se sobrescribieron salidas previas.
- Esta aceptación no provoca errores nativos posteriores al entrypoint: esa independencia se verifica mediante los casos de fallo inyectado de la matriz.
