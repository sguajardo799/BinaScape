# Tasks

## 1. Orquestador: contrato funcional e identidad del intento

- [x] 1.1 Añadir al layout de artefactos el directorio funcional `metadata/render_attempts/` y su fallback junto al manifiesto para el helper directo; verificar con tests de rutas que nunca depende de `diagnostics_dir` y no cambia rutas públicas de audio/metadatos.
- [x] 1.2 Crear el registro ligero del intento con UUID, snapshot inmutable de bytes, SHA-256 y `outcome.json` inicial antes de lanzar MATLAB; verificar unicidad, lectura del snapshot, fallo seguro de preparación y conservación de intentos previos con tests unitarios.
- [x] 1.3 Definir y verificar el lector de constancia versión 1 con inventario exacto, tipos, identidades, rutas, tamaños y hashes por bloques; cubrir constancia ajena, esquema desconocido, duplicados, extras, archivo alterado, temporal y manifiesto cambiado con tests.
- [x] 1.4 Desacoplar la generación de identidad funcional de `MatlabDiagnostics` permitiendo asociación por UUID sin dependencia de sus archivos; verificar que errores de directorio/eventos/resumen diagnóstico no alteran registros funcionales ni resultados.
- [x] 1.5 Documentar contexto funcional, layout, campos, hash sobre bytes y límites de confianza en `docs/architecture/contracts.md`; verificar correspondencia con fixtures de contrato Python y ausencia de cambios al YAML acústico.

## 2. MATLAB: constancia posterior a escrituras cerradas

- [x] 2.1 Extender `run_raven_static_render` con tercer argumento funcional opcional y conservar firmas anteriores; añadir tests MATLAB que ejerciten solo manifiesto, diagnóstico opcional y contexto funcional con diagnóstico ausente.
- [x] 2.2 Implementar SHA-256 por bloques y publicación de constancia UTF-8 mediante temporal y renombrado del mismo directorio, después de cerrar todas las salidas; verificar con tests MATLAB sin RAVEN huellas conocidas, rutas con espacios/no ASCII e inventarios de una y varias salidas.
- [x] 2.3 Comprobar retornos de escritura/cierre de metadatos y prohibir constancia final si falla WAV, sidecar, hash, escritura temporal o publicación; verificar mediante fallos inyectados que nunca se acepta un temporal ni se reemplaza evidencia de un intento anterior.
- [x] 2.4 Incorporar la publicación como última operación funcional del entrypoint, manteniendo eventos/mensajes como observacionales; verificar con tests que no se publica antes de cerrar el último artefacto y que un fallo de diagnóstico no impide la constancia.
- [x] 2.5 Actualizar `apps/matlab/README.md` con firma compatible, ejemplo funcional, constancia versión 1 y limitaciones de atomicidad/entorno; contrastar el ejemplo con tests de contrato y mantener explícito que el flujo dinámico no está soportado.

## 3. Orquestador: validación integral compartida

- [x] 3.1 Introducir funciones de validación compartida con perfiles normal/recuperación/reutilización, validez detallada y códigos de motivo, separando problemas de artefactos de rechazo acústico; verificar tests de cada categoría y metadatos opcionales frente a obligatorios para recuperación/canónica.
- [x] 3.2 Validar todo el WAV PCM por bloques, tamaño real de carga, frames, formato, canales y 44100 Hz; añadir fixtures válidos HRTF/HARTF/array y tests de texto como WAV, carga truncada con cabecera válida, vacío, formato no soportado y canales/frecuencia incorrectos.
- [x] 3.3 Contrastar frames con dimensiones del sidecar y duración objetivo usando redondeo MATLAB; verificar recorte, relleno, ausencia de objetivo y empates de media muestra sin usar el redondeo bancario de Python.
- [x] 3.4 Validar estructura/tipos/versiones 1.0/2.0/3.0 de sidecars, campos obligatorios, contradicciones y valores JSON no finitos; verificar con fixtures reales del productor y tests de `{}`, arrays en raíz, campos ausentes, booleanos numéricos y campos redundantes incompatibles.
- [x] 3.5 Comparar con runtime fuentes exactas sin duplicados, procedencia, rutas preparadas, ganancias, tiempos, poses y orientación transformadas según schema; verificar fixtures legacy, recortados y schemas 2/3 con reflexión Z y tolerancias definidas.
- [x] 3.6 Contrastar selección HRTF/HARTF o geometría/orden de canales del array, semilla normalizada y tratamiento de ruido; verificar casos incompatibles y omisiones de ruido soportadas sin introducir umbrales nuevos de calidad.
- [x] 3.7 Reutilizar y reforzar el control T30 canónico con diez frecuencias, valores finitos positivos y coherencia de campos derivados; verificar rechazo acústico separado de JSON corrupto, y que no se exige coincidencia nueva con el bin Sabine.
- [x] 3.8 Integrar la validación en `inspect_variant`, exigiendo validez para `completed` y sin tratar el runtime de entrada como salida producida; actualizar `test_output_index.py` y fixtures afectados para comprobar estados, motivos y rechazo de WAV ficticios.
- [x] 3.9 Documentar perfiles, campos obligatorios, formatos PCM y condiciones de rechazo en `docs/architecture/contracts.md`; verificar que cada comprobación documentada dispone de fixture positivo y negativo y distingue validación de calidad científica.

