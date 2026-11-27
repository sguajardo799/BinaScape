# Tasks

## 1. Orquestador: contrato de observación y ETA

- [x] 1.1 Añadir un evento tipado pequeño y callback opcional con emisión protegida y estado por etapa/unidad lógica; verificar con pruebas que un observador ausente o defectuoso no imprime, no modifica retornos y no sustituye errores funcionales.
- [x] 1.2 Implementar ETA secuencial con media móvil y ETA paralelo por tasa de reloj, calentamiento de tres aceptaciones y exclusión de reutilizaciones; verificar con reloj simulado reintentos/esperas, fallos, etapas vacías, reinicio entre etapas y valores finitos no negativos.
- [x] 1.3 Implementar presentación Rich en stderr, modo no interactivo de transiciones y cierre seguro, manteniendo un solo dueño de escritura; verificar TTY simulado, ancho reducido, texto literal, fallos de inicio/actualización/cierre e interrupción.
- [x] 1.4 Documentar en `apps/orchestrator/ARCHITECTURE.md` la separación entre observación y autoridad funcional y la API opcional; verificar que los ejemplos programáticos siguen siendo silenciosos sin callback.

## 2. Orquestador: planificación y publicación

- [x] 2.1 Instrumentar `inspect_static_plan()` para informar catálogos, evaluación y resultado único por índice, incluyendo conservadas, nuevas, fallos y conflictos; verificar las secuencias con pruebas de `test_static_plan.py` sin editar sampler ni fuentes incluidas en las huellas de generación.
- [x] 2.2 Conectar `_prepare_static_manifests()` y `apply_static_plan()` a transiciones de verificación y publicación; verificar que un conflicto de preflight no anuncia publicación y que las pruebas de reanudación protegida conservan bytes/mtime de entradas existentes.
- [x] 2.3 Añadir `--no-progress` a `generate-manifests` y activar el observador solo desde el CLI; verificar stdout y códigos 0/1/2 existentes, modo no interactivo sin ANSI y documentación del comando en `apps/orchestrator/README.md`.

## 3. Orquestador: render y actividad MATLAB

- [x] 3.1 Conectar la fase canónica al progreso por escena con invocaciones, reintentos y remuestreos separados; verificar aceptación tras reintento, rechazo T30 seguido de aceptación, agotamiento acústico y reutilización sin invocación en las pruebas de render existentes.
- [x] 3.2 Conectar variantes restantes al progreso después de la decisión persistida del coordinador, incluyendo reutilizadas recogidas previamente; verificar contabilidad única en secuencial/paralelo, resultados fuera de orden, corte del lote y trabajos no despachados pendientes.
- [x] 3.3 Registrar actividad por intento desde el runner y añadir lectura incremental tolerante de los eventos existentes en el ciclo de observación; verificar UUID ajeno, secuencia inválida, UTF-8/línea parcial, archivo ausente, intentos concurrentes y no repetición de avisos, conservando `test_matlab_diagnostics.py`.
- [x] 3.4 Garantizar parada y limpieza del sondeo sin cambiar `subprocess.run()` ni política de cancelación; verificar que APIs sin observador y modo desactivado no crean sondeo, que los recursos cierran ante `KeyboardInterrupt` y que los fallos diagnósticos no alteran aceptación ni reintentos.
- [x] 3.5 Añadir `--no-progress` a `render-static` y documentar etapas, actividad observada, reutilización y límites del ETA en `apps/orchestrator/README.md`; verificar resúmenes existentes y códigos de salida con `CliRunner`, y que no se afirma finalización a partir de `entrypoint_completed`.

## 4. Clarity backend: progreso local

- [x] 4.1 Declarar Rich como dependencia directa en `apps/clarity-backend/pyproject.toml` y actualizar los lockfiles gestionados aplicables sin cambios ajenos; verificar resolución/sincronización de dependencias con uv.
- [x] 4.2 Añadir presentación local y ETA en `run-manifest`, creando el observador desde el CLI y manteniendo `run_manifest()` silencioso por defecto; verificar total por líneas no vacías, éxitos/fallos, manifiesto vacío y persistencia de metadatos antes del avance con `apps/clarity-backend/tests/test_cli.py`.
- [x] 4.3 Añadir `--no-progress`, modo no interactivo y cierre protegido; verificar independencia de stdout, continuidad tras un job fallido, aborto por JSON inválido posterior sin cambiar outputs previos, fallo de escritura de metadatos e interrupción.
- [x] 4.4 Documentar uso directo, ETA y estados terminales en `apps/clarity-backend/README.md`; verificar los ejemplos CLI y que los contratos de WAV/sidecar y contenido de metadatos no incorporan campos de presentación.

