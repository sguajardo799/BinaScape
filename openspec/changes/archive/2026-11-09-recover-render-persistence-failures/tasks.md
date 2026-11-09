# Tasks

## 1. Orchestrator: errores explícitos en CLI

- [x] 1.1 Transportar contexto de escena, variante, intento, cadena y evidencia en errores de render y reparación, preservando causa original y notas secundarias; verificar con tests de `RenderStaticRunError` para error de proceso más fallo de persistencia y error sin intento.
- [x] 1.2 Mostrar resumen disponible y error concreto en stderr al finalizar `render-static`, incluyendo fallos de preflight y `--no-progress`; verificar con `CliRunner` código 1, tipo/mensaje `PermissionError`, rutas de reemplazo y ausencia de atribuciones ambientales inventadas.
- [x] 1.3 Documentar en `apps/orchestrator/README.md` cómo interpretar el error terminal y la diferencia entre archivos completos y aceptación confirmada; contrastar el ejemplo con las aserciones de salida CLI.

## 2. Orchestrator: verificación y publicación recuperable

- [x] 2.1 Implementar inspección de candidatos en `pipeline/persistence_recovery.py` reutilizando comprobaciones de constancia, manifiestos, artefactos, T30 y recursos RAVEN; añadir tests positivos de outcome versión 1 con código cero/no cero recuperable y negativos de constancia ausente/ajena, hashes alterados, T30 rechazado, índice contradictorio y proceso no observado.
- [x] 2.2 Añadir journal versionado por intento con preimágenes, hashes, error original y estados previstos, y outcomes reparados versión 2; verificar publicación previa a estados activos, rechazo seguro por lectores anteriores y conservación de UUID, ordinal y evidencia de proceso.
- [x] 2.3 Implementar publicación de reparación y finalización idempotente de estados parciales, sin restaurar registros ajenos del índice; verificar inyección de fallo en cada frontera, reinicio posterior, índice ausente/rezagado, temporales incompletos y rechazo de transiciones o journals manipulados.
- [x] 2.4 Integrar validación del journal en `render_history`, preflight de cadenas y caminos compartidos de reutilización/entrega a Clarity; verificar que una reparación incompleta no habilita aceptación aunque un archivo diga `committed`, y que `post_completion` conserva código, error y advertencia.
- [x] 2.5 Documentar el formato y ciclo de vida de reparación en `apps/orchestrator/README.md`, incluida la incompatibilidad conservadora con lectores anteriores; verificar correspondencia con fixtures y ausencia de instrucciones de edición manual de estados.

## 3. Orchestrator: reanudación automática y contabilidad

- [x] 3.1 Separar el preflight global de lectura de la publicación de reparaciones bajo `static_writer_lock`, recargar evidencia y validar otra vez antes de aplicar plan/despachar; verificar que otro conflicto del lote, cadena no resuelta, lock privado o circuito abierto impide toda reparación y lanzamiento.
- [x] 3.2 Activar recuperación solo con `resume_if_possible=true`, sin requerir sobrescritura, y conservar exclusiones de reintento de proceso; adaptar `test_failed_index_commit_cannot_become_legacy_after_restart` para distinguir reparación verificada de aceptación por mera existencia y comprobar que `max_attempts=1` no impide reparar ni autoriza una invocación nueva.
- [x] 3.3 Añadir `persistence_repairs` y `persistencias_reparadas` con cero por defecto y conteo por llamada; verificar primera reparación, reutilización posterior, lote mixto y separación de reanudadas, invocaciones, reintentos y recuperaciones `post_completion`.
- [x] 3.4 Añadir prueba de dos llamadas con backend simulado e inyección del fallo de reemplazo de outcome después de publicar el índice: segunda llamada repara y reutiliza el intento, conserva bytes/mtimes de todos los archivos protegidos y solo ejecuta las variantes pendientes; cubrir canónicas paralelas y otras variantes.
- [x] 3.5 Probar denegación persistente durante reparación: la llamada informa el nuevo error, no despacha el backend y una tercera llamada completa el mismo journal; verificar que invocaciones ya iniciadas en la llamada original se drenan y registran sin nuevos despachos tras el fallo.
- [x] 3.6 Documentar el relanzamiento normal de `render-static`, contador nuevo, requisitos de elegibilidad y diferencia con `reconcile-static`; verificar los ejemplos contra tests de CLI sin ejecutar ni modificar `outputs/directivity_experiment2`.

## 4. Verificación integrada del monorepo

- [x] 4.1 Ejecutar `uv run --project apps/orchestrator pytest apps/orchestrator/tests` y verificar que pasan regresiones de aceptación, reanudación, presupuesto persistente, reconciliación manual, aislamiento RAVEN y elegibilidad Clarity sin MATLAB real.
- [x] 4.2 Verificar el mecanismo compartido con fixtures Pyroomacoustics y RAVEN: constancias válidas se reparan sin invocar workers y las guardas son equivalentes; ejecutar los tests de resiliencia de ambos backends.
- [x] 4.3 Ejecutar `openspec validate recover-render-persistence-failures --strict` y revisar el diff final; verificar que cada escenario está cubierto y no se alteraron configuraciones de experimento, audio real ni contratos de backend.

## Verification

- Suite completa: `uv run --project apps/orchestrator pytest apps/orchestrator/tests -q --tb=short` — 1344 passed (578.66 s).
- Tras los últimos ajustes de validación/contexto y tests adicionales: `uv run --project apps/orchestrator pytest apps/orchestrator/tests/test_persistence_recovery.py apps/orchestrator/tests/test_render_errors.py -q --tb=short` — 50 passed (62.51 s).
- Resiliencia compartida RAVEN/Pyroomacoustics: 83 passed; concurrencia canónica, reparación y errores: 53 passed en las ejecuciones focalizadas correspondientes.
- `openspec validate recover-render-persistence-failures --strict` — valid; `git diff --check` — sin errores de whitespace.
- Ruff no estaba disponible en el entorno (`Failed to spawn: ruff`); no se instaló ninguna dependencia para este cambio.
- No se ejecutó el experimento real ni se modificaron sus configuraciones, audio o resultados. Las pruebas usan fixtures temporales y backends simulados.
