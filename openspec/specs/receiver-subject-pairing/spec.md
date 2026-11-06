# receiver-subject-pairing Specification

## Purpose

Emparejar por sujeto las respuestas cefálicas de una escena estática sin cambiar sus condiciones de emisión o recepción. Estos requisitos describen el comportamiento propuesto para el orquestador; no afirman que el muestreo independiente actual ya lo implemente.

## Requirements

### Requirement: Receiver subject identity convention

Cuando haya dos o más salidas cefálicas habilitadas, el orquestador SHALL identificar el sujeto por el nombre base del archivo, quitando un sufijo terminal `_HRTF`, `_HARTF_front` o `_HARTF_rear` si existe. SHALL comparar las identidades resultantes de forma exacta, sin convertir números, cambiar mayúsculas ni inferir identidad por orden de archivos.

#### Scenario: Current catalog naming
- **WHEN** los catálogos contienen `id10_HRTF.daff`, `id10_HARTF_front.daff` y `id10_HARTF_rear.daff`
- **THEN** los tres archivos tienen la identidad de receptor `id10`
- **AND** `id01` e `id1` no se consideran equivalentes

#### Scenario: Shared unsuffixed names
- **WHEN** dos catálogos habilitados contienen cada uno `subject01.daff`
- **THEN** ambos archivos tienen la identidad `subject01`
- **AND** no se exige renombrarlos a la convención `idNN`

### Requirement: Shared receiver subject selection

Para dos o más salidas entre `binaural_hrtf`, `bte_front_hartf` y `bte_rear_hartf` habilitadas, el orquestador SHALL seleccionar sin repetición una secuencia de sujetos de la intersección de sus catálogos. Cada salida SHALL usar los primeros `num_hrtfs` sujetos de esa secuencia, cuya longitud SHALL ser el máximo de las cantidades solicitadas. Las salidas ausentes, deshabilitadas y el array circular SHALL quedar fuera de la intersección.

#### Scenario: One matched triplet
- **WHEN** las tres salidas están habilitadas con `num_hrtfs: 1`
- **THEN** los tres elementos de `receiver.hrtfs` apuntan a respuestas del mismo sujeto
- **AND** ninguna salida realiza una selección independiente como sustitución

#### Scenario: Unequal output counts
- **WHEN** HRTF solicita dos respuestas y front y rear solicitan una cada una
- **THEN** se seleccionan dos sujetos distintos presentes en los tres catálogos
- **AND** front y rear usan el mismo sujeto que `binaural_hrtf__1`
- **AND** se conservan exactamente las cantidades solicitadas

#### Scenario: Only front and rear enabled
- **WHEN** solo front y rear están habilitadas y existe un sujeto común entre ellas
- **THEN** el emparejamiento no requiere que exista un catálogo HRTF habilitado

### Requirement: Pairing catalog validation

El orquestador SHALL rechazar antes del render los catálogos participantes que produzcan identidades vacías o duplicadas, y las configuraciones cuya intersección tenga menos sujetos que el máximo `num_hrtfs`. El error SHALL identificar salidas o catálogos afectados y el motivo; para capacidad insuficiente SHALL indicar sujetos comunes disponibles y cantidad necesaria. SHALL conservar la validación individual existente.

#### Scenario: Disjoint catalogs
- **WHEN** cada catálogo contiene archivos suficientes, pero no tienen sujetos en común
- **THEN** la configuración falla antes de invocar MATLAB y no se generan variantes de reemplazo
- **AND** el error indica cero sujetos comunes y la cantidad requerida

#### Scenario: Ambiguous identity
- **WHEN** un catálogo participante contiene `id10.daff` e `id10_HRTF.daff` y ambos coinciden con su patrón
- **THEN** la validación identifica `id10` como ambiguo e informa los archivos en conflicto

#### Scenario: Partial catalog overlap
- **WHEN** existen tres sujetos comunes y se solicitan dos por salida, aunque haya archivos exclusivos de cada catálogo
- **THEN** la validación acepta el emparejamiento y la selección excluye los sujetos no comunes

### Requirement: Deterministic selection and scene preservation

Con la misma configuración, semilla y catálogos, el orquestador SHALL generar la misma selección emparejada independientemente del orden de enumeración de archivos. Para entradas válidas antes y después del cambio, SHALL conservar respecto al muestreo anterior la sala, los estímulos, poses, ganancias y tiempos, excluyendo únicamente las respuestas seleccionadas. Esto no garantiza determinismo del motor RAVEN.

#### Scenario: File enumeration order changes
- **WHEN** se generan manifiestos con idénticas entradas y semilla, pero distinto orden de enumeración del sistema de archivos
- **THEN** la secuencia de sujetos y sus respuestas seleccionadas es idéntica

#### Scenario: Receiver pairing does not resample the scene
- **WHEN** se compara una configuración multi-output válida antes y después de incorporar el emparejamiento
- **THEN** coinciden sala, archivos de estímulo, directividades, posiciones, orientaciones, ganancias y tiempos
- **AND** las tres variantes runtime conservan las condiciones compartidas de la escena

### Requirement: Existing manifest and output compatibility

El orquestador SHALL conservar `receiver.hrtfs` con `hrtf_id` y `hrtf_path`, los identificadores sin sufijo cuando se solicita una respuesta y los ordinales `__1`, `__2` cuando se solicitan varias. SHALL mantener las rutas y nombres públicos de salida. El emparejamiento SHALL no exigir nuevos campos a MATLAB o Clarity ni reescribir manifiestos o resultados históricos.

#### Scenario: Existing renderer interface
- **WHEN** una escena emparejada solicita una respuesta por salida
- **THEN** los IDs siguen siendo `binaural_hrtf`, `bte_front_hartf` y `bte_rear_hartf`
- **AND** los manifiestos runtime conservan la ruta seleccionada correspondiente a cada salida
- **AND** no se introduce un nuevo campo obligatorio para procesarlos

### Requirement: Single output compatibility

Con una sola salida cefálica habilitada, el orquestador SHALL conservar la selección de archivos, las reglas de validación y el comportamiento de semilla anteriores, sin exigir intersección de identidades. Con solo array circular SHALL no requerir catálogos cefálicos. La elegibilidad de Clarity SHALL seguir siendo la definida por su contrato existente.

#### Scenario: Legacy single output catalog
- **WHEN** una configuración habilita únicamente HRTF con nombres de archivo arbitrarios y la misma semilla e inputs que antes
- **THEN** la selección de archivos coincide con la anterior y no se aplican restricciones nuevas de identidad

#### Scenario: Circular array only
- **WHEN** la configuración habilita solo el array circular
- **THEN** la generación continúa sin descubrir ni validar identidades HRTF o HARTF
