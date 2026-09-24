# PRD - muestreo uniforme aproximado de RT30 mediante tratamientos virtuales
Estado: borrador
Creado: 23.09.2026
Alcance: Reemplazar el limite unico `max_rt30_s` por un muestreo estratificado global del RT30 estimado, usando tratamientos virtuales de absorcion sobre paredes y cielo. No modificar scattering, geometria ni el output analyzer.

## Resumen

Hoy: El pipeline sortea geometria y materiales base, estima el RT30 mediante Sabine y acepta una sala si su promedio no supera `max_rt30_s`. Las configuraciones actuales producen principalmente salas reverberantes y el limite superior no controla como se distribuyen los casos aceptados.

Despues: El usuario configura un rango de RT30 y un ancho de bin. El pipeline intenta repartir las realizaciones acusticas de manera aproximadamente uniforme entre bins iguales, aplicando tratamientos virtuales de absorcion versionados a paredes y cielo. Las cuotas son blandas: una desviacion mayor al umbral configurable queda registrada, pero no invalida el lote.

La feature es un mecanismo de muestreo y rechazo, no un optimizador inverso de absorcion. No busca resolver coeficientes que alcancen un RT puntual.

## Historia

Como usuario que genera datos acusticos sinteticos, necesito incorporar casos menos reverberantes ademas de los ya generados, para cubrir de forma controlada distintos regimenes de reverberacion sin agregar muebles ni otras geometrías que encarezcan la simulacion.

Como usuario de datasets pequenos, necesito poder configurar un unico bin para una realizacion acustica reutilizada por varios archivos. Como usuario de entrenamiento de modelos, necesito aumentar a mas de `10^4` o `10^5` realizaciones y elegir bins mas granulares sin cambiar el mecanismo de generacion.

Como desarrollador, necesito reconstruir desde los metadatos el material base, tratamiento, cobertura y coeficientes efectivos aplicados, incluida la version del catalogo y de la formula de mezcla.

## Objetivos / No-Objetivos

### Objetivos

O1. Generar una distribucion global aproximadamente uniforme del RT30 estimado dentro de un rango configurable.

O2. Configurar bins de igual ancho mediante `min_s`, `max_s` y `bin_width_s`.

O3. Usar como metrica la media aritmetica del RT30 estimado en `125, 250, 500, 1000, 2000, 4000 y 8000 Hz`.

O4. Calcular y registrar el RT30 estimado en todas las bandas de octava RAVEN disponibles, aunque solo siete participen en la media.

O5. Reducir la reverberacion mediante tratamientos virtuales de absorcion sobre todas las paredes y el cielo, sin agregar geometria.

O6. Mantener intacto el scattering del material base.

O7. Compartir el presupuesto existente `scene_validation.max_scene_attempts` con los demas rechazos de escena, incluidas colisiones y posiciones invalidas; no agregar un presupuesto de intentos exclusivo para RT30.

O8. Mantener reproducibilidad para una version fija del sampler a partir de configuracion, seed e indice de realizacion.

O9. Registrar por superficie el material base, preset, cobertura, area tratada, absorcion efectiva y scattering conservado.

O10. Versionar independientemente el catalogo de tratamientos y la formula de mezcla.

O11. Admitir desde un solo bin hasta configuraciones granulares para lotes masivos.

O12. Mantener el diseno extensible a futuros generadores de geometria mediante un contrato basado en superficies, areas y coeficientes, no en tipos concretos de sala.

### No-Objetivos

NO1. Optimizar coeficientes o coberturas para alcanzar un RT objetivo puntual.

NO2. Garantizar una distribucion uniforme del T30 medido posteriormente por RAVEN.

NO3. Imponer limites o relaciones por banda individuales; en esta version solo se estratifica el promedio.

NO4. Cambiar o aleatorizar scattering.

NO5. Agregar muebles, difusores geometricos, obstaculos ni otras superficies.

NO6. Exigir realismo constructivo o representar un plano arquitectonico literal.

NO7. Soportar distribuciones no uniformes o bins de ancho desigual.

NO8. Garantizar uniformidad condicionada por geometria, material base u otra categoria; la evaluacion es exclusivamente global.

NO9. Conservar compatibilidad con `room_sampling.max_rt30_s` ni con el contrato anterior de metadatos.

NO10. Actualizar el output analyzer, generar histogramas, matrices de migracion o reportes CSV/JSON separados.

NO11. Calibrar Sabine contra RAVEN ni ejecutar el estudio piloto de sesgo. Esa evaluacion se realizara en otro trabajo despues de la implementacion.

## Como funciona Hoy -> Como debiese funcionar