## 5. Orquestador y Clarity: handoff sin barras en competencia

- [x] 5.1 Instrumentar `prepare_clarity_handoff()` para diferenciar deshabilitado, preparación, revalidación, sin pendientes, bloqueo y envío real; verificar cada transición con `test_clarity_handoff.py` y `test_render_pipeline_clarity.py`, sin sumar bloqueados/omitidos/reutilizados al total ejecutable del backend.
- [x] 5.2 Propagar la política opcional de progreso hasta `clarity_runner.py`, añadiendo `--no-progress` al comando cuando corresponda y suspendiendo la presentación padre mientras ejecuta el hijo; verificar construcción con uv/sin uv, auto-submit, `clarity-handoff --submit` y APIs programáticas silenciosas de extremo a extremo.
- [x] 5.3 Añadir `--no-progress` a `clarity-handoff` y documentar la diferencia entre habilitar, preparar y ejecutar degradado en el README del orquestador; verificar CLI con/sin envío y recuperación del resumen tras éxito o salida no cero del backend.

## 6. Verificación de integración

- [x] 6.1 Ejecutar `uv run --project apps/orchestrator pytest apps/orchestrator/tests` y `uv run --project apps/clarity-backend pytest apps/clarity-backend/tests`; verificar que las suites completas conservan los contratos de aceptación, reanudación, reintentos, elegibilidad, metadatos y códigos de salida.
- [x] 6.2 Añadir una prueba de equivalencia con progreso habilitado/deshabilitado que compare estados funcionales, semillas, invocaciones y artefactos protegidos; verificar que la huella de generación no cambia y que una reanudación completa no invoca MATLAB ni atribuye muestras temporales nuevas.
- [x] 6.3 Verificar en una terminal Windows una ejecución de varias escenas y perfiles, su reanudación, `--no-progress` y stderr redirigido; comprobar elapsed durante trabajo largo, ETA tras tres muestras cuando aplique, actividad por intento y ausencia de barras padre/hijo simultáneas. Registrar en la revisión si MATLAB/RAVEN no está disponible y cubrir el recorrido con subprocesses simulados.
- [x] 6.4 Ejecutar `openspec validate add-pipeline-progress --strict` y contrastar los escenarios de `pipeline-progress` con las pruebas entregadas; verificar que todos los requisitos nuevos tienen cobertura y que no se añadieron formatos persistentes ni cambios de alcance.

### Evidencia de verificación

- Suite completa del orquestador: 965 aprobadas; suite completa Clarity: 21 aprobadas. Tras añadir comprobaciones adicionales, el archivo focalizado `apps/orchestrator/tests/test_progress.py` pasa sus 25 pruebas.
- Los escenarios se verifican en `test_progress.py` de cada aplicación, `test_static_plan.py`, los casos T30 y paralelos de `test_generate_manifests.py`, y las pruebas de handoff/runner. Incluyen ETA con reloj simulado, actividad larga, aislamiento, consola simulada, reanudación protegida, orden de resolución y pendientes tras corte.
- `openspec validate add-pipeline-progress --strict` y lint de los tres módulos nuevos de producción: satisfactorios. La comprobación amplia de lint también señala reglas de estilo y guardas existentes en módulos previos; no se hizo una limpieza ajena al cambio.
- La lista de fuentes incluidas en la huella de generación no tiene diferencias. No se añaden formatos persistentes ni se modifican DSP, MATLAB o YAML experimental.
- **6.3 completada con confirmación del usuario (2026-10-24):** el usuario realizó la comprobación visual pendiente y confirmó que se ve correctamente. Esta confirmación complementa las pruebas automatizadas de consola simulada, reanudación, desactivación y redirección. La inspección visual fue realizada por el usuario, no por el agente, cuyo harness no dispone de TTY.
- **Observación de verificación corregida (2026-10-24):** interrumpir el muestreo con `KeyboardInterrupt` conserva pendiente la escena, sin incrementar resueltas ni conflictos. La regresión parametrizada comprueba la interrupción en la primera escena y después de una evaluación correcta; se confirmó el fallo antes de corregirlo. Pasan 67 pruebas de planificación, progreso y reanudación protegida, y la validación estricta de OpenSpec.
