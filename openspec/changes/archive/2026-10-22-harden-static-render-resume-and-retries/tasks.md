# Tasks

## 1. Orchestrator: preflight y contrato del plan

- [x] 1.1 Separar inspección del lote y aplicación de acciones en la preparación de manifiestos, manteniendo la protección de `write_manifest`; verificar con pruebas que un conflicto tardío no modifica ningún artefacto previo.
- [x] 1.2 Implementar validación estricta de manifiestos existentes, identidades, geometría, superficies y referencias; probar JSON corrupto, claves duplicadas, números no finitos y poses inválidas sin invocar MATLAB.
- [x] 1.3 Añadir el registro versionado `static_plan.json`, normalización funcional y hashes de entradas seleccionadas; probar que cambios de workers/reintentos no invalidan el plan y cambios acústicos, catálogo o número de escenas sí se detectan.
- [x] 1.4 Implementar adopción conservadora de planes históricos mediante replay de la realización persistida, incluidas escenas posteriores a T30 y semilla entera grande; verificar coincidencia funcional sin reescribir manifiestos ni afirmar huellas originales desconocidas.
- [x] 1.5 Completar únicamente manifiestos ausentes y conservar el historial de fallos/revisiones de muestreo; probar huecos intermedios, identificadores estables y conflictos de versiones de generación.
- [x] 1.6 Añadir exclusión de escritor por lote y revalidación de precondiciones antes de escrituras; probar dos escritores y bloqueo incierto sin romperlo automáticamente, y documentar la restricción en el README del orquestador.
- [x] 1.7 Documentar en README y `docs/architecture/contracts.md` el plan, adopción legacy y conflictos protegidos; verificar que los ejemplos explican `resume_if_possible=true` junto a `overwrite_existing=false` sin recomendar activar sobrescritura como arreglo.

## 2. Orchestrator: reutilización no destructiva

- [x] 2.1 Resolver/validar audio preparado sin efectos y separar su creación para trabajo nuevo; probar que un archivo preparado válido conserva bytes/mtime y uno alterado produce conflicto sin sustitución.
- [x] 2.2 Construir el runtime esperado en memoria y decidir reutilización antes de preparar tareas; probar cero llamadas al writer y a MATLAB para variantes válidas, incluida una recuperación `post_completion` histórica.
- [x] 2.3 Eliminar escrituras sobre escenas reutilizadas en aceptación canónica y refresco del lote, conservando rechazos T30; verificar snapshots de bytes y `mtime_ns` de escenas, runtime, audio preparado, WAV y sidecars tras dos ejecuciones.
- [x] 2.4 Publicar `static_run_summary.json` separado y diferenciar generación de aceptación acústica/render; probar resumen de lote interrumpido y conservación del journal de muestreo sin reescribir manifiestos históricos.
- [x] 2.5 Parametrizar el helper de configuración de pruebas y cubrir las cuatro combinaciones de reanudación/sobrescritura, con variantes válidas, ausentes, parciales e incompatibles; verificar especialmente que el caso `true/false` reutiliza válidas y ejecuta solo pendientes.
- [x] 2.6 Actualizar documentación de resumen externo y campos embebidos históricos; verificar coherencia de README, ejemplos y contrato con las rutas y estados comprobados en pruebas.

## 3. MATLAB y orchestrator: errores deterministas funcionales

- [x] 3.1 Extender aditivamente el contexto funcional MATLAB con `failure_path` y publicar un informe atómico versionado solo ante fallo de carga/validación; probar identidad, hash, categoría y conservación de la excepción original sin ejecutar RAVEN.
- [x] 3.2 Implementar lectura/verificación estricta del informe en Python y clasificación de errores de entrada/dependencias comprobables en preflight; probar informe válido, ajeno, corrupto, ausente y contradictorio sin interpretar consola.
- [x] 3.3 Probar en MATLAB llamadas sin contexto, contexto antiguo y fallo de escritura del informe, conservando comportamiento y error originales; verificar que no se emite constancia de finalización ficticia ni se altera diagnóstico de mejor esfuerzo.
- [x] 3.4 Documentar el nuevo archivo funcional y su distinción respecto de `completion.json` y eventos en ambos README y contratos; verificar que ningún ejemplo lo presenta como éxito o causa confirmada de un fallo desconocido.

