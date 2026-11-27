# Design

## Context

Véase `proposal.md` para la motivación. Este documento conserva la línea base histórica y describe las decisiones del cambio implementado en la copia de trabajo, incluida la reconciliación manual de interrupciones. La verificación con fixtures y procesos simulados no acredita por sí sola la adopción completa de un lote real ni confirma una causa interna de MATLAB.

Línea base anterior al cambio (no describe el estado actual de los módulos):

- `render_pipeline._prepare_static_manifests` volvía a muestrear y escribir todo antes de consultar el índice. `manifest_writer.write_manifest` ya rechazaba correctamente una escritura existente sin permiso de sobrescritura; esa protección se conserva.
- El flujo preparaba audio y escribía todos los runtime incluso cuando después reutilizaba variantes. La aceptación canónica y `_refresh_reverberation_batch` también reescribían manifiestos; no bastaba omitir una llamada al writer.
- `sampler.sample_static_scene` ya usaba semilla estable por escena e intento, base del replay de realizaciones persistidas, incluidas las seleccionadas después de T30.
- `should_resume_variant`, `render_history`, `render_validation` y `render_acceptance` ya ofrecían validación estricta, snapshots, constancias e historial fail-closed. El cambio mantiene estas garantías.
- El runner creaba un `RenderAttempt` por invocación y devolvía evidencia del proceso, sin política de reintentos. El ejecutor paralelo enviaba todas las tareas anticipadamente.
- El entrypoint MATLAB registraba errores solo en diagnóstico de mejor esfuerzo y consola, insuficientes para clasificar errores deterministas de forma funcional.
- Las pruebas de reanudación usaban principalmente `_write_config` con sobrescritura activada. La implementación amplió ese estado sin descartar los cambios de usuario previos.

## Goals / Non-Goals

**Goals:**

- Preparación en dos fases: inspección/validación sin escrituras y ejecución de un plan de acciones explícito.
- Mantener los nombres públicos y los contratos de aceptación, introduciendo registros aditivos versionados.
- Compartir una política pequeña y explícita entre ejecución canónica, secuencial y paralela.
- Resistir interrupciones sin perder presupuestos ni reutilizar evidencia ajena.
- Permitir intervención explícita y auditable sobre un intento interrumpido, separada de la aceptación funcional y sin automatizar la retirada de locks.

**Non-Goals:**

- Diagnosticar internamente MATLAB, corregir su arranque o asegurar que un nuevo intento tendrá éxito.
- Añadir MATLAB Engine, servicios, base de datos, framework de scheduling o soporte dinámico.
- Introducir timeouts, matar procesos huérfanos o reintentar automáticamente terminaciones no observadas.
- Permitir dos orquestadores escritores simultáneos sobre el mismo lote. Se exige exclusión del escritor antes de aplicar acciones; el paralelismo interno por variantes sigue soportado.
- Garantizar la validez interna de todos los formatos DAFF/MAT mediante Python. El preflight verifica lo accesible; MATLAB conserva su validación real.

## Decisions

### 1. Preflight del lote antes de cualquier modificación

Separar preparación de plan y aplicación. La primera carga JSON estrictamente, verifica identidades, esquema, finitud, geometría, superficies, rutas y entradas existentes, y produce acciones `reuse`, `create`, `render`, `conflict`. Un conflicto protegido aborta antes de preparar audio, publicar planes o cambiar índices. Obtener exclusión del escritor y revalidar las precondiciones de archivos antes de ejecutar las acciones; no se promete que un archivo externo no pueda cambiar entre lectura y uso.

El preflight incluye los vetos de cadenas, presupuestos y circuito antes de publicar el plan o escribir runtime. Un intento sin terminación observada sigue bloqueado aunque no existan salidas o se haya retirado el lock. Solo una reconciliación válida, conforme a la decisión 9, puede habilitar la continuación de esa cadena sin convertir el intento en aceptado.

