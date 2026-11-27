# PRD - compatibilidad actual y análisis de metadatos en Output Analyzer

> Nota editorial (13-10-2026): **CLOSED / IMPLEMENTED** por decision del responsable. Los estados de borrador, Hoy/Despues, criterios futuros y siguientes diffs del texto original son antecedentes historicos, no backlog vigente. Consulte [el indice PRD](README.md) y [la auditoria](../maintenance/documentation-audit.md) para contratos actuales, evidencias y diferencias entre revisiones. El contenido historico se preserva.

Estado: cuatro diffs E2E y ampliación RT30 en la vista por sala implementados
Creado: 12.10.2026
Actualizado: 13.10.2026 — requisito adicional de RT30 por sala implementado y validado.
Alcance: actualizar `apps/output-analyzer` para el contrato vigente de metadatos y ampliar sus análisis. 

## Resumen

El problema original del analizador conservaba supuestos anteriores a los cambios de geometría y serialización: interpreta salas mediante `dimensions_m`, puede fallar ante una fuente única serializada por MATLAB como objeto y cuenta variantes HRTF como escenas. Además, una comparación directa de los promedios RT30 de Sabine y RAVEN mezcla bandas diferentes.

El resultado esperado es un reporte único, basado exclusivamente en metadatos de la versión vigente, con parámetros generales agrupados, plantas de fuentes y receptor separadas por geometría y contadores explícitos de incidencias. Una entrada incompleta no debe impedir analizar el resto del run ni convertirse en un cero artificial.

## Decisiones confirmadas

1. Soportar exclusivamente el contrato vigente: `schema_version: "3.0"` para manifiestos de escena y sidecars de render. No incorporar compatibilidad con versiones anteriores ni asumir compatibilidad automática con versiones futuras.
2. Agrupar todos los parámetros generales en una sola sección del reporte; retirar de su presentación el bloque verboso `render_configuration`, conservando los datos de auditoría exportados.
3. Separar por `shoebox`, `l_shape` y `trapezoid` los gráficos de posiciones de fuentes y receiver/listener. Los demás análisis permanecen conjuntos; no crear tres reportes independientes. Cada grupo espacial tendrá un carrusel manual por escena, sin autoplay.
4. Leer exclusivamente metadatos; el contenido de los WAV queda fuera del alcance.
5. Reportar datos faltantes, incompatibles o malformados mediante contadores y continuar con las métricas calculables. Las diferencias RT30 dentro de la tolerancia numérica específica no son errores de metadatos.
6. Toda imagen debe incluir su propia leyenda visible; no depender del texto circundante del HTML para interpretar símbolos, colores o series.

## Necesidad del usuario y objetivos

Como responsable del dataset, necesito conocer su cobertura real, diversidad espacial y consistencia acústica sin volver a ejecutar el render ni inspeccionar audio.

- O1. Recuperar la generación del reporte con salidas actuales, incluida la serialización singleton de MATLAB.
- O2. Distinguir escenas únicas, variantes HRTF, fuentes únicas y registros degradados para evitar sesgos por duplicación.
- O3. Representar fielmente plantas rectangulares, cóncavas y trapezoidales, con coordenadas coherentes.
- O4. Permitir interpretar cada gráfico con su unidad, población válida, exclusiones y procedencia.
- O5. Facilitar la detección de faltantes, uniones inconsistentes y sesgos de muestreo desde los metadatos disponibles.

## Evidencia y referencias

La exploración del run `outputs/e2e_raven_rt30_binaural_20260924` encontró 100 manifiestos de escena, 200 sidecars de render y una mezcla de 32 shoebox, 32 L y 36 trapezoides. De los sidecars, 190 serializan las fuentes como lista y 10 como objeto. Un caso reproducible es `output_audio/render/binaural_hrtf/scene_static_0017__binaural_hrtf__1__render.json`.

