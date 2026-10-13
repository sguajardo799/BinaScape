# PRD — Auditoría y reorganización documental de BinaScape

Estado: Propuesto para ejecución  
Creado: 13-10-2026  
Alcance: Exclusivamente documentación Markdown y vínculos documentales. No modificar la implementación, pruebas, configuraciones operativas ni contratos de datos.

## 1. Resumen

BinaScape es un monorepo de generación y análisis de datasets acústicos binaurales. La documentación se encuentra distribuida entre el README raíz, README y documentos técnicos de las aplicaciones, instrucciones `AGENTS.md`, ejemplos de configuración y PRD históricos. Parte de esa información ha quedado desactualizada a medida que el software evolucionó.

Este trabajo debe **auditar el estado documental completo**, contrastar cada afirmación relevante contra la implementación actual de la rama `develop` y **reorganizar/actualizar la documentación**, sin alterar el funcionamiento del repositorio. Su resultado será una base técnica consistente para una migración posterior a OpenSpec. **No se debe instalar, inicializar ni generar OpenSpec en esta etapa.**

**Condiciones explícitas del responsable del proyecto:**

1. Incluir la aplicación `apps/output-analyzer` en el inventario, descripción arquitectónica y navegación documental. No considerarla una funcionalidad futura ni omitirla del flujo de artefactos.
2. **Todos los PRD existentes se consideran cerrados e implementados**, aunque algunos todavía indiquen «borrador». Tratarlos como antecedentes históricos, no como backlog de funcionalidades pendientes. Si una afirmación concreta de un PRD no coincide con el código, describir la discrepancia sin convertirla automáticamente en una tarea de implementación.
3. Ante cualquier contradicción, **el código de la rama `develop` es la fuente de verdad para el comportamiento implementado**. Confirmar con implementaciones, validadores, CLI y pruebas antes de corregir el texto. No cambiar código para adaptarlo a un PRD o README.
4. **Preguntar al usuario solo en casos extremos**: contradicción material que no puede resolverse examinando código, pruebas, esquemas ni ejemplos; o decisión editorial irreversible con alternativas igualmente válidas. En los demás casos, adoptar una decisión conservadora, documentarla y continuar.
5. **No tocar la implementación.** Únicamente se permiten cambios documentales. No modificar archivos fuente, tests, YAML de experimentos, JSON de ejemplo, `pyproject.toml`, dependencias, lockfile, datos ni assets. No hacer refactors ni correcciones de bugs.

## 2. Contexto y fuentes que se deben inspeccionar

El repositorio integra **cuatro aplicaciones**:

| Aplicación | Responsabilidad a documentar y verificar |
| --- | --- |
| `apps/orchestrator` | CLI Python, configuraciones de experimentos, muestreo de escenas, manifiestos y coordinación entre etapas |
| `apps/matlab` | Renderizado acústico con MATLAB/RAVEN, salidas WAV y metadatos; distinguir estático implementado de dinámico no soportado |
| `apps/clarity-backend` | Procesamiento de degradación auditiva con Clarity/MSBG a partir de trabajos JSONL |
| `apps/output-analyzer` | Análisis **solo de metadatos** de un run; informes, exportaciones, cobertura e incidencias; es una aplicación de análisis posterior, no una cuarta etapa obligatoria de renderizado |

Revisar como mínimo: `README.md`; `apps/*/README.md`; `apps/orchestrator/ARCHITECTURE.md`; `apps/*/AGENTS.md` existentes; `docs/PRD/*.md`; CLI, módulos de implementación y tests de cada aplicación; ejemplos y configuraciones **solo en lectura**. Revisar `pyproject.toml` y `uv.lock` **solo en lectura**, para distinguir aplicaciones presentes de miembros reales del workspace de `uv`.

PRD históricos identificados al preparar este encargo:

- `docs/PRD/add-fixed_position_policy.md`
- `docs/PRD/add-non-rectangular-rooms.md`
- `docs/PRD/add-uniform-rt30-sampling.md`
- `docs/PRD/clean_empty_dir.md`
- `docs/PRD/simplify-render-metadata.md`
- `docs/PRD/update-output-analyzer-metadata.md`

Realizar una búsqueda completa para detectar otros documentos similares antes de considerar el inventario terminado. La afirmación de que están todos implementados se toma como **estado de gestión proporcionado por el usuario**; no constituye permiso para inventar o afirmar comportamientos técnicos que el código no permita verificar.