No cambiar `write_manifest` para que acepte silenciosamente archivos existentes: su protección sigue siendo útil y es la preparación la que debe decidir si escribir. Con sobrescritura habilitada y reanudación activa se sigue prefiriendo reutilizar resultados compatibles; la mera autorización no obliga a repetirlos. Con reanudación desactivada y sobrescritura desactivada, los conflictos mantienen error explícito.

### 2. Plan verificable y compatibilidad histórica conservadora

Añadir `metadata/indexes/static_plan.json`, versionado y publicado atómicamente. Contiene configuración efectiva normalizada, hash del contrato de generación, identidad/número de escenas, versiones de generador/estimador y catálogo ordenado relevante. Excluir de la identidad acústica ajustes operativos como workers, reanudación, sobrescritura y reintentos; incluir el número de simulaciones porque afecta las cuotas y el plan global de ruido. Registrar referencias y hashes de entradas seleccionadas en nuevos planes para detectar sustituciones en rutas iguales.

Un registro no sustituye la comprobación del manifiesto. Validar la realización y comparar semántica funcional; no incluir contabilidad mutable en el hash acústico. Conservar las semillas enteras de muestreo sin convertirlas a `float`; distinguirlas de `render.seed`, que es la semilla efectiva del renderer.

Para lotes sin `static_plan.json`, reproducir en memoria la selección persistida usando semilla, índice, intento y versiones disponibles, y comparar entradas funcionales y selección del plan de ruido/IR. Para intentos posteriores a T30, usar el intento de escena persistido, no la primera realización. La comparación ignora únicamente contabilidad de muestreo/lote, no geometría, fuentes ni parámetros de render. Si no puede probarse compatibilidad, conflicto en vez de una migración por confianza. Solo publicar un registro de adopción después del preflight completo; marcar qué huellas se observaron ahora, sin afirmar que sean las originales.

Una salida histórica sin fallo conocido mantiene la vía legacy estricta existente. Un fallo o intento pendiente conocido nunca se convierte en legacy aunque falte el índice.

### 3. Entradas reutilizadas inmutables y resumen separado

Resolver y comprobar las rutas de audio preparado sin reescribirlas. Si deben existir para una variante reutilizada, comprobar contenido/dimensiones esperadas; generar exclusivamente archivos ausentes necesarios para trabajo nuevo, con publicación segura y sin sustitución oculta. Un archivo preparado incompatible es conflicto protegido.

Construir en memoria el runtime esperado y validar historial/artefactos antes de `_prepare_render_task`. No llamar al writer runtime ni al preparador de audio con efectos para una variante reutilizada. Crear las tareas solo para las variantes realmente ejecutables.

Si un runtime existente es semánticamente compatible, conservar sus bytes exactos también al continuar una cadena pendiente. Su huella se obtiene del archivo, no de una reserialización: JSON equivalentes con finales LF/CRLF pueden tener SHA-256 distintos. Los runtime nuevos usan LF; no se normalizan archivos históricos para hacerlos encajar con una cadena.

Publicar estadísticas actuales en `metadata/indexes/static_run_summary.json`, con contadores separados de escenas generadas, escenas acústicamente aceptadas y variantes completadas. Los campos históricos embebidos en manifiestos anteriores no se actualizan al reanudar. Conservar los rechazos T30 persistidos, en lugar de reiniciarlos a lista vacía al reutilizar. El journal de muestreo conserva fallos/revisiones por escena; las estadísticas se calculan sobre su estado vigente sin borrar el historial. Nuevas realizaciones por T30 solo sustituyen una escena aún no aceptada, según la política acústica ya existente y sin tocar escenas válidas anteriores.

Antes de lanzar una realización de reemplazo por T30, publicar su manifiesto y revisión del plan, conservando la realización anterior en el historial. Así, una interrupción durante un reintento de proceso no deja una entrada runtime nueva junto a una escena antigua. Una interrupción entre publicaciones que no pueda reconciliarse de forma inequívoca bloquea, no autoriza adopción por existencia.