| Evidencia | Referencia del repositorio | Implicación |
|---|---|---|
| Emisión de schema 3.0 y geometría poligonal | `apps/orchestrator/src/acoustic_orchestrator/experiment/scene_builder.py` | El contrato emitido es la referencia vigente; los PRD antiguos no sustituyen al productor actual. |
| Supuestos de dimensiones y fuentes iterables; un registro por sidecar | `apps/output-analyzer/src/output_analyzer/loader.py` | Corregir lectura, geometría y unidades de conteo. |
| Gráficos espaciales basados en dimensiones rectangulares | `apps/output-analyzer/src/output_analyzer/report.py` | Reemplazar la interpretación espacial anterior. |
| Conversión pública a RAVEN `[x,y,z] -> [x,y,-z]` | `apps/matlab/core/transform_schema2_pose_to_raven.m`, `build_sources_from_config.m` | No combinar posiciones de marcos distintos. |
| RAVEN informa diez bandas entre 31.5 y 16000 Hz | `apps/matlab/core/build_room_reverberation_summary.m` | Alinear frecuencias antes de comparar con Sabine. |
| Contrato geométrico y vértices autoritativos | `docs/PRD/add-non-rectangular-rooms.md` | Mantener área y paredes reales, no las del bounding box. |

## Alcance y no objetivos

Entradas: manifiestos de escena, sidecars de render, metadatos degradados e índices existentes del run. Incluir `metadata/indexes/scene_sampling_failures.jsonl` cuando exista. Los contratos de índices y metadatos degradados se validan según sus productores actuales; no exigirles el número 3.0 de otro tipo de documento.

Conservar la ejecución de un análisis por directorio y la entrega de HTML autocontenido, gráficos y datos normalizados JSON/JSONL/CSV. El contenido exportado debe permitir auditar los totales y gráficos sin abrir el HTML.

Fuera de alcance: lectura o comprobación del contenido WAV; RMS, clipping, LUFS, espectros, SNR medido o métricas binaurales derivadas del audio; reconstrucción de ganancias previas a la normalización sin trazabilidad; rerender; cambios en MATLAB, Clarity o el esquema del productor; migración de datos históricos; animación automática, visualización 3D o simulación interactiva; un nuevo framework de interfaz. Se permite navegación manual local entre plantas mediante controles accesibles y JavaScript embebido, manteniendo el HTML autocontenido.

## Organización del reporte

1. **Parámetros generales**: identificación del run, versión soportada y observada, backend, tasas de muestreo, configuración resumida disponible (sin el bloque `render_configuration` en HTML) y totales de escenas, variantes, fuentes y degradaciones. Mostrar valores distintos y sus frecuencias cuando un parámetro varíe; nunca presentar el primer valor como común a todo el run. Incluir aquí la distribución por tipo de sala, dimensiones envolventes, altura, área real, volumen y HRTF.
2. **Posiciones por geometría**: tres grupos, shoebox, L y trapezoidal, cada uno con fuentes y receptor/listener claramente diferenciados, leyenda por evento y un carrusel manual de plantas por escena. Mostrar el run, `scene_id`, posición actual/total y variantes HRTF asociadas; navegar no cambia poblaciones ni duplica plantas por HRTF.
3. **Acústica y fuentes**: distribuciones globales de distancia, DoA, elevación, RT30, eventos y parámetros declarados de fuentes.
4. **Cobertura y calidad de metadatos**: contadores de incidencias, uniones, cobertura de HRTF, muestreo y degradación.

No repetir los parámetros generales en los tres grupos espaciales. Un grupo sin datos muestra su estado vacío y población; no una gráfica ficticia.

## Requisitos funcionales

### RF1. Lectura del contrato vigente — P0

- Admitir `sources` como lista o como objeto único donde el productor MATLAB vigente lo serialice así; ambas formas deben producir la misma interpretación semántica. Una cadena u otra estructura inválida es una incidencia, no una fuente iterable.
- Leer la estructura vigente de `summary`; no mantener rutas legacy como estrategia de compatibilidad.
- Validar tipos, finitud, dimensiones de vectores e identificadores antes de calcular. Preservar ceros válidos de `gain_db`, `start_time_s` y coordenadas.
- Los documentos con versión explícita distinta de la soportada quedan fuera del análisis semántico de ese documento y aumentan el contador de esquema no soportado. Una versión ausente aumenta el contador de campo requerido faltante; no se infiere desde la ruta.
- La ausencia o invalidez de un documento no invalida otros documentos independientes válidos de la misma escena.

### RF2. Identidades, uniones y denominadores — P0

