# Guía de calibración: validación comparativa de simuladores (NH vs IH)

[Índice](../README.md) · [Guía de datasets para redes](calibration-nn-dataset.md) · [Orchestrator](../../apps/orchestrator/README.md) · [Backend](../../apps/clarity-backend/README.md)

Esta guía describe cómo presentar los mismos estímulos a oyentes normoyentes (NH) e hipoacúsicos (IH) para validar MSBG y WHIS. Los IH escuchan el render sin procesar. Los NH escuchan las versiones simuladas del mismo render (MSBG y WHIS) y el render sin procesar como control. Para que la comparación tenga sentido, cada versión debe llegar al tímpano al nivel físico con que se simuló.

## 1. Principio: una sola escala por render

Para cada render, el orquestador calcula una escala K, los dB SPL que corresponden a RMS digital 1,0. K se registra en `metadata/indexes/level_calibration.jsonl` y se copia sin cambios en cada job de ese render.

- MSBG y WHIS simulan cada oído a `K + 20·log10(rms_oído)`. Ambos oídos comparten K, de modo que la diferencia interaural de nivel (ILD) del render se conserva.
- Ningún procesador reescala la señal. Las salidas degradadas quedan en el dominio digital del render y en float32 (sin recorte).
- En la reproducción, el canal c recibe la ganancia `g_c = K − k_play_db_c`. Es la misma para la versión sin procesar y para todas las degradadas de ese render, así que cada una suena al SPL con que se simuló.

## 2. Medir la cadena de reproducción (`k_play_db`)

`k_play_db_c` son los dB SPL que produce RMS digital 1,0 en el canal c de la cadena real: interfaz, amplificador, audífonos y punto de medición.

1. Fije el volumen de la interfaz y del amplificador y no lo cambie durante la sesión.
2. Monte los audífonos en un acoplador u oído artificial (por ejemplo, un simulador IEC 60318-4) o en un simulador de cabeza.
3. Reproduzca por cada canal un tono de 1 kHz a −26 dBFS RMS (amplitud de pico `10^(−26/20)·√2`). Es la misma convención del tono de calibración de WHIS.
4. Mida el nivel `L` en dB SPL y calcule `k_play_db = L + 26`. Por ejemplo, si el tono mide 74,0 dB SPL a la izquierda y 72,0 a la derecha, entonces `k_play_db` vale 100,0 y 98,0.
5. Repita la medición si cambia el volumen, la interfaz, los audífonos o su colocación.

Escriba el resultado en un archivo como [`apps/orchestrator/examples/playback_calibration.yaml`](../../apps/orchestrator/examples/playback_calibration.yaml). `max_spl_db` es el nivel máximo que ningún canal de ninguna versión debe superar:

```yaml
playback_id: lab_hd650
k_play_db: {left: 100.0, right: 98.0}
max_spl_db: 85.0
```

La ecualización de los audífonos (HpTF) queda fuera de este flujo. Si se usa, debe aplicarse por igual a todas las versiones.

## 3. Elegir la corrección de oído externo y medio (`outer_middle_ear`)

Ambos simuladores convierten el SPL de la señal en la excitación de la cóclea a través de una transferencia de oído externo y medio. Esta debe corresponder al punto donde se midieron las HRTF del render y ser la misma para los dos procesadores:

| Punto de medición de las HRTF | `outer_middle_ear` |
|---|---|
| Entrada del canal auditivo bloqueado (punto de referencia del oído, ERP) | `itu` (recomendado; nativo en MSBG y WHIS) |
| Tímpano | `eardrum` (en MSBG deja solo la transferencia de oído medio) |
| Señal sin HRTF, referida a campo libre frontal | `free_field` |
| Señal sin HRTF, referida a campo difuso | `diffuse_field` |

Confirme el punto de medición en la documentación del set de HRTF (`assets/hrtf`). `outer_middle_ear` es obligatorio cuando se usa `whis`.

## 4. Configurar la calibración de la corrida