Si el remuestreo canónico sustituiría una variante secundaria válida protegida por `overwrite_existing=false`, detenerse sin modificar esa variante. Cuando el reemplazo de la escena está autorizado, conservar los WAV, sidecars, runtime y recortes de la realización descartada mediante inventarios con huellas antes de preparar la siguiente. Esto evita colisiones de recortes cuando una fuente con el mismo identificador cambia de PCM entre realizaciones. Un presupuesto T30 agotado se persiste en `static_plan.json` como estado terminal de `failed_scenes`, además del historial; no se trata como un manifiesto accidentalmente ausente que deba restaurarse al reiniciar.

Alternativa descartada: actualizar `reverberation_batch` en cada manifiesto conservado; contradice la conservación de bytes y mezcla estado del lote con entrada acústica.

### 4. Configuración de proceso y semántica de cadena

Añadir bajo `execution.matlab_process_retry`:

```yaml
max_attempts: 3
initial_delay_s: 2.0
max_delay_s: 30.0
max_consecutive_failures: 5
```

Validar tipos estrictos, finitud y rangos. La espera después del fallo ordinal `n` es `min(max_delay_s, initial_delay_s * 2**(n-1))`. `max_attempts` incluye la primera invocación. Estos son los valores por defecto definidos para la política general, no ajustes exclusivos del experimento observado; cada cadena conserva la política que fijó al crearse.

Una cadena corresponde a una variante y una realización acústica concreta. Un remuestreo T30 crea otra realización/cadena y sigue consumiendo el presupuesto acústico existente; los reintentos de proceso nunca lo alteran. La racha de fallos del lote evita multiplicar invocaciones durante una avería general.

Persistir estado de cadena en un registro funcional por `chain_id` y añadir a cada outcome `chain_id`, ordinal, intento previo, hash de entrada, política fijada, clasificación y decisión de programación. No cambiar el significado de `decision=abort` de aceptación: indica rechazo del intento; `retry_decision=scheduled|not_retryable|exhausted|circuit_open` pertenece a la programación.

Reservar y persistir el ordinal antes del lanzamiento. Un reinicio continúa cadenas pendientes identificables y respeta su deadline de espera; no reinicia el contador. Un intento `running` con terminación no observada bloquea la cadena hasta intervención, porque no se puede afirmar que haya dejado de escribir. Una cadena agotada/cortada no se reabre silenciosamente: una ejecución intencional con `resume_if_possible=false` y `overwrite_existing=true` abre nuevas cadenas preservando las anteriores. Los intentos históricos sin cadena pueden preceder una nueva cadena si no hay salidas protegidas, sin fingir que ya tenían esta política.

La intervención manual no es ese reset: conserva el ordinal ya consumido, la cadena y su política. Una confirmación acreditada permite `retry_classification=confirmed_interruption`, con `decision=abort` y resultado de proceso todavía desconocido. No modifica por sí sola configuración, circuito, índices de renders ni elegibilidad de salidas.

### 5. Clasificación general, sin regex de consola

Evaluar primero la aceptación existente:

| Evidencia | Acción de programación |
|---|---|
| Render aceptado con código cero | Continuar; reiniciar racha |
| Recuperación `post_completion` válida | Continuar con advertencia; no repetir; reiniciar racha |
| Artefactos válidos y rechazo T30 | Política acústica existente |
| Código no cero observado, sin aceptación ni error determinista acreditado | Candidato a nuevo intento si queda presupuesto |
| Lanzamiento fallido, terminación no observada, fallo de almacenamiento | Bloquear continuación automática; no presumir cierre del proceso |
| Interrupción con reconciliación manual válida | Autorizar programación con presupuesto restante; no aceptar el intento ni lanzar MATLAB desde la operación administrativa |
| Entrada inválida o dependencia ausente acreditada | Abortar |
| Constancia/informe con identidad, hash o inventario contradictorio | Abortar por integridad |
| Código cero pero render funcionalmente inválido | Abortar; no maquillar validación con reintentos |