- Escena: identidad `scene_id`; variante de render: escena más identidad HRTF vigente; fuente única: escena más `source_id`. Conservar también los registros por variante para analizar cobertura.
- Informar por separado escenas descubiertas válidas, escenas con render, variantes de render únicas, fuentes únicas y filas fuente-variante. Nunca llamar «escenas» al número de sidecars.
- Unir mediante identidades explícitas y verificar concordancia con los índices cuando estén disponibles. No asignar silenciosamente la primera HRTF del manifiesto a un sidecar sin identidad.
- Las distribuciones de geometría, receptor, fuentes y eventos cuentan cada escena o fuente una sola vez. Las distribuciones HRTF cuentan variantes; los perfiles degradados cuentan registros degradados únicos según el contrato vigente.
- Duplicados equivalentes no incrementan poblaciones; los conflictos entre registros de igual identidad se contabilizan y excluyen de la métrica afectada, sin elegir silenciosamente uno.
- Cada resultado declara unidad de observación, `n` válido y `n` excluido. Para totales esperados se usa solo configuración o índices autoritativos disponibles; sin esa información, se informa «esperado no disponible».

### RF3. Geometría y posiciones — P0

- Usar `room.geometry.type`, `footprint_vertices_m`, `height_m` y `wall_ids`. Los vértices son autoritativos; `generated_from` es trazabilidad.
- Calcular área del polígono, incluida la concavidad L, y volumen como área por altura. Las extensiones X/Z pueden mostrarse como dimensiones de la envolvente, nunca como área real de una sala no rectangular.
- Usar el marco público: posición `[x,y,z]`, Y vertical y planta `[x,z]`, en metros. Priorizar las poses del manifiesto. Una posición del sidecar solo puede usarse tras una conversión documentada desde el marco RAVEN y con procedencia visible; si no puede establecerse el marco, excluir el dato espacial y contabilizarlo.
- Mostrar plantas exactas por escena en un carrusel manual dentro de cada grupo de geometría, con proporción de ejes igual, contorno, identificador, receptor y fuentes. Una tarjeta corresponde a una escena/realización dentro del run (el «experimento» navegable), no a cada variante HRTF. Incluir controles Anterior/Siguiente y selección directa por `scene_id`, estado «i de N», foco/etiquetas accesibles y navegación por teclado; deshabilitar extremos, sin salto automático ni autoplay. Un grupo vacío muestra estado vacío; uno con una escena deshabilita ambos controles. Mostrar `hrtf_id`/`variant_id` asociados como contexto y trazabilidad, sin producir tarjetas duplicadas. La selección afecta solo a su grupo de geometría. Conservar offsets y concavidades; un punto en el recorte de una L no está dentro de la sala.
- Los agregados espaciales opcionales deben declarar su normalización y no dibujar una planta única como si representara todas las salas. No superponer posiciones de distintas salas sobre un contorno arbitrario ni duplicarlas por HRTF.
- Calcular distancia fuente-receptor, azimut global, DoA relativo a la orientación válida del receptor y elevación en un marco común. No sustituir orientación ausente por cero. Si dos posiciones coinciden, la distancia cero es válida y los ángulos indefinidos se excluyen.
- Incorporar contadores de puntos fuera de huella/altura y distribuciones de distancia mínima a paredes reales. Comparar con márgenes configurados solo cuando estén disponibles; no asumir un umbral universal.

### RF4. RT30 comparable — P0

