# Guía de calibración: datasets para redes neuronales

[Índice](../README.md) · [Guía de validación de simuladores](calibration-simulator-validation.md) · [Orchestrator](../../apps/orchestrator/README.md) · [Backend](../../apps/clarity-backend/README.md)

Esta guía describe cómo generar cientos de horas de tripletas alineadas (render sin procesar, MSBG y WHIS) cuya relación con el nivel físico sea conocida. La pérdida auditiva depende del nivel, así que el dataset debe cubrir un rango de niveles y conservar, para cada archivo, a qué dB SPL se simuló.

## 1. Configuración: rango de nivel reproducible

```yaml
experiment:
  random_seed: 42
hearing_degradation:
  enabled: true
  input_targets: [binaural_hrtf]
  hearing_profiles_path: ../hearing/training_profiles.yaml
  processors: [msbg, whis]
  calibration:
    mode: presentation_level
    presentation_level_range_db_spl: [55, 80]
  outer_middle_ear: itu
  msbg:
    level_handling: true_level
  whis:
    install_root: C:/Tesis/whis
    synth_method: dtvf
```

- El nivel P de cada render se elige de forma uniforme en `[min, max]` a partir de SHA-256 de `experiment.random_seed` y el `variant_id`. Es el mismo P en cualquier ejecución o reanudación y no depende del orden de los renders.
- El rango debe reflejar los niveles de uso esperados. Ejemplos: 55–80 dB SPL para voz conversacional a fuerte; 45–65 dB SPL para entornos tranquilos.
- Use `nh_reference` como perfil de control dentro del dataset.

## 2. El índice de calibración

`metadata/indexes/level_calibration.jsonl` tiene un registro por render:

| Campo | Significado |
|---|---|
| `presentation_level_db_spl` | P, el Leq binaural del render en dB SPL |
| `effective_scale_db_spl` | K, los dB SPL de RMS digital 1,0 para ese render |
| `channels.left/right.leq_db_spl` | SPL de cada oído (`K + 20·log10 rms`) |
| `silent_channels`, `status` | Canales nulos; un render totalmente silencioso no tiene K y sus jobs quedan `blocked` |
| `random_seed`, `configured`, `mode` | Lo necesario para reproducir P |

Los sidecars degradados repiten el registro (`calibration`), así que un archivo degradado se puede interpretar sin el índice.

## 3. Llevar todo el dataset a una escala absoluta común

Cada render tiene su propio K: los WAV no son comparables en nivel entre sí. Hay dos formas válidas de entregarlos a la red.

**a) Escala absoluta común.** Elija un `K_ref` para todo el dataset (por ejemplo 120 dB SPL, que deja margen) y multiplique cada versión del render por la misma constante:

```python
x_abs = x * 10 ** ((K - K_ref) / 20)   # a partir de ahí, RMS digital 1,0 ≡ K_ref dB SPL en todo el dataset
```

Aplique la misma constante a la versión sin procesar, a MSBG y a WHIS del mismo render. Guarde en float32, que ya es el formato de las salidas degradadas.

**b) Nivel como condicionamiento.** Deje los WAV como están (normalizados por render) y entregue a la red P, K o el Leq por oído como entrada adicional.

**No normalice archivo por archivo después de degradar:** ni por pico, ni por RMS, ni por LUFS de cada versión por separado.

- Borra la información de nivel que define la severidad simulada.
- Rompe la relación entre la versión sin procesar y la degradada (la atenuación de la pérdida desaparecería).
- Si se hace por canal, destruye la ILD.

Si necesita normalizar, calcule una sola ganancia por render (por ejemplo, a partir de la versión sin procesar) y aplíquela a las tres versiones.

## 4. Tripletas alineadas

Para cada render y perfil:

```text
output_audio/render/<output_type>/<render>.wav                          # sin procesar
output_audio/degraded/msbg/<output_type>/<perfil>/<render>.wav          # MSBG
output_audio/degraded/whis/<output_type>/<perfil>/<render>.wav          # WHIS
```

