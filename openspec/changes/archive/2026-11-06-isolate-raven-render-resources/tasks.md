# Tasks

## 1. Orquestador: recursos privados y procedencia

- [x] 1.1 Añadir al layout y a un helper Python localizado la descripción pura del workspace por run, variante y realización, junto con la identidad técnica RAVEN; verificar con tests que workers/UUID no cambian la entrada y que runs, variantes y realizaciones distintas no colisionan, incluso con iguales nombres de slots.
- [x] 1.2 Implementar la derivación mínima del RPF y copia del seed a `RavenModels`, con base privada de materiales y resolución de rutas consistente con RAVEN; verificar con fixtures Windows absolutos/relativos, espacios, codificación y finales de línea que solo cambian las claves autorizadas y no se altera el RPF original.
- [x] 1.3 Publicar y comprobar `provenance.json` versionado y huellas de entradas inmutables; añadir tests de archivos alterados, claves ambiguas, modelo ilegible, ancestros con `RavenModels` y fallos de preparación que demuestren error previo al proceso sin fallback global ni reemplazos silenciosos.
- [x] 1.4 Documentar en `apps/orchestrator/README.md` el layout, la propiedad de archivos mutables y los errores de preparación; verificar que los ejemplos corresponden al layout generado y no requieren opciones YAML nuevas.

## 2. Orquestador: runtime, dispatch y realización compartida

- [x] 2.1 Integrar descripción/materialización en `matlab_runner.py` y `render_pipeline.py` antes del snapshot y del fan-out, cubriendo también la entrada directa del runner sin transformación posterior de snapshots; verificar con tests del runner y `test_render_attempt.py` que comando, bytes ejecutados y SHA-256 siguen vinculados al mismo manifiesto.
- [x] 2.2 Incorporar la comprobación de condiciones comunes de las variantes respecto a la escena sin nuevos sorteos; verificar en `test_generate_manifests.py` que sala, absorción, scattering, fuentes, tiempos, ganancias y pose permanecen iguales en tripletas y que un desacuerdo físico se detecta antes del dispatch.
- [x] 2.3 Conservar cuotas por escena y el remuestreo canónico completo; verificar con treinta escenas y seis bins cuotas objetivo de cinco, noventa variantes para tripletas, igualdad del plan entre uno/cuatro workers y un workspace nuevo para toda realización posterior a rechazo T30.
- [x] 2.4 Conservar el camino Pyroomacoustics, single-output, múltiples sujetos, array circular y esquemas legacy/poligonales; verificar con pruebas parametrizadas que solo RAVEN genera recursos privados y que IDs, nombres públicos, canales, selección de IR y elegibilidad Clarity no cambian.
- [x] 2.5 Documentar junto al flujo de render el alcance automático del aislamiento y la excepción de llamadas manuales directas a MATLAB; verificar en la documentación que no se presenta como determinismo bit a bit ni como nuevo umbral T30 productivo.

## 3. Orquestador: reintentos, evidencia y reanudación

- [x] 3.1 Vincular la preparación aislada estable a las cadenas existentes e inventariar los modelos/materiales generados en la evidencia del intento; verificar en `test_process_retries.py` y `test_render_retry_dispatch.py` que un reintento conserva entrada/presupuesto, cambia UUID/ordinal y nunca introduce dos escritores de un workspace.
- [x] 3.2 Extender el preflight y la construcción del runtime esperado con comprobación de procedencia, sin ignorar `base_rpf_file` en el comparador general; verificar en `test_static_resume_safety.py` conflictos previos a escrituras ante RPF original/privado alterado, registro ausente o recursos protegidos faltantes.
- [x] 3.3 Añadir regresiones de reanudación completa y parcial de variantes aisladas; verificar cero invocaciones e igualdad de bytes/mtime en todos los archivos protegidos de las reutilizadas, sin reconstruir recursos como efecto de inspeccionar compatibilidad.
- [x] 3.4 Preservar validación legacy e incorporar procedencia `verified_private`/`legacy_unverified` en índice y resumen; verificar con fixtures históricos y lotes mixtos que no se reescriben runtimes ni se inventa aislamiento, y que una cadena histórica pendiente produce conflicto explícito sin reiniciar su presupuesto.
- [x] 3.5 Documentar la reanudación, estados de procedencia y migración recomendada a un run nuevo completo; verificar que la guía conserva las opciones existentes de sobrescritura, advierte sobre resultados históricos aparentemente coherentes y no ordena borrar datos ni editar el toolbox.