- Mostrar por separado RT30 estimado por Sabine y T30 informado por RAVEN, con su método, unidad y población. No presentarlos como medidas extraídas del WAV.
- Emparejar por escena y frecuencia central explícita en Hz. No alinear por índice, interpolar bandas ausentes ni comparar directamente promedios sobre conjuntos diferentes.
- En el run de referencia, comparar las siete bandas comunes: 125, 250, 500, 1000, 2000, 4000 y 8000 Hz. Mantener las bandas adicionales de RAVEN en su distribución individual.
- Mostrar diferencias RAVEN menos Sabine por banda y resumen sobre el mismo conjunto de bandas válidas, con número de pares. Los valores no finitos, no positivos, sin frecuencia o con correspondencia ambigua se excluyen del par y se cuentan.
- Evitar ponderar una sala varias veces por HRTF. Consolidar por escena y frecuencia después de deduplicar identidades de variante. **Esta revisión sustituye la exclusión de una escena entera ante cualquier diferencia entre variantes.** Evaluar equivalencia de RT30 con tolerancia propia: `abs(a-b) <= max(1e-6 s, 1e-6 * max(abs(a), abs(b)))`. Verificar todas las parejas válidas de variantes, no encadenar equivalencias ni reutilizar la tolerancia genérica de geometría/identidades. La tolerancia es numérica, no un umbral de diferencia acústicamente aceptable: la parte absoluta está en segundos y la relativa es adimensional. Implementar constantes RT30 con nombre (`RT30_ABS_TOL_S`, `RT30_REL_TOL`), compartidas por lector/reporte, sin ajuste automático por run ni nuevo parámetro CLI en este diff. La decisión de ingeniería fija resolución de equivalencia de un microsegundo y una parte por millón para este consumidor; el E2E verifica el criterio, no define una tolerancia como múltiplo de su máximo observado. Esto no constituye una precisión certificada del simulador: conservar valores/dispersión originales y revisar las constantes explícitamente si otros fixtures/runs o documentación del productor lo contradicen. Probar las fronteras con valores sintéticos de 0.1, 1 y 10 s, además del run; no aumentar epsilon hasta eliminar todos los avisos. Documentar constantes/unidades en las exportaciones.
- Para variantes equivalentes, usar su media aritmética por escena/frecuencia como estimación consolidada, conservar mínimo, máximo, dispersión, `n_variants` contribuyentes y procedencia. El resultado aporta una sola observación por escena/frecuencia. Duplicar un documento con la misma identidad no cambia el resultado. Las diferencias dentro de tolerancia no generan `rt30_conflict`, `rt30_not_comparable` ni estado parcial por sí mismas.
- Si existe diferencia por encima de tolerancia, emitir `rt30_conflict` con escena, frecuencia, variantes y magnitud; excluir únicamente esa banda de las distribuciones/comparaciones consolidadas RAVEN dependientes. Conservar las otras bandas válidas, los datos individuales y Sabine independiente. Bandas ausentes o inválidas afectan solo al par correspondiente; informar cuántas variantes válidas soportan cada consolidación sin inventar valores para las restantes. No atribuir una diferencia al efecto físico HRTF sin evidencia adicional.
- Las distribuciones individuales y consolidadas deben distinguir sus unidades: variante/frecuencia para auditoría y escena/frecuencia para comparación. La igualdad o equivalencia numérica no prueba que el simulador sea determinista.

### RF5. Incidencias y continuidad — P0

| Incidencia | Unidad del contador | Exclusión |
|---|---|---|
| Esquema no soportado | Documento | Análisis semántico de ese documento |
| JSON ilegible o estructura raíz incorrecta; JSONL inválido | Archivo o línea, respectivamente | Documento o línea afectados |
| Campo requerido ausente o valor malformado | Entidad y campo, una vez por categoría | Solo métricas que dependan de ese campo |
| Manifiesto/sidecar/unión faltante o huérfana | Identidad esperada conocida | Métricas que requieran esa unión |
| Identidad duplicada o en conflicto | Identidad | Duplicación del conteo; además métricas conflictivas |
| Geometría, pose o marco inválidos | Escena o fuente afectada | Métricas espaciales dependientes |
| Diferencia RT30 material entre variantes | Escena y frecuencia, con variantes implicadas | Solo consolidación RAVEN y comparación de la banda afectada |
| Banda RT30 no comparable | Escena y frecuencia | Par de comparación afectado; las diferencias dentro de tolerancia no cuentan |
| Índice opcional ausente | Archivo esperado por el analizador | Indicadores dependientes del índice; no asumir cero fallos |

Los contadores se muestran en HTML y datos exportados, con total de entidades afectadas sin duplicación y desglose por categoría. Para RT30, enlazar la exclusión de cada par con su causa (`cause_id` o equivalente): los `rt30_not_comparable` derivados de un mismo conflicto no son nuevos fallos independientes. La CLI debe resumir categorías, escenas y pares afectados, con una muestra acotada de identidades y referencia a `incidents.jsonl` para el detalle, en lugar de imprimir cientos de avisos repetidos. La reducción de ruido de consola no elimina incidencias de los archivos auditables. Las categorías pueden solaparse; no sumar sus subtotales como si fueran entidades distintas. Incluir referencias de archivo/identidad para auditar las incidencias.

Un reporte con incidencias se genera como parcial; uno sin observaciones válidas se genera con contadores y estado «sin datos analizables». Una métrica sin población válida indica «no disponible», nunca cero. Estos estados no provocan fallo por calidad de metadatos. No se promete continuar ante fallos fatales de infraestructura, como un directorio raíz inaccesible o imposibilidad de escribir el reporte; deben producir un error operativo claro.

### RF6. Análisis adicionales desde metadatos — P1

