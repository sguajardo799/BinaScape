# PRD - soporte de salas trapezoidales y tipo L
Estado: borrador
Creado: 22.09.2026
Alcance: Incorporar salas estaticas parametrizadas de tipo shoebox, trapezoidal y L en el orquestador y el backend MATLAB/RAVEN. No implementar geometria dinamica ni actualizar el output analyzer en esta feature.

## Resumen

Hoy: El pipeline representa toda sala como un paralelepipedo rectangular definido por largo, ancho y alto. El muestreo de posiciones, la validacion de margenes, las superficies, el calculo preliminar de RT30 y la construccion RAVEN dependen de esa suposicion.

Despues: Cada escena puede elegir de forma reproducible una geometria shoebox, trapezoidal o L segun proporciones configurables. El orquestador materializa una huella 2D autoritativa, valida y con altura constante; el backend MATLAB la extruye y la convierte en caras RAVEN mediante `setModelToFaces`. Las posiciones, materiales, areas y RT30 se calculan sobre la geometria real.

## Historia

Como usuario que genera escenas acusticas sinteticas, necesito variar la forma de la sala y no solo sus dimensiones, para obtener conjuntos de datos con mayor diversidad geometrica sin definir manualmente puntos y caras de RAVEN.

Como usuario tecnico, necesito que una escena sea completamente reconstruible desde su manifiesto y que la misma configuracion, semilla e indice de escena produzcan exactamente la misma geometria, materiales y posiciones.

Como desarrollador del backend, necesito recibir una huella normalizada y explicita, con IDs de pared estables, para construir las caras RAVEN sin reimplementar la parametrizacion del YAML en MATLAB.

## Objetivos / No-Objetivos

### Objetivos

O1. Permitir una mezcla configurable de salas `shoebox`, `trapezoid` y `l_shape` por experimento.

O2. Definir trapecios convexos mediante `base_a_m`, `base_b_m`, `depth_m` y `top_offset_m`; se permiten bases iguales y no se expone rotacion global.

O3. Definir salas L ortogonales como un rectangulo exterior menos una esquina rectangular.

O4. Mantener paredes verticales, piso y cielo horizontales y una altura constante por sala.

O5. Aplicar un margen minimo de 0.5 m respecto de cualquier pared a receptor y fuentes.

O6. Sortear un material independiente por tramo de pared desde el pool comun de paredes.

O7. Mantener la guardia `max_rt30_s` usando areas y volumen reales.

O8. Garantizar que `config + seed + scene_index` reproduzca exactamente la escena, incluidos reintentos.

O9. Emitir manifiestos schema `2.0` con vertices autoritativos y suficientes datos para reconstruir y graficar la sala.

O10. Validar la integracion con un smoke test real de MATLAB/RAVEN antes de merge o release.

### No-Objetivos

NO1. Aceptar vertices arbitrarios o geometria RAVEN manual en el YAML de experimento.

NO2. Soportar huecos interiores, patios, obstaculos, multiples recortes o salas multinivel.

NO3. Soportar paredes o techos inclinados, alturas variables o geometria dinamica.

NO4. Generar PNG, SVG u otros artefactos graficos como salida productiva del pipeline.

NO5. Actualizar el output analyzer. Ese consumidor debe migrarse posteriormente a schema `2.0` y dejar de inferir area como largo por ancho.

NO6. Conservar indefinidamente el contrato antiguo. Solo se acepta una normalizacion localizada del shoebox legacy si no crea un segundo pipeline interno.

NO7. Cambiar politicas de fuentes salvo lo necesario para operar sobre la nueva geometria.

## Como funciona Hoy -> Como debiese funcionar

```text
HOY                                      DESPUES
dimensions_m = [L, W, H]                 geometry.shape_mix con tres tipos
seis superficies fijas                   N paredes + piso + cielo
setModelToShoebox(L, W, H)               setModelToFaces(points, faces, materials)
punto dentro de caja                     punto dentro de huella poligonal
margen contra limites por eje            distancia minima a segmentos
pared elegida entre cuatro IDs           tramo elegido proporcional a longitud
areas rectangulares para Sabine          areas reales por tramo y huella
schema 1.0                               schema 2.0
```

## Decisiones confirmadas