## 4. Orquestador: harness MATLAB opt-in

- [x] 4.1 Añadir un harness reproducible dentro de `apps/orchestrator` que invoque el flujo de producción para dos escenas fijas y tripletas en runs nuevos, con uno y cuatro workers; verificar tests Python de su preparación y comparación sin depender de MATLAB ni de `outputs/rt_pairing_investigation_cec8d2352d`.
- [x] 4.2 Ejecutar el harness con MATLAB/RAVEN disponible y documentar el comando exacto, prerrequisitos y ruta de evidencia; verificar doce renders exitosos, propiedad exclusiva de modelos/materiales y concordancia de las diez bandas dentro de cada tripleta y contra el run secuencial con `atol=1e-5 s` y `rtol=1e-5`.
- [x] 4.3 Ampliar la evidencia del harness con huellas originales/globales antes y después e inventario de los RPF técnicos nuevos; verificar que RPF base, modelos y materiales globales no cambian, sin borrar archivos preexistentes ni exigir que se reproduzca una carrera en cada ejecución.

## 5. Verificación integrada del cambio

- [x] 5.1 Ejecutar la suite de `apps/orchestrator/tests` con el entorno del proyecto y los checks estáticos configurados; verificar ausencia de regresiones en aceptación, recuperación, reintentos, reanudación, planificación RT, progreso y entrega Clarity, registrando comandos y resultados reales.
- [x] 5.2 Revisar el diff final y los artefactos con `openspec validate isolate-raven-render-resources --strict`; verificar que solo se implementaron cambios de orquestador y sus pruebas/documentación, no se editó código MATLAB/toolbox y cada escenario de los deltas tiene prueba automatizada o evidencia del harness identificable.

## Workflow follow-up

- Solicitar revisión y ejecutar el workflow de verificación antes de archivar.
- Archivar únicamente por petición posterior; no regenerar datasets históricos ni sincronizar specs principales como efecto de crear esta propuesta.

## Verificación registrada

- Suite final: `.venv\Scripts\python.exe -m pytest apps/orchestrator/tests -q --durations=10` → **1300 passed**, 598,83 s.
- Ruff en `raven_resources.py`, `verify_raven_isolation.py` y sus dos módulos de tests nuevos → **All checks passed**. Comparación automatizada de `uvx ruff check apps/orchestrator/src --output-format json` contra los mismos archivos de `HEAD` → 96 diagnósticos previos, 96 actuales, **cero nuevos**.
- Mypy: `uvx --with pydantic --with typer --with rich --with types-PyYAML mypy --follow-imports silent src/acoustic_orchestrator/pipeline/raven_resources.py src/acoustic_orchestrator/verify_raven_isolation.py src/acoustic_orchestrator/pipeline/matlab_runner.py` desde `apps/orchestrator` → **Success, 3 source files**. El chequeo global `mypy src` conserva **31 errores preexistentes**, incluidos imports/stubs opcionales ausentes en el entorno aislado; comparación automatizada con un snapshot temporal de `HEAD` → **cero errores nuevos**. No se declaran verdes esos chequeos globales ni se cambian áreas ajenas para silenciarlos.
- Harness real inicial: `.venv\Scripts\python.exe -m acoustic_orchestrator.verify_raven_isolation configs/experiments/static_example.yml --output outputs/raven_isolation_acceptance_20261106_s42_a` → 12 procesos exitosos, 14 comparaciones satisfactorias, recursos exclusivos y huellas globales sin cambios; diferencia máxima por banda `2.4512760810058154e-7 s`.
- Reanudación sobre los seis renders reales del run de cuatro workers → seis variantes reutilizadas, cero invocaciones MATLAB e igualdad de bytes/mtime de escenas, runtime, audio preparado, recursos, salidas e intentos previos.
- Harness final repetido con `--output outputs/raven_isolation_acceptance_20261106_s42_b` → `ok=true`, 12/12 procesos exitosos, 14 comparaciones satisfactorias, doce conjuntos de recursos exclusivos, doce RPF técnicos nuevos y huellas globales sin cambios; diferencia máxima por banda `1.072771954602203e-7 s`. Reporte: `outputs/raven_isolation_acceptance_20261106_s42_b/report.json`.
- Revisión final: `git diff --check` sin errores de whitespace; `openspec validate isolate-raven-render-resources --strict` válido; inventario Git limitado a `apps/orchestrator/` y los artefactos de este cambio, sin cambios MATLAB/toolbox ni specs principales.