```text
HOY                                      DESPUES
max_rt30_s unico                         rango [min_s, max_s] y bins iguales
aceptar si RT30 <= maximo                intentar llenar una cuota por bin
media de 10 bandas de octava             media de 125 Hz a 8 kHz, siete bandas
material base directo                    material base + tratamiento virtual
coeficientes leidos solo desde .mat      coeficientes efectivos en manifiesto
fallo al exceder limite                  fallback blando y diagnostico de desvio
sin version de tratamiento               catalogo y mezcla versionados
```

## Decisiones confirmadas

- La distribucion se define sobre el RT30 estimado antes de RAVEN.
- La estimacion conserva Sabine como metodo inicial.
- La media es aritmetica y usa siete bandas: 125 Hz a 8 kHz.
- Se registran las diez bandas de octava RAVEN: 31.5 Hz a 16 kHz.
- Los bins tienen siempre el mismo ancho y la distribucion solicitada es uniforme.
- Los bins son semiabiertos `[inferior, superior)`, excepto el ultimo, que incluye `max_s`.
- La tolerancia inicial es `0.10` y es configurable.
- La tolerancia es diagnostica: nunca relaja coeficientes, invalida un lote ni mueve escenas silenciosamente.
- La cuota se calcula sobre realizaciones acusticas unicas, no sobre archivos de audio derivados.
- La uniformidad se evalua globalmente.
- Todas las paredes y el cielo son elegibles; el piso no recibe tratamiento.
- Cada superficie admite como maximo un preset.
- No se exige simetria ni limite de diferencia entre coberturas de paredes.
- No se limita la cantidad total de tratamiento virtual.
- El scattering se conserva desde el material base.
- Los presets son internos, sinteticos, documentados y versionados.
- El catalogo puede permitir `none` o cobertura cero, sin reservar una cuota para escenas sin tratamiento.
- Los bins secos pueden usar distribuciones de propuesta distintas para aumentar su tasa de aceptacion.
- No se selecciona el candidato mas cercano al bin solicitado.
- `max_rt30_s` se elimina; no se requiere retrocompatibilidad.
- El output analyzer y la calibracion Sabine-RAVEN quedan fuera de alcance.

## Metrica RT30

### Estimacion por banda

Para cada banda de octava RAVEN `f`:

```text
A_eq(f) = sum(area_superficie * alpha_efectiva_superficie(f))
RT30(f) = 0.161 * volumen / A_eq(f)
```

El estimador debe calcular y registrar:

```text
[31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000] Hz
```

La metrica usada para clasificar una realizacion es:

```text
RT30_mean = arithmetic_mean(
    RT30(125), RT30(250), RT30(500), RT30(1000),
    RT30(2000), RT30(4000), RT30(8000)
)
```

No existe validacion de minimos, maximos ni forma espectral del RT30 por banda en esta version. Las bandas fuera de la media se conservan para trazabilidad y analisis posterior.

### Unidad de conteo

Una observacion para las cuotas es una realizacion acustica unica de:

- geometria;
- materiales base;
- plan de tratamiento;
- coeficientes efectivos.

Los archivos renderizados con distintas fuentes, estimulos, HRTF o degradaciones que reutilizan esa misma realizacion no incrementan el conteo del bin. Por ejemplo, veinte archivos de una sola sala constituyen una observacion de reverberacion.

## Bins y cuotas

### Construccion de bins

Para el ejemplo:

```yaml
range_s: {min: 0.1, max: 1.2}
bin_width_s: 0.1
```

se construyen:

```text
[0.1, 0.2), [0.2, 0.3), ..., [1.0, 1.1), [1.1, 1.2]
```

Reglas:

- `min_s`, `max_s` y `bin_width_s` deben ser finitos y positivos;
- `min_s < max_s`;
- `(max_s - min_s) / bin_width_s` debe ser entero con tolerancia absoluta de `1e-9 s` y tolerancia relativa de `1e-12`, aplicadas a la reconstruccion del rango en segundos, no al cociente adimensional;
- debe existir al menos un bin;
- los bordes se construyen con aritmetica estable para no perder valores por errores de punto flotante;
- una realizacion fuera del rango no pertenece a ningun bin.

Un unico bin se expresa configurando `bin_width_s = max_s - min_s`. No se requiere un modo especial.

### Cuotas nominales

Para `N` realizaciones y `K` bins:

```text
base = floor(N / K)
remainder = N mod K
```

Cada bin recibe `base` o `base + 1` realizaciones. Los bins que reciben el resto se eligen mediante una permutacion determinista derivada de la seed, para no favorecer siempre los RT bajos.