- El YAML usa formas parametrizadas, no vertices manuales.
- `shape_mix` admite multiples tipos y sus probabilidades suman exactamente `1.0`.
- La altura se muestrea desde un rango comun a todas las formas.
- El trapecio usa dos bases paralelas, profundidad y offset; admite bases iguales y no tiene parametro de rotacion.
- La L es ortogonal y se obtiene retirando una esquina rectangular del rectangulo exterior.
- Las esquinas permitidas se configuran como una lista y se eligen uniformemente.
- Las paredes son verticales y la altura es constante.
- El margen geometrico es 0.5 m.
- Una L sin region util se descarta y remuestrea.
- Una politica dirigida a pared elige tramos proporcionalmente a su longitud.
- `fixed_position` reintenta el receptor dentro de la sala; si no logra una escena valida, remuestrea la escena.
- Cada tramo sortea su material de forma independiente desde el pool comun de paredes.
- `max_rt30_s` se mantiene con areas reales.
- El manifiesto usa schema `2.0`; los vertices son autoritativos y `generated_from` es trazabilidad.
- `dimensions_m` no forma parte del nuevo contrato.
- Los IDs `wall_###` son canonicos y no direccionales.
- El tipo elegido para una escena se conserva durante sus reintentos.
- Al agotar reintentos, la escena se registra como fallida y el run continua para evaluacion manual.
- No se generan plots productivos; si existe un helper/test de visualizacion.
- El smoke test RAVEN real es opt-in, pero obligatorio antes de merge o release.

## Casos de uso

### CU1. Experimento con mezcla de formas

El usuario configura probabilidades para shoebox, trapecio y L. Cada `scene_index` elige un tipo una sola vez y conserva ese tipo durante todos sus reintentos.

### CU2. Trapecios asimetricos

El usuario configura rangos de bases, profundidad y offset. El pipeline genera cuadrilateros convexos, incluidos paralelogramos cuando las bases tienen el mismo largo.

### CU3. Salas L con distintas esquinas ausentes

El usuario configura una lista de esquinas posibles. Para cada sala L se elige una esquina uniformemente y se generan seis tramos de pared.

### CU4. Materiales distintos por pared

Cada tramo `wall_###` selecciona de forma independiente un material del pool de paredes. Piso y cielo conservan sus pools separados.

### CU5. Reconstruccion y debug

Un desarrollador abre el manifiesto y reconstruye la huella, el orden de paredes, materiales, receptor y fuentes sin consultar el YAML original ni ejecutar nuevamente el sampler.

## Contrato YAML propuesto

```yaml
room_sampling:
  max_rt30_s: 1.0

  geometry:
    height_m:
      min: 2.2
      max: 3.0

    shape_mix:
      - type: shoebox
        probability: 0.30
        length_m:
          min: 3.5
          max: 7.0
        width_m:
          min: 3.5
          max: 6.0

      - type: trapezoid
        probability: 0.35
        base_a_m:
          min: 3.5
          max: 7.0
        base_b_m:
          min: 3.0
          max: 7.0
        depth_m:
          min: 3.5
          max: 6.0
        top_offset_m:
          min: -2.0
          max: 2.0

      - type: l_shape
        probability: 0.35
        outer_length_m:
          min: 5.0
          max: 8.0
        outer_width_m:
          min: 5.0
          max: 8.0
        cutout_length_m:
          min: 1.0
          max: 3.0
        cutout_width_m:
          min: 1.0
          max: 3.0
        removed_corners:
          - north_east
          - north_west
          - south_east
          - south_west

  materials:
    walls:
      - bricks
      - concrete
      - glass
      - drywall
    floor:
      - wood
      - plaster
    ceiling:
      - wood
      - plaster
```

### Reglas del YAML

- `shape_mix` no puede estar vacio.
- Cada `probability` debe ser finita, mayor que cero y la suma debe ser `1.0` dentro de la tolerancia numerica usada por el validador.
- No puede repetirse un `type` dentro de `shape_mix`.
- Todo rango requiere valores finitos, positivos cuando representan una longitud, y `min <= max`.
- `top_offset_m` puede ser negativo, cero o positivo.
- `removed_corners` no puede estar vacio, no permite duplicados y solo acepta las cuatro esquinas canonicas.
- Los valores muestreados de `cutout_length_m` y `cutout_width_m` deben ser menores que sus dimensiones exteriores correspondientes.
- El ancho util de cada brazo L debe ser estrictamente mayor que `1.0 m`, equivalente a dos margenes de 0.5 m. Una muestra que no cumpla se remuestrea.
- La altura valida debe permitir la politica de altura del receptor y las fuentes.

