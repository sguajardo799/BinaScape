# Design

## Context

La motivación está en `proposal.md`; el contrato propuesto está en `specs/pipeline-progress/spec.md`.

Estado observado:

- `cli.py` usa Typer y conserva resúmenes en stdout. El orquestador ya declara Rich.
- `inspect_static_plan()` evalúa escenas y `apply_static_plan()` publica después del preflight; la inspección no debe convertirse en escritura.
- `_render_static_scenes_locked()` resuelve canónicas secuencialmente, incluyendo remuestreos T30, y acumula las variantes restantes. Estas últimas pasan por ejecución secuencial o `ThreadPoolExecutor`.
- `_RenderCoordinator.resolve()` persiste aceptación y decide reintentos/circuito. Terminar un subprocess no equivale a resolver una unidad funcional.
- El runner MATLAB usa `subprocess.run()` y redirige su consola a un log por intento. Los eventos JSONL ya contienen identidad, secuencia, etapas y pasos; hoy se recopilan al final.
- Clarity se invoca con consola heredada. `run_manifest()` procesa líneas no vacías una a una, y `_process_job()` devuelve después de escribir metadatos de éxito o fallo.
- El handoff revalida entradas antes del envío y solo envía jobs pendientes elegibles. `enabled` y `auto_submit` tienen responsabilidades distintas.
- `_generation_contract()` incorpora hashes del sampler, constructores, geometría, audio y validador. Editarlos para feedback visual invalidaría planes previos aunque no cambie la acústica.

Las especificaciones existentes de diagnóstico, reanudación, reintentos y elegibilidad se mantienen. Algunas introducciones históricas aún describen comportamiento como propuesto, aunque código y pruebas ya lo implementan; este cambio no las reescribe.

## Goals / Non-Goals

**Goals:**

- Separar observación de ejecución mediante un callback opcional, sin importar Rich desde el núcleo funcional.
- Dar feedback continuo durante subprocess bloqueantes conservando el modelo actual de ejecución.
- Tener un solo dueño de la presentación en cada proceso y evitar mezcla de barras padre/hijo.
- Mantener contabilidad verificable en reanudación, rechazo T30 y resolución paralela.

**Non-Goals:**

- Modificar archivos incluidos en las huellas de generación, eventos emitidos por MATLAB, DSP o planificación acústica.
- Añadir porcentajes dentro de RAVEN, un ETA global, predicciones históricas persistentes, procesamiento paralelo Clarity o un protocolo IPC nuevo.
- Cambiar políticas de cancelación, estados funcionales, semántica de errores ni formatos de artefactos.

## Decisions

### 1. Callback pequeño y presentación local

Añadir un módulo pequeño de progreso en el orquestador con un evento tipado y un callback opcional. Los datos se limitan a etapa, unidad lógica, estado, identidad, contadores, tiempo monotónico y ubicación diagnóstica cuando exista. El CLI crea el observador; las APIs sin callback permanecen silenciosas. Las emisiones pasan por una guarda que desactiva el observador si falla, sin capturar interrupciones del operador como fallos de presentación.

El observador mantiene estado protegido para callbacks concurrentes; solo su ciclo de presentación escribe en consola. No introduce registro global, bus de eventos, clases base ni almacenamiento propio. Los eventos de presentación no se guardan en los manifiestos ni índices.

Clarity tiene una ayuda local independiente para Rich y ETA, sin importar el orquestador. Se acepta duplicar la pequeña presentación antes que crear un paquete compartido entre aplicaciones separadas.

**Alternativas:** imprimir desde el núcleo acopla pruebas y consola; envolver solo las listas con una barra cuenta envíos en vez de decisiones; un framework de eventos excede esta necesidad.

### 2. Etapas con denominadores estables

| Etapa | Total | Cuándo avanza | Contadores auxiliares |
|---|---|---|---|
| Evaluación del plan | `num_simulations` | Una vez al terminar de evaluar cada índice, incluso fallo/conflicto | Nuevas, conservadas, fallidas; conflictos identificados aparte |
| Verificación/publicación | Indeterminado | Transiciones explícitas, no porcentaje | Operación actual |
| Canónicas | Escenas con manifiesto al iniciar render | Aceptación/reutilización verificada o fallo terminal de escena | Aceptadas, reutilizadas, fallidas, invocaciones, reintentos, remuestreos |
| Variantes restantes | Variantes no canónicas de escenas aceptadas | Reutilización verificada o decisión terminal persistida | Aceptadas, reutilizadas, fallidas, activas |
| Preparación de Clarity | Indeterminado | Preparación, revalidación, pendiente de envío o bloqueo | Planificados, reutilizados, bloqueados, omitidos |
| Degradado real | Líneas no vacías enviadas al backend | Job procesado con resultado persistido | Completados y fallidos |