El plan de bins objetivo se mezcla de forma determinista y se asigna por indice de realizacion. Para una misma configuracion, seed y cantidad de realizaciones, las cuotas y asignaciones objetivo deben ser identicas.

Se permite `N < K`. En ese caso algunos bins tienen cuota cero y los metadatos deben advertir que la granularidad solicitada excede la resolucion del lote.

### Tolerancia blanda

Por bin se registra:

```text
absolute_deviation = abs(observed_count - target_count)
relative_deviation = absolute_deviation / max(1, target_count)
```

Si `relative_deviation > quota_tolerance_fraction`, el bin se marca `above_tolerance`. Esto no aborta ni invalida el lote.

Para cuotas pequenas tambien se registra:

```text
quota_resolution_fraction = 1 / max(1, target_count)
```

Si esa resolucion es mayor que la tolerancia, el lote declara `insufficient_statistical_resolution: true`. La advertencia no altera resultados; hace explicito que un porcentaje como 10 % no puede evaluarse finamente con una o pocas observaciones.

## Tratamientos virtuales

### Modelo de mezcla

Para cada superficie elegible y cada una de las 31 bandas de tercio de octava usadas por los materiales RAVEN:

```text
alpha_effective(f) =
    (1 - coverage) * alpha_base(f)
    + coverage * alpha_treatment(f)
```

El area tratada se calcula como:

```text
treated_area_m2 = surface_area_m2 * coverage
```

`coverage` pertenece a `[0, 1]`. Una superficie usa cero o un preset; nunca combina dos tratamientos.

La absorcion efectiva calculada por el orquestador es autoritativa. El estimador y MATLAB/RAVEN deben consumir exactamente el mismo vector persistido, sin reconstruir la mezcla de forma independiente.

### Superficies

- Todas las paredes `wall_###` son elegibles.
- El cielo es elegible.
- El piso conserva exclusivamente su material base.
- Preset y cobertura pueden muestrearse independientemente por superficie.
- No existe requisito de simetria entre paredes opuestas.
- No existe limite de diferencia de cobertura entre paredes.
- No existe limite global de area tratada.

### Scattering

El vector de scattering efectivo es una copia exacta del scattering del material base. El preset no aporta scattering y la cobertura no lo interpola.

### Catalogo interno

El orquestador incorpora un catalogo interno con al menos presets sinteticos ligero, medio y fuerte. Cada entrada contiene:

- `preset_id` estable;
- version de catalogo;
- familia e intencion acustica;
- descripcion que indique que no representa un producto comercial;
- 31 coeficientes de absorcion en las frecuencias RAVEN;
- documentacion de la curva y su uso esperado.

Modificar coeficientes de una entrada existente exige incrementar `treatment_catalog_version`. Un `preset_id` no puede cambiar de significado silenciosamente dentro de la misma version.

La formula se identifica independientemente:

```text
absorption_mix_model = area_weighted_linear
absorption_mix_model_version = 1
```

Cambiar la ecuacion, el orden de bandas o la semantica de cobertura exige una nueva version del modelo de mezcla.

## Plausibilidad exigida

La feature exige solo los niveles 1 y 2 siguientes.

### Nivel 1 - validez numerica

- Cada preset contiene exactamente 31 coeficientes finitos.
- Absorcion base, de tratamiento y efectiva pertenecen a `[0, 1]`.
- Cobertura pertenece a `[0, 1]`.
- Areas y volumen son finitos y positivos.
- No se aplica `clamp` silencioso; un valor invalido es un error de catalogo o candidato.
- El resultado de Sabine por banda y su media deben ser finitos y positivos.

### Nivel 2 - plausibilidad acustica

- Las curvas representan familias sinteticas reconocibles de tratamiento ligero, medio o fuerte.
- Las curvas deben ser suaves en frecuencia y evitar saltos no justificados entre bandas adyacentes.
- En el catalogo v1, la diferencia absoluta maxima entre coeficientes de bandas adyacentes es `0.08`.
- La descripcion documenta la tendencia espectral y la intencion de cada preset.
- La version del catalogo fija la regla de validacion de suavidad usada por sus tests.
- Una cobertura mayor interpola monotonamente desde el material base hacia el preset en cada banda.

### Realismo constructivo no exigido

No se exige que cantidad, ubicacion, montaje o costo correspondan a una sala que se construiria literalmente. Se acepta, por ejemplo, tratar todas las paredes y el cielo con coberturas altas si los coeficientes son numericamente validos y acusticamente plausibles.

El resultado representa una sala acusticamente equivalente, no un plano constructivo. No deben introducirse limites de cobertura o area para simular normas arquitectonicas sin una nueva decision de producto.

