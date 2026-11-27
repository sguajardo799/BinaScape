# Design

## Context

Ver proposal.md (Why). Estado actual relevante:

- **Concurrencia.** `render_pipeline.py` usa un `ThreadPoolExecutor` de `num_workers` hilos (etapa canónica en `_render_canonical_in_parallel`, resto en `_run_render_tasks_in_parallel`). Cada hilo se bloquea en `subprocess.run` dentro de `_run_render_process` (`matlab_runner.py:149`), que es el único punto donde se lanza el proceso de render y el que llena `exit_code`, `termination_observed` y `process_status`.
- **Coordinación.** `_RenderCoordinator.prepare` (que reserva y espera `not_before` en el hilo despachador) y `resolve` (aceptación → `classify_attempt` → `BatchCircuit.resolve` → `RetryChain.resolve_attempt`) solo consumen `MatlabProcessResult`. No saben cómo se lanzó el proceso.
- **Contratos.** La aceptación (`render_acceptance.evaluate_render`) y la reanudación (`render_history.resume_evidence`) dependen del código de salida, de la terminación observada, de `completion.json`/`failure.json` y, para `post_completion`, de `error.type == "CalledProcessError"`.
- **MATLAB.** `run_raven_static_render(snapshot, diagnostics, completion_context)` es reentrante por variante. El espacio de trabajo de RAVEN se identifica por el nombre de proyecto de la variante, bajo `C:\ITASoftware\Raven\RavenInput`. Hay estado de sesión: `persistent` en `emit_render_event.m` (indexado por `attempt_id`), `rng` global y `addpath`.
- **Preferencias.** `isolate-matlab-prefdir` crea un `MATLAB_PREFDIR` privado por proceso. Los workers de un pool `Processes` comparten la carpeta de preferencias de la sesión que los abre.

## Goals / Non-Goals

**Goals:**
- Una sola petición de licencia por sesión, con hasta `num_workers` renders RAVEN en paralelo.
- No tocar la aceptación, los reintentos, el corte del lote, la reanudación, `reconcile-static` ni `reset-circuit`: el modo `pool` produce el mismo `MatlabProcessResult` y los mismos archivos por intento.
- Que el modo `process` siga siendo el valor por defecto y no cambie su comportamiento.

**Non-Goals:**
- Clasificar fallos de licencia ni ajustar la política de espera.
- Usar `pool` con Pyroomacoustics.
- Pools distribuidos (MATLAB Parallel Server) o pools de hilos (`Threads`).
- Reemplazar el `ThreadPoolExecutor` de Python o el modelo de despacho.

## Decisions

### 1. Servidor de trabajos con cola en archivos, no un `parfor` por lote

La sesión ejecuta un entrypoint nuevo, `run_raven_pool_server(session_dir, pool_size)`. Abre `parpool('Processes', pool_size)` y atiende una cola en disco:

- Python publica `session_dir/requests/<attempt_id>.json` de forma atómica (temporal + rename), con los mismos tres argumentos que hoy recibe `run_raven_static_render`.
- El servidor toma cada solicitud nueva y la lanza con `parfeval`. Cuando el future termina, publica `session_dir/results/<attempt_id>.json` con `exit_code` (0 o 1), mensaje de error, worker, inicio y fin, y escribe el `Diary` del future en la consola del intento.
- Señales: `ready.json` (pool abierto y preparado), `stop` (orden de cierre) y `heartbeat.json`, que se reescribe en cada vuelta del bucle.

En Python, cada hilo del `ThreadPoolExecutor` hace lo mismo que hoy, pero en lugar de `subprocess.run` publica la solicitud y espera su resultado o la caída de la sesión. Así siguen intactos el despacho incremental, `prepare`/`resolve` por intento, la espera de reintento en el hilo despachador y el corte a mitad de lote.