Un WAV ausente/truncado o sidecar parcial dejado por un proceso no cero no es por sí mismo una contradicción de identidad: puede aislarse antes de otro intento. Una constancia final inválida o procedencia incompatible sí es contradicción. La consola y los eventos solo ayudan a investigar; su ausencia no identifica una etapa ni modifica la política. Un código no cero desconocido puede repetir un error determinista no clasificado, pero solo hasta el límite y sin etiquetarlo como transitorio confirmado.

### 6. Evidencia funcional de error de entrada en MATLAB

Extender aditivamente el contexto funcional con `failure_path`, distinto de `completion_path`. Al fallar la carga/validación de entrada, publicar atómicamente `failure.json` con versión, identidad de intento/variante, hash de entrada, categoría `invalid_input` y error original. Acotar la captura a esa fase: no clasificar todo error de inicialización o simulación como determinista. Las comprobaciones Python pueden acreditar por sí mismas archivos/dependencias ausentes antes del lanzamiento.

Python prepara la ruta y verifica informe estricto, identidad, hash y categoría. Informe corrupto/ajeno implica conflicto de integridad. Si falla su escritura, MATLAB conserva y relanza la excepción original; no fabrica constancia ni un éxito. La ausencia del informe no prueba transitoriedad. Las llamadas sin contexto o con contexto antiguo mantienen su firma y no exigen este archivo. `matlab-render-diagnostics` no cambia: sigue sin decidir reintentos ni aceptación.

### 7. Aislar restos propios sin cambiar rutas públicas

Mantener las rutas públicas actuales y no introducir una promoción de WAV/metadatos que obligue a reescribir constancias y sidecars. Antes de una cadena nueva, inventariar salidas preexistentes. Con `overwrite_existing=false`, las no reutilizables preexistentes bloquean; las variantes sin salidas pueden ejecutarse.

Después de persistir un fallo observado reintentable, o validar una reconciliación manual que habilite continuación, archivar únicamente sus propios WAV/metadatos bajo el directorio de ese intento, registrando ruta original, ruta de archivo, tamaño y SHA-256. En el caso manual, el inventario debe coincidir con el confirmado por el operador: archivos aparecidos o alterados después no se atribuyen al intento anterior. El snapshot y las constancias existentes permanecen intactos; archivar salidas no convierte un outcome rechazado en aceptable. No hay nuevo lanzamiento hasta comprobar que el archivo se completó, su journal quedó publicado y las rutas destino están libres. Los siguientes intentos reciben el mismo runtime, pero UUID y constancia propios.

El movimiento de varios archivos no es una transacción: publicar primero intención de archivo y luego confirmación por archivo. Tras interrupción se reconcilian origen/destino y hashes; estados ambiguos bloquean, nunca se borran a ciegas. Archivar restos propios de una cadena ya iniciada no autoriza mover archivos protegidos que existían antes de ella. Al agotar el presupuesto se conserva la última evidencia sin disparar limpieza destructiva. Las reglas existentes de descarte T30 deben conservar el historial funcional y no afectar otros intentos.

Alternativa descartada para este cambio: staging de todas las salidas y promoción posterior. Aislaría más la publicación, pero obligaría a resolver rutas en sidecars, hashes de constancias y compatibilidad Clarity; el archivo journalizado cubre la repetición tras terminación observada o reconciliación manual válida sin cambiar los nombres públicos ni la aceptación funcional.

### 8. Coordinación del lote, resumen y exclusión

La coordinación es dueña del índice, cadena y contador de fallos; los workers ejecutan una sola invocación. Reemplazar el envío anticipado de todas las tareas por despacho acotado a `num_workers`, con revisión del corte antes de cada invocación/reintento. Compartir el controlador con la ruta canónica: no implementar tres bucles de política distintos.