## Estrategia de muestreo

### Flujo principal

```text
build_global_plan(N, bins, seed):
    target_quotas = balanced_integer_quotas(N, bins, seed)
    target_sequence = deterministic_shuffle(expand(target_quotas), seed)
    return target_quotas, target_sequence

sample_acoustic_realization(scene_index, target_bin):
    in_range_candidates = empty reservoir

    for attempt in 1..scene_validation.max_scene_attempts:
        sample geometry and base materials
        sample treatment proposal conditioned on target_bin
        compute effective absorption for every surface
        estimate all RT30 bands and the seven-band mean

        if mean is inside configured global range:
            retain candidate with unbiased reservoir sampling

        if mean is inside target_bin:
            continue with receiver/source validation
            if complete scene is valid:
                accept as target_bin match

        otherwise continue using the same shared attempt budget

    if a valid in-range candidate exists:
        accept a uniformly retained fallback candidate
        record requested bin != obtained bin and fallback=true
    else:
        record failed realization with diagnostics
```

La implementacion puede organizar los reintentos internos de otra manera, pero debe conservar estas invariantes:

- no usar la distancia al centro o borde del bin para seleccionar candidatos;
- no modificar una propuesta a partir del error de RT observado;
- no ejecutar biseccion, gradientes, busqueda local ni ajuste iterativo;
- no ampliar o relajar el rango configurado;
- no consumir mas de `scene_validation.max_scene_attempts` intentos totales de escena;
- elegir el fallback de manera no sesgada entre candidatos validos dentro del rango, no elegir el mas cercano;
- registrar todos los motivos de rechazo y el uso del fallback.

### Propuestas condicionadas por bin

Para mejorar eficiencia sin optimizar, cada bin puede declarar o derivar pesos de propuesta diferentes:

- bins secos: mayor probabilidad de presets fuertes, coberturas altas y cielo tratado;
- bins medios: mayor probabilidad de presets y coberturas intermedias;
- bins reverberantes: mayor probabilidad de `none`, cobertura baja o tratamiento ligero.

Los pesos se eligen antes de evaluar el candidato y no se corrigen usando su error respecto del bin. Dada una configuracion y seed, su uso debe ser reproducible. La estrategia y su version quedan registradas en metadatos.

No se exige que cada preset tenga igual probabilidad ni que cada bin comparta la misma distribucion de propuestas.

## Contrato YAML propuesto

```yaml
room_sampling:
  reverberation:
    metric: estimated_rt30_s
    estimator: sabine
    estimator_version: sabine_polygon_octaves_v4
    aggregation: arithmetic_mean
    mean_bands_hz: [125, 250, 500, 1000, 2000, 4000, 8000]

    distribution:
      type: uniform
      scope: global
      range_s:
        min: 0.1
        max: 1.2
      bin_width_s: 0.1
      quota_tolerance_fraction: 0.10

    treatment:
      catalog_version: 1
      mix_model: area_weighted_linear
      mix_model_version: 1
      eligible_surface_types: [wall, ceiling]
      max_treatments_per_surface: 1
      preserve_base_scattering: true
      allow_none: true
      wall_coverage: {min: 0.0, max: 1.0}
      ceiling_coverage: {min: 0.0, max: 1.0}

  geometry:
    # configuracion existente
  materials:
    # pools de materiales base existentes
```

### Reglas del YAML

- Los enums de la primera version solo aceptan los valores mostrados para metrica, estimador, agregacion, distribucion, scope y modelo de mezcla.
- `mean_bands_hz` debe coincidir exactamente con las siete bandas confirmadas; se expone para hacer el contrato visible, no para soportar combinaciones arbitrarias en esta version.
- `quota_tolerance_fraction` debe ser finito y pertenecer a `[0, 1]`.
- Los rangos de cobertura deben ser finitos, cumplir `0 <= min <= max <= 1` y pueden fijar un valor usando `min = max`.
- `eligible_surface_types` debe contener exactamente `wall` y `ceiling` en esta version.
- `max_treatments_per_surface` debe ser `1`.
- `preserve_base_scattering` debe ser `true`.
- `catalog_version` y `mix_model_version` deben existir en el runtime.
- Cualquier clave desconocida se rechaza mediante el modelo estricto existente.
- `room_sampling.max_rt30_s` deja de ser valido.

## Contrato de manifiesto

Ejemplo abreviado por realizacion:

