# Tasks

## 1. Orchestrator: tolerancia acotada del reemplazo

- [x] 1.1 Implementar un helper de reemplazo con cinco intentos totales solo para `winerror` 5/32/33; verificar con tests y sleeps falsos las esperas 50/100/200/400 ms, éxito intermedio, agotamiento, propagación inmediata de otros errores y conservación de excepción, códigos, rutas y notas.
- [x] 1.2 Integrarlo en publicación de outcome, cadena/circuito, journal y resumen estático, conservando cierre/fsync/locks y sin repetir serialización o transacciones completas; verificar fallos inyectados en cada publicador y que la política del índice sigue siendo la existente, sin bucles anidados.
- [x] 1.3 Documentar en `apps/orchestrator/README.md` la espera máxima programada, errores elegibles y diferencia con reintentos de proceso; verificar ejemplos con tests de CLI para éxito transitorio y denegación persistente con `--no-progress`.

## 2. Orchestrator: candidato y auditoría de cierre

- [x] 2.1 Separar validación común y elegibilidad por acción en `persistence_recovery.py`, incorporando outcome confirmado versión 1 con cadena/intento `running` y clasificación ausente o aceptada coherente; verificar positivos con código cero y `post_completion`, presupuesto de una invocación y negativos de constancia, hashes, T30, identidad, outcome pendiente y terminación no observada, sin regresión de reparación anterior.
- [x] 2.2 Verificar resolución única aceptada del mismo UUID en el circuito, capturar preimagen/vínculo y preservar su prefijo histórico; verificar con tests que no se llama a `BatchCircuit.resolve`, no cambian bytes/mtime del circuito por la reparación, se admiten posteriores resoluciones legítimas y se bloquean ausencia, duplicación, contradicción o alteración del prefijo.
- [x] 2.3 Extender lectores/validadores de journal con versión 2 y acción `finalize_chain`, cuatro preimágenes y outcome reparado versión 2; verificar que no se inventa un error original desconocido, se conservan hechos del proceso y que versiones/acciones desconocidas o preimágenes manipuladas se rechazan mientras journals versión 1 válidos siguen intactos.
- [x] 2.4 Implementar la publicación retomable del nuevo cierre usando el journal antes de modificar estados activos; verificar interrupción en cada frontera (incluida la clasificación ya publicada), reanudación del mismo registro, idempotencia, rechazo de estados ajenos y conservación de manifiestos, recursos, constancias, WAV y sidecars mediante bytes/mtime.
- [x] 2.5 Documentar en `apps/orchestrator/README.md` la acción nueva, compatibilidad de formatos y procedimiento de rollback seguro; verificar correspondencia con fixtures de ambos formatos y ausencia de instrucciones para editar `running`, rebajar esquemas o borrar evidencia.

## 3. Orchestrator: preflight, reutilización y entrega

- [x] 3.1 Ajustar preflight global y autorización específica del preflight de cadenas para reconocer exclusivamente cierres acreditados antes de levantar el veto `running`; verificar cero escrituras y cero despachos ante otro intento no observado, cadena no mapeable, ancestría ambigua, lock, circuito abierto o conflicto en otra escena, sin añadir un bypass genérico.
- [x] 3.2 Exigir cierre coherente de cadenas conocidas en el camino compartido de historial/reutilización/Clarity, incluida aceptación versión 1 sin journal; verificar bloqueo antes de finalizar, aceptación tras cierre completo, rechazo de reparación parcial y rollback incompatible, manteniendo el comportamiento legacy sin cadena y la elegibilidad exclusiva de HRTF para Clarity.
- [x] 3.3 Integrar conteo por UUID de finalizaciones confirmadas en `persistence_repairs`/`persistencias_reparadas`; verificar lote mixto con reparación anterior, cierre nuevo y pendientes, llamada posterior con contador cero y ausencia de nuevas invocaciones, reintentos, fallos de proceso o presupuesto por retries de escritura/cierre.
- [x] 3.4 Añadir pruebas de dos llamadas con RAVEN y Pyroomacoustics simulados, incluyendo canónicas paralelas y otras variantes, e inyectar el fallo después de aceptación/circuito y antes de clasificación o cierre de cadena; verificar que la segunda llamada reutiliza ese UUID, solo ejecuta pendientes, preserva entradas/semilla/bytes/mtimes y que una nueva denegación se retoma en una tercera llamada sin recontabilizar el circuito.
- [x] 3.5 Documentar el relanzamiento normal, el caso distinto de terminación desconocida y el significado del error tras agotar la espera; verificar contra tests de CLI que se muestran contexto, ruta y causa sin atribuir un responsable ambiental no acreditado ni anunciar recuperación garantizada.

## 4. Verificación integrada

- [x] 4.1 Ejecutar `uv run --project apps/orchestrator pytest apps/orchestrator/tests` y registrar el resultado; verificar las regresiones de aceptación, reanudación, reconciliación manual, circuito/presupuesto, aislamiento y elegibilidad Clarity junto con las pruebas nuevas, sin MATLAB real.
- [x] 4.2 Ejecutar `openspec validate harden-render-state-finalization --strict`, revisar el diff y comprobar cobertura de los escenarios; verificar que solo se modifica el área autorizada del orquestador y que no se tocaron configuraciones, resultados reales, contratos de backend ni tareas completadas del cambio anterior.

## Workflow follow-up

### Verification evidence (2026-11-07)

- `uv run --project apps/orchestrator pytest apps/orchestrator/tests`: **1450 passed in 663.58s**, Windows / Python 3.12.12; no real MATLAB or experiment results used.
- New coverage: 34 bounded-replacement tests, 63 finalization tests, and CLI transient/exhausted-publication checks. Existing acceptance-repair, legacy reuse, reconciliation, budget/circuit, isolation and HRTF-only Clarity suites pass.
- The initial full run passed 1444 tests; a final-code rerun timed out at 600 seconds without failures and was repeated with a longer timeout to obtain the complete result above.
- Optional Ruff check unavailable: executable not installed; no dependency/configuration changes made.
- `openspec validate harden-render-state-finalization --strict`: valid. `git diff --check`: passed. Scope review: implementation/tests/README confined to `apps/orchestrator`, plus this task tracker; pre-existing changes, previous change tasks, backend contracts, configs and real outputs preserved. No archive/sync performed.

- Revisar/verificar ambos cambios antes de archivar; sincronizar/archivar primero `recover-render-persistence-failures` y después `harden-render-state-finalization` para conservar el orden de sus deltas.
- No ejecutar la E2E real ni aplicar reparaciones al run como parte de las pruebas de desarrollo; una futura llamada operativa del usuario verificará su evidencia vigente.