Alternativa descartada: un `parfor` sobre todas las variantes del lote. Rompe los reintentos por cadena, el corte del lote y la reanudación incremental, porque el resultado solo llega al final y un fallo deja todo el lote sin resolver.

Alternativa descartada: MATLAB Engine para Python. `apps/orchestrator/AGENTS.md` pide `subprocess` y no Engine sin una necesidad concreta. Además, ata la versión de Python a la de MATLAB.

### 2. El resultado del trabajo se traduce al `MatlabProcessResult` de siempre

Una función nueva, `render_manifest_with_matlab_pool(manifest_path, session, ...)`, reutiliza `_prepare_process` y `MatlabDiagnostics` y devuelve `MatlabProcessResult`:

- Resultado con `exit_code` 0: `succeeded`, terminación observada.
- Resultado con `exit_code` distinto de 0: `failed`, terminación observada, y `error = subprocess.CalledProcessError(exit_code, <descripción del trabajo>)`. Se mantiene ese tipo de error para que `resume_evidence` y la recuperación `post_completion` no cambien.
- Sesión caída antes del resultado: `failed`, terminación observada (el proceso de la sesión terminó y con él todos sus workers), con el código de salida de la sesión, o `1` si no hay uno.
- Fallo al publicar la solicitud (`OSError`): `launch_failed`, igual que un fallo de lanzamiento hoy.

Para repartir el trabajo común sin duplicarlo, `_run_render_process` se divide en dos partes: el armazón (registro, diagnóstico, progreso, estado) y una función de ejecución. En modo `process`, esa función llama a `subprocess.run`. En modo `pool`, publica la solicitud y espera el resultado.

### 3. Ciclo de vida de la sesión en `render_pipeline`

Un objeto `MatlabPoolSession` (en `pipeline/matlab_pool.py`) se crea en `_render_static_scenes_locked`, dentro del `static_writer_lock`, solo si `matlab_runner == "pool"` y hay variantes RAVEN pendientes. Se cierra en un `finally`: escribe `stop`, espera la salida del proceso con un plazo de gracia y, si no sale, termina el proceso.

- **Arranque perezoso y relanzamiento.** La sesión arranca en el primer despacho. Si el proceso ya no está vivo, el siguiente despacho arranca una sesión nueva con otro `session_id`, protegido por un lock para que dos hilos no la arranquen a la vez.
- **Arranque fallido.** Si el proceso termina antes de `ready.json`, o no está listo dentro de `execution.matlab_pool.startup_timeout_s` (por defecto 600 s), el intento que la esperaba se resuelve como fallo con el código de salida de la sesión, y la consola de la sesión se copia en su `console.log`. Así el error 5001 pasa por los reintentos y la espera de siempre.
- **Detección de caída.** El hilo que espera el resultado comprueba el resultado, `proc.poll()` y la antigüedad del heartbeat. Si el heartbeat supera `heartbeat_timeout_s` (por defecto 300 s) sin que el proceso termine, la sesión se mata y los intentos en curso se resuelven como sesión caída.
- **Ubicación.** La sesión vive en `metadata/matlab_pool/<session_id>/`, con `invocation.json`, `console.log`, `requests/`, `results/` y `ready.json`.

Las nuevas opciones van en un bloque opcional `execution.matlab_pool` con `startup_timeout_s` y `heartbeat_timeout_s`, ambos positivos y con valor por defecto. Ninguna entra en la identidad del plan (`static_plan.py:144` copia solo campos elegidos de `execution`).

### 4. Preferencias: carpeta privada por sesión y primer acceso serializado

La sesión se lanza con `isolated_matlab_env()` (de `isolate-matlab-prefdir`) y elimina la carpeta al cerrarse. Los workers de un pool `Processes` local heredan esa carpeta. Antes de escribir `ready.json`, el servidor ejecuta en un bloque `spmd` el primer acceso a las preferencias de ITA (`ita_preferences`), de a un worker por vez con `spmdBarrier` entre turnos. Después de eso, las lecturas posteriores ya no reescriben el archivo a la vez en varios workers.

