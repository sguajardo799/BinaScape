# hearing-processor-selection Specification

## Purpose

Seleccionar los procesadores de degradación auditiva (Clarity MSBG, WHIS o ambos) que se aplican a cada render, validar por procesador su configuración y el catálogo de perfiles, y mantener la identidad de procesador y calibración explícita en identificadores, rutas, manifiestos, sidecars y reanudación, de modo que las salidas de un mismo render sean comparables y nunca se confundan.

## Requirements

### Requirement: Lista de procesadores con MSBG por defecto
La configuración SHALL aceptar `hearing_degradation.processors`, una lista no vacía y sin repetidos con valores en {`msbg`, `whis`}. Si está ausente, SHALL valer `[msbg]`. Por cada render elegible y perfil, el orquestador SHALL planificar un job por procesador de la lista.

#### Scenario: Ambos procesadores
- **WHEN** `processors` es `[msbg, whis]` y hay un render elegible y dos perfiles
- **THEN** se planifican cuatro jobs: uno por perfil y procesador

#### Scenario: Procesador desconocido o repetido
- **WHEN** `processors` es `[msbg, msbg]` o contiene `ahaah`
- **THEN** la validación falla nombrando `hearing_degradation.processors`

### Requirement: Configuración específica por procesador
Con `whis` en la lista SHALL exigirse `hearing_degradation.whis.install_root`, con `synth_method` opcional en {`dtvf`, `fbanasyn`}. Sin `whis`, un bloque `whis` SHALL rechazarse. `hearing_degradation.msbg.level_handling` SHALL aceptar `true_level` (por defecto) y `legacy_active_rescale`, y SHALL rechazarse si `msbg` no está en la lista.

#### Scenario: Bloque sin su procesador
- **WHEN** `processors` es `[msbg]` e incluye `whis.install_root`
- **THEN** la validación falla nombrando `hearing_degradation.whis` como no aplicable

### Requirement: Corrección de oído externo y medio compartida
`hearing_degradation.outer_middle_ear` SHALL aceptar `free_field`, `diffuse_field`, `itu` y `eardrum`, y SHALL aplicarse igual a todos los procesadores de la corrida. Si se omite, SHALL valer `free_field` cuando la lista es `[msbg]` y SHALL ser obligatorio cuando incluye `whis`. El valor aplicado SHALL registrarse en cada sidecar.

#### Scenario: Misma referencia en ambos procesadores
- **WHEN** `processors` es `[msbg, whis]` y `outer_middle_ear` es `itu`
- **THEN** ambos jobs de un render usan la corrección ITU y sus sidecars registran `itu`

#### Scenario: Corrección omitida con WHIS
- **WHEN** `processors` incluye `whis` y no se declara `outer_middle_ear`
- **THEN** la validación falla nombrando el campo como obligatorio

#### Scenario: Tímpano en MSBG
- **WHEN** `outer_middle_ear` es `eardrum` y se procesa con `msbg`
- **THEN** MSBG aplica solo la transferencia de oído medio, sin corrección de campo a tímpano

### Requirement: Extensiones opcionales del catálogo de perfiles
Cada oído de un perfil SHALL poder declarar la banda `125` en `loss_db_by_band` y un `compression_health` opcional: un número en [0, 1] o un mapa por banda con las mismas claves que `loss_db_by_band`. Los valores no finitos o fuera de rango y las claves desconocidas SHALL rechazarse con cualquier procesador.

#### Scenario: Compresión fuera de rango
- **WHEN** un perfil declara `compression_health: {"4000": 1.2}` en el oído derecho
- **THEN** la validación falla nombrando el perfil, el oído y la banda

### Requirement: Requisitos del catálogo para WHIS
Si `whis` está en la lista, todo perfil SHALL declarar `compression_health` en ambos oídos y ningún umbral SHALL ser negativo. La validación SHALL fallar antes de planificar y nombrar cada perfil infractor.

#### Scenario: Perfil sin salud de compresión
- **WHEN** `processors` incluye `whis` y un perfil no declara `compression_health` en el oído izquierdo
- **THEN** la validación falla nombrando el perfil y `left`

### Requirement: MSBG ignora las extensiones de forma trazable
MSBG SHALL ignorar la banda `125` y `compression_health` sin cambiar su salida, y su sidecar SHALL listarlos como ignorados por oído.

#### Scenario: Perfil extendido en MSBG
- **WHEN** un perfil con banda `125` y `compression_health` se procesa con `msbg`
- **THEN** el WAV es idéntico al obtenido sin esos campos y el sidecar los registra como ignorados

