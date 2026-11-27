# whis-hearing-processor Specification

## Purpose

Aplicar el simulador WHIS v302 (con el banco gammachirp GCFBv234) como procesador de degradación auditiva sobre renders binaurales HRTF, con el mismo contrato de manifiesto y sidecar que MSBG y con calibración, remuestreo, parámetros y versión registrados.

## Requirements

### Requirement: Formato de entrada y salida
El procesador WHIS SHALL aceptar WAV estéreo de 2 canales a 44100 o 48000 Hz y rechazar con un job `failed` cualquier otra frecuencia o número de canales. El WAV de salida SHALL tener la misma frecuencia de muestreo y el mismo número de muestras que la entrada, y SHALL escribirse en float32 sea cual sea el subtipo de entrada.

#### Scenario: Frecuencia no soportada
- **WHEN** la entrada de un job WHIS está a 32000 Hz
- **THEN** el job queda `failed` con un error que nombra la frecuencia y las soportadas

#### Scenario: Longitud conservada
- **WHEN** un job WHIS procesa una entrada PCM de 16 bits de 441000 muestras a 44100 Hz
- **THEN** la salida tiene 441000 muestras a 44100 Hz en float32

### Requirement: Procesamiento a 48 kHz
WHIS SHALL procesar a 48000 Hz. Una entrada a 44100 Hz SHALL remuestrearse a 48000 Hz antes del procesamiento y volver a 44100 Hz después, con el mismo filtro para ambos canales. El sidecar SHALL registrar la frecuencia de proceso y si hubo remuestreo.

#### Scenario: Entrada a 44.1 kHz
- **WHEN** un job WHIS recibe una entrada a 44100 Hz
- **THEN** el sidecar registra `processing_sample_rate_hz: 48000` y `resampled: true`

### Requirement: Procesamiento por oído con nivel calibrado
Cada canal SHALL procesarse de forma independiente, con el audiograma y la salud de compresión de su oído y con su nivel SPL derivado del K del render que trae el job. El procesador SHALL NOT usar una ruta que normalice cada canal a un nivel fijo.

#### Scenario: Pérdida asimétrica
- **WHEN** un perfil declara una pérdida mayor en el oído derecho y la entrada es diótica
- **THEN** el canal derecho de salida recibe más atenuación que el izquierdo

#### Scenario: Diferencia de nivel interaural
- **WHEN** una entrada con el canal izquierdo 10 dB por encima del derecho se procesa con un perfil simétrico
- **THEN** el canal izquierdo se simula a un nivel SPL 10 dB mayor que el derecho, y no ambos al mismo nivel

### Requirement: Mapeo de audiograma a las bandas de WHIS
WHIS SHALL recibir los umbrales en 125, 250, 500, 1000, 2000, 4000 y 8000 Hz. Si un oído no declara la banda `125`, SHALL usarse el valor de `250` y el sidecar SHALL registrar `125` como banda extrapolada de ese oído.

#### Scenario: Perfil sin banda de 125 Hz
- **WHEN** un perfil declara solo las bandas de 250 a 8000 Hz
- **THEN** el umbral aplicado en 125 Hz es igual al de 250 Hz y `degradation_applied.<oído>.extrapolated_bands` contiene `125`

### Requirement: Salud de compresión pedida y aplicada
El sidecar SHALL registrar por oído la `compression_health` pedida y la efectivamente aplicada por WHIS en cada banda, incluso cuando WHIS la ajusta para que sea compatible con el umbral, y SHALL registrar el reparto de la pérdida entre componente activa y pasiva que informa WHIS.

#### Scenario: Ajuste interno de compresión
- **WHEN** WHIS corrige la salud de compresión de una banda para que sea coherente con su umbral
- **THEN** el sidecar muestra en esa banda valores pedido y aplicado distintos, sin que el job falle

### Requirement: Corrección de oído según el valor compartido
WHIS SHALL aplicar la corrección de oído externo y medio que indica el job (`free_field` → `FreeField`, `diffuse_field` → `DiffuseField`, `itu` → `ITU`, `eardrum` → `EarDrum`), sin un valor por defecto propio.

#### Scenario: Corrección ITU
- **WHEN** un job WHIS declara `outer_middle_ear: itu`
- **THEN** WHIS procesa con la corrección `ITU` de GCFB y el sidecar registra `itu`

### Requirement: Método de síntesis
El método de síntesis SHALL ser `dtvf` por defecto y `fbanasyn` si se configura, y SHALL registrarse en el sidecar.

#### Scenario: Síntesis por banco de filtros
- **WHEN** la configuración declara `synth_method: fbanasyn`
- **THEN** los jobs usan análisis-síntesis por banco de filtros y el sidecar lo registra

### Requirement: Aislamiento de fallos con una sesión MATLAB por manifiesto
El backend SHALL procesar todos los jobs WHIS de un manifiesto en una sola sesión MATLAB. El fallo de un job SHALL producir su sidecar `failed` sin detener los demás. Si la sesión termina de forma anómala, cada job sin resultado SHALL quedar `failed` con un error de sesión. El backend SHALL salir con código 1 si algún job falló.

#### Scenario: Un job falla dentro de la sesión
- **WHEN** WHIS lanza un error al procesar uno de tres jobs
- **THEN** ese job tiene un sidecar `failed` con el error de MATLAB, los otros dos se completan y el backend sale con código 1

#### Scenario: La sesión MATLAB muere
- **WHEN** el proceso MATLAB termina antes de procesar todos los jobs
- **THEN** los jobs sin resultado tienen sidecar `failed` con un error de sesión y ninguno queda sin sidecar

### Requirement: Sin recorte
La salida SHALL conservar los valores por encima de la escala completa sin recortarlos ni reescalarlos, y su pico SHALL registrarse en el sidecar de los jobs completados.

#### Scenario: Salida que excede la escala completa
- **WHEN** la salida de un job con entrada PCM de 16 bits tiene un pico de 1.05
- **THEN** el job se completa, el WAV float32 conserva el pico de 1.05 y el sidecar registra `output_peak: 1.05`

### Requirement: Trazabilidad de versión
El bloque `processor` del sidecar SHALL incluir `name: whis`, `whis_version: WHISv302`, `gcfb_version: GCFBv234`, el commit de cada checkout (o `null` si no es un repositorio git), la versión de MATLAB y los parámetros aplicados (`synth_method`, `outer_middle_ear`, `processing_sample_rate_hz`).

#### Scenario: Checkout sin git
- **WHEN** `install_root` es una copia sin metadatos git
- **THEN** los campos de commit son `null` y el job se completa igualmente
