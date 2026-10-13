# Documentación de BinaScape

Entrada técnica del monorepo: cuatro aplicaciones, generación estática y análisis posterior opcional. La referencia auditada es `develop@05f9b27`; se conservan las capacidades adicionales verificadas del checkout `update-fix-output-analyzer@1a3b1c1`, identificadas en la [auditoría](maintenance/documentation-audit.md).

| Necesidad | Documento |
|---|---|
| Empezar y localizar aplicaciones | [README raíz](../README.md) |
| Comprender límites y responsabilidades | [Arquitectura](architecture/overview.md) |
| Seguir archivos entre componentes | [Flujo de artefactos](architecture/data-flow.md) |
| Consultar versiones, coordenadas y validación | [Contratos](architecture/contracts.md) |
| Preparar entorno | [Instalación y entorno](development/setup.md) |
| Ejecutar pruebas existentes | [Validación y pruebas](development/testing.md) |
| Revisar evidencias y cambios documentales | [Auditoría](maintenance/documentation-audit.md) |
| Consultar decisiones históricas cerradas | [Índice PRD](PRD/README.md) |

## Guías locales

- [Orchestrator](../apps/orchestrator/README.md) y [arquitectura de módulos](../apps/orchestrator/ARCHITECTURE.md).
- [MATLAB/RAVEN](../apps/matlab/README.md).
- [Clarity/MSBG](../apps/clarity-backend/README.md).
- [Output Analyzer](../apps/output-analyzer/README.md).

Las instrucciones para agentes permanecen junto a sus aplicaciones: [Orchestrator](../apps/orchestrator/AGENTS.md), [MATLAB](../apps/matlab/AGENTS.md) y [Clarity](../apps/clarity-backend/AGENTS.md). No reemplazan a contratos técnicos; el contexto histórico señalado en la auditoría se resuelve con código y documentación vigente.

## Vigencia y futura migración

Los ejemplos ilustran entradas, pero productores y validadores determinan el comportamiento implementado. Los PRD conservan sus textos originales con una nota editorial de cierre; no se interpretan como backlog. Los resultados E2E históricos no son garantías universales.

Esta entrega no inicializa OpenSpec. Para una migración posterior, `docs/` seguirá albergando arquitectura y guías; `openspec/specs/` correspondería a requisitos actuales y `openspec/changes/` a cambios. Es una separación futura, no directorios creados ni enlaces a archivos existentes.
