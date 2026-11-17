# Spec Delta

## Purpose

Permitir renders RAVEN concurrentes con una sola licencia de MATLAB, ejecutando las variantes de un lote en un pool local de workers dentro de una única sesión de MATLAB, sin cambiar los contratos de intento, aceptación, reintentos y reanudación.

## ADDED Requirements

### Requirement: Selección del modo de ejecución MATLAB

La configuración SHALL aceptar `execution.matlab_runner` con los valores `process` y `pool`. Si falta, el valor efectivo SHALL ser `process`, con el comportamiento actual de un `matlab -batch` por intento. Cualquier otro valor SHALL rechazarse en la validación con un error que nombre el campo y los valores admitidos. `pool` SHALL rechazarse antes de planificar cuando el backend efectivo no sea `raven`. El modo SHALL NOT formar parte de la identidad del plan estático ni de la compatibilidad de reanudación, de modo que un lote iniciado en un modo pueda reanudarse en el otro.

#### Scenario: Valor por defecto
- **WHEN** la configuración no define `execution.matlab_runner`
- **THEN** cada intento RAVEN se ejecuta en su propio proceso `matlab -batch`, igual que antes de este cambio

#### Scenario: Valor inválido
- **WHEN** `execution.matlab_runner` vale `threads`
- **THEN** la validación falla antes de planificar con un error que nombra `execution.matlab_runner` y los valores `process` y `pool`

#### Scenario: Pool con backend no RAVEN
- **WHEN** `render.backend` es `pyroomacoustics` y `execution.matlab_runner` es `pool`
- **THEN** la validación falla antes de planificar nombrando el campo y el backend

#### Scenario: Reanudar en otro modo
- **WHEN** un lote se renderizó en parte con `process` y se relanza con `pool` sin otros cambios
- **THEN** las variantes ya aceptadas se reanudan como completas y no se renderizan de nuevo

### Requirement: Una sola sesión de MATLAB por ejecución de render

En modo `pool`, cada ejecución de `render-static` SHALL lanzar como máximo una sesión de MATLAB viva a la vez, mediante un único proceso `matlab -batch`. La sesión SHALL abrir un pool local de procesos con `num_workers` workers, o menos si hay menos variantes pendientes. A lo sumo `num_workers` variantes SHALL renderizarse a la vez. La sesión SHALL arrancar antes del primer despacho, y SHALL cerrarse y terminar su proceso al acabar la ejecución, con éxito o con error, incluido el caso de corte del lote. Una sesión que no queda lista dentro del tiempo de arranque configurado SHALL tratarse como sesión caída.

#### Scenario: Concurrencia con un solo proceso MATLAB
- **WHEN** un lote con 8 variantes pendientes se renderiza con `pool` y `num_workers: 4`
- **THEN** existe un solo proceso `matlab -batch` lanzado por el orquestador y nunca hay más de 4 variantes renderizándose a la vez

#### Scenario: Cierre al terminar
- **WHEN** termina la ejecución, por completarse, por error o por corte del lote
- **THEN** la sesión de MATLAB recibe la orden de cierre, su proceso termina y el orquestador registra su código de salida

### Requirement: Un trabajo del pool es un intento con terminación observada

En modo `pool`, cada variante despachada SHALL seguir siendo un intento con UUID, snapshot de entrada, `outcome.json` y rutas de `completion.json` y `failure.json` propios, igual que en modo `process`. MATLAB SHALL renderizar el trabajo con el mismo entrypoint y los mismos argumentos que en modo `process`. Al terminar el trabajo, SHALL publicar un resultado por intento con código de salida entero: `0` si el entrypoint terminó sin error y distinto de cero si lanzó un error. El orquestador SHALL tomar ese resultado como terminación observada con ese código de salida, y SHALL aplicar sin cambios la aceptación, la clasificación, los reintentos, el corte del lote y los criterios de reanudación vigentes. Un fallo con código distinto de cero SHALL quedar registrado con el mismo tipo de error que en modo `process`, para que las reglas de recuperación `post_completion` y de reanudación lo acepten igual.

#### Scenario: Trabajo correcto
- **WHEN** un trabajo del pool termina sin error y escribió su `completion.json`
- **THEN** el intento queda con `process_status=succeeded`, `exit_code=0`, `termination_observed=true` y se acepta como en modo `process`