## 4. Orquestador: proceso, política y persistencia

- [x] 4.1 Hacer que el runner entregue evidencia del proceso y excepción original, pase snapshot/contexto funcional a MATLAB y mantenga diagnóstico de mejor esfuerzo; verificar con subprocess simulado códigos cero/no cero, lanzamiento fallido, rutas escapadas y terminación no observada sin inventar éxito.
- [x] 4.2 Implementar la tabla de decisión del diseño fuera del diagnóstico: recuperar únicamente con constancia y validación completas, rechazar contradicciones incluso con código cero y conservar errores originales; cubrir toda la tabla con tests parametrizados.
- [x] 4.3 Aplicar la evaluación a la variante canónica antes de abortar por código no cero, manteniendo puerta T30, descarte y presupuesto de remuestreo; verificar recuperación seguida de T30 válido, rechazo T30 con código cero/no cero y agotamiento sin aceptar escena ni ocultar procesos fallidos.
- [x] 4.4 Aplicar idéntica política a tareas secuenciales y paralelas; verificar con tests de integración workers 1 y 3, variantes mezcladas recuperables/no recuperables y aislamiento de constancias/decisiones.
- [x] 4.5 Persistir `outcome.json` final e índice mediante publicación atómica antes de continuar tras recuperación, añadiendo campos de proceso/validez/decisión sin perder campos existentes; verificar fallos de persistencia y cadena de `RenderStaticRunError` con ambos errores accesibles.
- [x] 4.6 Ampliar resumen y CLI para mostrar fallos del proceso, recuperaciones actuales y reutilizaciones de recuperaciones previas; verificar salida visible, ausencia de éxito limpio ficticio y conservación de exits CLI por fallos y muestreo.
- [x] 4.7 Actualizar tests diagnósticos y `test_render_diagnostics_integration.py` para distinguir propagación conservadora y recuperación funcional; verificar invariancia al perder logs/eventos/resumen sin fabricar `entrypoint_completed`.
- [x] 4.8 Documentar política de continuación, advertencias persistentes, separación `post_completion`/`post_entrypoint` y ausencia de diagnóstico causal en `apps/orchestrator/README.md`; verificar que ejemplos y contadores coinciden con tests CLI.

## 5. Orquestador: reanudación y entrega a Clarity

- [x] 5.1 Preservar decisiones anteriores antes de publicar el plan o sobrescribir runtimes y consultar historial funcional si falta el índice; verificar que intentos `running`, abortados o rechazados no se vuelven completados por reinicio, incluso tras fallo al guardar el índice.
- [x] 5.2 Revalidar salidas recuperadas contra constancia original y manifiesto vigente normalizado, preservando advertencia y UUID sin crear invocación; verificar cambios acústicos rechazados y cambios solo de metadatos agregados del lote aceptados.
- [x] 5.3 Mantener reanudación legacy sin constancia solo si supera validación estricta y no tiene veto funcional conocido; verificar que no se inventa proceso/constancia, que archivos débiles requieren rerender y que se respeta `resume_if_possible`.
- [x] 5.4 Revalidar archivos y procedencia antes de crear/enviar jobs Clarity, manteniendo elegibilidad HRTF y rutas degradadas; añadir tests de modificación posterior al índice, HRTF recuperada válida e inelegibilidad HARTF/array incluso con dos canales.
- [x] 5.5 Documentar reanudación, migración de índices aditiva, archivos históricos rechazados y recuperación preservada en `apps/orchestrator/README.md`; verificar ejemplos frente a tests de reinicio y handoff.

## 6. Verificación integrada entre componentes

- [x] 6.1 Ejecutar la suite completa del orquestador con `uv run --project apps/orchestrator pytest apps/orchestrator/tests`; registrar resultado y resolver regresiones, manteniendo fixtures WAV reales sin relajar validadores para hacer pasar tests.
- [x] 6.2 Ejecutar las suites MATLAB de constancia, exportación y diagnóstico sin RAVEN, y comprobar que Python valida una constancia realmente emitida por MATLAB con los mismos bytes/hashes; registrar versión y resultados del entorno utilizado.
- [x] 6.3 Realizar smoke test Windows/MATLAB/RAVEN en directorio nuevo con render normal y fallo posterior a publicación inducido de manera controlada solo en el harness de prueba; verificar recuperación con `process_status=failed`, artefactos íntegros, T30 válido y advertencia durable sin modificar audio de producción.
- [x] 6.4 Verificar de extremo a extremo render, reinicio sin nueva invocación y handoff HRTF tras recuperación, así como rechazo de artefacto alterado; registrar evidencia de ausencia de jobs para salidas inválidas y de conservación del historial T30.
- [x] 6.5 Revisar trazabilidad entre escenarios de ambos deltas y tests/evidencias, y validar el cambio con `openspec validate separate-matlab-process-render-outcome --strict`; no declarar completas verificaciones MATLAB/RAVEN que no se hayan ejecutado.