Los callbacks de planificación se sitúan en `static_plan.py`, no en el sampler. Tras evaluar todas las escenas se cambia a verificación/publicación, sin afirmar que el plan ya se publicó. Los conflictos acumulados pueden cerrar la evaluación con error aunque su contador haya alcanzado el total.

Las identidades lógicas de render se mantienen estables entre reintentos. El intento UUID identifica actividad, no una unidad adicional. Un rechazo T30 cambia el intento y conserva pendiente la escena. Un agotamiento acústico la resuelve como fallida. Si falla persistencia o se aborta antes de resolver trabajo, queda pendiente y la etapa se marca interrumpida.

Las variantes no canónicas reutilizadas se recogen durante la fase canónica y se presentan al abrir su propia etapa; el total incluye reutilizadas más ejecutables, pero solo las ejecutables alimentan el ETA. Escenas descartadas por agotamiento T30 no aportan variantes pendientes ficticias a la siguiente etapa. Una etapa sin trabajo se etiqueta explícitamente sin ETA.

En paralelo, actualizar la resolución lógica en el coordinador después de la persistencia y la decisión de reintento. Los workers solo informan actividad del intento. No contar cada future terminado como variante resuelta si se va a reintentar. Después del corte, drenar los ya iniciados como hace el código actual y mantener pendientes los no despachados.

### 3. Actividad MATLAB sin reemplazar subprocess.run

Cuando existe observador, el runner registra el intento y su `events_path` al comenzar, junto con escena y variante. Un único ciclo de observación por presentación consulta los intentos activos cada 500 ms y refresca elapsed/actividad; el hilo que invoca MATLAB sigue bloqueado en `subprocess.run()`.

Leer JSONL incrementalmente con offset y buffer de cola parcial; aceptar únicamente líneas completas con versión, UUID y secuencia válidos. Reutilizar la validación de eventos mediante una ayuda compartida dentro del módulo diagnóstico si hace falta, preservando su comportamiento final y pruebas actuales. Evitar releer el archivo entero y emitir la misma advertencia en cada refresco. Eventos de otros intentos no se muestran. Una secuencia terminada no acredita aceptación; después de la salida del proceso el texto puede indicar validación mientras el coordinador resuelve.

Limitar las filas de actividad al número de workers configurado y al ancho disponible, sin crear una barra por cada variante histórica. Si no hay eventos, mostrar arranque/actividad interna desconocida, nunca una etapa inferida del log. Sin modo interactivo no es necesario sondear archivos: se conservan transiciones breves y contadores finales.

El ciclo usa señal de parada y cierre en `finally`, sin esperar a que MATLAB termine ni introducir joins que dependan de subprocesses. Un fallo de lectura solo elimina la información de actividad afectada. `--no-progress` y APIs sin observador no crean hilo ni sondeo.

**Alternativas:** analizar `console.log` es frágil; migrar a `Popen` amplía innecesariamente la superficie de interrupción; tratar las tres etapas MATLAB como tercios de tiempo fabrica precisión.

### 4. ETA por etapa basado en la ejecución actual

Usar reloj monotónico inyectable en pruebas. Reiniciar estimadores entre planificación, canónicas, variantes y degradado. No utilizar la velocidad automática de Rich sobre el contador visual: incluir reutilizaciones produciría un ETA artificialmente optimista.

- **Secuencial:** media móvil exponencial de duración por unidad nueva y aceptada, con factor 0,3. Para canónicas, medir desde el inicio del trabajo lógico hasta su aceptación, incluyendo invocaciones fallidas, esperas y remuestreos T30. Para planificación, usar escenas nuevas evaluadas satisfactoriamente; no usar conservadas ni fallos como muestras rápidas. Para Clarity, usar jobs completados, no resultados fallidos.
- **Variantes paralelas:** tasa acumulada de aceptaciones nuevas sobre tiempo de reloj desde el primer despacho ejecutable. Incluye esperas y reintentos ocurridos en ese intervalo; no sumar tiempos de workers ni multiplicar la tasa otra vez por concurrencia. Recalcular con el tiempo actual para reflejar trabajos largos todavía activos.
- Habilitar ETA tras tres muestras aceptadas. Multiplicar duración estimada por unidades ejecutables pendientes, o dividir pendientes por tasa en paralelo. Fallos terminales reducen pendientes pero no añaden muestras de aceptación. Las reutilizaciones solo actualizan el contador visual.
- No extrapolar el ETA secuencial restando a ciegas el elapsed de un trabajo largo: conservar estimación aproximada y tiempo transcurrido; el ETA puede aumentar. Mientras haya pendientes, presentar al menos un segundo o `calculando`, nunca cero como finalización.
- Validar finitud y denominadores; etapas vacías, interrumpidas o sin suficientes muestras no muestran ETA numérico.