## 4. Orchestrator: cadenas, aislamiento y política de proceso

- [x] 4.1 Añadir `execution.matlab_process_retry` con defaults 3/2/30/5 y tipos/rangos estrictos; probar YAML anterior, `max_attempts=1`, booleanos usados como enteros, NaN/infinito, valores negativos y máximo menor que inicial, y documentar los campos en el ejemplo canónico.
- [x] 4.2 Añadir cadena versionada, ordinal reservado, vínculos a intentos y decisión de programación separada de aceptación; probar unicidad, mismo hash/semilla por cadena y persistencia antes de cada lanzamiento.
- [x] 4.3 Implementar la matriz de clasificación del diseño conservando `evaluate_render` como autoridad funcional; probar fallo no cero con mensajes distintos o sin diagnóstico, lanzamiento fallido, terminación no observada, código cero inválido y contradicción de constancia.
- [x] 4.4 Implementar archivo journalizado de restos propios con inventario, rutas y hashes; probar WAV parcial, sidecar parcial, archivos completos sin constancia y rechazo a mover salidas protegidas anteriores a la cadena.
- [x] 4.5 Implementar reconciliación de archivo interrumpido y fallos de persistencia; inyectar errores entre cada movimiento/commit y verificar que no hay nuevo lanzamiento ni pérdida de evidencia en estados ambiguos.
- [x] 4.6 Implementar backoff con reloj/espera sustituibles en pruebas y continuación de cadenas pendientes; probar esperas 2/4, límite máximo, reinicio entre intentos, presupuesto agotado y bloqueo por intento `running` sin terminación observada.
- [x] 4.7 Preservar lectura de outcomes históricos sin cadena y definir apertura explícita de nueva ejecución mediante reanudación desactivada y sobrescritura activa; probar que un restart normal no renueva presupuestos y documentar intervención/rollback sin borrar evidencia.

## 5. Orchestrator: coordinación, índice y CLI

- [x] 5.1 Integrar la misma política en camino canónico y ejecución no canónica secuencial; simular fallo temprano seguido de éxito y verificar dos UUID, una variante aceptada y primer proceso todavía fallido.
- [x] 5.2 Sustituir el despacho paralelo anticipado por envío acotado a workers y registrar racha/corte desde la coordinación; probar con barreras que no hay nuevos lanzamientos tras el umbral y que workers ya iniciados terminan con evidencia aislada.
- [x] 5.3 Persistir el corte del lote y su orden de resolución, con reinicio de racha por aceptación y corte enclavado; probar fallo persistente general, éxito antes del umbral y éxito tardío que no reabre el lote.
- [x] 5.4 Integrar historial/índice con cadenas sin borrar decisiones anteriores; probar reanudación después de éxito en segundo intento, reinicio sin índice y veto ante evidencia funcional pendiente o rechazada.
- [x] 5.5 Añadir contadores de invocaciones, reintentos y variantes aceptadas tras reintento manteniendo `render_jobs` y contadores de recuperación; probar salida CLI y excepción terminal con vínculos a toda la cadena, y documentar ejemplos de resumen.
- [x] 5.6 Verificar interacción con T30 y Clarity: probar remuestreo acústico con cadena distinta, constancia válida con T30 inválido, recuperación sin repetición y bloqueo de entrega de salidas incompletas de cualquier intento.

## 6. Verificación integrada

- [x] 6.1 Ejecutar la suite Python del orquestador y los chequeos de estilo/tipos configurados; registrar comandos, resultados y cualquier limitación de entorno, sin revertir cambios previos del usuario.
- [x] 6.2 Ejecutar pruebas MATLAB de validación, diagnóstico, constancia e informe de fallo con el entorno disponible; registrar pruebas no ejecutables por dependencias sin presentarlas como aprobadas.
- [x] 6.3 Añadir y ejecutar una regresión end-to-end con subprocess simulado: lote parcialmente completado, fallo temprano genérico, restart protegido y éxito posterior; verificar invocaciones exactas, límites, hashes/mtime de reutilizados y ausencia de aceptación parcial sin depender de los archivos reales de `directivity2_0476`.
- [x] 6.4 Validar coherencia final entre escenarios delta, contratos y pruebas, ejecutar `openspec validate harden-static-render-resume-and-retries --strict` y revisar el diff para confirmar que los outputs reales del experimento no se modificaron.