## Diagramas geometricos

### Trapecio

```text
z
^                    base_b_m
|       D=(o,d) +----------------+ C=(o+b,d)
|                /                \
|               /                  \
|              /                    \
|     A=(0,0) +----------------------+ B=(a,0)  -> x
|                    base_a_m

a = base_a_m
b = base_b_m
d = depth_m
o = top_offset_m
```

Vertices previos a la normalizacion:

```text
[(0, 0), (a, 0), (o + b, d), (o, d)]
```

Con `a > 0`, `b > 0` y `d > 0`, la huella es convexa para cualquier offset finito. Luego se traslada para que `min(x)=0` y `min(z)=0`.

### Sala L

Ejemplo con esquina `north_east` removida:

```text
z
^
|  +----------------D
|  |                |
|  |                C---------+
|  |                          |
|  |                          |
|  A--------------------------B  -> x
|
|  rectangulo exterior - recorte de esquina superior derecha
```

Forma equivalente con dimensiones:

```text
+--------------------+---------+
|                    | recorte |
|                    |         |  cutout_width_m
|                    +---------+
|                              |
|                              |
+------------------------------+
       outer_length_m
```

Las otras esquinas se obtienen mediante transformaciones deterministas del mismo patron, seguidas de canonicalizacion.

## Contrato de manifiesto schema 2.0

Ejemplo abreviado:

```json
{
  "schema_version": "2.0",
  "scene_type": "static",
  "room": {
    "room_id": "room_0042",
    "geometry": {
      "type": "l_shape",
      "height_m": 2.6,
      "footprint_vertices_m": [
        [0.0, 0.0],
        [7.0, 0.0],
        [7.0, 3.5],
        [4.5, 3.5],
        [4.5, 6.0],
        [0.0, 6.0]
      ],
      "wall_ids": [
        "wall_001",
        "wall_002",
        "wall_003",
        "wall_004",
        "wall_005",
        "wall_006"
      ],
      "generated_from": {
        "outer_length_m": 7.0,
        "outer_width_m": 6.0,
        "cutout_length_m": 2.5,
        "cutout_width_m": 2.5,
        "removed_corner": "north_east"
      }
    },
    "materials": {
      "wall_001": "bricks",
      "wall_002": "glass",
      "wall_003": "drywall",
      "wall_004": "bricks",
      "wall_005": "concrete",
      "wall_006": "bricks",
      "floor": "wood",
      "ceiling": "plaster"
    },
    "material_files": {
      "wall_001": {
        "material_id": "bricks",
        "material_path": "C:/absolute/path/Bricks.mat"
      },
      "floor": {
        "material_id": "wood",
        "material_path": "C:/absolute/path/Wood.mat"
      },
      "ceiling": {
        "material_id": "plaster",
        "material_path": "C:/absolute/path/Plaster.mat"
      }
    }
  }
}
```

El ejemplo omite entradas repetitivas de `material_files`, receptor, fuentes y render.

### Reglas del manifiesto

- `footprint_vertices_m` es la fuente de verdad geometrica.
- `generated_from` explica como se genero la huella, pero nunca se usa para reconstruirla en MATLAB.
- `dimensions_m` se retira del nuevo contrato.
- `wall_ids` tiene exactamente el mismo numero de elementos que vertices.
- `wall_ids[i]` corresponde a la arista desde `vertices[i]` hasta `vertices[(i+1) mod N]`.
- `materials` y `material_files` contienen exactamente todos los `wall_ids`, mas `floor` y `ceiling`.
- `material_files.<surface>.material_id` coincide con `materials.<surface>`.
- Todo `material_path` es absoluto y existe antes del render.
- Los campos derivados como area, volumen o longitudes pueden incluirse en metadata de diagnostico, pero no sustituyen los vertices autoritativos.
- Los puntos `receiver.position_m` y `sources[].position_m` usan el mismo sistema de coordenadas que la geometria.