Este estimador prioriza honestidad y simplicidad sobre precisión con cargas heterogéneas. No persiste tiempos nuevos ni modifica metadatos funcionales.

### 5. Padre e hijo no compiten por la terminal

El orquestador termina o suspende su presentación Rich antes del envío Clarity y deja que el backend muestre su propia barra con stderr heredado. Tras el retorno recupera presentación breve para reinspección/resumen. No capturar stdout para extraer progreso ni incorporar campos a los jobs JSONL.

Añadir `--no-progress` a los tres comandos del orquestador y a `run-manifest` de Clarity. Propagar desactivación por argumento del comando subprocess, tanto con uv como sin uv. Transportar la política de presentación como argumento opcional de las funciones de handoff/runner, no como campo YAML. Las llamadas programáticas sin observador deben desactivar también la presentación del backend que lancen. La CLI Clarity crea su presentación y la pasa opcionalmente a `run_manifest()`; llamar directamente a esa función sigue siendo silencioso.

En automático, comprobar `stderr.isatty()`: Rich animado en terminal y mensajes de inicio/fin/transición sin cursor ni ANSI fuera de terminal. `--no-progress` suprime ambos modos, no advertencias ni resúmenes existentes. Escapar identidades/rutas como texto literal y adaptar ancho; no requieren soporte Unicode para informar avance.

En Clarity, contar primero las líneas no vacías del texto que ya se lee completo, manteniendo parseo JSON por línea durante el recorrido. No preparsear todo el lote: eso cambiaría el comportamiento actual si hay una línea inválida después de jobs válidos. Avanzar tras `_process_job()` y distinguir retorno falso de excepción que aborta el recorrido.

### 6. Compatibilidad y dependencias

Declarar Rich directamente en `apps/clarity-backend/pyproject.toml` y actualizar únicamente los lockfiles gestionados correspondientes. No depender de un paquete instalado transitivamente por PyClarity ni añadir dependencia directa al backend desde el orquestador.

Conservar firmas posicionales existentes añadiendo parámetros opcionales keyword-only, retornos, resúmenes y códigos de salida. No editar archivos incluidos en `_generation_contract()` ni su lista de hashes para evitar invalidar planes por cambios visuales. Las pruebas de equivalencia deben verificar ausencia de invocaciones adicionales, mismos estados, semillas y artefactos protegidos, y huella de generación sin cambios.

## Risks / Trade-offs

- [ETA variable por geometría, perfiles y arranque MATLAB] -> Marcarlo aproximado, calentamiento de tres muestras, separar etapas y excluir reutilizaciones.
- [Doble contabilidad entre intento y trabajo] -> Identidad lógica estable y avance solo tras decisión terminal; pruebas de T30, proceso y futures fuera de orden.
- [Sondeo diagnóstico introduce carga o errores repetidos] -> Lectura incremental cada 500 ms, memoria acotada, sin sondeo cuando no es interactivo y aislamiento por intento.
- [Barras mezcladas con warnings o subprocesses] -> Un dueño de presentación por proceso, consola Rich para mensajes propios y suspensión del padre durante Clarity; conservar logs MATLAB existentes.
- [Error de consola reemplaza un error real] -> Guardas en emisión y cierre; pruebas de fallos en arranque, refresco y finalización, tanto con éxito como con error funcional.
- [Nueva opción cambia comandos esperados en pruebas] -> Mantener construcción base compatible y probar la propagación explícita, con uv y sin uv.
- [Huellas de generación invalidan reanudación] -> Instrumentar solo coordinación/planificación, sin tocar fuentes generadoras hasheadas; prueba de reanudación protegida.

## Migration Plan

1. Implementar callbacks y estimadores con pruebas unitarias, inicialmente sin presentación activada desde APIs.
2. Conectar planificación y render, incluyendo observación diagnóstica y modo CLI automático/desactivado.
3. Incorporar presentación local Clarity, dependencia directa y propagación de política; no migrar artefactos existentes.
4. Ejecutar regresiones y verificar terminal interactiva/no interactiva en Windows; documentar comandos y límites del ETA.
5. Para desactivar operativamente usar `--no-progress`. Una reversión del cambio no requiere migración de datos: no se añaden formatos persistentes ni campos funcionales.