- Las tres tienen la misma frecuencia de muestreo y la misma longitud.
- Las degradadas tienen compensada la latencia constante de cada procesador (`processor.latency_compensation_samples` en el sidecar). Con `legacy_active_rescale` MSBG no compensa: no use ese modo para datasets nuevos.
- **MSBG no es de fase lineal.** Incluso con `nh_reference`, adelanta las bajas frecuencias respecto de las altas: 100–300 Hz unas 280 muestras, 300–1000 Hz unas 70 (a 44,1 kHz). Por eso la correlación cruzada con voz da unas −290 muestras aunque el impulso esté alineado. Si la red aprende un mapeo muestra a muestra entre la versión sin procesar y la de MSBG, esta dispersión forma parte del objetivo. WHIS con `nh_reference` es casi identidad (SNR 23,5 dB, desfase 0). Con pérdida, WHIS agrega un retardo de grupo propio del filtro (unas 20 muestras con una pérdida en pendiente).
- Use solo jobs con `status: completed`. El analizador (`output-analyzer analyze`) informa los estados por procesador. Lectura mínima:

```python
import json, pathlib
run = pathlib.Path("outputs/artifacts/<run_name>")
cal = {json.loads(l)["variant_id"]: json.loads(l) for l in (run / "metadata/indexes/level_calibration.jsonl").read_text().splitlines()}
for line in (run / "metadata/indexes/clarity_index.jsonl").read_text().splitlines():
    job = json.loads(line)
    if job["status"] in {"completed", "skipped"} and job["validation"]["ok"]:
        k = cal[job["variant_id"]]["effective_scale_db_spl"]
        # job["input_wav_path"] (sin procesar) y job["expected_output_wav_path"] (degradado) comparten K
```

## 5. Reanudar lotes largos

Cada job lleva una `processing_signature`: procesador, calibración configurada, semilla (con rango de nivel), `outer_middle_ear` y `level_handling` o `synth_method`. Un resultado solo se reutiliza si la firma y el K coinciden.

- Reanude con la misma configuración: los jobs completados se reutilizan y solo se procesan los pendientes o fallidos.
- Agregar `whis` a una corrida que solo tenía `msbg` planifica únicamente los jobs WHIS.
- Cambiar la semilla, el rango de nivel, `outer_middle_ear` o `synth_method` reprocesa todo lo afectado. No los cambie a mitad de un dataset.
- Actualizar WHIS en el mismo `install_root` no invalida resultados (los commits se registran en el sidecar). Use `runner.force_rerun: true` si quiere reprocesar.

## 6. Costo y particionado

Medido en un equipo Windows de 16 hilos (MATLAB R2026b, pyclarity 0.8.0):

| Procesador | Cómputo por segundo de audio estéreo | Costo fijo |
|---|---|---|
| MSBG | ≈1,1 s | ≈1,5 s por proceso (medición de latencia) |
| WHIS | ≈2,6 s | ≈10–15 s de arranque de MATLAB por manifiesto, más 1 s de sonda de latencia |

Horas de cómputo ≈ horas de audio × perfiles × (1,1 + 2,6) para `[msbg, whis]` en un solo proceso. Por ejemplo, 100 h de renders con 4 perfiles: 100 × 4 × 3,7 ≈ 1480 h de cómputo secuencial (≈1040 h de WHIS y ≈440 h de MSBG). En la práctica hay que paralelizar:

- Parta el dataset en varias corridas (`outputs.run_name` distinto, misma semilla y mismo rango) y ejecute sus handoffs en procesos paralelos. Cada manifiesto abre una sola sesión de MATLAB para todos sus jobs WHIS. El número de sesiones simultáneas lo limitan la licencia de MATLAB y los núcleos.
- Las corridas parciales se pueden repetir: la firma garantiza que la reanudación no rehaga lo completado.
- Mida el factor real en su equipo con una corrida pequeña antes de planificar el lote completo. Los tiempos se pueden calcular desde los registros de la sesión (`whis_work/matlab_session.log`, líneas "times RealTime").

## 7. Lista de verificación

- [ ] `calibration.mode: presentation_level` con `presentation_level_range_db_spl` y semilla fija.
- [ ] `outer_middle_ear` coherente con el punto de medición de las HRTF (ver la [guía de validación](calibration-simulator-validation.md#3-elegir-la-corrección-de-oído-externo-y-medio-outer_middle_ear)).
- [ ] `msbg.level_handling: true_level`.
- [ ] Perfiles con `compression_health` y sin umbrales negativos; `nh_reference` incluido.
- [ ] Escala común `K_ref` o nivel como condicionamiento; sin normalizar por archivo.
- [ ] Solo jobs `completed` y validados; tripletas con la misma longitud.