## Sistema de coordenadas y unidades

```text
Unidad de longitud: metros
Pose 3D: [x, y, z]
y: eje vertical y altura
Piso: y = 0
Cielo: y = geometry.height_m
Huella 2D: pares [x, z]
Origen normalizado: min(x) = 0 y min(z) = 0
```

El contrato publico no invierte Z. Si RAVEN requiere una conversion de ejes o signo, esta ocurre exclusivamente en el adaptador MATLAB. El smoke test debe verificar que geometria, receptor, fuentes y orientaciones usan la misma transformacion. La discrepancia actual entre la documentacion arquitectonica y las llamadas MATLAB directas debe resolverse durante la implementacion, sin cambiar el contrato publico aqui definido.

## Invariantes geometricas

Toda huella debe cumplir:

1. Tener vertices finitos y aristas de longitud positiva.
2. Ser un poligono simple, sin autointersecciones.
3. Tener area estrictamente positiva.
4. Usar un winding canonico unico.
5. Comenzar en un vertice elegido por una regla determinista.
6. Estar trasladada a `min(x)=0`, `min(z)=0`.
7. No contener vertices consecutivos duplicados.
8. Tener una region util no vacia despues de aplicar el margen de 0.5 m.

Adicionalmente:

- `shoebox` tiene cuatro vertices y angulos rectos.
- `trapezoid` tiene cuatro vertices, es convexo y posee el par de bases paralelas definido por el contrato.
- `l_shape` tiene seis vertices, es ortogonal, posee exactamente un angulo reflex de 270 grados y equivale al rectangulo exterior menos el recorte configurado.

### Canonicalizacion y wall IDs

La implementacion define una unica orientacion de recorrido y un vertice inicial determinista, por ejemplo el vertice lexicograficamente menor con una regla de desempate documentada. Despues asigna:

```text
edge[0] -> wall_001
edge[1] -> wall_002
...
edge[N-1] -> wall_NNN
```

Los IDs no prometen significado norte/sur/este/oeste. Su contrato es ser deterministas, corresponder a una arista explicita y permitir reconstruccion y plotting.

## Sampler, reintentos y fallo por escena

### Flujo principal

```text
sample_scene(config, seed, scene_index):
    rng = deterministic_rng(config, seed, scene_index)
    shape_type = weighted_choice_once(config.geometry.shape_mix, rng)

    for scene_attempt in 1..max_attempts:
        params = sample_parameters_for(shape_type, rng)
        height = sample_height(rng)
        footprint = build_and_canonicalize(shape_type, params)

        if not valid_geometry(footprint, height):
            continue
        if empty(inset(footprint, 0.5 m)):
            continue

        wall_ids = assign_canonical_wall_ids(footprint)
        materials = sample_each_surface_independently(wall_ids, rng)
        room = assemble_room(footprint, height, wall_ids, materials)

        rt30 = estimate_rt30_with_real_areas(room)
        if rt30 > max_rt30_s:
            continue

        for receiver_attempt in 1..max_attempts:
            receiver = sample_receiver_in_usable_region(room, rng)
            if not valid_receiver(receiver, room):
                continue

            sources = try_sample_sources(room, receiver, rng)
            if sources are valid:
                return SUCCESS(room, receiver, sources)

        # Incluye fixed_position sin solucion para los receptores probados.
        # Conserva shape_type, pero el siguiente scene_attempt remuestrea
        # parametros, geometria, materiales y receptor.

    record_failed_scene(stage=last_failed_stage, full_diagnostics)
    return FAILED
```

### Semantica de reintentos

- El tipo se elige una sola vez por `scene_index` y no cambia al rechazar una muestra.
- Los intentos consumen RNG en un orden estable y documentado.
- Una L inviable remuestrea sus parametros sin cambiar de tipo.
- Los rechazos por RT30 remuestrean geometria y materiales del mismo tipo.
- Para `fixed_position`, primero se reintenta el receptor dentro de la misma sala.
- Si no existe una colocacion valida para los receptores probados, el siguiente intento remuestrea la escena completa sin cambiar el tipo.
- Si se agota el presupuesto total de intentos de escena, esta se registra como fallida.
- Fallar una escena no detiene automaticamente las restantes; el run continua para permitir evaluacion manual.
- Una escena fallida no se sustituye silenciosamente por un indice adicional.
- El resultado global del run debe distinguir exito completo de exito parcial y listar todos los indices fallidos.

