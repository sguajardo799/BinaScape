# Design

## Context

Ver `proposal.md` para motivación y evidencia. La investigación y el código actual establecen estos límites:

| Punto actual | Consecuencia para el diseño |
|---|---|
| `sampler.py` asigna bins por `scene_index`; `_refresh_reverberation_batch` cuenta escenas | No modificar el plan RT para resolver una colisión de archivos |
| `build_single_receiver_render_manifest` copia la escena sin variar la sala | Conservar los valores físicos; intervenir solo en entradas técnicas |
| `prepare_raven_project.m` copia el RPF a `C:/ITASoftware/Raven/RavenInput/<project_name>.rpf` | Un RPF base privado no basta: su identidad de proyecto también debe distinguir runs y realizaciones |
| `itaRavenProject.setModelToFaces` escribe `room_<caras>_faces` bajo el prefijo hasta `RavenModels`; sin ese texto usa el directorio del RPF copiado | Incluir un componente privado `RavenModels` y comprobar que el toolbox resolverá exactamente ese directorio |
| `setModelToShoebox` usa el mismo criterio de directorio; `setMaterial` escribe por nombre de slot en `ProjectPath_MaterialDB` | Aislar modelos y materiales para esquemas legacy y poligonales |
| `RenderAttempt` captura bytes antes del proceso y las cadenas se vinculan a su hash | Preparar el runtime aislado antes de reservar el intento; no reescribir snapshots |
| `equivalent_render_inputs` compara `base_rpf_file` como ruta funcional | Resolver explícitamente procedencia; no excluir esa clave del comparador general |
| La reanudación verifica todas las variantes antes de materializar entradas | Separar descripción pura, validación y materialización |

El comportamiento futuro de aislamiento está definido en `specs/raven-render-isolation/spec.md`. El delta de reanudación añade garantías sin sustituir las ya existentes.

## Goals / Non-Goals

**Goals:**
- Aislamiento por variante y realización, manteniendo la concurrencia entre escenas y salidas.
- Identidades estables durante reintentos de proceso y nuevas durante remuestreo acústico.
- Preflight sin escrituras y procedencia comprobable para las entradas derivadas.
- Integración directa con el flujo existente, mediante funciones y un helper localizado, sin un framework de workspaces.

**Non-Goals:**
- Editar archivos fuente del toolbox, de `apps/matlab` o de los otros backends.
- Garantizar igualdad bit a bit de audio entre IR distintas ni determinismo absoluto del motor.
- Añadir un gate productivo de igualdad T30, forzar que RAVEN coincida con Sabine o alterar fallback/cuotas.
- Auditar o regenerar automáticamente los datasets históricos; ampliar el soporte de RPF a configuraciones que el adaptador no puede resolver con seguridad.

## Decisions

### 1. Workspace estable por variante y realización

Añadir al layout interno un árbol bajo `metadata/raven_resources/<variant_id>/<realization_key>/` con:

```text
base.rpf
provenance.json
RavenModels/seed.ac
RavenModels/<modelo generado por el toolbox>
materials/<slots generados por el toolbox>.mat
```

`realization_key` deriva de las entradas funcionales previas al aislamiento, las huellas del RPF original y modelo base, y la versión de la transformación. No depende del UUID de intento, worker, hora ni orden de terminación. Excluir contabilidad de lote y rutas derivadas de esta clave para evitar ciclos de hash. Las rutas absolutas bajo cada run separan sus recursos.

El `project_name` técnico incluye un digest corto de la raíz del run, variante y realización, para evitar que dos runs con el mismo nombre local colisionen en el `RavenInput` global impuesto por el adaptador MATLAB. El nombre debe ser Windows/RAVEN-safe y acotado. No cambiar `scene_id`, `variant_id`, nombres WAV/sidecar ni el `project_name` del manifiesto de escena.

Los reintentos de proceso pueden usar el mismo workspace porque la cadena y el lock existentes impiden dos escritores simultáneos de la misma variante. Cada intento conserva su evidencia; otro intento de muestreo crea otro workspace y no borra el anterior.

La propiedad del workspace se concreta con `writer.lock` adquirido atómicamente también por el runner directo, para impedir solapamientos entre llamadas directas y coordinadas. En un intento reservado, el propietario es su UUID. Una terminación no observada conserva el veto; el preflight lo bloquea antes de publicar cambios, incluso con sobrescritura. Solo después de verificar y archivar una reconciliación de la cadena se archiva y retira el lock de ese UUID. Un propietario diferente nunca se retira. El runner standalone sin cadena requiere un workspace nuevo si no puede acreditar terminación.

