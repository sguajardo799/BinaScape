# Proposal

## Why

En `directivity_experiment103` tres renders fallaron con `Invalid default value for property 'mUserName' in class 'itaMeta': Unable to read file '...\MATLAB\R2025b\matlabprefs.mat'` (y el equivalente en `itaAudio.mSamplingRate`). Todos los procesos MATLAB del lote comparten la carpeta de preferencias del usuario, y el ITA-Toolbox reescribe `matlabprefs.mat` en cada proceso (`ita_preferences` llama a `setpref('RWTH_ITA_ToolboxPrefs', 'AuthorStr', ...)` al primer acceso). Con `num_workers > 1`, un proceso puede leer el archivo mientras otro lo reemplaza. Los reintentos ocultan el síntoma, pero consumen presupuesto y suman fallos a la racha del corte del lote.

## What Changes

- Cada invocación de MATLAB lanzada por el orquestador recibe una carpeta de preferencias privada y vacía mediante la variable de entorno `MATLAB_PREFDIR`. Esa carpeta no la comparte ningún otro proceso y se crea fuera de las salidas del lote.
- La carpeta se elimina al terminar el proceso, con éxito o con error. Si no se puede eliminar, se emite una advertencia y el resultado del render no cambia.
- El registro del intento anota la ruta de la carpeta usada y el resultado de la limpieza.
- La carpeta de preferencias del usuario (`matlabprefs.mat` y el resto de su contenido, incluida la sesión de licencia) deja de ser leída o escrita por los renders.
- El script de diagnóstico `scripts/diagnostics/rt30_reproducibility.py`, que lanza MATLAB por su cuenta, aplica el mismo aislamiento.
- Sin cambios en el backend Pyroomacoustics, la configuración YAML, los manifiestos ni el código MATLAB.

Comprobado antes de proponer:
- MATLAB R2025b arranca y obtiene licencia con `MATLAB_PREFDIR` apuntando a una carpeta vacía.
- El ITA-Toolbox funciona sin preferencias guardadas: usa sus valores por defecto y solo imprime la advertencia de `ita_check4toolboxsetup`.
- Las preferencias guardadas hoy son solo `AuthorStr` y `lastToolboxSetupVerNum`, así que los renders actuales ya usan los valores por defecto del resto.

## Capabilities

### New Capabilities

_(ninguna)_

### Modified Capabilities

- `raven-render-isolation`: se añade el aislamiento de las preferencias de MATLAB por proceso de render, su limpieza y su trazabilidad. Los requisitos existentes de materiales y modelo privados no cambian.

## Impact

- `apps/orchestrator/src/acoustic_orchestrator/pipeline/matlab_runner.py` (`render_manifest_with_matlab` / `_run_render_process`): crear la carpeta, pasar el entorno, limpiar y registrar.
- `scripts/diagnostics/rt30_reproducibility.py`: mismo aislamiento al lanzar MATLAB.
- Registro del intento (`render_attempts/<id>/outcome.json`): nuevo campo opcional. Hay que confirmar que los lectores existentes (historial, reconciliación, reinicio del corte) lo toleran.
- Docs: `docs/architecture/contracts.md` y `apps/orchestrator/README.md` (aislamiento de procesos MATLAB).
- Tests en `apps/orchestrator/tests/`.