### Diagnostico minimo de fallo

Cada escena fallida registra como minimo:

- `scene_index` y seed efectiva;
- tipo elegido;
- etapa (`geometry`, `rt30`, `receiver`, `sources` o `fixed_position`);
- cantidad de intentos;
- ultimos parametros muestreados;
- ultima razon de rechazo;
- mejor estimacion RT30 observada cuando corresponda;
- path o identificador del registro de fallo.

## Muestreo de posiciones y paredes

### Punto uniforme dentro de la sala

```text
sample_point(footprint, clearance):
    usable = inset_polygon(footprint, clearance)
    triangles = triangulate(usable)
    triangle = weighted_choice(triangles, weight=triangle.area)
    xz = sample_uniform_barycentric(triangle)
    y = sample_allowed_height()
    return [xz.x, y, xz.z]
```

La seleccion ponderada por area evita sesgo entre triangulos. Todo punto se valida nuevamente contra la huella y el margen para absorber tolerancias numericas.

### Distancia a paredes

La distancia horizontal minima de un punto se calcula contra todos los segmentos de la huella, no contra el bounding box:

```text
wall_clearance(point_xz, footprint):
    return min(distance_point_to_segment(point_xz, edge)
               for edge in footprint.edges)
```

El margen requerido es `>= 0.5 m` para receptor y fuentes. Piso, cielo y restricciones de altura se validan por separado.

### Eleccion de una pared objetivo

```text
sample_wall(footprint, rng):
    edge = weighted_choice(footprint.edges, weight=edge.length)
    t = uniform(valid_interval_on_edge)
    point = interpolate(edge.start, edge.end, t)
    point = point + 0.5 m * inward_normal(edge)
    return point, edge.wall_id, inward_normal(edge)
```

La orientacion `facing_surface_normal` se deriva de la normal interior del tramo, no de una tabla de cuatro yaw cardinales.

## Area, volumen y RT30

### Geometria derivada

```text
floor_area_m2 = abs(shoelace(footprint_vertices)) / 2
ceiling_area_m2 = floor_area_m2
volume_m3 = floor_area_m2 * height_m

for each wall edge:
    wall_area_m2[wall_id] = edge_length(edge) * height_m
```

Para una L tambien se puede comprobar:

```text
floor_area_m2 = outer_length * outer_width
                - cutout_length * cutout_width
```

La formula shoelace sobre los vertices autoritativos es la fuente comun para todos los tipos; la formula parametrica sirve como invariante de test.

### Guardia Sabine

Por banda:

```text
equivalent_absorption_m2 =
    floor_area   * alpha_floor
  + ceiling_area * alpha_ceiling
  + sum(wall_area[id] * alpha_wall[id] for each wall id)

rt30_s = 0.161 * volume_m3 / equivalent_absorption_m2
```

Se mantienen las bandas, agregacion, validaciones de finitud y semantica de `max_rt30_s` existentes. Solo cambia el calculo geometrico de volumen y areas.

## Construccion MATLAB/RAVEN

El backend schema `2.0` usa `setModelToFaces`, no `setModelToShoebox`.

```text
build_raven_faces(room):
    footprint = room.geometry.footprint_vertices_m
    h = room.geometry.height_m

    floor_points   = [(x, 0, z) for each (x,z)]
    ceiling_points = [(x, h, z) for each (x,z)]
    points = floor_points + ceiling_points

    cap_triangles = triangulate(footprint)
    floor_faces = orient_inward(cap_triangles, material="floor")
    ceiling_faces = orient_inward(cap_triangles, material="ceiling")

    wall_faces = []
    for each edge i:
        wall_faces.append(
            inward_oriented_quad(edge, height=h,
                                 material=room.geometry.wall_ids[i]))

    faces = floor_faces + ceiling_faces + wall_faces
    assert_normals_point_inward(faces, interior_reference_points)

    rpf.setModelToFaces(points, faces, material_slot_names)
```