**Alternativas descartadas:** solo nombres de RPF únicos no aíslan geometrías/materiales; un workspace por escena permite interferencias entre sus variantes; uno por UUID cambia innecesariamente la entrada y el hash en cada reintento.

### 2. Derivación mínima del RPF desde Python

Crear un helper en `apps/orchestrator/src/acoustic_orchestrator/pipeline/` con operaciones separadas para describir, verificar y materializar los recursos. Copiar el modelo original a `RavenModels/seed.ac` y cambiar únicamente `[Rooms].Model` y `[Global].ProjectPath_MaterialDB` en el RPF privado. El nombre técnico se aporta en el runtime existente; no añadir campos obligatorios al contrato MATLAB.

Preservar bytes ajenos a esas claves, codificación y finales de línea del RPF; no usar una serialización INI que normalice silenciosamente valores o comentarios. Resolver rutas originales con la semántica RAVEN existente, no respecto a la nueva ubicación del archivo: los caminos relativos `..` usados por el toolbox se resuelven contra la instalación de RAVEN, no arbitrariamente contra el RPF. No copiar toda la base global de materiales: el adaptador escribe los slots requeridos para la sala concreta. No cambiar rutas de recursos de lectura que ya sean válidas.

Validar secciones/claves únicas, modelo base legible, ubicación privada y directorios permitidos. La ruta que recibe el toolbox debe contener una sola ocurrencia relevante de `RavenModels`; si un ancestro la introduce o el RPF es ambiguo/no resoluble, emitir error explícito antes del dispatch, no usar el fallback global `RavenInput`. Probar paths Windows con espacios y separadores admitidos.

`provenance.json` versionado registra original, huellas, semántica de resolución, versión de derivación, variante, realización, rutas privadas y huellas del RPF/seed inmutables. Publicar archivos preparados sin reemplazar evidencia existente; verificar antes de reutilizar. Inventariar los modelos/materiales generados al resolver el intento y asociar sus huellas a esa evidencia, separando estos archivos mutables de los inputs inmutables del workspace.

**Alternativas descartadas:** modificar `setMaterial`/`setModelToFaces` incumple el alcance; generar la acústica en Python duplica al renderer; serialización global elimina el paralelismo y no evita interferencias con otras ejecuciones externas.

### 3. Integración antes del snapshot y del fan-out

En `matlab_runner.py`, la construcción del runtime RAVEN debe poder describir de forma determinista la entrada aislada sin escribir. En `render_pipeline.py`, el preflight valida esa descripción, los archivos existentes y las políticas de reanudación antes de materializar recursos de variantes realmente pendientes. Mantener el hilo coordinador como dueño de preparación y persistencia; los workers siguen ejecutando procesos.

La materialización termina antes de `preflight_chain`, reserva y `RenderAttempt`. El snapshot, el comando, `manifest_sha256` y la constancia siguen refiriéndose a los mismos bytes. No transformar después `input_manifest.json` ni confundir el hash funcional con un hash del RPF.

Extender la misma preparación a las entradas RAVEN del runner directo del orquestador, o rechazar explícitamente una entrada que evite el aislamiento; no dejar un camino de producción del orquestador que pueda ejecutar recursos compartidos silenciosamente. La llamada manual directa al entrypoint MATLAB queda fuera de esta garantía y conserva su comportamiento.

Antes del dispatch, comparar las condiciones comunes de las variantes con su escena usando los valores existentes, excluyendo solo selección/descriptor de receptor y datos técnicos autorizados. No agregar sorteos ni cambiar el estado RNG. Mantener el tratamiento canónico y las listas de variantes existentes.

**Alternativa descartada:** transformar solo la copia que recibe MATLAB rompe la correspondencia con el manifiesto hasheado y deja la reanudación sin entrada verificable.

### 4. Reanudación y procedencia histórica

Para runtimes aislados, reconstruir la descripción esperada desde el RPF original vigente y verificar la procedencia, las huellas protegidas y la correspondencia exacta del runtime. No cambiar la regla general de comparación de `base_rpf_file`; la traducción autorizada ocurre en la construcción de la entrada esperada, con evidencia validada.

Una reanudación válida no crea otro workspace ni modifica RPF, seed, registro, modelos, materiales o snapshots. Recursos protegidos ausentes o alterados causan conflicto antes de cualquier escritura; no se reparan automáticamente bajo `overwrite_existing=false`. Para una variante pendiente, la creación o archivo de restos se subordina a su cadena y a las reglas existentes de protección, presupuesto y reconciliación.