| Análisis | Valor para el usuario | Población y límites |
|---|---|---|
| Matriz escena × HRTF y conciliación de índices | Detectar variantes faltantes, duplicadas o huérfanas | Variantes únicas; completitud solo con expectativa conocida |
| Cobertura de eventos, número de fuentes, ganancia e inicio | Detectar desbalance del dataset | Fuentes únicas; separar ausencia de evento de categorías reales |
| Materiales por superficie y áreas | Entender diversidad de condiciones acústicas | Escenas únicas; paredes por `wall_ids`, sin asumir cuatro paredes ni direcciones cardinales |
| Muestreo RT30, intentos y fallos | Identificar sesgos por rechazo o falta de cobertura | `reverberation_sampling`, `sampling`, `reverberation_batch` e índice de fallos; consolidar datos de batch repetidos en manifiestos |
| Perfiles auditivos, estados y asimetría declarada | Revisar cobertura de degradación | Metadatos degradados; comparar oídos sobre bandas comunes explícitas |
| Ruido de fondo declarado | Revisar frecuencia de aplicación y parámetros disponibles | `summary.background_noise`; no inferir SNR efectivo ni nivel acústico del WAV |

Los análisis P1 forman parte del alcance posterior a resolver P0. Si faltan sus metadatos, mostrar cobertura disponible y contadores; no bloquear la entrega básica ni inventar observaciones.


### RF7. Presentación legible y leyendas autosuficientes — siguiente diff

- Retirar `render_configuration` de tablas, desplegables y volcado de parámetros del HTML: actualmente contiene una entrada `render` por escena, incluidas rutas largas. No basta plegarlo por defecto. Conservar `general_parameters.json` y demás exportaciones de auditoría salvo futura migración explícita; no eliminar `scene_id`, `variant_id`, HRTF, rutas de procedencia ni parámetros generales resumidos (p. ej., frecuencias de muestreo). Ninguna métrica ni conteo depende de ocultar este campo de presentación.
- Toda imagen/SVG, tanto embebida como exportada, incluye leyenda visible dentro de la imagen. En plantas: contorno de sala, receptor y fuentes, significado de formas y colores por evento; si se muestra orientación o un punto fuera de sala, explicar su símbolo. En histogramas: qué representan las barras y la población/unidad del conteo. En gráficos categóricos: correspondencia color/categoría. En RT30: método/serie, banda o conjunto explícito, segundos y signo de la diferencia «RAVEN − Sabine». Una gráfica de una sola serie también debe identificarla; no usar únicamente título, tooltip o leyenda HTML externa.
- En estados sin datos, sustituir la leyenda de series inexistentes por un mensaje visible «sin datos/no disponible» con `n` válido/excluido y unidad de observación; no dibujar símbolos que sugieran observaciones reales. Evitar cortes o solapamientos de leyendas en SVG aislado y tamaños de pantalla estrechos.

### RF8. RT30 por banda y promedio en la vista de cada sala — implementado

- Cada tarjeta de sala en **Positions by geometry** DEBE (MUST) mostrar, junto a su planta, una tabla identificada por `scene_id`: frecuencia central (Hz), «RT30 estimado — Sabine (s)» y «T30 informado — RAVEN consolidado (s)». La tabla y sus promedios cambian con la escena seleccionada mediante cualquiera de los controles del carrusel; no pertenecen a una HRTF individual ni se duplican por variante.
- DEBE reutilizar los valores normalizados de RF4: Sabine por escena/frecuencia y RAVEN consolidado con tolerancia, media entre variantes válidas y procedencia existentes. Mostrar las frecuencias explícitas disponibles en orden ascendente, incluidas las adicionales, señalando cuáles participan en el promedio comparable. No recalcular ni relajar tolerancias en la presentación.
- Mostrar ambos **promedios aritméticos comparables** sobre exactamente las mismas bandas: intersección de `mean_bands_hz` de Sabine y bandas con un par válido Sabine/RAVEN para esa escena, conforme a `rt30_pairs` y `rt30_matched_means`. Indicar lista de frecuencias, `n` de pares/bandas válidas y `n` excluido respecto de las bandas seleccionadas para comparación. Las bandas adicionales se muestran como información individual, sin incorporarlas a estos promedios ni sustituirlos por `mean_t30_s` de diez bandas de RAVEN.
- Una celda ausente, inválida o conflictiva DEBE mostrar «no disponible» y el motivo; conservar el valor independiente del otro método. Si no hay bandas comunes válidas, ambos promedios comparables muestran «no disponible», con `n=0`, nunca cero segundos. Mantener acceso a `n_variants`, dispersión, causa de exclusión y procedencia/originales de RF4; no ocultar una discrepancia material mediante el promedio de sala. La tabla es una presentación de los datos auditables existentes, no una nueva medición del audio.