### Piso y cielo

Piso y cielo se triangulan, incluso para formas convexas, para usar un unico camino y no depender de soporte RAVEN para poligonos concavos. Todos los triangulos del piso comparten el material semantico `floor`; todos los del cielo comparten `ceiling`.

La triangulacion no debe introducir vertices fuera de la huella ni cambiar el area total. Para una L, la suma de areas de triangulos debe coincidir con el area del rectangulo exterior menos el recorte.

### Normales

RAVEN requiere que las normales apunten al interior. El orden de vertices de cada cara se deriva del winding canonico y se verifica geometricamente. No se debe depender solo de una inversion fija sin test.

### Materiales

- La lista de nombres entregada a `setModelToFaces` contiene los IDs semanticos unicos usados por las caras.
- Varias caras tecnicas pueden referenciar el mismo material semantico.
- Cada `wall_###` puede tener coeficientes distintos.
- La aplicacion de absorcion y scattering se resuelve por identidad de nombre, no por el orden devuelto por `getRoomMaterialNames`.
- El backend debe rechazar nombres faltantes, duplicados o inesperados con errores explicitos.

## Reproducibilidad

Para una version fija del sampler:

```text
(config normalizada, seed, scene_index) -> manifiesto determinista
```

Esto incluye:

- tipo de forma;
- parametros y altura;
- esquina removida;
- vertices canonicalizados;
- IDs y materiales;
- reintentos y resultado de guardia RT30;
- receptor, fuentes y politicas de pared;
- registro de fallo cuando no se logra una escena valida.

La implementacion debe evitar iterar sobre mapas sin orden estable, depender del orden del filesystem o usar RNG global fuera del flujo controlado. Un cambio deliberado del algoritmo que altere escenas debe documentarse como cambio de version del sampler/estimator.

## Compatibilidad y migracion

### YAML legacy

Se puede aceptar temporalmente `room_sampling.dimensions_m` como un shoebox con probabilidad `1.0` solo si la conversion queda localizada en el loader:

```text
legacy dimensions_m
    -> normalize once
    -> new internal geometry model
```

No se permiten ramas shoebox legacy en sampler, scene builder, estimador acustico o MATLAB. Si la normalizacion complica significativamente los modelos estrictos o produce ambiguedad, se elimina y se actualizan los YAML de desarrollo.

### Manifiesto

- Los nuevos manifiestos usan exclusivamente schema `2.0`.
- No se escribe `dimensions_m` en schema `2.0`.
- El backend puede conservar temporalmente el lector `1.0` y `setModelToShoebox` si queda aislado, pero la feature no exige compatibilidad indefinida.
- No se mezclan campos `1.0` y `2.0` en un mismo manifiesto.

### Consumidores posteriores

El output analyzer se actualizara en otra feature para leer tipo, area real, volumen y bounds. Hasta entonces no debe interpretar manifiestos `2.0` como si fueran rectangulares. Esta dependencia no bloquea la construccion ni el render de la feature actual.

## Cambios por componente

### `apps/orchestrator/config`

- Modelos discriminados para las tres variantes.
- Validacion de probabilidades, rangos, esquinas y factibilidad basica.
- Normalizacion legacy opcional y localizada.

### `apps/orchestrator/experiment/sampler.py`

- Seleccion de tipo una vez por escena.
- Generadores de huella parametrizados.
- Canonicalizacion, inset, triangulacion y operaciones punto/poligono.
- Muestreo uniforme por area.
- Distancia a segmentos, eleccion de pared por longitud y normales interiores.
- Reintentos deterministas y registro de escenas fallidas.

Se puede extraer un modulo geometrico pequeno si mantiene estas operaciones puras y evita sobrecargar `sampler.py`. No se requiere un framework generico de geometria.

### `apps/orchestrator/experiment/room_acoustics.py`

- Area de huella por shoelace.
- Volumen por extrusion.
- Areas independientes por tramo.
- Guardia Sabine con los materiales reales de cada superficie.

### `apps/orchestrator/experiment/scene_builder.py`

- Emision de schema `2.0`.
- `geometry`, vertices, wall IDs y `generated_from`.
- Retiro de `dimensions_m`.