## 3. Problemas documentales conocidos

Estos elementos son puntos de partida para verificar, no una lista exhaustiva ni sustituyen la auditoría:

- El README raíz presenta tres aplicaciones y omite `output-analyzer`.
- `apps/orchestrator/ARCHITECTURE.md` contiene referencias heredadas a `clarity-processor-py` y formulaciones de Clarity como trabajo futuro.
- Varios PRD históricos conservan estados de «borrador» incompatibles con el estado de gestión declarado.
- Hay documentación y ejemplos repartidos entre raíz y aplicaciones; corresponde explicar el rol de cada uno, evitando calificarlos como normativos sin comprobar su uso real.
- Debe distinguirse explícitamente el pipeline de renderizado estático soportado de los entrypoints dinámicos aún no soportados, salvo que la implementación haya cambiado.
- El contrato de manifiestos de escena/render, la generación de trabajos Clarity, la procedencia de coordenadas y los esquemas admitidos por `output-analyzer` requieren descripción consistente basada en código y tests.

## 4. Objetivos

**O1 — Inventario completo.** Enumerar documentos relevantes, ubicación, propósito, solapamientos, referencias rotas, versiones y grado de vigencia.

**O2 — Contraste técnico.** Identificar y resolver contradicciones documentales mediante evidencia verificable en la implementación actual.

**O3 — Arquitectura actual.** Documentar las cuatro aplicaciones, sus límites, interfaces y el flujo de artefactos entre ellas, distinguiendo lo obligatorio de lo opcional.

**O4 — Organización y navegación.** Crear una estructura de documentación simple, coherente y enlazada desde la raíz; reducir duplicaciones sin perder información relevante.

**O5 — Conservación histórica.** Preservar los PRD anteriores como decisiones y requisitos históricos **cerrados/implementados**, diferenciándolos de la documentación técnica vigente.

**O6 — Preparación para OpenSpec.** Dejar clara la separación futura entre arquitectura/guías (`docs/`), requisitos actuales (`openspec/specs/`) y cambios (`openspec/changes/`), pero sin crear dichas estructuras todavía.

## 5. No objetivos

- No implementar OpenSpec ni modificar el flujo de agentes en esta entrega.
- No añadir features, reparar bugs, refactorizar ni optimizar código.
- No modificar tests, crear tests nuevos, actualizar dependencias, mover aplicaciones ni cambiar CI.
- No cambiar la semántica, nombres o versión de contratos YAML/JSON/JSONL ni regenerar artefactos.
- No ejecutar simulaciones que creen artefactos o muten datos del proyecto como parte de la documentación.
- No borrar PRD históricos ni reabrir su ciclo de desarrollo.
- No presentar capacidades planificadas o experimentales como soportadas.

## 6. Requisitos funcionales

### RF-01 — Inventario documental trazable

Recorrer el repositorio y generar un registro en `docs/maintenance/documentation-audit.md` con: ruta original, categoría, audiencia, propósito, estado documental, fuente técnica consultada, decisión (`conservar`, `actualizar`, `consolidar`, `mover`, `archivar`) y ruta final. Incluir archivos Markdown fuera de `docs/`, así como documentación embebida en las aplicaciones que defina contratos o flujos.

**Aceptación:** ningún README, `ARCHITECTURE.md`, `AGENTS.md` ni PRD encontrado queda sin clasificación; las decisiones de eliminación o consolidación tienen justificación.

### RF-02 — Regla de verdad y resolución de contradicciones

Para cada afirmación conflictiva, revisar en orden: (1) código de producción, (2) validadores, schemas e interfaces públicas, (3) tests y fixtures, (4) configuraciones y ejemplos, (5) documentación previa/PRD. Usar el código para describir qué ocurre y los tests como evidencia adicional de su comportamiento esperado; si el comportamiento depende de runtime no accesible, indicar esa limitación.

Registrar las discrepancias relevantes con: documentos en conflicto, evidencia (`ruta`, símbolo o test), resolución adoptada y grado de certeza. No inventar un comportamiento para completar una sección.

**Escalada excepcional:** preguntar únicamente cuando ninguna evidencia disponible permita resolver una contradicción material o sea imprescindible decidir entre alternativas editoriales irreversibles. Antes de preguntar, dejar constancia de lo investigado y plantear una única pregunta concreta. Ante dudas menores, conservar contenido y marcarlo como pendiente de confirmación documental.