Los runtimes históricos conservan su comparación y validación estrictas actuales, sin sustituir sus rutas por nuevas rutas privadas para hacerlos pasar como compatibles. Añadir al índice/resumen una procedencia por variante: `verified_private` para ejecución con evidencia comprobada y `legacy_unverified` para la histórica. Este estado no reemplaza la validez del render ni certifica retrospectivamente materiales efectivos. Un lote mixto se identifica como tal y no se presenta como íntegramente aislado.

Una nueva ejecución pendiente siempre usa aislamiento; si existe una cadena histórica activa, no se cambia su entrada ni se renueva su presupuesto silenciosamente. Informar un conflicto de migración y solicitar un nuevo run o el reinicio/sobrescritura explícito ya soportado. La reutilización válida no equivale a abrir otra cadena.

**Alternativas descartadas:** ignorar todas las diferencias de RPF admitiría cambios acústicos reales; migrar runtimes históricos in situ viola la protección y el hash de cadena; invalidar todos los resultados históricos por defecto cambia innecesariamente su contrato de compatibilidad.

### 5. Verificación automatizada y harness real

Pruebas Python sin MATLAB cubrirán exclusividad, derivación, procedencia, preflight, identidad de snapshots/cadenas, reanudación y conservación de valores físicos/cuotas. Incluir fixtures con varias geometrías, esquemas legacy/poligonal, múltiples sujetos, array y backend alternativo. No depender de archivos de investigación locales ni de tiempos de ejecución para demostrar propiedad exclusiva de rutas.

Añadir un harness opt-in dentro de `apps/orchestrator` que use el flujo de producción del orquestador y compare dos escenas fijas con tripletas bajo uno y cuatro workers, en runs nuevos. Comparar las diez bandas por frecuencia y salida contra la referencia secuencial y dentro de cada tripleta con `abs(a-b) <= 1e-5 + 1e-5 * abs(b)` segundos. Registrar entradas, RPF efectivos, rutas, T30, códigos de salida y resultados de las comparaciones. No comparar formas de onda entre IR diferentes ni promedios de distintas bandas.

La prueba compartida que reproduce la carrera puede quedar como control diagnóstico aislado y opt-in; no exigir que una carrera no determinista se manifieste en cada ejecución. El criterio de éxito es la exclusividad de rutas y la concordancia del camino aislado. Verificar asimismo que RPF original y modelos/materiales globales conservan huellas antes/después; los RPF técnicos nuevos en `RavenInput` se inventarían como efectos conocidos del adaptador, no como contaminación de la base global.

## Risks / Trade-offs

- [Dependencia de la semántica literal `RavenModels` del toolbox instalado] → Encapsularla, validarla antes del proceso y cubrirla con el harness; fallar sin fallback compartido ante una versión incompatible.
- [RPF relativos o no estándar] → Transformación mínima, resolución contrastada con la instalación y rechazo explícito de ambigüedades; no prometer portabilidad nueva.
- [Espacio en disco por realizaciones conservadas] → Copiar solo seed/RPF y conservar evidencia; no añadir limpieza automática destructiva en este cambio.
- [Un resultado histórico coherente en metadatos puede haberse renderizado con recursos ajenos] → Marcar `legacy_unverified` y recomendar regenerar el run completo para obtener garantías, incluso si algunas tripletas coinciden numéricamente.
- [Cambios en hashes/identidades pueden reiniciar cadenas accidentalmente] → Preparación estable antes de reserva, pruebas de ordinal/presupuesto y conflicto explícito para cadenas históricas pendientes.
- [La tolerancia numérica del harness no demuestra igualdad de BRIR] → Limitar la conclusión a exclusividad de recursos y T30 coherente; conservar los contratos acústicos y de evidencia existentes.

## Migration Plan

1. Implementar preparación/procedencia y su integración RAVEN con pruebas Python, sin modificar los otros componentes.
2. Ejecutar el harness opt-in y registrar evidencia de dos escenas, seis salidas por run y comparación de uno/cuatro workers.
3. Activar el aislamiento automáticamente para nuevos renders del orquestador; documentar ubicación, fallos de preflight y estados históricos.
4. Para datasets anteriores, conservar archivos y recomendar un run nuevo completo. No borrar ni reemplazar automáticamente las 13 tripletas discrepantes detectadas ni dar por correctas las restantes.
5. Rollback: retirar la integración/helper y los cambios de índice/documentación en una reversión acotada; conservar runs y evidencia creados. No borrar recursos privados ni reescribir runtimes para hacerlos compatibles con una versión antigua. Cualquier versión sin aislamiento se ejecutaría con un solo worker como mitigación temporal, sin prometer protección frente a escritores externos.
