# Spec Delta

## ADDED Requirements

### Requirement: Estado visible de parada controlada
La CLI render-static SHALL informar parada solicitada, cantidad de procesos que espera y resultado detenido sin presentar pendientes como completos. Tras una parada limpia SHALL devolver 130 y un resumen parcial; si falla la integridad o persistencia SHALL devolver 1 y conservar contexto, ruta y causa. Los mensajes de parada y error SHALL mantenerse con --no-progress.

#### Scenario: Drenaje lento
- **WHEN** el operador solicita parar y un render tarda en terminar
- **THEN** se informa que se espera su finalización sin prometer un plazo ni atribuir un bloqueo ambiental no acreditado

#### Scenario: Parada durante preflight
- **WHEN** se solicita parar durante verificación previa sin procesos activos
- **THEN** se sale en el siguiente punto seguro sin despachar, sin anunciar render completado y sin iniciar nuevas publicaciones del plan

#### Scenario: Persistencia fallida durante parada
- **WHEN** se agotan reemplazos o falla la publicación de un resultado drenado
- **THEN** el error de almacenamiento permanece visible con UUID y ruta conocidos, se drenan los demás activos y no se anuncia una parada completamente persistida ni recuperación garantizada

#### Scenario: Proceso fallido pero correctamente resuelto
- **WHEN** un proceso termina con código no cero durante una parada y su fallo queda duradero, con siguiente intento programado o resultado terminal
- **THEN** el resumen conserva el fallo y el código 130 identifica la solicitud del usuario, sin convertir el proceso en éxito ni ocultar un circuito abierto

## MODIFIED Requirements

### Requirement: Progreso sin autoridad funcional
Activar o desactivar progreso SHALL preservar generación, aceptación, reanudación, políticas de reintento, elegibilidad Clarity, artefactos funcionales y códigos de salida. Fallos exclusivos de observación o presentación SHALL NOT sustituir errores funcionales ni provocar reintentos. Las llamadas programáticas existentes SHALL permanecer silenciosas por defecto. Los recursos de presentación SHALL cerrarse al salir o interrumpir.

#### Scenario: Presentación defectuosa
- **WHEN** falla la actualización de consola durante un render correcto o fallido
- **THEN** se desactiva la presentación afectada sin cambiar el resultado funcional ni sustituir su error original

#### Scenario: APIs sin observador
- **WHEN** un consumidor invoca las funciones programáticas existentes sin solicitar progreso
- **THEN** no se abre una interfaz de progreso ni se modifica el valor devuelto

#### Scenario: Interrupción del operador
- **WHEN** el operador interrumpe una ejecución activa
- **THEN** se cierran la presentación y su observación sin declarar completados los pendientes; render-static sigue su política de parada cooperativa independientemente de activar progreso, sin cambiar la política de interrupción de los demás comandos
