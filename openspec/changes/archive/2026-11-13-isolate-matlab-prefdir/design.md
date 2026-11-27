# Design

## Context

Ver `proposal.md` (Why). Estado actual:

- `render_manifest_with_matlab` (`pipeline/matlab_runner.py`) arma `matlab -batch ...` y llama a `_run_render_process` sin `env`, así que MATLAB hereda el entorno y usa la carpeta de preferencias del usuario (`%APPDATA%\MathWorks\MATLAB\R2025b`).
- `_run_render_process` ya acepta `env` (lo usa el worker de Pyroomacoustics), registra el comando en el intento antes de lanzar y registra estado, código y error al terminar.
- `ita_preferences` mantiene un caché `persistent` por proceso. En el primer acceso a `AuthorStr`, llama a `setpref`, que reescribe `matlabprefs.mat`. Las propiedades por defecto de `itaMeta` (`mUserName`) e `itaAudio` (`mSamplingRate`) leen preferencias al construirse, lo que puede ocurrir tarde (en `getBinauralImpulseResponseItaAudio`). Por eso la carrera aparece después de que RAVEN ya terminó.
- La carpeta de preferencias no guarda la licencia: la prueba con una carpeta vacía arrancó y obtuvo licencia.
- `scripts/diagnostics/rt30_reproducibility.py` lanza MATLAB con su propio `subprocess.run`.

## Goals / Non-Goals

**Goals:**
- Eliminar la carrera sobre `matlabprefs.mat` sin tocar el código MATLAB ni el ITA-Toolbox.
- Mantener resultados idénticos y la clasificación actual de los intentos.

**Non-Goals:**
- Resolver la caída del servicio de licencias (`error 5001`). Es un problema del entorno y no lo cubre este cambio.
- Conservar preferencias del usuario dentro de los renders (no se copian).
- Aislar el `userpath`: el `startup` de ITA sigue cargándose desde el `userpath` del usuario, que es de solo lectura para los renders.

## Decisions

1. **Carpeta vacía por invocación, no una copia de la del usuario.**
   - Una copia trasladaría al lote archivos de sesión (`matlabPkey.p12`, `thisMatlab.pem`) y preferencias ajenas.
   - Una carpeta vacía es determinista, y el comportamiento de ITA no cambia porque hoy ya usa los valores por defecto salvo `AuthorStr`, que solo es texto de metadatos.
   - *Alternativa descartada:* precargar las preferencias de ITA desde MATLAB. Su caché es `persistent` interno, así que precargarlo también escribe con `setpref`.
   - *Alternativa descartada:* serializar el acceso con un lock entre procesos. El acceso ocurre dentro de ITA, en momentos que no controlamos.

2. **Una carpeta por invocación, no una por worker.** Los workers son hilos del coordinador sin una identidad de slot estable. Crear la carpeta por intento garantiza unicidad incluso con reintentos y reanudaciones. El costo es un arranque de MATLAB con caché de ajustes vacío, que se mide en la verificación operativa.

3. **Ubicación en el directorio temporal del sistema** (`tempfile.mkdtemp(prefix="binascape-matlab-prefs-")`), fuera de `outputs/`. MATLAB escribe ahí cachés (por ejemplo `epfwk_cache-*.json`, de alrededor de 1 MB) que no son evidencia del lote. La ruta y el resultado de la limpieza se anotan en el intento como `matlab_prefdir: {"path": ..., "cleanup": "removed" | "failed"}`.

4. **Ciclo de vida dentro de `render_manifest_with_matlab`:** se crea la carpeta antes de `_run_render_process`, se pasa `env={**os.environ, "MATLAB_PREFDIR": <dir>}`, y en un `finally` se ejecuta `shutil.rmtree`. Si falla, se emite `diagnostic_warning` y se marca `cleanup="failed"` sin propagar la excepción. Como el registro del intento ya se guardó dentro de `_run_render_process`, el campo de limpieza se añade y se guarda después. Si ese guardado falla, se trata igual que hoy cualquier fallo de persistencia posterior al proceso: se preserva el error original.
   - *Alternativa:* hacerlo dentro de `_run_render_process`. Se descarta porque ese núcleo es compartido con Pyroomacoustics, que no lo necesita.

5. **Compatibilidad de lectores.** El campo nuevo es opcional. Hay que confirmar que `load_render_history`, `_resolved_outcome`, `_validate_original` (reconciliación) y `reset-circuit` no rechazan claves desconocidas; los tests de esas rutas lo cubren.

6. **El script de diagnóstico** reutiliza un helper `isolated_matlab_env()` exportado por `matlab_runner`, que devuelve el entorno y la ruta, en vez de duplicar la lógica.

## Risks / Trade-offs

- [El arranque con carpeta vacía es más lento] → Medir la duración de un intento antes y después en la verificación operativa. Si el costo es relevante, reconsiderar una carpeta por slot.
- [Quedan carpetas huérfanas si el orquestador muere antes del `finally`] → Están en el temporal del sistema con un prefijo identificable y no afectan la reanudación. Se documenta cómo borrarlas.
- [Otra herramienta MATLAB del lote (fuera del runner) sigue usando la carpeta del usuario] → Solo `render_manifest_with_matlab` y el script de diagnóstico lanzan MATLAB. Una búsqueda de `"-batch"` en el repo lo confirma en las tareas.
- [Una versión futura de MATLAB guarda la licencia en la carpeta de preferencias] → La verificación operativa incluye un render real. Si falla la licencia, el intento falla y queda registrado, sin corromper nada.

## Migration Plan

Sin migración: aplica a las nuevas invocaciones. Los lotes en curso pueden reanudarse con normalidad. Para revertir basta con revertir el commit.