### RF-03 — Cobertura de las cuatro aplicaciones

Actualizar la documentación principal para presentar `orchestrator`, `matlab`, `clarity-backend` y `output-analyzer`. Describir entradas, salidas, puntos de invocación, dependencias, límites de responsabilidad y cómo desarrollar/probar cada una, **solo si puede confirmarse en el repositorio**.

Para `output-analyzer`, comprobar especialmente la CLI real, entradas de metadatos, versiones admitidas, generación de informes y formatos exportados. Documentar que puede analizar artefactos de una ejecución sin ser un paso obligatorio del pipeline de generación, siempre que lo confirme la implementación. No asumir que pertenece al workspace `uv` solo porque existe su carpeta.

### RF-04 — Arquitectura y contratos

Documentar las rutas de intercambio: configuración de experimento → manifiestos de escena → render WAV/sidecars → trabajos y salidas Clarity → análisis posterior de metadatos. Separar archivos normativos para la interoperabilidad de artefactos ilustrativos. Incluir rutas de salida, esquemas/versiones realmente usados, restricciones de entrada, tratamiento de errores y convenciones espaciales comprobables.

### RF-05 — Reorganización no destructiva

Proponer y aplicar, cuando sea consistente con los enlaces existentes, esta estructura mínima:

```text
README.md
apps/
  orchestrator/README.md
  orchestrator/ARCHITECTURE.md   # consolidar o convertir en enlace si corresponde
  matlab/README.md
  clarity-backend/README.md
  output-analyzer/README.md
docs/
  README.md                      # índice navegable
  architecture/
    overview.md
    data-flow.md
    contracts.md
  development/
    setup.md
    testing.md
  maintenance/
    documentation-audit.md
  PRD/                           # conservación histórica de todos los PRD
    README.md                     # índice con estado CLOSED / IMPLEMENTED
    *.md
```

La estructura es una **propuesta, no una orden de crear archivos vacíos**. Fusionar páginas cuando la separación produzca duplicación. Mantener instrucciones específicas junto a cada aplicación y trasladar a documentación compartida solo información verdaderamente transversal. Preservar la utilidad de `AGENTS.md`: no moverlos ni reescribir instrucciones operativas fuera de alcance.

### RF-06 — Gestión histórica de PRD

Todos los PRD previos son **cerrados e implementados** según decisión del responsable. Crear un índice `docs/PRD/README.md` que indique ese estado común, nombre/ruta de cada PRD, funcionalidad tratada y referencias a la documentación vigente. Corregir indicadores «borrador» que aparenten trabajo pendiente, preferentemente mediante una nota editorial inequívoca que preserve el contenido histórico original. **No reescribir secciones «Hoy → Después» como si fueran el estado actual**: son contexto histórico. Si el código contradice una afirmación del PRD, anotar la divergencia en la auditoría, no alterar la implementación.

### RF-07 — Navegación y eliminación de contradicciones

Actualizar el README raíz como punto de entrada, enlazar las cuatro aplicaciones y el índice `docs/README.md`, normalizar terminología y corregir referencias a rutas/archivos inexistentes. Evitar repetir descripciones profundas de contratos en múltiples sitios; usar enlaces a una fuente documental principal.

### RF-08 — Cierre y trazabilidad

Entregar un resumen final con archivos Markdown creados/modificados/movidos, decisiones principales, discrepancias resueltas y puntos no verificables. El resumen debe dejar explícito que no hubo cambios de implementación.

## 7. Plan de ejecución

### Fase 1 — Auditoría

1. Registrar la rama y revisión analizadas; trabajar sobre `develop` o una rama documental derivada de ella.
2. Inventariar todas las fuentes y PRD históricos.
3. Inspeccionar CLI, código, validadores y tests de los cuatro proyectos **en modo lectura**.
4. Construir la matriz de arquitectura, artefactos e interfaces.
5. Documentar discrepancias, sus evidencias y la resolución elegida.
6. Clasificar duplicados y recomendar sus destinos.

**Salida obligatoria:** `docs/maintenance/documentation-audit.md` con inventario, discrepancias y decisiones. No iniciar la reorganización sin completar la revisión suficiente de los documentos afectados.