### `apps/matlab/core/validate_static_scene_config.m`

- Validacion del contrato `2.0`.
- Validacion de vertices, paredes y materiales variables.
- Coherencia entre posiciones y geometria.

### `apps/matlab/core/build_room_from_config.m`

- Adaptacion de huella extruida a `points`, `faces` y slots de material.
- Uso de `setModelToFaces`.
- Triangulacion de caps y normales interiores.
- Aplicacion de materiales por identidad.

### Tests y mocks MATLAB

- Mock para `setModelToFaces`.
- Captura de puntos, caras, materiales y llamadas `setMaterial`.
- Tests de winding, indices, triangulacion y correspondencia de slots.

### Ejemplos y documentacion

- Actualizar el par normativo YAML/manifiesto estatico.
- Documentar schema `2.0`, coordenadas y smoke test.
- Incluir al menos un ejemplo trapezoidal y uno L.

### Fuera de esta feature

- Adaptacion de `apps/output-analyzer`.
- Dashboards o plots productivos.
- Geometria dinamica.

## Plotting de desarrollo

Debe existir un helper o test capaz de mostrar:

- huella;
- orden de vertices;
- etiquetas `wall_###`;
- receptor y fuentes;
- opcionalmente normales interiores y triangulacion.

Este plotting sirve para debug y tests manuales. No es ejecutado por defecto ni genera artefactos dentro del output productivo.

## Criterios de aceptacion

CA1. Una configuracion valida puede mezclar los tres tipos y las probabilidades se validan contra suma `1.0`.

CA2. La misma configuracion, seed e indice genera exactamente el mismo manifiesto, incluido un registro de fallo determinista si corresponde.

CA3. Todo manifiesto nuevo usa `schema_version: "2.0"`, no contiene `dimensions_m` y puede reconstruirse solo desde sus vertices, altura y wall IDs.

CA4. Trapecios con offsets negativos, cero y positivos son simples, convexos y correctamente normalizados. Bases iguales son aceptadas.

CA5. Las cuatro variantes de esquina L generan seis paredes en orden canonico y area igual a exterior menos recorte.

CA6. Ningun receptor ni fuente aceptado queda fuera de la huella o a menos de 0.5 m de una pared.

CA7. Las paredes objetivo se eligen proporcionalmente a su longitud y `facing_surface_normal` usa la normal interior real.

CA8. Cada pared puede recibir un material distinto; piso y cielo mantienen un material semantico aunque tengan varias caras tecnicas.

CA9. La estimacion RT30 usa area y volumen reales y conserva las bandas y agregacion existentes.

CA10. MATLAB construye schema `2.0` mediante `setModelToFaces`; las normales apuntan al interior y los slots se resuelven por identidad.

CA11. Una escena que agota reintentos queda registrada con diagnostico completo; las otras escenas continuan y el run informa estado parcial.

CA12. Existe un plotting de desarrollo que reconstruye la sala desde el manifiesto, sin generar outputs productivos.

CA13. El smoke test opt-in renderiza al menos un trapecio asimetrico y una L concava con RAVEN real, obtiene BRIR/T30 y no presenta errores geometricos o de materiales.

CA14. El smoke test real se ejecuta y aprueba antes de merge o release, aunque no forme parte de la suite local/CI por defecto.

## Estrategia de tests

### Unitarios de configuracion

- probabilidades validas, suma incorrecta, cero, negativas, NaN e infinitos;
- variantes repetidas o mix vacio;
- rangos invalidos;
- corners vacios, duplicados o desconocidos;
- normalizacion legacy, si se implementa.

### Unitarios y property tests geometricos

- construccion de cada forma;
- offsets trapezoidales negativos, positivos y extremos;
- bases iguales;
- las cuatro esquinas L;
- poligono simple, winding, area, traslacion y IDs;
- area por shoelace contra formula parametrica;
- inset y rechazo de brazos L inviables;
- triangulacion con conservacion de area;
- punto dentro/fuera y distancia a segmentos;
- muestreo uniforme por triangulos ponderados por area;
- normal interior por arista.

### Tests del sampler