Contar cada fallo de proceso no recuperado al persistir su resolución. Un render aceptado reinicia la racha; T30 sin fallo de proceso no suma ni reinicia por sí solo. Una vez alcanzado el umbral, el corte queda enclavado para esa ejecución: un éxito tardío de un worker ya iniciado no reabre despachos. Esperar y registrar esos workers; no matarlos. El estado del corte es funcional y se conserva para no renovar automáticamente el presupuesto al reiniciar.

Mantener `render_jobs` como número de trabajos/variantes intentados de la ejecución, no invocaciones. Añadir `matlab_invocations`, `process_retries`, `variants_completed_after_retry` y razón del corte; los contadores de procesos fallidos/recuperados incluyen todas las invocaciones reales de la ejecución. No crear invocaciones para reutilización; preservar advertencias históricas. La excepción terminal conserva error original y referencias a todos los intentos de la cadena.

Una reconciliación tampoco crea una invocación ni un proceso fallido observado: el intento interrumpido conserva su ordinal, pero no se inventa un código para contabilizarlo. Una invocación posterior sí cuenta como proceso real y como reintento de la cadena; su eventual aceptación no recupera retrospectivamente el intento interrumpido.

Usar exclusión de escritor por lote para impedir dos CLI sobre las mismas rutas; un bloqueo de estado incierto no se rompe automáticamente. No sustituye el paralelismo interno ni un sistema distribuido.

### 9. Reconciliación manual explícita de interrupciones

`reconcile-static <run_root> --chain <chain_id>` inspecciona sin escrituras. No usa un YAML ni crea locks en ese modo. Informa el UUID del intento actual y su presupuesto; no considera la ausencia de un PID como prueba de terminación ni como autorización implícita.

Aplicar requiere todos los argumentos `--apply --attempt <attempt_id> --confirm-stopped --reason <motivo>`. Fijar el intento evita que repetir una orden antigua reconcilie accidentalmente una invocación nueva de la misma cadena. La operación adquiere el lock del lote y vuelve a comprobar identidad, snapshot, runtime, inventario y procesos antes de publicar. No retira locks existentes ni mata procesos. La comprobación local es conservadora: bloquea MATLAB/RAVEN y posibles orquestadores de render/generación; una consulta fallida, vacía o no verificable bloquea, incluso con confirmación del operador.

La implementación reside en `interruption_recovery.py`, expuesta por la CLI y verificada por las primitivas de cadenas. Es una extensión administrativa del orquestador; no modifica el entrypoint MATLAB, la validación acústica ni la independencia del diagnóstico. Requiere evidencia suficiente de una interrupción sin resultado observado; constancias/informes funcionales presentes, salidas completas verificables, datos contradictorios, archivos preexistentes protegidos o un circuito abierto exigen otra intervención y no se descartan con este comando.

La evidencia se guarda en `metadata/render_attempts/<attempt_id>/interruption_reconciliation/`:

- `chain.before.json` y `outcome.before.json`: preimágenes de bytes exactos, conservadas sin reemplazo.
- `record.json`: registro versionado con identidad de la intervención, cadena e intento, rutas, huellas, operador, host, fecha, motivo, confirmación e inventario de salidas.
- Un vínculo por identidad, ruta y SHA-256 desde outcome y entrada de la cadena permite comprobar que ambos corresponden a esa misma intervención. Editar solamente un estado no satisface esa comprobación.

Publicar primero las preimágenes y el registro; después, outcome y cadena mediante publicaciones individuales. No es una transacción de varios archivos: una interrupción mantiene bloqueado el render hasta completar la misma intención registrada. Repetir la orden con el mismo intento es idempotente, conserva el registro original y no consume otro ordinal; evidencia inesperada bloquea en lugar de sobrescribirse.

El outcome reconciliado queda `process_status=interrupted`, `decision=abort`, `exit_code=null` y `termination_observed=false`. Se preservan el error original y la fecha de finalización desconocida; la fecha de intervención se registra por separado. `confirmed_interruption` identifica autorización administrativa, no finalización funcional ni recuperación `post_completion`. Los archivos diagnósticos originales no se reescriben para fingir que observaron un cierre.