```json
{
  "room": {
    "acoustic_surfaces": {
      "wall_001": {
        "surface_area_m2": 12.5,
        "base_material": {
          "material_id": "painted_concrete",
          "material_path": "C:/absolute/path/PaintedConcrete.mat",
          "absorption": ["31 values"],
          "scattering": ["31 values"]
        },
        "treatment": {
          "preset_id": "broadband_medium",
          "catalog_version": 1,
          "coverage": 0.42,
          "treated_area_m2": 5.25,
          "absorption": ["31 values"]
        },
        "effective_absorption": ["31 values"],
        "effective_scattering": ["31 values"]
      }
    }
  },
  "reverberation_sampling": {
    "requested_bin": {"index": 4, "lower_s": 0.5, "upper_s": 0.6},
    "obtained_bin": {"index": 4, "lower_s": 0.5, "upper_s": 0.6},
    "matched_requested_bin": true,
    "fallback_used": false,
    "attempts": 3,
    "estimator": "sabine",
    "estimator_version": "sabine_polygon_octaves_v4",
    "aggregation": "arithmetic_mean",
    "mean_bands_hz": [125, 250, 500, 1000, 2000, 4000, 8000],
    "estimated_mean_rt30_s": 0.547,
    "rt30_by_band_s": {"31.5": 0.82, "63": 0.74, "125": 0.66},
    "treatment_catalog_version": 1,
    "absorption_mix_model": "area_weighted_linear",
    "absorption_mix_model_version": 1,
    "proposal_strategy_version": 1,
    "rejections_by_reason": {}
  }
}
```

Los arrays ilustrativos deben contener valores numericos reales y 31 elementos en el manifiesto productivo.

### Metadatos por realizacion

Se registra como minimo:

- bin solicitado y bin obtenido, con indice y bordes;
- coincidencia y uso de fallback;
- RT30 medio estimado;
- RT30 estimado en las diez bandas;
- bandas usadas en la media;
- intentos consumidos y rechazos por causa;
- geometria, volumen y areas usados por el estimador;
- por superficie: material base, preset o `none`, cobertura, area total y tratada;
- absorcion base, de preset y efectiva;
- scattering base y efectivo conservado;
- versiones de estimador, sampler de propuestas, catalogo y mezcla;
- seed efectiva e indice de realizacion.

### Metadatos por lote

Los metadatos existentes del lote incorporan:

- rango, ancho y bordes de bins;
- numero de realizaciones acusticas contabilizadas;
- cuotas nominales y conteos observados;
- desviacion absoluta y relativa por bin;
- tolerancia usada y bins `above_tolerance`;
- resolucion estadistica y marca `insufficient_statistical_resolution`;
- bins sin observaciones, dificiles o con cuota cero;
- cantidad de coincidencias, fallbacks y realizaciones fallidas;
- razones de rechazo agregadas;
- versiones de todos los algoritmos relevantes.

El estado es `complete` cuando no hay fallos ni bins sobre tolerancia,
`complete_with_deviation` cuando el lote termina sin fallos pero al menos un bin
supera la tolerancia, `partial` cuando hay realizaciones aceptadas y fallidas, y
`failed` cuando ninguna realizacion fue aceptada.

No se genera un archivo resumen adicional: esta informacion vive en los metadatos/manifiestos ya usados por el pipeline.

## Contrato MATLAB/RAVEN

- MATLAB recibe `effective_absorption` y `effective_scattering` por superficie desde el manifiesto.
- MATLAB no vuelve a cargar la absorcion base para recomputar la mezcla.
- `effective_scattering` debe ser identico al vector base persistido.
- Las 31 bandas se validan como finitas y dentro de `[0, 1]` antes de llamar `setMaterial`.
- Los slots de las caras tecnicas que representan una misma superficie reciben el mismo par de vectores efectivos.
- Los coeficientes devueltos en metadata de render deben permitir comprobar igualdad con los del manifiesto.

### T30 posterior de RAVEN

RAVEN no participa en la seleccion de bin ni en la cuota. Su T30 se registra para analisis posterior.

Si RAVEN no produce un T30 valido en alguna banda requerida por su contrato:

1. registrar escena, banda, diagnostico y estado del render;
2. descartar esa realizacion como resultado valido;
3. reintentar la realizacion usando el mecanismo y presupuesto general de reintentos, sin crear un limite exclusivo para T30;
4. si se agota el presupuesto, marcar la realizacion como fallida y conservar el diagnostico.

El reintento no usa el T30 de RAVEN para corregir tratamiento, cambiar de bin ni calibrar Sabine.

## Reproducibilidad

Para una version fija de algoritmos:

```text
(config normalizada, seed, numero de realizaciones, scene_index)
    -> bin objetivo, secuencia de propuestas y resultado deterministas
```

Esto incluye:

