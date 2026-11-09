# Spec Delta

## ADDED Requirements

### Requirement: Reanudación automática tras fallo de publicación
Con `resume_if_possible=true`, `render-static` SHALL detectar y reparar aceptaciones no persistidas recuperables según `static-render-acceptance` antes de despachar nuevas variantes. SHALL hacerlo sin flags adicionales ni exigir `overwrite_existing=true`. Con reanudación desactivada SHALL NOT aplicar esa reparación automática.

#### Scenario: Segunda llamada sobre lote detenido por WinError 5
- **WHEN** se repite el mismo comando con entradas compatibles, el bloqueo de almacenamiento ya no existe y el intento cumple el contrato de reparación
- **THEN** se confirma y reutiliza ese intento, se reutilizan las variantes ya aceptadas y solo las pendientes autorizadas invocan el backend

#### Scenario: Recuperación de evidencia anterior a este cambio
- **WHEN** un outcome versión 1 registra `storage_failure` y `acceptance_persistence_failed` con resultado de proceso observado, cadena coherente y constancia íntegra, pero no tiene campos nuevos de reparación
- **THEN** se permite verificarlo y repararlo mediante la misma auditoría sin fabricar evidencia de ejecución pasada

#### Scenario: Índice ausente o rezagado
- **WHEN** la evidencia recuperable identifica inequívocamente el intento y sus salidas y el índice falta o conserva el estado anterior sin referencias contradictorias
- **THEN** se reconstruye su registro desde evidencia verificada, sin tratarlo como resultado legacy ni reemplazar referencias a otro intento

#### Scenario: Reanudación desactivada
- **WHEN** se invoca `render-static` con `resume_if_possible=false`
- **THEN** no se ejecuta reparación automática y se aplican las reglas vigentes de conflicto o sobrescritura explícita

### Requirement: Reparación subordinada al preflight y exclusión de escritor
La reparación SHALL ejecutarse bajo exclusión de escritor, después de comprobar compatibilidad del lote y ausencia de vetos de integridad o procesos no resueltos. SHALL NOT retirar locks, resolver terminaciones no observadas, reiniciar circuitos ni renovar presupuestos. Si no puede verificarse su seguridad SHALL bloquearse con motivo explícito.

#### Scenario: Otra cadena conserva un proceso no resuelto
- **WHEN** hay un candidato reparable pero el lote contiene un intento reservado o en ejecución sin terminación observada
- **THEN** no se publica la reparación ni se despachan renders; sigue siendo necesaria la intervención prevista por el contrato existente

#### Scenario: Conflicto en otra escena del lote
- **WHEN** el preflight detecta entradas incompatibles o evidencia contradictoria antes de reparar un candidato
- **THEN** se informa el conflicto sin modificar plan, índice, outcomes ni archivos protegidos

#### Scenario: Circuito o lock persistente
- **WHEN** el circuito del lote está abierto o no puede adquirirse el lock de escritor
- **THEN** la reparación no rompe ni reinicia ese estado y no permite nuevos despachos

### Requirement: Reparación no destructiva y fallo recurrente visible
La reparación SHALL conservar bytes y fechas de modificación de manifiestos, fuentes preparadas, recursos privados, constancias, WAV y sidecars. Si vuelve a fallar la publicación SHALL detener nuevos despachos, conservar una reparación retomable y explicar el nuevo error sin declarar la variante aceptada.

#### Scenario: Comparación de archivos antes y después
- **WHEN** se repara una aceptación válida y se completa la reanudación
- **THEN** solo cambian metadatos de estado, auditoría y resumen; no se regeneran ni archivan entradas o salidas protegidas

#### Scenario: Persisten los permisos denegados
- **WHEN** el almacenamiento vuelve a impedir completar la reparación
- **THEN** la CLI finaliza con error explícito, no lanza el backend y la siguiente llamada puede retomar la misma reparación si la evidencia continúa siendo válida