Para estímulos de escucha conviene `presentation_level` con un nivel fijo P, el Leq binaural sin ponderar de cada render. Así todos los estímulos se presentan al mismo nivel, sea cual sea la ganancia digital del render:

```yaml
hearing_degradation:
  enabled: true
  input_targets: [binaural_hrtf]
  hearing_profiles_path: ../hearing/participants.yaml
  processors: [msbg, whis]
  calibration:
    mode: presentation_level
    presentation_level_db_spl: 70
  outer_middle_ear: itu
  msbg:
    level_handling: true_level
  whis:
    install_root: C:/Tesis/whis
    synth_method: dtvf
```

- **Cómo elegir P:** debe ser audible para el oyente IH (bien por encima de sus umbrales en las bandas del habla) y quedar bajo `max_spl_db` en ambos oídos, incluso con la ILD del render. El SPL estimado de cada oído está en `level_calibration.jsonl` (`channels.<oído>.leq_db_spl`).
- **`fixed_scale`:** sirve solo si se quiere conservar la ganancia relativa entre renders. Con este modo, el limitador de pico del render puede alterar la escala.
- **Medida de nivel:** P es el Leq del archivo completo (`rms_unweighted_full_file`), así que incluye silencios y la cola de reverberación. No es el nivel de habla activa.

## 5. Perfiles por participante y salud de compresión

Cree un perfil por oyente IH con su audiograma en 125–8000 Hz (125 es opcional; si falta, WHIS copia 250 y lo registra en `extrapolated_bands`). WHIS además necesita `compression_health` (0 = compresión coclear perdida, 1 = sana), que el audiograma no determina. Barra varios valores como perfiles separados:

```yaml
profiles:
  - hearing_profile_id: p07_ch0
    ears:
      left:  {loss_db_by_band: {250: 20, 500: 25, 1000: 35, 2000: 50, 4000: 60, 8000: 65}, compression_health: 0}
      right: {loss_db_by_band: {250: 20, 500: 30, 1000: 40, 2000: 55, 4000: 65, 8000: 70}, compression_health: 0}
  - hearing_profile_id: p07_ch05
    # mismo audiograma, compression_health: 0.5
  - hearing_profile_id: p07_ch1
    # mismo audiograma, compression_health: 1
  - hearing_profile_id: nh_reference
    ears:
      left:  {loss_db_by_band: {125: 0, 250: 0, 500: 0, 1000: 0, 2000: 0, 4000: 0, 8000: 0}, compression_health: 1}
      right: {loss_db_by_band: {125: 0, 250: 0, 500: 0, 1000: 0, 2000: 0, 4000: 0, 8000: 0}, compression_health: 1}
```

- WHIS puede ajustar la salud de compresión pedida para que sea coherente con el umbral de cada banda. El sidecar registra los valores pedidos y aplicados (`compression_health_requested`, `compression_health_applied`) y el reparto de la pérdida en componente activa y pasiva (`pin_loss_db_act`, `pin_loss_db_pas`).
- MSBG no tiene equivalente: ignora `compression_health` y la banda 125, y los lista en `ignored_profile_fields`. Los perfiles `pXX_ch*` producen la misma salida MSBG.
- WHIS no admite umbrales negativos.

## 6. Control `nh_reference` y diferencias conocidas entre simuladores

`nh_reference` (0 dB HL, compresión 1) separa el efecto de la pérdida del efecto propio de cada simulador. Valores medidos con `itu` y K = 100 dB (MATLAB R2026b, WHISv302/GCFBv234, pyclarity 0.8.0):

| Medida con `nh_reference` | MSBG | WHIS |
|---|---|---|
| Ganancia de banda ancha, ruido blanco | +0,22 dB | −0,61 dB |
| Ganancia de banda ancha, voz | −0,65 dB | +0,00 dB |
| Banda a −3 dB (ruido blanco) | 75–18077 Hz | 0–19811 Hz |
| Alineación del impulso | ±0 muestras | ±0 muestras |
| Desfase de correlación cruzada con voz | −293 muestras | 0 |
| SNR salida/entrada con voz | −0,8 dB (no idéntica) | 23,5 dB |

