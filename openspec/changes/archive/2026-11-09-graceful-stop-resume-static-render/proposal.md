# Proposal

## Why

Un `Ctrl+C` puede salir del coordinador antes de evaluar y cerrar resultados de procesos ya terminados, dejando cadenas `running` que el relanzamiento no sabe continuar. El caso reportado tiene código 1 y terminación observada, pero decisión/commit pendientes: no pertenece a la reparación de aceptación confirmada implementada en el cambio anterior.

## What Changes

- Dar al primer `Ctrl+C` en `render-static` semántica de parada controlada, elegida por el usuario: dejar de despachar, esperar procesos activos, evaluar y persistir sus resultados, y salir sin lanzar reintentos, remuestreos ni Clarity nuevos.
- Aislar los procesos de render de la señal de consola del padre para que la solicitud de parada no los mate incidentalmente, tanto en Windows como en POSIX y con RAVEN/Pyroomacoustics.
- Mantener reintentos o continuación acústica legítima pendientes para el próximo comando normal, conservando presupuesto, semillas, historial y artefactos protegidos.
- Añadir recuperación auditable de resultados observados cuya evaluación/publicación quedó pendiente, separada de `finalize_chain` y de la reconciliación manual de terminación desconocida. Una constancia existente se verificará; su mera presencia no acredita aceptación.
- Resolver publicación de outcome, índice, cadena y eventual resolución de circuito de forma retomable, sin duplicar contabilidad ni inventar el orden histórico de resoluciones ausentes.
- Informar «parada solicitada / esperando N procesos / detenido», devolver código 130 tras una parada limpia y conservar diagnósticos de errores reales, también con `--no-progress`.
- **BREAKING para lectores antiguos de nuevos registros recuperados:** introducir formato explícito para evaluación retomada, rechazable por lectores incompatibles, sin migrar journals/outcomes ya correctos.

## Capabilities

### New Capabilities

Ninguna; el comportamiento pertenece a capacidades existentes.

### Modified Capabilities

- `matlab-process-retries`: parada cooperativa, drenaje de invocaciones y conservación de continuaciones autorizadas sin renovar presupuesto/circuito.
- `static-render-resumption`: recuperación global de intentos con resultado observado y evaluación pendiente; conservación de los vetos reales.
- `static-render-acceptance`: auditoría y lectura segura de la evaluación retomada, sin modificar criterios de aceptación o elegibilidad Clarity.
- `pipeline-progress`: estado y contrato de salida de la parada controlada, independiente de la presentación.

## Impact

- Implementación futura solo en `apps/orchestrator`: CLI, ciclo de vida de procesos, coordinación secuencial/paralela, recuperación, lectores, pruebas y README. Sin cambios acústicos, dependencias nuevas ni YAML obligatorio; sin modificar contratos de MATLAB, worker PRA o Clarity.
- Parte de `recover-render-persistence-failures` y `harden-render-state-finalization`, implementados y todavía sin archivar. Mantener sus tareas intactas y sincronizar/archivar las deltas en ese orden antes de este cambio.
- La planificación solo leyó el outcome/cadena y el inventario del intento reportado: `termination_observed=true`, código 1, `decision=pending`, `commit_status=pending`, constancia presente y ninguna resolución de ese UUID en un circuito cerrado. No verificó todavía integridad/acústica de sus salidas, ni reparó el run.
- No incluye cancelación inmediata, recuperación automática de terminación desconocida, retirada de locks ni ejecución de la E2E real durante desarrollo. El trabajo autorizado ahora es solo planificación.