- distribucion de tipos dentro de tolerancia estadistica en una muestra grande;
- tipo inmutable durante reintentos;
- pared elegida proporcional a longitud;
- materiales independientes por tramo;
- `fixed_position` valido e invalido en el recorte L;
- guardia RT30 con areas reales;
- registro de fallo y continuacion del run;
- snapshot de reproducibilidad para seed e indices conocidos.

### Tests de contrato

- round-trip de manifiesto `2.0`;
- vertices autoritativos y correspondencia con wall IDs;
- ausencia de `dimensions_m`;
- paths de materiales absolutos;
- rechazo de superficies faltantes, extras o duplicadas.

### Tests MATLAB con mocks

- puntos y caras esperados por forma;
- indices validos y winding correcto;
- caps triangulados;
- material compartido entre triangulos de piso/cielo;
- material independiente por pared;
- slots RAVEN retornados en orden arbitrario;
- errores explicitos para slots faltantes, duplicados o ambiguos.

### Smoke test real opt-in

Precondiciones: Windows, MATLAB, ITA Toolbox, RAVEN, base RPF, materiales, fuente y HRTF reales.

El smoke oficial ejecuta directamente la configuracion canonica mediante el orquestador:

```sh
uv run --project apps/orchestrator acoustic-orchestrator render-static configs/experiments/static_example.yml
```

Casos minimos:

1. Trapecio asimetrico con materiales distintos por pared.
2. L concava con piso y cielo triangulados.

Para cada caso:

- abrir/renderizar el proyecto RAVEN;
- inspeccionar `plotModel` y normales hacia el interior;
- verificar receptor y fuentes dentro de la sala;
- ejecutar simulacion;
- obtener BRIR y T30;
- confirmar salida y metadata;
- reconstruir la huella desde el manifiesto.

Este test es opt-in por sus dependencias locales, pero es un gate obligatorio y documentado antes de merge o release.

## Riesgos y mitigaciones

### R1. RAVEN y caras concavas

Riesgo: RAVEN puede no aceptar caps concavos o puede interpretar mal su winding.

Mitigacion: triangular piso y cielo en caras convexas, verificar normales y exigir smoke test real.

### R2. Transformacion de coordenadas inconsistente

Riesgo: documentacion existente menciona una inversion Z que no es visible en las llamadas MATLAB revisadas.

Mitigacion: contrato publico unico, transformacion solo en el adaptador y prueba conjunta de geometria/poses/orientaciones.

### R3. Sesgo por rechazos

Riesgo: tipos con mas muestras invalidas podrian quedar subrepresentados.

Mitigacion: elegir el tipo una vez y conservarlo; registrar fallos en vez de cambiar silenciosamente de tipo.

### R4. Reintentos y costo

Riesgo: L estrechas, RT30 exigente o `fixed_position` pueden consumir muchos intentos.

Mitigacion: validacion temprana de rangos, diagnosticos completos y presupuestos acotados.

### R5. Tolerancias geometricas

Riesgo: puntos cerca de aristas o triangulos degenerados producen resultados distintos entre Python y MATLAB.

Mitigacion: definir una tolerancia comun, revalidar puntos y cubrir limites con property tests.

### R6. Compatibilidad legacy contaminante

Riesgo: mantener dos contratos aumenta ramas y errores.

Mitigacion: normalizar una sola vez o retirar compatibilidad si no queda localizada.

### R7. Run parcialmente exitoso

Riesgo: continuar tras fallos puede ser confundido con exito total.

Mitigacion: estado final explicito, lista de escenas fallidas y diagnosticos persistentes; nunca sustituir indices silenciosamente.

### R8. IDs estables pero no semanticos

Riesgo: consumidores pueden asumir que `wall_001` siempre apunta a una direccion cardinal.

Mitigacion: documentar que el ID identifica una arista canonica, no una direccion; usar vertices para cualquier interpretacion espacial.

## Preguntas abiertas

No quedan preguntas de producto bloqueantes para iniciar el diseno e implementacion.

Durante la implementacion deben resolverse y documentarse dos detalles tecnicos mediante pruebas, sin alterar el contrato del PRD:

1. La transformacion exacta de ejes/signos requerida por la version local de RAVEN.
2. La tolerancia numerica comun para canonicalizacion, inset, triangulacion y validacion de margenes.