- reparto del resto entre bins;
- orden de asignacion de bins objetivo;
- geometria y materiales base;
- presets y coberturas;
- fallback retenido;
- intentos y causas de rechazo;
- registro de fallo.

La implementacion no debe depender del orden del filesystem, iteracion no ordenada de mapas, RNG global ni orden de ejecucion de workers. Un cambio deliberado que altere resultados incrementa la version del sampler o estrategia correspondiente.

## Cambios por componente

### `apps/orchestrator/src/acoustic_orchestrator/config/models.py`

- Reemplazar `max_rt30_s` por modelos estrictos de reverberacion, distribucion y tratamiento.
- Modelar rango, ancho, tolerancia, coberturas y versiones.

### `apps/orchestrator/src/acoustic_orchestrator/config/validator.py`

- Validar divisibilidad del rango, enums, tolerancia, coberturas y versiones.
- Validar numericamente el catalogo y su regla de plausibilidad acustica.
- Rechazar `max_rt30_s` y configuraciones antiguas.

### Catalogo interno de tratamientos

- Incorporar un recurso versionado y documentado con 31 bandas por preset.
- Exponer una unica API de lectura por `catalog_version` y `preset_id`.
- Incluir tests que impidan modificar una version publicada sin actualizar su identificador.

### `apps/orchestrator/src/acoustic_orchestrator/experiment/room_acoustics.py`

- Aceptar coeficientes efectivos por superficie.
- Conservar las diez estimaciones por banda.
- Cambiar la media a las siete bandas confirmadas.
- Versionar el estimador actualizado.

### `apps/orchestrator/src/acoustic_orchestrator/experiment/sampler.py`

- Construir cuotas y secuencia global de bins.
- Muestrear tratamientos condicionados por bin.
- Aplicar mezcla, fallback no sesgado y diagnosticos.
- Compartir `max_scene_attempts` con el resto de validaciones.
- Mantener independencia del orden de workers.

La planificacion global puede extraerse a un modulo puro si evita convertir `sampler.py` en un coordinador de estado mutable.

### Constructor de manifiestos y metadatos de lote

- Persistir superficies acusticas efectivas y trazabilidad completa.
- Agregar cuotas, observaciones, tolerancia y desviaciones globales.
- Distinguir exito completo, exito con desviacion y exito parcial por fallos.

### `apps/matlab/core/validate_static_scene_config.m`

- Validar los vectores efectivos por superficie y sus versiones requeridas.
- Rechazar coeficientes faltantes, no finitos, fuera de rango o con longitud incorrecta.

### `apps/matlab/core/build_room_from_config.m`

- Aplicar directamente absorcion y scattering efectivos del manifiesto.
- Mantener asignacion por identidad de superficie y soporte de multiples caras tecnicas.

### Configuraciones, ejemplos y documentacion

- Migrar ejemplos desde `max_rt30_s` al contrato nuevo.
- Documentar catalogo, niveles de plausibilidad y significado estadistico de tolerancia.
- Incluir un ejemplo de un bin y otro multibin `0.1-1.2 s`.

### Fuera de esta feature

- `apps/output-analyzer`;
- calibracion o correccion Sabine-RAVEN;
- dashboards, histogramas y matriz de migracion;
- nuevos generadores geometricos.

## Casos de uso

### CU1. Dataset grande uniforme aproximado

El usuario solicita mas de `10^5` realizaciones entre 0.1 y 1.2 s con bins de 0.05 s. El pipeline asigna cuotas equilibradas, usa propuestas condicionadas y registra cualquier desvio superior al 10 %.

### CU2. Prueba auditiva con una sola sala

El usuario configura un unico bin y una realizacion acustica que se reutiliza para veinte archivos. La cuota contabiliza una observacion y los veinte archivos conservan referencia a la misma realizacion.

### CU3. Bin seco dificil

El bin objetivo requiere alta absorcion. Tras agotar intentos no aparece una escena en ese bin, pero existen candidatos dentro del rango global. Se acepta uno elegido sin criterio de cercania, se registra fallback y el resumen muestra la desviacion.

### CU4. Bin sin candidatos en el rango

Ningun intento produce una escena dentro del rango global. La realizacion falla, el lote continua y el resumen registra indice, bin solicitado, ultimo diagnostico y conteo de rechazos.

### CU5. Sala sin tratamiento

El sampler elige `none` o cobertura cero para todas las superficies elegibles. La realizacion puede aceptarse si cae en el rango; no existe cuota especial para este caso.

### CU6. T30 invalido de RAVEN

La estimacion previa es valida, pero RAVEN no entrega una banda. El pipeline registra el fallo y reintenta sin usar el valor medido para modificar el tratamiento.