Alternativa descartada: una carpeta de preferencias por worker. MATLAB no deja fijar `MATLAB_PREFDIR` por worker en un pool local.

### 5. Estado de sesión en los workers

- `addpath(genpath(apps/matlab))` se hace una vez en el cliente y en todos los workers (`parfevalOnAll`) antes de `ready.json`.
- `rng(seed)` ya se fija por variante en `prepare_raven_project.m`, así que la reproducibilidad no depende del worker.
- El identificador del worker lo informa el propio trabajo (`getCurrentWorker` dentro del envoltorio que ejecuta `run_raven_static_render`), porque `Future.Worker` no está disponible después de terminar.
- `emit_render_event` usa un `persistent` indexado por `attempt_id`: sirve sin cambios dentro de un worker.
- El espacio de trabajo de RAVEN ya es privado por variante. Dentro de una ejecución, `_RenderCoordinator` nunca tiene en curso dos intentos de la misma variante, así que no hay colisión.

### 6. Validación

`ExecutionConfig.matlab_runner: Literal["process", "pool"] = "process"`. La validación semántica rechaza `pool` cuando el backend efectivo no es `raven`, con el mismo estilo que las reglas de `render-backend-selection`.

## Risks / Trade-offs

- **[Riesgo] Que en esta licencia los workers del pool sí pidan licencia propia.** → Se verifica en la tarea 1.1 antes de implementar: se abre `parpool('Processes', 4)` y se observa que no hay error de licencia. Si falla, el cambio se detiene y se replantea.
- **[Riesgo] Que RAVEN o ITA-Toolbox no funcionen dentro de un worker** (rutas, `system()`, GUI). → La tarea 1.1 renderiza una variante real dentro de un worker y la compara con la salida del modo `process`.
- **[Riesgo] Un solo proceso concentra los fallos: si la sesión cae, fallan todos los intentos en curso y la racha del corte puede subir `num_workers` de golpe.** → Es el mismo comportamiento que tuvo `directivity_experiment104` con procesos sueltos. El corte existe justamente para eso, y `reset-circuit` lo reabre. Queda documentado.
- **[Riesgo] Fuga de memoria o estado entre renders en workers de larga vida.** → Fuera de alcance por ahora. Si aparece, se puede reiniciar el pool cada N trabajos sin cambiar los specs.
- **[Riesgo] Intentos colgados si el heartbeat no avanza porque un render largo bloquea el bucle del servidor.** → El bucle del cliente no renderiza: solo atiende la cola y los futures, así que el heartbeat avanza aunque los workers estén ocupados.
- **[Trade-off] Más código en MATLAB y un protocolo de archivos nuevo.** → Se acepta a cambio de no tocar los contratos de reintentos y reanudación. El protocolo es local a `metadata/matlab_pool/` y no es un contrato público.
- **[Riesgo] Interacción con la recuperación de interrupciones.** Si el orquestador muere, la sesión de pool puede quedar viva escribiendo salidas. → `interruption_recovery` ya bloquea mientras haya procesos `matlab` vivos. Además, el servidor se cierra solo si su proceso padre desaparece (comprobación del PID padre en el heartbeat).

## Migration Plan

- Opcional y desactivado por defecto: los lotes existentes no cambian.
- Para usarlo: `execution.matlab_runner: pool` con un `num_workers` que la máquina pueda sostener. En la máquina actual el pool abre con 3 workers y falla con 4 (el cuarto worker termina con estado 1 al arrancar, probablemente por memoria): usar `num_workers: 3`. Un pool que no arranca se trata como sesión caída (decisión 3).
- Vuelta atrás: quitar el campo o poner `process`. La reanudación no depende del modo.
- Debe aplicarse después de archivar `isolate-matlab-prefdir`, porque reutiliza `isolated_matlab_env`.
