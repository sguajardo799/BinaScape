# Spec Delta

## ADDED Requirements

### Requirement: Finalización automática con terminación acreditada
Con `resume_if_possible=true`, `render-static` SHALL detectar el último intento inequívoco de una cadena con cierre pendiente y completar su finalización si existe aceptación confirmada, terminación observada, constancia íntegra y validación funcional completa compatible. SHALL permitirlo sin sobrescritura, sin flags nuevos y sin invocar el backend para ese intento.

#### Scenario: Segunda llamada tras fallo al guardar clasificación
- **WHEN** una cadena permanece `running`, su outcome versión 1 está `committed` y aceptado con código cero observado, y la clasificación no llegó a publicarse
- **THEN** el preflight revalida el intento y, si cumple todas las guardas, publica su finalización auditable antes de reutilizarlo y continuar las pendientes

#### Scenario: Clasificación publicada pero cadena sin cerrar
- **WHEN** el outcome ya contiene clasificación aceptada y decisión terminal coherentes pero la escritura de la cadena no se completó
- **THEN** se permite completar el mismo cierre sin requerir repetir el proceso ni atribuir éxito a un temporal

#### Scenario: Resultado no aceptable
- **WHEN** falta constancia, el outcome no está confirmado, T30 canónico es rechazado o la evidencia contradice identidad, entradas o artefactos
- **THEN** esta finalización automática no acepta el intento ni programa otro proceso para eludir el rechazo

### Requirement: Excepción acreditada al veto de cadena running
El preflight SHALL levantar el veto de `running` únicamente para candidatos de finalización verificados positivamente. SHALL comprobar todo el lote bajo exclusión de escritor antes de publicar reparaciones. Un lock, circuito abierto, conflicto o intento sin terminación observada SHALL bloquear la operación sin cambiar estados protegidos ni inferir terminación por ausencia de PID.

#### Scenario: Otro intento tiene terminación desconocida
- **WHEN** un candidato es verificable pero otra cadena conserva una invocación no resuelta sin resultado observado
- **THEN** no se publican reparaciones ni se lanzan pendientes; el contrato de reconciliación manual de esa invocación se conserva

#### Scenario: Running sin acreditación suficiente
- **WHEN** una cadena está `running` y solo existen WAV, mensajes de éxito, temporales o ausencia de procesos en el inventario local
- **THEN** se mantiene el bloqueo y no se interpreta ese estado como un cierre reparable

#### Scenario: Conflicto en otra escena o lock persistente
- **WHEN** el preflight global detecta incompatibilidad en otra escena o no puede verificar exclusión de escritor
- **THEN** no modifica outcomes, cadenas, índices ni evidencia de reparación antes de informar el conflicto

### Requirement: Finalización no destructiva y contabilidad conservada
La finalización SHALL conservar bytes y fechas de modificación de entradas, recursos privados, constancias, WAV y sidecars. SHALL contabilizarse una vez en `persistence_repairs` y `persistencias_reparadas` de la llamada que la confirma, además de la reutilización pertinente, sin aumentar invocaciones, reintentos de proceso ni presupuesto consumido.

#### Scenario: Run mixto después del cierre
- **WHEN** una llamada completa un cierre reparable, reutiliza resultados ya aceptados y ejecuta variantes pendientes autorizadas
- **THEN** cuenta una reparación de persistencia y solo las pendientes aumentan las invocaciones; los archivos protegidos anteriores conservan contenido y mtime

#### Scenario: Repetición del comando tras cierre completo
- **WHEN** se repite el comando sobre el mismo intento ya finalizado sin cambios de evidencia
- **THEN** no se crea otra reparación ni un UUID nuevo y su contador de reparaciones en esa llamada es cero

#### Scenario: Reanudación desactivada
- **WHEN** `resume_if_possible=false`
- **THEN** no se ejecuta esta finalización automática y se conservan las reglas vigentes de conflicto y sobrescritura explícita