## Criterios de aceptacion

CA1. La configuracion de ejemplo `[0.1, 1.2]` con ancho `0.1` produce exactamente once bins, con ultimo extremo inclusivo.

CA2. Una diferencia de punto flotante dentro de la tolerancia documentada no crea un bin espurio; una division realmente no entera se rechaza.

CA3. La cuota por bin difiere como maximo en una realizacion antes del muestreo y el reparto del resto es reproducible.

CA4. La clasificacion usa solo la media aritmetica de 125, 250, 500, 1000, 2000, 4000 y 8000 Hz.

CA5. El manifiesto registra RT30 para las diez bandas de octava, incluidas las no usadas en la media.

CA6. La tolerancia no rechaza el lote; todo bin sobre el umbral queda identificado con su desviacion.

CA7. Lotes con baja resolucion estadistica quedan marcados explicitamente y pueden ejecutarse.

CA8. Cada pared y el cielo pueden recibir independientemente cero o un tratamiento; el piso no recibe ninguno.

CA9. Toda superficie registra area y `treated_area_m2 = area * coverage` dentro de tolerancia numerica.

CA10. Los vectores efectivos cumplen la mezcla lineal en las 31 bandas y MATLAB consume exactamente esos vectores.

CA11. El scattering efectivo coincide elemento a elemento con el material base.

CA12. Catalogo y formula de mezcla tienen versiones independientes persistidas en manifiesto y metadata.

CA13. Ningun preset o resultado viola el nivel 1 de plausibilidad y todos los presets satisfacen la regla versionada del nivel 2.

CA14. Una configuracion puede tratar todas las paredes y el cielo al 100 %; no existe rechazo por realismo constructivo.

CA15. El sampler no usa cercania, error observado ni ajuste iterativo para escoger o transformar candidatos.

CA16. Al agotar intentos, el fallback se elige sin sesgo entre candidatos validos del rango y se registra; si no existe, la realizacion falla con diagnostico.

CA17. El total de intentos de escena nunca supera `scene_validation.max_scene_attempts` por realizar una seleccion RT30.

CA18. Misma configuracion, seed, cantidad e indice producen el mismo bin objetivo, tratamiento, coeficientes y diagnostico, independientemente del numero de workers.

CA19. El lote informa cuotas nominales, observadas, desviaciones, fallbacks y fallos dentro de los metadatos existentes.

CA20. `room_sampling.max_rt30_s` se rechaza y ningun ejemplo normativo depende de el.

CA21. Un T30 invalido de RAVEN deja evidencia y activa un reintento acotado; nunca se usa para reclasificar la escena.

CA22. Un nuevo generador geometrico puede integrarse entregando superficies, areas y volumen sin modificar el contrato de tratamientos.

## Estrategia de tests

### Configuracion

- rango y ancho validos con uno y multiples bins;
- division no entera, ancho cero/negativo, NaN e infinitos;
- tolerancia fuera de `[0, 1]`;
- coberturas invalidas;
- versiones inexistentes;
- rechazo explicito de `max_rt30_s`;
- rechazo de enums o bandas diferentes al contrato v1.

### Catalogo y mezcla

- longitud, finitud y rango de los 31 coeficientes;
- regla versionada de suavidad/plausibilidad;
- mezcla para coberturas 0, 1 e intermedias;
- monotonicidad hacia el preset por banda;
- ausencia de clamp;
- scattering inalterado;
- area tratada por superficie;
- snapshot por version del catalogo.

### Estimador

- Sabine con absorciones efectivas y areas reales;
- registro de diez bandas;
- media exacta de las siete bandas seleccionadas;
- rechazo de absorcion equivalente o RT30 no finito/no positivo;
- igualdad entre coeficientes estimados y persistidos.

### Plan global y sampler

- cuotas para `N` divisible y no divisible por `K`;
- `N < K` y marca de resolucion insuficiente;
- reparto determinista del resto;
- bordes de bin, incluido `max_s`;
- propuesta condicionada reproducible;
- candidato que coincide en primer intento;
- rechazos por geometria, RT30, receptor, fuentes y colisiones compartiendo presupuesto;
- fallback mediante muestreo de reservorio sin preferencia por cercania;
- fallo sin candidatos dentro del rango;
- conteos observados y advertencia de tolerancia;
- invariancia ante orden de workers.

### Contrato y metadatos

- round-trip del manifiesto con tratamientos;
- cobertura de todas las superficies esperadas y ninguna extra;
- 31 coeficientes efectivos por superficie;
- versiones y seed presentes;
- cuotas y desviaciones consistentes con manifiestos aceptados;
- una realizacion reutilizada por varios archivos se cuenta una sola vez.

