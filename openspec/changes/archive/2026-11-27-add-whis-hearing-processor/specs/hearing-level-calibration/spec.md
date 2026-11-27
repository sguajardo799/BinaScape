# Spec Delta

## Purpose

Declarar de forma explícita y trazable la relación entre nivel digital y dB SPL de cada render, compartida por todos los procesadores de degradación auditiva y por la exportación para presentación, de modo que la severidad simulada no dependa de la escala arbitraria del render, la diferencia interaural de nivel se conserve y las versiones de un mismo render sean comparables.

## ADDED Requirements

### Requirement: Modos de calibración explícitos
La configuración SHALL aceptar `hearing_degradation.calibration.mode` con los valores `fixed_scale` y `presentation_level`. `fixed_scale` SHALL exigir `digital_rms1_spl_db` finito. `presentation_level` SHALL exigir exactamente uno de `presentation_level_db_spl` (finito) o `presentation_level_range_db_spl` (`[min, max]` finitos con min ≤ max). Los campos de otro modo y los modos desconocidos SHALL rechazarse nombrando el campo.

#### Scenario: Escala fija válida
- **WHEN** la configuración declara `mode: fixed_scale` y `digital_rms1_spl_db: 100`
- **THEN** la validación acepta la calibración y todos los renders usan 100 dB como escala efectiva

#### Scenario: Nivel fijo y rango a la vez
- **WHEN** la configuración declara `presentation_level_db_spl` y `presentation_level_range_db_spl`
- **THEN** la validación falla indicando que son excluyentes

#### Scenario: Modo desconocido
- **WHEN** la configuración declara `mode: auto`
- **THEN** la validación falla antes de planificar, listando `fixed_scale` y `presentation_level`

### Requirement: Calibración por defecto y obligatoria
Sin `hearing_degradation.calibration`, si `processors` contiene solo `msbg`, la calibración efectiva SHALL ser `fixed_scale` con 90 dB y registrarse como por defecto. Si `processors` incluye `whis`, el bloque SHALL ser obligatorio y la validación SHALL fallar si falta.

#### Scenario: Configuración existente
- **WHEN** una configuración sin `calibration` ni `processors` habilita la degradación
- **THEN** se usa `fixed_scale` con 90 dB y los sidecars lo marcan como calibración por defecto

#### Scenario: WHIS sin calibración
- **WHEN** `processors` es `[msbg, whis]` y no hay bloque `calibration`
- **THEN** la validación falla nombrando `hearing_degradation.calibration` como obligatorio

### Requirement: Escala efectiva única por render
El orquestador SHALL calcular una escala efectiva K (dB SPL para RMS digital 1.0) una sola vez por render y entregarla sin cambios a cada job de ese render, sea cual sea su procesador o perfil. En `fixed_scale`, K = `digital_rms1_spl_db`. En `presentation_level`, K = P − 10·log10((ms_L + ms_R)/2), donde `ms_c` es la media cuadrática sin ponderar del canal sobre el archivo completo y P el nivel de presentación del render.

#### Scenario: Procesadores del mismo render
- **WHEN** un render se procesa con dos perfiles y con `msbg` y `whis`
- **THEN** los cuatro jobs reciben y registran el mismo K

#### Scenario: Escenas a distinto nivel digital
- **WHEN** dos renders difieren solo en 12 dB de ganancia digital y P es fijo
- **THEN** sus K difieren en 12 dB y ambos se simulan al mismo Leq binaural

### Requirement: Nivel de presentación muestreado de forma determinista
Con `presentation_level_range_db_spl`, el nivel P de cada render SHALL muestrearse de forma uniforme en el rango a partir de `experiment.random_seed` y del `variant_id`, de modo que la misma configuración produzca el mismo P para el mismo render en cualquier ejecución o reanudación.

