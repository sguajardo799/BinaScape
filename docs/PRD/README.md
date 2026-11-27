# PRD históricos — CLOSED / IMPLEMENTED

[Índice documental](../README.md) · [Auditoría](../maintenance/documentation-audit.md)

Todos los PRD anteriores se consideran **CLOSED / IMPLEMENTED** por decisión explícita del responsable. Es estado de gestión, no garantía de que cada frase histórica describa el código actual ni de que una capacidad esté en todas las ramas. Sus notas editoriales prevalecen sobre estados antiguos de borrador, Hoy/Después o siguientes diffs; el contenido original se conserva.

| PRD | Funcionalidad histórica | Estado | Referencia vigente |
|---|---|---|---|
| [fixed_position](add-fixed_position_policy.md) | Fuentes deterministas respecto del receptor | CLOSED / IMPLEMENTED | [Orchestrator](../../apps/orchestrator/README.md) |
| [Salas no rectangulares](add-non-rectangular-rooms.md) | Shoebox, L y trapecios, huellas y paredes | CLOSED / IMPLEMENTED | [Contratos geométricos](../architecture/contracts.md#geometría-superficies-y-poses) |
| [Muestreo RT30](add-uniform-rt30-sampling.md) | Bins globales y tratamientos virtuales | CLOSED / IMPLEMENTED | [Reverberación](../architecture/contracts.md#reverberación-y-tratamientos) |
| [Directorios de salida](clean_empty_dir.md) | Layout y directorios bajo demanda | CLOSED / IMPLEMENTED | [Flujo y layout](../architecture/data-flow.md) |
| [Metadatos render](simplify-render-metadata.md) | Summary/batch de variantes sin repetición innecesaria | CLOSED / IMPLEMENTED | [MATLAB](../../apps/matlab/README.md) |
| [Output Analyzer](update-output-analyzer-metadata.md) | Contratos actuales, geometría, incidencias, RT30 y presentación | CLOSED / IMPLEMENTED | [Guía HEAD](../../apps/output-analyzer/README.md) y [diferencias develop/HEAD](../maintenance/documentation-audit.md#diferencias-del-analizador-entre-revisiones) |

## Encargo de esta entrega

[Auditoría y reorganización documental](update-audit-docs.md) contiene la instrucción original, preservada sin reescritura. Estado de la entrega: **CLOSED / IMPLEMENTED**, con revisión documental independiente y dos tests de contrato documental aprobados. Su resultado y cierre de aceptación se registran en [documentation-audit](../maintenance/documentation-audit.md). No se abre un nuevo PRD de implementación ni se reabren los históricos.

La documentación técnica vigente está en las guías y arquitectura enlazadas. La migración a OpenSpec queda para otro trabajo; esta entrega no crea sus estructuras.