## Criterios de aceptación

- CA1. El run real de referencia genera el reporte sin el fallo por singleton e informa 100 escenas, 200 variantes y 32/32/36 escenas por geometría. Los diez sidecars singleton se procesan correctamente.
- CA2. Una fuente semánticamente idéntica como objeto o lista de un elemento produce los mismos datos y gráficos. Entradas malformadas se contabilizan y no detienen el reporte.
- CA3. Duplicar variantes HRTF no altera las distribuciones por escena/fuente; sí altera la cobertura por variante cuando la identidad es nueva. Duplicados de igual identidad no aumentan los totales únicos.
- CA4. Los parámetros generales aparecen en una sola sección y las posiciones se agrupan en tres carruseles manuales por geometría dentro de un único HTML. Cada tarjeta identifica run/escena, receptor, fuentes y variantes asociadas. Anterior/Siguiente, selección directa y teclado funcionan sin autoplay, con estados de extremos, vacío y escena única.
- CA5. Una L conocida tiene área exterior menos recorte; un punto dentro de su envolvente pero dentro del recorte se detecta fuera. Un trapecio conserva sus vértices y proporciones. Los ejes corresponden a X/Z y la altura a Y.
- CA6. Las poses equivalentes del manifiesto y RAVEN transformado coinciden; un marco desconocido no genera una posición mezclada. La orientación ausente no produce DoA relativo ficticio y los ceros válidos se conservan.
- CA7. RT30 compara exactamente las frecuencias comunes aunque el orden cambie. Bandas ausentes, inválidas o duplicadas ambiguas incrementan exclusiones; nunca se comparan sin alineación los promedios de siete y diez bandas.
- CA8. Casos de esquema antiguo, futuro, ausente, JSON roto, línea JSONL inválida, unión faltante y geometría inválida generan contadores verificables y reporte parcial; las métricas independientes válidas permanecen disponibles.
- CA9. Con cero observaciones válidas se generan el reporte de estado y sus contadores. Un error operativo de escritura se distingue de una incidencia de datos.
- CA10. El análisis puede ejecutarse sobre una copia que contenga solo los metadatos del run, sin WAV ni acceso a sus rutas. No abre ni analiza archivos de audio, materiales o HRTF referenciados.
- CA11. Los totales, exclusiones y poblaciones coinciden entre HTML y exportaciones. Las filas permiten rastrear identidades y procedencia; los datos de batch repetidos no multiplican los intentos o fallos.
- CA12. Los análisis P1 presentan datos disponibles con denominadores correctos, o estado no disponible y contadores cuando falta soporte. La cobertura esperada desconocida no se presenta como 0 % ni como 100 %.

- CA13. En `e2e_raven_rt30_binaural_20261012`, tras el diff RT30, las diferencias observadas dentro de la tolerancia se consolidan en 100 escenas × 7 bandas = 700 pares válidos; no se producen los 100 falsos `rt30_conflict` ni sus 700 exclusiones derivadas. Permanecen 100 escenas, 200 variantes, 234 fuentes únicas y 468 filas fuente-variante. Este resultado es un criterio futuro, no una afirmación sobre el código actual.
- CA14. Fixtures cubren igualdad exacta, valores justo dentro/fuera de la tolerancia RT30, orden distinto de variantes/bandas, duplicado de la misma identidad y tres variantes con equivalencia no transitiva. Una discrepancia material aislada excluye solo esa frecuencia; Sabine y las demás bandas siguen disponibles. Exportar razón y `n_variants` por banda. Probar que la CLI resume un conflicto causal sin listar cada aviso derivado, y que conserva la totalidad de incidencias y exclusiones auditables.
- CA15. `render_configuration` no aparece en la presentación HTML ni mediante un desplegable; los archivos de auditoría conservan la configuración y la trazabilidad. Los totales y métricas coinciden antes/después de ocultarlo.
- CA16. Cada SVG puede interpretarse por separado gracias a su leyenda visible, ejes, unidades y población; se comprueban plantas de las tres geometrías, histogramas, categorías, RT30 y estado vacío. No hay leyendas recortadas ni solo tooltips.

