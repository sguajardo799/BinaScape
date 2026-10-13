# Contratos e interoperabilidad

[Índice](../README.md) · [Flujo](data-flow.md) · [Auditoría y evidencias](../maintenance/documentation-audit.md)

Esta referencia describe comportamiento observado en productores/validadores, no crea schemas operativos nuevos. Base develop 05f9b27; las diferencias HEAD del analizador están identificadas.

## Versiones por documento

| Artefacto | Productor | Consumidor y restricción |
|---|---|---|
| Experimento YAML | Usuario/configuración | Orchestrator config loader/models/validator; solo modo estático válido |
| Scene y runtime render JSON | `scene_builder.build_static_manifest`, `matlab_runner` | Orchestrator emite 3.0; MATLAB admite 1.0/2.0/3.0 y normaliza ausencia a 1.0 |
| Sidecar render JSON | MATLAB `export_render_metadata` | Versión de escena exportada; flujo actual 3.0 |
| Jobs Clarity JSONL | `clarity_handoff`, constante 1.0 | Backend consume campos/paths; no valida schema_version del job |
| Sidecar degradado JSON | Clarity `SCHEMA_VERSION` 1.0 | Resultado completed/failed; HEAD analyzer admite exactamente 1.0 |
| Índices JSONL | Pipeline/output_index y muestreo | Contratos de sus campos e identidades; no exigirles versión 3.0 de escenas |

El analizador develop lee por campos sin validador estricto de versiones. HEAD admite exactamente el string 3.0 para escenas y render, y 1.0 para degradados; registra versiones ausentes/incompatibles como incidencias y conserva observaciones independientes válidas. No asume compatibilidad futura.

## Configuración y ejemplos

El YAML describe un espacio de muestreo, no una escena lista para MATLAB. Rutas relativas se resuelven contra el directorio del YAML. `room_sampling.reverberation` sustituye al antiguo límite `max_rt30_s`; el loader rechaza raíces legacy `outputs.rendered_wav_dir` y `outputs.render_metadata_dir`.

Modelos y validadores son la evidencia de claves, tipos, rangos y relaciones aceptados. Los ejemplos de [configuración](../../configs/experiments/) y [escenas locales](../../apps/matlab/example/) son ilustrativos: algunos conservan contratos legacy o dinámicos y necesitan assets específicos. No modificarlos ni llamarlos normativos por existir.

El pool de fuentes usa WAV (extensión sin distinguir mayúsculas) e ignora sidecars. Pool vacío aborta con su ruta. Con `source_sampling.timing.total_duration_s`, lectura/recorte requieren PCM compatible con Python `wave`, incluso sin offsets; headers inválidos/no soportados no se omiten silenciosamente.

## Geometría, superficies y poses

El productor actual emite `room.geometry.type`, `footprint_vertices_m`, `height_m`, `wall_ids` y trazabilidad `generated_from`. La huella es autoritativa; `shoebox`, `trapezoid` y `l_shape` son prismas de altura constante. El área usa el polígono real y el volumen área × altura. La envolvente rectangular no reemplaza el área de una L o un trapecio.

Schemas 2/3 emplean paredes `wall_###`, floor y ceiling; 3.0 añade superficies acústicas con coeficientes efectivos, tratamientos y `reverberation_sampling`. El legacy 1.0 mantiene shoebox. Los IDs de pared son canónicos, no direcciones cardinales. MATLAB valida y construye el prisma con `setModelToFaces`; no vuelve a muestrear la geometría.

Marco público: `[x,y,z]` en metros, Y vertical, huella `[x,z]`. Scene y runtime JSON conservan poses públicas. MATLAB `transform_schema2_pose_to_raven` refleja Z en posición, view_vector y up_vector para schemas 2/3. `summary.sources` se exporta desde fuentes adaptadas RAVEN; no mezclar esas coordenadas directamente con las de la escena.

HEAD analyzer prioriza poses del manifiesto; el fallback documentado desde sidecar RAVEN + ITA refleja Z de vuelta al marco público y declara procedencia. Si no se establece marco/orientación, excluye los cálculos espaciales afectados; yaw ausente no es cero. develop carece de esta conversión ampliada.

## Reverberación y tratamientos

El sampler estratifica el RT30 estimado mediante bins de igual ancho, cuotas balanceadas y tratamientos virtuales versionados en paredes/cielo, conservando scattering; el piso no recibe tratamiento. Usa el presupuesto compartido de intentos de escena y puede seleccionar un fallback válido en rango. Las cuotas son diagnósticas, no garantía de uniformidad exacta ni de uniformidad del T30 posterior de RAVEN.

La estimación Sabine conserva diez bandas de octava, pero su media/clasificación usa 125, 250, 500, 1000, 2000, 4000 y 8000 Hz. RAVEN informa diez bandas 31.5–16000 Hz; `mean_t30_s` requiere diez valores finitos positivos o queda vacío/unavailable. Estas medias no son directamente comparables y no provienen de mediciones del WAV.

HEAD analyzer alinea frecuencias explícitas y consolida variantes por escena/banda con tolerancia `abs(a-b) <= max(1e-6 s, 1e-6 * max(abs(a), abs(b)))` sobre todas las parejas válidas. Un conflicto excluye solo la banda RAVEN afectada y su par; Sabine y otras bandas permanecen disponibles. Es equivalencia numérica fija, no precisión certificada del simulador. Los promedios comparables reutilizan el mismo conjunto de pares válidos, sin interpolar ni ponderar una sala por HRTF.

## Clarity y errores

`expected_output_wav_path` y `expected_output_metadata_path` son autoritativos. Paths relativos de jobs se resuelven respecto del directorio de trabajo del proceso, no del JSONL. `backend_invocation` sirve al orquestador; no se copia al resultado por el backend. El WAV degradado conserva basename y su sidecar el stem.

El catálogo valida perfiles únicos, dos oídos y pérdidas finitas. MSBG exige exactamente 250, 500, 1000, 2000, 4000 y 8000 Hz por oído, WAV estéreo 44100 Hz; bandas extras o ausentes fallan. El procesamiento preserva longitud, sample rate y subtipo original.

Errores de procesamiento/perfil capturados escriben metadata failed y permiten continuar; un lote con fallos sale 1. JSONL malformado, campos estructurales ausentes o problemas al preparar/escribir paths pueden abortar fuera de esa recuperación. No prometer tolerancia universal.

HEAD analyzer distingue incidencias de metadatos de fallos operativos: metadatos incompletos pueden dar reporte parcial o sin observaciones, mientras OSError de raíz/escritura produce error de CLI. Su reporte no verifica audio ni SNR medido, clipping, LUFS o calidad acústica. Las exportaciones conservan denominadores y procedencia; la [guía local](../../apps/output-analyzer/README.md) lista poblaciones y formatos.