### Correspondencia de requisitos y evidencia

| Requisito del delta | Pruebas o evidencia |
|---|---|
| Exclusividad de recursos | `test_raven_resources.py`: runs/variantes/realizaciones distintas, bloqueos entre runners directos y coordinados; harness final con rutas efectivas exclusivas |
| Procedencia y derivación | `test_raven_resources.py`: transformación mínima, rutas, bytes, duplicados, entradas ilegibles y recursos alterados; huellas originales/globales del harness |
| Condiciones compartidas | `test_generate_manifests.py`: `test_private_resources_exist_before_dispatch_and_snapshot` y `test_variant_physical_disagreement_is_detected_before_dispatch`; comparación T30 de tripletas del harness |
| Cuotas y remuestreo | `test_thirty_scene_rt_plan_counts_scenes_not_ninety_outputs` y pruebas `invalid_canonical_raven_t30`, con recursos anteriores conservados y nueva realización común |
| Evidencia y reintentos | `test_render_attempt.py`, `test_render_retry_dispatch.py` y `test_interruption_recovery.py`: snapshots, hashes, presupuesto, UUID e inventarios, más archivo del lock reconciliado de su propietario |
| Compatibilidad de backends y salidas | Parametrización en `test_raven_resources.py`, pruebas de pairing, Clarity y `test_pra_pipeline.py`; comparación T30 del harness sin nuevo gate productivo |
| Compatibilidad de RPF derivados | `test_static_resume_safety.py`: conflictos antes de escrituras por original/privado/seed/procedencia/materiales alterados o propietario no reconciliado |
| Reanudación no destructiva | Tests de reanudación aislada completa/parcial en `test_static_resume_safety.py` y comprobación adicional sobre los seis renders reales |
| Procedencia histórica | Fixtures históricos y mixtos, veto a cadena histórica pendiente en `test_static_resume_safety.py`; runs nuevos independientes y documentación de regeneración explícita |

### Advertencias abiertas aceptadas al archivar

La verificación posterior identificó dos divergencias reproducibles. El usuario confirmó sincronizar y archivar conservándolas abiertas; no se consideran corregidas por el archivo:

- `apps/orchestrator/src/acoustic_orchestrator/pipeline/render_pipeline.py:1305`: la comprobación del inventario de recursos generados depende de que exista una entrada completada en `render_index.jsonl`. Si falta el índice, se pueden reutilizar variantes con materiales alterados como `verified_private`. Resolver la evidencia del intento aceptado desde el historial independientemente del índice y añadir una regresión.
- `apps/orchestrator/src/acoustic_orchestrator/pipeline/raven_resources.py:115`: un prefijo válido para el validador, como `escena_ñ`, se rechaza durante el render por una restricción ASCII no declarada. Separar la identidad técnica de los nombres públicos y añadir pruebas de render/reanudación con prefijos no ASCII.