```text
running / resultado no observado
                |
     reconciliacion manual validada
                v
outcome: interrupted / abort / exit_code=null
                |
       +--------+---------+
       |                  |
  queda presupuesto   sin presupuesto
       |                  |
chain: scheduled     chain: exhausted
       |
render-static por separado
       |
nuevo UUID / ordinal siguiente / misma entrada
```

La operación administrativa no lanza MATLAB, no acepta salidas, no reinicia el circuito y no modifica los renders válidos de otras variantes. El archivo de restos parciales se realiza al continuar el render, conforme al inventario confirmado y al journal existente; si los archivos cambian después de confirmar, no se trasladan automáticamente. El nuevo intento sigue sujeto a su propia aceptación, T30 y revalidación antes de Clarity.

Alternativas descartadas: eliminar la cadena, cambiar `running` a `failed` a mano o reabrirla por antigüedad/ausencia de PID. Perderían evidencia, podrían renovar presupuesto o permitir dos escritores sin acreditar qué intervención autorizó la continuación.

## Risks / Trade-offs

- [Repetir un error desconocido pero determinista] --> Presupuesto pequeño, corte global y clasificación funcional de errores de entrada; no prometer causa ni recuperación.
- [Coste de verificar lotes históricos y fuentes preparadas] --> Comparación determinista solo de lo necesario; registro de adopción verificable y hashes nuevos sin inventar evidencia pasada.
- [Cambios de catálogo o versiones impiden replay legacy] --> Conflicto conservador con motivo; usar otro run o regeneración explícitamente autorizada.
- [Interrupción durante archivo o commit] --> Journal y estados pendientes; bloquear ambigüedades en lugar de reinterpretar restos como completos.
- [Cambios de usuario ya presentes] --> Implementar sobre contratos actuales y ampliar sus pruebas sin revertirlos.
- [Consumidores leen contabilidad embebida antigua] --> Documentar el resumen externo como fuente actual y conservar campos legacy como snapshot histórico.
- [Estado previo del filesystem no prueba su validez interna en MATLAB] --> Preflight no reemplaza el validador real ni la aceptación de salidas.
- [Ausencia de proceso confundida con terminación observada] --> Confirmación explícita más inspección local y evidencia vinculada, conservando código desconocido y bloqueo automático sin reconciliación válida.
- [Estados parcialmente publicados o archivos posteriores a la intervención] --> Repetición idempotente de la misma intención y verificación de preimágenes/inventario; bloquear contradicciones, no renovar presupuesto ni reclamar archivos nuevos.

## Migration Plan

1. Añadir modelos y lectores versionados, preflight y pruebas sin tocar salidas reales del experimento.
2. Implementar reutilización no destructiva y adopción legacy. Validar con fixtures de realizaciones posteriores a T30 y combinaciones de opciones.
3. Incorporar informe MATLAB, cadena persistente, archivo de restos y controlador de reintentos en todas las rutas.
4. Incorporar `reconcile-static` y su verificación de evidencia en las cadenas; comprobar inspección sin escrituras, guardas, publicaciones interrumpidas, presupuesto agotado y posterior invocación con otro UUID. La entrega de la operación no autoriza aplicarla automáticamente sobre un lote real.
5. Actualizar ejemplos/docs con defaults, forma de desactivar reintentos, límites de restart, reconciliación explícita y conflictos de sobrescritura.
6. Verificar suite Python y pruebas MATLAB con entorno disponible; no usar la reejecución del lote real como prueba automática ni interpretar una inspección parcial como certificación de adopción completa.
7. Rollback operativo: `max_attempts=1` desactiva repetición en cadenas nuevas, no cambia políticas ya fijadas. Una versión antigua que desconozca los campos nuevos requiere YAML compatible y no debe reanudar cadenas pendientes cuya evidencia no pueda verificar, incluida la reconciliación manual. Conservar los registros y usar un run distinto o mantener un lector compatible; no borrar evidencia ni modificar resultados desconocidos para hacer compatible un rollback.