### MATLAB con mocks

- aplicacion directa de coeficientes efectivos;
- scattering igual al manifiesto;
- multiples caras tecnicas por superficie;
- rechazo de bandas faltantes, extras o invalidas;
- metadata aplicada igual a la solicitada.

### Integracion opt-in con RAVEN

- render de al menos un caso seco, uno medio y uno reverberante;
- captura de T30 de RAVEN sin usarlo para bins;
- simulacion controlada de banda T30 ausente y verificacion de reintento/fallo acotado;
- comprobacion de que no se agrega geometria por tratamiento.

## Riesgos y mitigaciones

### R1. Bins bajos inviables

Riesgo: Algunas geometrías y presets no pueden alcanzar el extremo inferior, especialmente 0.1 s.

Mitigacion: Propuestas condicionadas, fallback blando, diagnostico de bins dificiles y prohibicion de relajar silenciosamente el rango.

### R2. Costo desigual entre bins

Riesgo: Los bins secos consumen mas intentos y reducen throughput en lotes masivos.

Mitigacion: Pesos de propuesta por bin, presupuesto compartido y metricas de rechazos/attempts en metadata.

### R3. Sabine con absorcion alta

Riesgo: El estimador puede presentar sesgo respecto de RAVEN en salas muy absorbentes.

Mitigacion: Versionar el estimador, registrar todas las bandas y diferir calibracion al estudio posterior sin corregir resultados en esta feature.

### R4. Cuotas globales con ejecucion paralela

Riesgo: Completar bins segun el orden de workers rompe reproducibilidad.

Mitigacion: Preasignar bins por indice y calcular el resumen al final; no usar un contador mutable dependiente del orden de finalizacion.

### R5. Metadata voluminosa

Riesgo: Persistir vectores base, preset, efectivos y scattering por superficie aumenta el tamaño de manifiestos.

Mitigacion: Priorizar reconstruccion exacta en esta version; cualquier deduplicacion futura debe preservar referencias versionadas y no queda incluida aqui.

### R6. Confusion entre tratamiento y material real

Riesgo: Un consumidor puede interpretar los presets como productos o soluciones constructivas.

Mitigacion: Nombres sinteticos, documentacion explicita y separacion entre `base_material`, `treatment` y coeficientes efectivos.

### R7. Lotes pequenos y tolerancia porcentual

Riesgo: Una escena representa saltos de 100 % cuando la cuota es uno.

Mitigacion: Registrar resolucion estadistica, mantener la tolerancia solo diagnostica y permitir un unico bin.

### R8. Reintento tardio por RAVEN

Riesgo: Un T30 invalido se detecta despues de generar y renderizar la escena, complicando cuotas y reproducibilidad.

Mitigacion: Tratar el reintento como parte del ciclo de la misma realizacion, conservar bin objetivo e indice, versionar el ordinal de reintento y recomputar conteos solo con resultados validos.

## Migracion

- Eliminar `room_sampling.max_rt30_s` de modelos, ejemplos y documentacion.
- Agregar el bloque obligatorio `room_sampling.reverberation`.
- Regenerar manifiestos; no se mezclan campos del guard antiguo con el contrato nuevo.
- Incrementar las versiones de schema/metadata que correspondan durante el diseño.
- Los consumidores que no soporten el nuevo schema deben fallar explicitamente, no ignorar tratamientos.

No se requiere adaptador legacy ni periodo de convivencia entre ambos modos.

## Entregables

1. Modelos y validacion del nuevo YAML.
2. Catalogo interno versionado y documentado de tratamientos.
3. Mezcla de absorcion por superficie y estimador actualizado.
4. Planificador global de bins, sampler condicionado y fallback blando.
5. Manifiesto y metadata de lote con trazabilidad completa.
6. Aplicacion de coeficientes efectivos en MATLAB/RAVEN.
7. Reintento acotado ante T30 RAVEN invalido.
8. Migracion de configuraciones y documentacion normativa.
9. Tests unitarios, de contrato, integracion y reproducibilidad.

## Preguntas abiertas

No quedan preguntas de producto bloqueantes para iniciar especificacion tecnica y diseño.

Durante el diseño deben fijarse, sin cambiar el comportamiento de producto:

1. La ubicacion exacta y formato del catalogo interno versionado.
2. La tolerancia numerica para divisibilidad de bins y comparacion de bordes.
3. La regla cuantitativa versionada que valida suavidad espectral del nivel 2.
4. La version de schema de manifiesto y metadata.
5. El punto de orquestacion exacto para reintentar una realizacion cuando RAVEN no entrega una banda T30.