## 7. Orchestrator: reconciliación manual de interrupciones

Las tareas siguientes registran trabajo implementado después de las 34 tareas originales. Sus marcas se apoyan en la implementación y las pruebas ya ejecutadas, no en haber aplicado la recuperación sobre una ejecución real.

- [x] 7.1 Exponer `reconcile-static <run_root> --chain <UUID>` como inspección sin escrituras y exigir `--apply --attempt <UUID> --confirm-stopped --reason <motivo>` para publicar; verificar con `test_interruption_recovery.py` que la CLI no lanza MATLAB, no crea un lock en inspección y rechaza confirmaciones incompletas o un intento distinto del autorizado.
- [x] 7.2 Aplicar guardas de exclusión de escritor, consulta local conservadora de procesos e integridad de cadena, snapshot, runtime e inventario; verificar en `test_interruption_recovery.py` el bloqueo ante procesos activos, consulta fallida o vacía, lock existente, evidencia alterada, constancia/informe funcional, salidas completas, circuito abierto y archivos preexistentes protegidos.
- [x] 7.3 Conservar preimágenes exactas de cadena/outcome y un registro versionado vinculado por identidad y huellas antes de publicar estados; verificar en `test_interruption_recovery.py` bytes originales con CRLF, idempotencia, publicaciones interrumpidas completables y rechazo de marcadores, preimágenes o registros alterados, manteniendo desconocidos código y finalización originales.
- [x] 7.4 Integrar `confirmed_interruption` en la validación y programación de cadenas sin aceptar el render anterior; verificar en `test_interruption_recovery.py` conservación del ordinal y la política, siguiente UUID con presupuesto restante, agotamiento sin renovación y bloqueo de reutilización hasta una aceptación posterior, sin inventar invocaciones, fallos observados ni recuperación `post_completion`.
- [x] 7.5 Verificar el inventario confirmado antes de archivar restos propios y continuar con el journal existente; probar en `test_interruption_recovery.py` conservación de PCM parcial y bloqueo ante archivos aparecidos o alterados después de confirmar, sin atribuirlos al intento interrumpido ni sobrescribirlos.
- [x] 7.6 Documentar operación, confirmaciones, evidencia, límites e intervención separada de `render-static` en el README del orquestador y `docs/architecture/contracts.md`; verificar los ejemplos con las pruebas CLI y registrar los resultados de regresión, pruebas específicas, Ruff y mypy indicados abajo, sin afirmar reconciliación ni renderización del lote real.

## Evidencia de verificación de la reconciliación

Resultados obtenidos durante la implementación, anteriores a esta actualización de artefactos. Esta revisión de planificación no vuelve a ejecutar código ni modifica el estado de una ejecución.

- Regresión completa: `.\.venv\Scripts\python.exe -m pytest apps/orchestrator/tests -q --disable-warnings --maxfail=2` — **939 pruebas aprobadas**.
- Pruebas específicas finales: `.\.venv\Scripts\python.exe -m pytest apps/orchestrator/tests/test_interruption_recovery.py -q` — **29 pruebas aprobadas**. Es una ejecución distinta, con cobertura superpuesta a la regresión; las cifras no se suman como pruebas únicas.
- Estilo: `.\.venv\Scripts\python.exe -m ruff check apps/orchestrator` — aprobado.
- Tipos: `.\.venv\Scripts\python.exe -m mypy --config-file apps/orchestrator/pyproject.toml apps/orchestrator/src` — aprobado, 33 archivos fuente.
- Comprobación operativa: la inspección de solo lectura de la cadena real interrumpida mostró un intento consumido y dos restantes. No se utilizó `--apply`, no se creó una reconciliación en ese lote y no se lanzó MATLAB para reanudarlo.
- Alcance: estos resultados no acreditan una causa interna del fallo MATLAB, una terminación pasada no observada ni la adopción completa del lote legado. La comprobación previa de reproducción completa de ese plan excedió su límite de tiempo; esa limitación permanece y no se convierte en una prueba aprobada por actualizar estos artefactos.