- CA17. **Dado** un carrusel con dos salas con RT30 diferentes, **cuando** se cambia mediante Anterior/Siguiente, selección directa o teclado, **entonces** planta, `scene_id`, tabla por banda y ambos promedios corresponden a la misma sala, sin duplicación por HRTF.
- CA18. **Dadas** siete bandas comunes válidas y bandas adicionales RAVEN, **cuando** se muestra la sala, **entonces** la tabla ordena todas las frecuencias disponibles, identifica las siete incluidas y ambos promedios coinciden con la exportación de pares sobre esas siete bandas (`n=7`); las adicionales no alteran el denominador.
- CA19. **Dada** una banda RAVEN conflictiva o ausente, **cuando** se muestra la sala, **entonces** su celda indica el motivo, Sabine sigue visible y ambos promedios usan solo pares restantes. **Dada** una escena sin pares válidos, **cuando** se muestra, **entonces** ambos promedios son «no disponible», `n=0`, y siguen accesibles valores individuales y procedencia.

## Validación y prioridades de entrega

Primero verificar P0 con casos de contrato 3.0, singleton, deduplicación, geometrías, marcos, bandas e incidencias; después completar los análisis P1 y sus denominadores. Conservar el run real como comprobación de integración cuando esté disponible y preparar fixtures mínimos vigentes para que las pruebas no dependan del dataset local.

Revisar visualmente las tres geometrías, estados vacíos, leyendas, unidades y consistencia de números exportados. El conjunto de pruebas actual necesita casos vigentes: los fixtures antiguos sin schema y con dos fuentes no bastan. No se requiere MATLAB/RAVEN ni un render nuevo para validar este consumidor.

## Revisión E2E del 12.10.2026 y diffs pendientes

### Evidencia observada