#### Scenario: Reanudación con rango
- **WHEN** una corrida con rango [55, 80] se interrumpe y se reanuda
- **THEN** cada render conserva el P y el K de la primera ejecución

#### Scenario: Cambio de semilla
- **WHEN** solo cambia `experiment.random_seed`
- **THEN** los P cambian y ningún resultado degradado previo se reutiliza

### Requirement: Índice de calibración por render
El orquestador SHALL registrar por render en `metadata/indexes/level_calibration.jsonl`: `variant_id`, modo, valores configurados, si es por defecto, P (si aplica), K, la media cuadrática y el Leq SPL de cada canal, los canales silenciosos y la medida de nivel (`rms_unweighted_full_file`). Los jobs y la exportación SHALL usar este registro.

#### Scenario: Auditoría de nivel
- **WHEN** un analista consulta el índice de calibración
- **THEN** puede reconstruir el SPL con que se simuló y con que debe presentarse cada oído de cada render

### Requirement: Una sola escala para ambos canales sin reescalar la señal
Ambos canales SHALL usar el mismo K, con nivel SPL por canal `K + 20·log10(rms_canal)`. Ningún procesador SHALL normalizar canales por separado ni dejar la salida reescalada: la salida degradada SHALL quedar en el mismo dominio digital que la entrada, salvo por el efecto de la pérdida simulada.

#### Scenario: ILD conservada
- **WHEN** una entrada tiene el canal izquierdo 6 dB por encima del derecho
- **THEN** los niveles SPL registrados por canal difieren en 6 dB con cualquier modo y procesador

### Requirement: Entradas silenciosas
Un canal con energía nula SHALL producir un canal de salida silencioso y marcarse como silencioso, sin niveles infinitos. En `presentation_level`, un render con ambos canales silenciosos SHALL dejar bloqueados sus jobs con un motivo explícito, porque K no está definido.

#### Scenario: Render silencioso con nivel de presentación
- **WHEN** ambos canales de un render son cero y el modo es `presentation_level`
- **THEN** sus jobs quedan `blocked` con el motivo `calibration_undefined_silent_input` y no se envían al backend

#### Scenario: Un canal silencioso
- **WHEN** solo el canal derecho es cero y el modo es `fixed_scale`
- **THEN** el canal derecho de salida es cero, el izquierdo se procesa con normalidad y el sidecar marca el derecho como silencioso

### Requirement: MSBG simula el nivel real
Con `msbg.level_handling: true_level` (por defecto), MSBG SHALL simular cada canal a su nivel SPL real según K, compensando el reescalado interno que iguala el RMS de los tramos activos al del archivo completo, y SHALL devolver la salida en el dominio digital de la entrada, escrita en float32. Con `legacy_active_rescale`, SHALL reproducir el comportamiento anterior (`equiv_0db_spl = K − 20`, sin compensación, subtipo de entrada), registrándolo.

#### Scenario: Voz con pausas
- **WHEN** un canal tiene un RMS de tramos activos 6 dB mayor que el del archivo completo y se procesa con `true_level` y perfil `nh_reference`
- **THEN** la salida no queda atenuada 6 dB respecto de la entrada, a diferencia de `legacy_active_rescale`

#### Scenario: Reproducción del comportamiento anterior
- **WHEN** se usa `legacy_active_rescale` con `fixed_scale` 90 dB y `outer_middle_ear: free_field`
- **THEN** el WAV es idéntico muestra a muestra al que producía la integración anterior para la misma entrada y perfil

### Requirement: Calibración registrada en el sidecar
El sidecar de cada job completado SHALL repetir el registro de calibración de su render (modo, P, K, Leq por canal, canales silenciosos, medida de nivel) y el `level_handling` aplicado cuando el procesador es MSBG.

#### Scenario: Sidecar autocontenido
- **WHEN** un analista lee solo el sidecar de un job completado
- **THEN** obtiene el SPL con que se simuló cada oído sin consultar la configuración ni el índice