### Requirement: Identificadores y layout por procesador
Cada job SHALL identificarse como `clarity__<variant_id>__<hearing_profile_id>__<procesador>`, y su WAV y sidecar SHALL escribirse en `output_audio/degraded/<procesador>/<output_type>/<hearing_profile_id>/` con el basename del WAV renderizado. Esto aplica a todos los procesadores, incluido `msbg`.

#### Scenario: Salidas de ambos procesadores
- **WHEN** un render HRTF se degrada con `msbg` y `whis` para el mismo perfil
- **THEN** existen dos WAV con el mismo basename, uno bajo `degraded/msbg/...` y otro bajo `degraded/whis/...`, y ninguno sobrescribe al otro

### Requirement: Alineación temporal con la entrada
Cada procesador SHALL compensar su latencia constante de modo que el WAV degradado quede alineado muestra a muestra con la entrada y tenga su misma longitud. La compensación aplicada SHALL registrarse en muestras en el sidecar. La única excepción es MSBG con `level_handling: legacy_active_rescale`, que reproduce la salida anterior sin compensar y registra `latency_compensated: false` con 0 muestras.

#### Scenario: Impulso de prueba
- **WHEN** se procesa un impulso con el perfil `nh_reference` y cualquier procesador (MSBG con `true_level`)
- **THEN** el máximo de la salida coincide en ±1 muestra con la posición del impulso de entrada

#### Scenario: MSBG legado
- **WHEN** un job MSBG usa `legacy_active_rescale`
- **THEN** la salida no se desplaza y el sidecar registra `latency_compensated: false` y `latency_compensation_samples: 0`

### Requirement: El manifiesto lleva procesador, calibración y firma
Cada job SHALL incluir su procesador, la calibración del render (P, K, Leq por canal, canales silenciosos), `outer_middle_ear`, la configuración del procesador y una `processing_signature` canónica. El backend SHALL usar esos valores y SHALL NOT sustituirlos por valores propios ni recalcular K.

#### Scenario: Job WHIS
- **WHEN** el orquestador planifica un job `whis`
- **THEN** su línea de manifiesto contiene `processor: "whis"`, K del render, `outer_middle_ear`, `install_root`, `synth_method` y la firma

### Requirement: Validación del sidecar por procesador
Para un resultado `completed`, el orquestador SHALL exigir que el sidecar declare `processor.name` igual al del job, la misma `processing_signature` y un bloque `calibration` con K igual al del job, además de los campos estables vigentes. Si no, el resultado SHALL quedar inválido.

#### Scenario: Sidecar de otro procesador
- **WHEN** el sidecar esperado de un job `whis` declara `processor.name: msbg`
- **THEN** el resultado se marca inválido por procesador no coincidente

### Requirement: Reanudación por firma de procesamiento
Un resultado SHALL reutilizarse solo si su `processing_signature` coincide con la del job actual. La firma SHALL cubrir procesador, calibración configurada, semilla cuando hay rango de nivel, `outer_middle_ear` y la configuración del procesador (`level_handling` o `synth_method`). Las salidas del layout anterior a este cambio SHALL NOT reutilizarse.

#### Scenario: Cambio de corrección de oído
- **WHEN** una corrida completada con `itu` se reanuda con `free_field`
- **THEN** ningún job se reutiliza

#### Scenario: Agregar un procesador
- **WHEN** una corrida completada con `[msbg]` se reanuda con `[msbg, whis]` sin otros cambios
- **THEN** los jobs MSBG se reutilizan y solo se planifican y envían los jobs WHIS

#### Scenario: Corrida con el layout anterior
- **WHEN** se reanuda una corrida cuyas salidas degradadas están en `degraded/<output_type>/<perfil>/`
- **THEN** esos resultados no se reutilizan y los jobs se reprocesan en el layout nuevo

### Requirement: Verificación previa del entorno WHIS
Si la lista incluye `whis`, el envío SHALL bloquearse con un mensaje explícito, sin lanzar procesos, si `install_root` no contiene `WHIS/WHISv302` y `gammachirp-filterbank/GCFBv234`, o si no se encuentra el ejecutable de MATLAB. Los jobs MSBG del mismo manifiesto SHALL NOT bloquearse por ello.

#### Scenario: GCFB ausente
- **WHEN** `install_root` no contiene `gammachirp-filterbank/GCFBv234`
- **THEN** los jobs WHIS quedan `blocked` con la ruta faltante y los jobs MSBG se envían igualmente
