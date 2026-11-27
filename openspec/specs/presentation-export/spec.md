# presentation-export Specification

## Purpose

Exportar, para una cadena de reproducción calibrada, las versiones sin procesar y degradadas de cada render con una misma ganancia, de modo que se presenten al nivel físico con que se simularon y puedan compararse respuestas de oyentes normoyentes e hipoacúsicos.

## Requirements

### Requirement: Calibración de reproducción declarada
El comando `export-presentation <run_root> --playback <archivo>` SHALL leer una calibración de reproducción con `playback_id` (path-safe), `k_play_db` por canal (`left`, `right`: dB SPL para RMS digital 1.0 en la cadena medida) y `max_spl_db`. La documentación SHALL indicar cómo obtener `k_play_db` a partir del tono de 1 kHz a −26 dBFS RMS (`k_play_db = L_medido + 26`). Los valores no finitos o ausentes SHALL rechazarse antes de escribir nada.

#### Scenario: Calibración incompleta
- **WHEN** el archivo de reproducción no declara `k_play_db.right`
- **THEN** el comando falla nombrando el campo y no escribe ningún archivo

### Requirement: Misma ganancia para todas las versiones de un render
Para cada render con calibración registrada, la exportación SHALL aplicar al canal c la ganancia `g_c = K_render − k_play_db_c` dB a la versión sin procesar y a cada salida degradada completada de ese render. El mismo render SHALL exportarse siempre con la misma ganancia por canal, sea cual sea su versión.

#### Scenario: Tres versiones del mismo render
- **WHEN** un render con K = 95 dB tiene salidas `msbg` y `whis` completadas y `k_play_db` vale 100 dB en ambos canales
- **THEN** la versión sin procesar, la de MSBG y la de WHIS se exportan con −5 dB en ambos canales

#### Scenario: Canales con distinta sensibilidad
- **WHEN** `k_play_db.left` es 100 y `k_play_db.right` es 98
- **THEN** el canal derecho de cada versión recibe 2 dB más que el izquierdo

### Requirement: Layout de exportación
Las versiones SHALL escribirse en `output_audio/presentation/<playback_id>/unprocessed/<output_type>/` y `output_audio/presentation/<playback_id>/<procesador>/<output_type>/<hearing_profile_id>/`, con el basename del render, en float32 y con un sidecar JSON que registre el render, la versión, K, `k_play_db`, la ganancia por canal y el SPL estimado por canal.

#### Scenario: Exportación de una corrida
- **WHEN** se exporta una corrida con `playback_id: lab_hd650`
- **THEN** cada versión está bajo `output_audio/presentation/lab_hd650/` en su subdirectorio, con su sidecar

### Requirement: Límites de seguridad sin recorte
Una versión SHALL NOT exportarse si su SPL estimado por canal (`K_render + 20·log10(rms_c)` para la versión sin procesar, y el equivalente medido para las degradadas) supera `max_spl_db`, o si su pico tras la ganancia supera 1.0. En ese caso, todas las versiones de ese render SHALL omitirse y registrarse con el motivo, en vez de recortarse o atenuarse por separado.

#### Scenario: Render demasiado fuerte
- **WHEN** la versión sin procesar de un render alcanzaría 92 dB SPL y `max_spl_db` es 85
- **THEN** ninguna versión de ese render se exporta y el resumen indica `exceeds_max_spl`

#### Scenario: Pico tras la ganancia
- **WHEN** la ganancia llevaría el pico de la salida WHIS de un render a 1.2
- **THEN** ninguna versión de ese render se exporta y el resumen indica `would_clip`

### Requirement: Solo renders completos y calibrados
La exportación SHALL incluir solo renders con registro de calibración y SHALL exportar únicamente las salidas degradadas `completed` y válidas. Un render sin salidas degradadas válidas SHALL exportar igualmente su versión sin procesar. El comando SHALL resumir lo exportado y lo omitido por motivo.

#### Scenario: Salida WHIS fallida
- **WHEN** el job WHIS de un render falló y el MSBG se completó
- **THEN** se exportan la versión sin procesar y la de MSBG, y el resumen registra la ausencia de WHIS para ese render
