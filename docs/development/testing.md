# Pruebas y validación

[Índice](../README.md) · [Entorno](setup.md)

Esta auditoría cambia documentación, no comportamiento. Se revisaron tests como evidencia y se controla navegación/diff; no se ejecutan renders, degradaciones reales ni regeneraciones de runs para verificar texto. Los resultados históricos de PRD no se presentan como una nueva ejecución.

## Pruebas Python existentes

Tras instalar los extras dev del workspace desde la raíz:

```sh
uv run --project apps/orchestrator pytest apps/orchestrator/tests
uv run --project apps/orchestrator pytest apps/clarity-backend/tests
uv run --project apps/output-analyzer pytest apps/output-analyzer/tests
```

Clarity usa el runner pytest del entorno del orquestador porque no declara un extra dev propio; tras sincronizar todas las apps el backend está disponible en el entorno compartido. Se indican rutas explícitas para evitar confundir la raíz de descubrimiento de pytest con la app seleccionada.

| Aplicación | Cobertura relevante inspeccionada |
|---|---|
| Orchestrator | Validación, muestreo reproducible, geometría/fixed_position, RT30, runtime audio, layout/índices/resume, Clarity y contratos documentales |
| Clarity | Perfiles/bandas, procesamiento estéreo, paths de salida explícitos y continuación ante errores capturados |
| Analyzer | Lectura/report básico en develop; en HEAD schemas, singletons, identidades, geometría/marcos, tolerancia RT30, exportaciones/carruseles/incidencias |

`apps/orchestrator/tests/test_degraded_output_docs_contract.py` controla marcadores de nombres/path en README, ARCHITECTURE y AGENTS; una consolidación documental debe preservarlos. Los tests del analizador HEAD no requieren MATLAB ni archivos WAV.

## MATLAB

Desde `apps/matlab`, con MATLAB y dependencias locales:

```matlab
runtests('tests')
```

Hay tests de validador, geometría/materiales/poses, audio/ruido, naming, variantes, metadata y T30; varios usan mocks. Un test con mocks no certifica ejecución real RAVEN. El smoke de render usa el entorno y assets reales y crea artefactos, por lo que no forma parte de esta auditoría documental.

## Controles de esta entrega

Verificar que todos los Markdown originales estén clasificados, que las cuatro aplicaciones tengan navegación y que los PRD históricos indiquen CLOSED / IMPLEMENTED. Revisar links locales de todos los documentos modificados y sus destinos, separando links de paths ilustrativos o dependencias locales. Usar `git diff --check`, `git diff --name-only` y `git status --short` para confirmar que esta entrega solo modifica/crea `.md`.

No se certifican QA dinámica de navegador, acústica del audio, determinismo RAVEN, disponibilidad/licencias/toolboxes ni resultados E2E históricos. El registro de controles ejecutados se añade al cierre en la [auditoría](../maintenance/documentation-audit.md).