### Fase 2 — Reorganización

1. Crear índice central `docs/README.md`.
2. Actualizar README raíz con las cuatro aplicaciones y enlaces.
3. Consolidar arquitectura, flujo de datos, contratos y guías comunes.
4. Actualizar README locales, eliminando afirmaciones obsoletas y preservando instrucciones específicas.
5. Crear índice histórico de PRD y normalizar la condición de cierre documental.
6. Reparar enlaces relativos e identificar enlaces a archivos que solo existen en entornos locales.
7. Completar la auditoría con rutas finales y un changelog de la migración documental.

**Salida obligatoria:** documentación navegable y coherente, respaldada por evidencia del repositorio.

## 8. Requisitos no funcionales y salvaguardas

- **Cero modificaciones fuera de `.md`**, salvo aprobación expresa posterior en otro trabajo. El conjunto exacto de archivos permitidos se limita a Markdown documental; archivos de implementación o contratos operativos son de solo lectura.
- **No efectuar cambios semánticos** en sistemas, esquemas, scripts, tests, configuración ni estructura de ejecución.
- Minimizar cambios de rutas; si se mueve una página, actualizar todos los enlaces internos.
- No borrar información histórica: archivar, indexar o consolidar con referencias.
- Usar nombres estables, Markdown legible y ejemplos reales verificados.
- Diferenciar «soportado», «opcional», «experimental», «placeholder» y «no implementado».
- Conservar afirmaciones técnicas sobre versiones y requisitos solamente si están corroboradas por código/configuración.
- No crear documentación vacía ni duplicar contenido para cumplir artificialmente una estructura propuesta.

## 9. Validación y criterios de aceptación

- [ ] Se inventariaron todas las fuentes documentales, incluidos los seis PRD identificados y cualquier otro hallado.
- [ ] Las cuatro aplicaciones aparecen correctamente en README raíz y arquitectura: `orchestrator`, `matlab`, `clarity-backend`, `output-analyzer`.
- [ ] `output-analyzer` tiene su función, entradas/salidas y relación con el pipeline correctamente descritas.
- [ ] Los PRD se presentan inequívocamente como **cerrados/implementados**, no como tareas pendientes.
- [ ] Las contradicciones detectadas se resolvieron con evidencia de código; las no verificables están identificadas sin especulación.
- [ ] Se corrigieron nombres/rutas heredados sin afectar las rutas operativas reales.
- [ ] `docs/README.md` proporciona navegación funcional y no hay enlaces relativos rotos dentro del material modificado.
- [ ] El flujo de datos y los contratos documentados coinciden con el código y los tests revisados.
- [ ] Las capacidades experimentales o no implementadas están distinguidas de las soportadas.
- [ ] El `git diff --name-only` contiene **solo documentos Markdown**; no hay cambios en implementaciones, tests, configs, dependencias, scripts ni assets.
- [ ] No se inicializó OpenSpec ni se abrieron nuevos PRD de implementación.
- [ ] La entrega final incluye inventario, resumen de decisiones, discrepancias y limitaciones de verificación.

## 10. Instrucciones directas para el agente ejecutor

> Primero inspecciona la rama `develop` completa y audita las cuatro aplicaciones, incluyendo `apps/output-analyzer`. Considera **todos los PRD existentes cerrados e implementados**, aunque sus encabezados históricos digan «borrador». Para toda contradicción, verifica la implementación actual —código como fuente de verdad, validadores y tests como soporte— y corrige solamente la documentación. Pregunta al usuario únicamente si surge un bloqueo material irresoluble tras investigar; decide de forma conservadora en los demás casos. **Prohibido modificar código, tests, configuraciones, esquemas o cualquier parte de la implementación.** No inicialices OpenSpec todavía. Entrega una documentación coherente y navegable, un inventario con evidencias y un diff exclusivamente documental.

## 11. Referencias del repositorio

- [README principal](https://github.com/sguajardo799/BinaScape/blob/develop/README.md)
- [Arquitectura del orquestador](https://github.com/sguajardo799/BinaScape/blob/develop/apps/orchestrator/ARCHITECTURE.md)
- [Output Analyzer](https://github.com/sguajardo799/BinaScape/tree/develop/apps/output-analyzer)
- [PRD históricos](https://github.com/sguajardo799/BinaScape/tree/develop/docs/PRD)