Se analizó la salida ya existente de `uv run --project apps/output-analyzer output-analyzer analyze .\outputs\e2e_raven_rt30_binaural_20261012\`, sin ejecutar MATLAB, rerenderizar ni leer audio. `analysis/summary.json` y `analysis/report.html` confirman 100 escenas, 200 variantes, 234 fuentes únicas, 468 filas fuente-variante y 200 degradados; las geometrías son 32 shoebox, 32 L y 36 trapezoides. El estado actual es parcial por 100 `rt30_conflict` y 700 `rt30_not_comparable` derivados. No son 800 entidades independientes: son 100 escenas afectadas, cada una con siete comparaciones excluidas.

Los 200 sidecars contienen diez bandas RAVEN finitas y positivas. En las 100 parejas de manifiestos runtime coinciden sala/materiales, fuentes, poses del receptor y muestreo; cambian las identidades HRTF y los campos de proyecto/salida propios de cada variante. Comparando sus bandas comunes 125–8000 Hz: mediana de `abs(T30_variante1 − T30_variante2)` = **2.74388458e-8 s** y máximo = **1.73169787e-7 s**; diferencia relativa simétrica máxima = **0.0000469314 %**. Incluyendo las diez bandas, el máximo es **2.47035448e-7 s**. Todas las parejas pasan el criterio RT30 específico de RF4 (`atol=1e-6 s`, `rtol=1e-6`); este cálculo diagnóstico no modificó el analizador ni regeneró su reporte.

Ejemplo reproducible: `scene_static_0001__binaural_hrtf__1__render.json` y `scene_static_0001__binaural_hrtf__2__render.json` bajo `output_audio/render/binaural_hrtf/`. A 125 Hz reportan **0.6322865388896697 s** y **0.632286456586174 s** (diferencia absoluta **8.23034957e-8 s**); ambos declaran `effective_seed: 42`.

### Diagnóstico y límite de la conclusión

La causa confirmada de los avisos es del consumidor: `loader.py` aplica `_same` con tolerancia genérica absoluta/relativa `1e-8` a los diccionarios RAVEN completos. Una sola banda fuera de esa tolerancia marca toda la escena como conflictiva, invalida incluso elegibilidad de registros Sabine y emite después siete incidencias de no comparabilidad. En este E2E la regla es demasiado estricta para variaciones numéricas submicrosegundo y produce falsos conflictos. La idea de detectar discrepancias materiales entre variantes sigue siendo válida; se corrigen el criterio de equivalencia y la granularidad de exclusión, no se suprime el control.

El productor `apps/matlab/static/render_static_scene.m` ejecuta `rpf.run()` por HRTF y luego obtiene `rpf.getT30()`; `build_room_reverberation_summary.m` solo normaliza/serializa las diez bandas. `prepare_raven_project.m` aplica `rng(..., 'twister')` en MATLAB, declara `raven_seed_applied = false` y advierte que la seed solo se aplica al RNG MATLAB. `setFixReflectionPattern(1)`/`setFixPoissonSequence(1)` no constituyen por sí solos evidencia de identidad bit a bit entre ejecuciones. Esto explica por qué no debe asumirse igualdad exacta, pero **no identifica experimentalmente la causa interna** de las diferencias RAVEN/ITA (redondeo, paralelismo o estado del simulador). Tampoco permite atribuirlas a un efecto acústico de la HRTF. Esa investigación requeriría instrumentación del productor y queda fuera de estos diffs.

El run antiguo `e2e_raven_rt30_binaural_20260924` permanece como evidencia histórica de singleton y conteos; no se extrapola este diagnóstico numérico a diferencias no cuantificadas de otros runs.

### Registro de diffs y ampliación documental del 13.10.2026

| Diff | Archivos/área prevista | Resultado requerido |
|---|---|---|
| RT30: tolerancia propia, exclusión por banda y resumen de avisos | `loader.py`, `report.py`, `cli.py`, tests, README | Consolidación equivalente, 700 pares recuperados en este E2E, cifras de dispersión y procedencia; RF4, CA13–14 |
| Carrusel manual de plantas | `report.py`, tests del HTML/controles | Una escena por tarjeta dentro de cada geometría; identidad y HRTF asociadas, sin autoplay; RF3, CA4 |
| Ocultar `render_configuration` | Presentación general de `report.py`, tests | HTML conciso sin borrar exportación auditable; RF7, CA15 |
| Leyenda en cada imagen | Generadores SVG de `report.py`, tests y QA visual | Símbolos/colores/series comprensibles en cada SVG aislado; RF7, CA16 |

Estado de los cuatro diffs anteriores: **implementados**, con 45 pruebas aprobadas y 700 pares recuperados en el E2E; la QA dinámica de navegador no pudo verificarse por indisponibilidad del navegador. Este registro conserva esa limitación y no reabre los diffs anteriores.

**Ampliación RF8 implementada (13.10.2026):** tabla RT30 por banda y promedios comparables dentro de cada tarjeta de sala de Positions by geometry (CA17–CA19), con pruebas y documentación de uso. Validación: 48 pruebas aprobadas, revisión independiente sin hallazgos y reporte E2E regenerado. Las 100 tarjetas muestran diez bandas, siete incluidas y tres adicionales; ambos promedios coinciden con las exportaciones. Se mantienen 100 escenas, 200 variantes, 234 fuentes, 468 filas y 700 pares sin incidencias. La sincronización se conserva al pertenecer tabla y planta a la misma tarjeta; no se certifica QA dinámica de navegador en esta revisión.

## Riesgos, dependencias y reversión

- **Deriva de contrato:** fijar 3.0 para escena/render en esta entrega y documentar los contratos de cada entrada. Una futura versión necesita revisión explícita.
- **Doble conteo:** conservar identidades y denominadores separados, incluida la consolidación de metadatos de batch y RT30 repetidos.
- **Representación engañosa:** usar plantas reales y marcos conocidos; no inferir geometría desde dimensiones envolventes ni orientaciones ausentes.
- **Disponibilidad del run:** sus cifras son un criterio de integración específico, no un valor fijo de producto; los fixtures reproducibles cubren la validación automatizada.
- **Cambios en exportaciones:** documentar unidades de observación y nombres actualizados al implementar para que otros consumidores no interpreten filas de variantes como escenas.

La implementación afectará al lector, reporte, documentación y pruebas de `apps/output-analyzer`; no requiere cambiar productores ni metadatos originales. Los artefactos de análisis deben poder regenerarse. La reversión consiste en revertir el cambio del analizador y sus artefactos derivados, preservando el run original; no restaura compatibilidad con el contrato vigente si se vuelve a la versión defectuosa.

Las decisiones para los siguientes diffs están definidas en esta revisión. La atribución interna de la variación numérica a RAVEN/ITA queda abierta como investigación del productor; no bloquea corregir el consumidor con equivalencia numérica explícita. No se amplía el soporte de esquemas históricos.