#### Scenario: Trabajo con error
- **WHEN** el entrypoint lanza un error dentro del worker
- **THEN** el intento queda con `process_status=failed`, código de salida distinto de cero y terminación observada, y se clasifica y reintenta con la política vigente

#### Scenario: Entrada inválida
- **WHEN** MATLAB publica `failure.json` con categoría `invalid_input` para un trabajo
- **THEN** el intento se aborta sin reintento, igual que en modo `process`

### Requirement: Caída de la sesión o de un worker

Si la sesión de MATLAB termina o deja de responder mientras hay intentos en curso, el orquestador SHALL observar la terminación de su proceso y resolver cada intento en curso como fallo de proceso con terminación observada y código de salida distinto de cero, sin esperar indefinidamente. Si un worker del pool muere durante un trabajo, ese intento SHALL resolverse del mismo modo. Esos intentos SHALL contar para la racha del corte del lote y seguir la política de reintentos vigente, incluida su espera. Antes de despachar un intento nuevo, el orquestador SHALL arrancar una sesión nueva si la anterior ya no está viva. Si una sesión no llega a estar lista, el intento que esperaba despacharse SHALL resolverse como fallo de proceso observado con el código de salida de la sesión.

#### Scenario: Error de licencia al arrancar la sesión
- **WHEN** la sesión termina con código 1 antes de estar lista y su consola contiene `error 5001`
- **THEN** el intento que esperaba despacharse queda como fallo de proceso observado, con la consola de la sesión disponible en su diagnóstico, y se programa su reintento según la política vigente

#### Scenario: Sesión caída con intentos en curso
- **WHEN** el proceso de la sesión termina mientras 3 intentos están en curso
- **THEN** los 3 intentos quedan resueltos como fallos de proceso observados, la racha del corte suma 3 y el siguiente despacho arranca una sesión nueva

#### Scenario: Worker caído
- **WHEN** un worker del pool termina de forma anormal durante un trabajo
- **THEN** ese intento queda como fallo de proceso observado y los demás trabajos de la sesión siguen en curso

### Requirement: Aislamiento de preferencias en la sesión de pool

La sesión de pool SHALL usar una carpeta privada de preferencias de MATLAB creada para esa sesión, fuera de las salidas del lote, y SHALL eliminarla best-effort al terminar. Los renders SHALL NOT leer ni escribir la carpeta de preferencias del usuario. Antes de despachar trabajos, la sesión SHALL completar en cada worker, de a uno por vez, el primer acceso del ITA-Toolbox a sus preferencias, de modo que ningún render escriba `matlabprefs.mat` a la vez que otro worker lo lee. El aislamiento de materiales y modelo de RAVEN por variante SHALL mantenerse igual que en modo `process`.

#### Scenario: Workers concurrentes sin carrera de preferencias
- **WHEN** 4 workers renderizan variantes a la vez en una sesión de pool
- **THEN** ningún intento falla con `Unable to read file '...matlabprefs.mat'`

#### Scenario: Carpeta de preferencias de la sesión
- **WHEN** termina una sesión de pool
- **THEN** su carpeta privada de preferencias se elimina, o queda una advertencia si no se pudo eliminar, sin cambiar el resultado de ningún intento

### Requirement: Trazabilidad y diagnóstico de trabajos del pool

En modo `pool`, cada intento SHALL registrar el modo `pool`, el identificador de la sesión y el identificador del worker que lo renderizó. La salida de consola de cada trabajo SHALL guardarse en el `console.log` del diagnóstico de ese intento, y los eventos de MATLAB SHALL seguir escribiéndose en su `events.jsonl`. La sesión SHALL tener su propio registro de diagnóstico con el comando, la consola, la carpeta de preferencias, la hora de inicio y de fin y el código de salida. El progreso SHALL emitir `attempt_begin` y `attempt_end` por intento, como en modo `process`.

#### Scenario: Intento trazable
- **WHEN** se consulta el `outcome.json` de un intento renderizado en modo `pool`
- **THEN** contiene el modo, el identificador de la sesión y el del worker, y su `console.log` contiene solo la salida de ese trabajo

#### Scenario: Diagnóstico de la sesión
- **WHEN** una sesión de pool termina, con éxito o con error
- **THEN** existe un registro de esa sesión con su comando, consola, carpeta de preferencias, tiempos y código de salida