- **Ancho de banda:** al comparar las tres versiones, aplique el mismo pasa-bajos (por ejemplo, 16 kHz) y, si interesa, el mismo pasa-altos (100 Hz). Así la diferencia de ancho de banda entre MSBG y WHIS no actúa como pista.
- **Dispersión temporal de MSBG:** aun con 0 dB HL, el banco gammatone de MSBG adelanta las bajas frecuencias respecto de las altas (100–300 Hz unas 280 muestras, 300–1000 Hz unas 70, por encima de 3 kHz alineadas a 44,1 kHz). La compensación de latencia alinea el pico del impulso, pero no la voz. Para un oyente es un efecto de fase pequeño; para comparar formas de onda hay que considerarlo.
- **Retardo de grupo en WHIS con pérdida:** los filtros de fase mínima de WHIS introducen, con pérdida, un retardo de grupo que depende de la frecuencia (unas 20 muestras con una pérdida en pendiente). Es parte del filtro simulado y no se compensa.

## 7. Flujo completo

```sh
# 1. Render de las escenas
uv run --project apps/orchestrator acoustic-orchestrator render-static configs/experiments/<experimento>.yml
# 2. Degradación con ambos simuladores (un job por render, perfil y procesador)
uv run --project apps/orchestrator acoustic-orchestrator clarity-handoff configs/experiments/<experimento>.yml --submit
# 3. Exportación a la cadena calibrada (misma ganancia para las tres versiones)
uv run --project apps/orchestrator acoustic-orchestrator export-presentation outputs/artifacts/<run_name> --playback lab_hd650.yaml
# 4. Revisión de metadatos y estados por procesador
uv run --project apps/output-analyzer output-analyzer analyze outputs/artifacts/<run_name>
```

La exportación escribe float32 en `output_audio/presentation/<playback_id>/`:

- `unprocessed/<output_type>/<render>.wav`: el render sin procesar.
- `<msbg|whis>/<output_type>/<perfil>/<render>.wav`: las versiones degradadas.

Cada WAV va acompañado de un JSON con K, `k_play_db`, la ganancia y el SPL estimado por oído. Si alguna versión de un render superaría `max_spl_db` o recortaría, ese render no se exporta (`exceeds_max_spl` o `would_clip` en `export_summary.json`). Baje P o el volumen de reproducción y vuelva a medir `k_play_db`.

## 8. Qué escucha cada grupo

| Grupo | Estímulos | Propósito |
|---|---|---|
| IH (participante pXX) | `unprocessed` | Respuesta real con pérdida auditiva |
| NH | `msbg/.../pXX_*` y `whis/.../pXX_*` | Respuesta simulada del mismo participante; un bloque por `compression_health` en WHIS |
| NH | `unprocessed` | Control sin pérdida |
| NH (opcional) | `msbg/.../nh_reference` y `whis/.../nh_reference` | Efecto propio de cada simulador sin pérdida |

Todos los estímulos de un mismo render deben presentarse con la misma cadena y el mismo `playback_id`.

## 9. Qué registrar

- `playback_id`, `k_play_db` por canal, fecha de la medición, acoplador usado y `max_spl_db`.
- Corrida (`run_name`), `processing_signature` y versiones de los simuladores: pyclarity, WHISv302/GCFBv234 con sus commits y la release de MATLAB. Todo está en cada sidecar degradado.
- Por estímulo: render, versión, perfil, `compression_health` pedida y aplicada, P, K y SPL por oído (sidecar de exportación).
- Respuestas de cada oyente asociadas al archivo exportado exacto.

## 10. Tiempos de cómputo

Medidos en un equipo Windows de 16 hilos con escenas de 3 s:

- MSBG: ≈1,1 s por segundo de audio estéreo.
- WHIS: ≈2,6 s por segundo de audio estéreo (1,26–1,35 veces el tiempo real por canal), más ~10–15 s de arranque de MATLAB por manifiesto.

Para unas decenas de estímulos por participante, el costo es de minutos. La guía de [datasets para redes](calibration-nn-dataset.md) trata el caso de cientos de horas.
