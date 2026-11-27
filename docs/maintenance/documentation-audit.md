# Auditoría y reorganización documental

Fecha: 13-10-2026. Alcance: Markdown documental; implementación, pruebas, configuraciones, contratos operativos y datos se inspeccionaron solo en lectura.

## Revisiones y regla de verdad

Base `develop`: `05f9b27ddee7c4a25a98f2aeb41f438fd374980b`. Checkout auditado: `update-fix-output-analyzer`, HEAD `1a3b1c1d63437753924cfb78593327346307b0e8`. `develop` es ancestro de HEAD. Se conserva esta rama derivada y su trabajo; no se hizo checkout, reset ni commit. Al inicio solo existía como cambio local el encargo no rastreado `docs/PRD/update-audit-docs.md`.

La referencia de comportamiento solicitada es el código de `develop`, corroborado por validadores, interfaces, tests y ejemplos en ese orden. También se contrastó HEAD para conservar la documentación del analizador ya implementado en esta rama. Una capacidad exclusiva de HEAD se identifica expresamente: no se atribuye a `develop` ni se convierte en backlog. Las diferencias de rama abarcan `.gitignore`, ejemplo estático, analizador (README, CLI, lector, reporte y tests) y su PRD; estos cambios anteriores no forman parte del diff de esta auditoría.

## Inventario completo

Búsqueda: `rg --files --hidden -g '*.md'` excluyendo `.git` y entornos de dependencias. Se encontraron 17 documentos originales, incluidos los seis PRD históricos identificados, el encargo y un registro de skills oculto. No hay AGENTS raíz ni AGENTS de output-analyzer en este checkout. Ruta final igual a original salvo indicación; no se elimina ni mueve ningún documento.

| Ruta original | Categoría / audiencia | Propósito y vigencia | Fuente técnica consultada | Decisión / ruta final |
|---|---|---|---|---|
| `README.md` | Entrada / usuarios y desarrolladores | Flujo estático vigente; omite analizador, workspace y ejemplo obsoletos | `pyproject.toml`, `uv.lock`, CLI de las tres apps Python | Actualizar / misma ruta |
| `apps/orchestrator/README.md` | Guía local / operadores | CLI y configuración mayormente vigentes | `cli.py`, config loader/validator, scene_builder, pipeline y tests | Actualizar / misma ruta |
| `apps/orchestrator/ARCHITECTURE.md` | Arquitectura / desarrolladores | Nombres, coordenadas runtime y configuración heredados | matlab_runner, output_paths, clarity_handoff, tests render | Consolidar transversal en `docs/architecture/`; conservar guía local |
| `apps/orchestrator/AGENTS.md` | Operación / agentes | Reglas útiles; contexto Clarity histórico | clarity_runner/handoff, CLI, tests contratos docs | Conservar instrucciones / misma ruta; discrepancia registrada |
| `apps/orchestrator/.atl/skill-registry.md` | Registro local / coordinador | Paths absolutos de otro checkout | Existencia de apps y AGENTS actuales | Conservar fuera de cambios operativos / misma ruta; limitación registrada |
| `apps/matlab/README.md` | Guía local / operadores MATLAB | Estático vigente; materiales y media T30 incompletos | validator, build_room, transform_pose, export_metadata, tests | Actualizar / misma ruta |
| `apps/matlab/AGENTS.md` | Operación / agentes | Estático soportado y dinámico placeholder correctamente separados | dynamic/render_dynamic_scene, static/render_static_scene | Conservar / misma ruta |
| `apps/clarity-backend/README.md` | Guía local / operadores | JSONL y MSBG vigentes | cli, msbg, catálogo perfiles y tests | Actualizar navegación y precisiones / misma ruta |
| `apps/clarity-backend/AGENTS.md` | Operación / agentes | Tres etapas de generación coherentes con analizador opcional | cli y msbg | Conservar / misma ruta |
| `apps/output-analyzer/README.md` | Guía local / usuarios | Vigente para HEAD; capacidades difieren de develop | loader, report, cli, tests en ambas revisiones | Actualizar alcance y navegación / misma ruta |
| `docs/PRD/add-fixed_position_policy.md` | PRD histórico / mantenedores | CLOSED / IMPLEMENTED por decisión del responsable; Hoy/Después históricos | config models/validator, sampler, tests geometry | Archivar mediante nota e índice / misma ruta |
| `docs/PRD/add-non-rectangular-rooms.md` | PRD histórico / mantenedores | CLOSED / IMPLEMENTED; schema 2.0 histórico, productor actual 3.0 | geometry, scene_builder, MATLAB validator/build_room, tests | Archivar mediante nota e índice / misma ruta |
| `docs/PRD/add-uniform-rt30-sampling.md` | PRD histórico / mantenedores | CLOSED / IMPLEMENTED; estimación y cuotas diagnosticadas | reverberation_sampling, room_acoustics, treatment_catalog, sampler, tests | Archivar mediante nota e índice / misma ruta |
| `docs/PRD/clean_empty_dir.md` | PRD histórico / mantenedores | CLOSED / IMPLEMENTED; nombres propuestos no contrato actual | output_paths.resolve_artifact_layout, runtime_audio, tests output_paths | Archivar mediante nota e índice / misma ruta |
| `docs/PRD/simplify-render-metadata.md` | PRD histórico / mantenedores | CLOSED / IMPLEMENTED; summary/batch compactos | MATLAB export_render_metadata y tests | Archivar mediante nota e índice / misma ruta |
| `docs/PRD/update-output-analyzer-metadata.md` | PRD histórico / mantenedores | CLOSED / IMPLEMENTED por gestión; texto conserva diagnósticos previos | HEAD/develop analyzer loader/report/CLI/tests | Archivar mediante nota e índice / misma ruta; separar revisiones |
| `docs/PRD/update-audit-docs.md` | Encargo documental / mantenedores | Instrucción original de esta entrega | Inventario y verificaciones aquí registrados | Conservar texto original / misma ruta; cierre en índice al validar |

Fuentes embebidas no Markdown (solo lectura): `configs/experiments/{static_example,speech_fixed_position_e2e}.yml`, `configs/hearing/*.yaml`, `apps/orchestrator/examples/*`, `apps/matlab/example/*`, docstrings de CLI/loader/validators/builders/exporters, fixtures y tests. Los ejemplos ilustran casos; no sustituyen a productores ni validadores. Sus paths a WAV, DAFF/HRTF, materiales y RPF son dependencias del entorno local, no enlaces documentales garantizados. `pyproject.toml` y `uv.lock` corroboran tres miembros Python del workspace: orchestrator, clarity-backend, output-analyzer; MATLAB es una aplicación hermana fuera de uv.

## Arquitectura e interfaces verificadas

| App | Entrada / invocación | Salida / responsabilidad | Evidencia |
|---|---|---|---|
| Orchestrator | YAML; generate-manifests, render-static, clarity-handoff [--submit] | Muestreo, escenas 3.0, runtime render, índices y trabajos Clarity 1.0 | cli, config loader/validator, scene_builder, pipeline |
| MATLAB | Scene JSON; run_raven_static_render | WAV estéreo 44100 Hz y sidecars; RAVEN/ITA | validate_static_scene_config, render_static_scene, export_render_metadata |
| Clarity | JSONL; clarity-backend run-manifest | WAV degradado y sidecar 1.0 a paths explícitos | clarity_backend/cli.py y msbg.py |
| Output Analyzer | Directorio run; output-analyzer analyze [--output-dir] | Análisis posterior opcional de metadatos; HTML, SVG, JSON/JSONL/CSV | output_analyzer/cli.py, loader.py, report.py; diferencias abajo |

## Discrepancias y resoluciones

Certeza alta significa evidencia directa de código/validadores y soporte de tests inspeccionados; no certifica ejecución del runtime externo.

| Documento / afirmación | Evidencia (ruta y símbolo o test) | Resolución | Certeza |
|---|---|---|---|
| README raíz: tres apps y dos miembros uv | root pyproject `[tool.uv.workspace]`, uv.lock `[manifest]` | Cuatro apps; tres Python en uv, MATLAB fuera | Alta |
| README raíz: `configs/experiments/sim_config.yml` | Inventario configs; archivo ausente, speech_fixed_position_e2e presente | Retirar referencia inexistente; enlazar ejemplos presentes | Alta |
| ARCHITECTURE y AGENTS orchestrator: clarity-processor-py futuro | pipeline/clarity_handoff.prepare_clarity_handoff, clarity_runner y CLI | Arquitectura vigente usa clarity-backend implementado opcional; AGENTS preservado con contexto obsoleto registrado | Alta |
| ARCHITECTURE: render estático/dinámico y prioridad futura | config/validator.validate_config; MATLAB dynamic/render_dynamic_scene TODO | Estático soportado; dinámico placeholder no soportado | Alta |
| ARCHITECTURE: runtime refleja poses | matlab_runner.write_single_hrtf_render_manifest; test_render_static_scenes_preserves_public_geometry_poses_and_orientations_in_runtime_manifests | Escena y runtime conservan marco público; MATLAB transforma Z | Alta |
| ARCHITECTURE: outputs.rendered_wav_dir/render_metadata_dir | config/loader._reject_legacy_output_roots; output_paths.resolve_artifact_layout | Documentar artifact_root/run_name y layout metadata/output_audio | Alta |
| ARCHITECTURE: ejemplos normativos | config/models, validator, scene_builder, MATLAB validator | Ejemplos ilustrativos; evidencia principal implementación | Alta |
| README MATLAB: materiales de seis superficies como regla general | validate_static_scene_config.normalize_polygon_room_config/normalize_acoustic_surfaces; build_room_from_config | Legacy 1.0 separado de prisma poligonal 2/3 con wall_ids, floor, ceiling y coeficientes efectivos | Alta |
| README MATLAB: media T30 de valores positivos disponibles | build_room_reverberation_summary.compute_mean_t30 | Media solo con diez bandas finitas positivas; caso contrario unavailable | Alta |
| README Clarity: perfil mínimo de una banda | msbg._normalize_loss_db_by_band; test_build_audiograms_rejects_unsupported_band | Perfil procesable exige exactamente seis bandas por oído, no solo validación estructural del catálogo | Alta |
| README Clarity: continuidad para cualquier fallo | cli.run_manifest/_process_job; tests de errores de perfiles/audio | Recuperación limitada a errores capturados; JSONL roto, campos requeridos o fallos de paths/escritura pueden abortar | Alta |
| README Clarity: backend_invocation preservado y schema del job | cli._job_identity_payload/SCHEMA_VERSION, sin validación schema entrada | Bloque controla upstream, no se copia al resultado; versión resultado 1.0 distinta de validación de entrada | Alta |
| PRD geometría: productor schema 2.0 y analyzer futuro | scene_builder.build_static_manifest; MATLAB validator; analyzer en ambas ramas | Contexto histórico preservado; actual emisión 3.0 y analizador existente | Alta |
| PRD clean_empty_dir: nombres propuestos outputs/audio | output_paths.resolve_artifact_layout; tests/test_output_paths.py | Layout actual metadata, output_audio y cropped_audio bajo demanda | Alta |
| PRD con borrador o siguientes diffs | Decisión explícita del responsable; PRD update analyzer conserva diagnósticos anteriores | Nota CLOSED / IMPLEMENTED sin borrar requisitos ni convertir discrepancias en trabajo de código | Alta para gestión; capacidades solo según revisión |
| Registry oculto: D:/Tesis/Binaural-Impared-Hear absoluto | apps/orchestrator/.atl/skill-registry.md | Dependencia local heredada, no ruta portable; preservar flujo agentes fuera de alcance | Alta |
| Analyzer README develop: HTML autónomo | develop report.py usa imágenes relativas, loader sin control estricto de schema | No atribuir autonomía ni schema estricto a develop; describir alcance HEAD explícito | Alta |

## Diferencias del analizador entre revisiones

| Capacidad | develop 05f9b27 | HEAD 1a3b1c1 |
|---|---|---|
| Uso independiente, solo metadatos | Implementado | Implementado |
| Rechazo explícito de versiones escena/render | No valida schema_version | Exactamente 3.0; faltantes/incompatibles son incidencias; degradados 1.0 |
| Identidades y cobertura | Registros de sidecars; sin normalización ampliada | Escenas/fuentes únicas, variantes y filas fuente-variante separadas |
| HTML | Imágenes SVG relativas; conservar carpeta images | SVG inline, autónomo, carruseles manuales por geometría |
| RT30 | Sin pares por frecuencia y consolidación ampliada | Consolidación por escena/banda, tolerancia propia y tabla por sala |
| Exportaciones | summary, render_scenes JSON, joined_render_sources y degraded_metadata JSONL/CSV | Poblaciones e incidencias ampliadas JSON/JSONL/CSV; parámetros, métricas y cobertura |

La condición de gestión CLOSED / IMPLEMENTED del PRD del analizador no implica que estas ampliaciones estén en el commit local de develop. La guía local describe HEAD y enlaza esta matriz.

## Decisiones editoriales

Conservar rutas existentes. Crear índice docs, arquitectura compartida (overview/data-flow/contracts), guías comunes setup/testing e índice PRD. Mantener detalles específicos en README locales; ARCHITECTURE del orquestador conserva explicación de módulos y remite a contratos compartidos. No mover AGENTS ni cambiar sus reglas. No eliminar historia: insertar notas editoriales a PRD conservando el contenido histórico sin reescribirlo, salvo nota y espacios de separación. Registrar el encargo actual por separado y cerrar su entrega tras validar.

OpenSpec no se inicializa: una futura migración distinguirá guías/arquitectura en docs, requisitos actuales en openspec/specs y cambios en openspec/changes. Esos directorios se mencionan como diseño futuro, sin crearlos.

## Límites de verificación

No se ejecutan simulaciones ni se regeneran runs. MATLAB/RAVEN/ITA, licencias, assets locales, exactitud acústica y QA dinámica de navegador no se certifican. prepare_raven_project usa ruta Windows local; MATLAB rng no aplica seed RAVEN (raven_seed_applied=false). No prometer reproducibilidad bit a bit. Los números de E2E en PRD son evidencia histórica específica, no resultados de esta auditoría. No hay engram disponible; se persiste la trazabilidad en este documento.

## Changelog y validación

Auditoría inicial persistida antes de reorganizar. Se actualizaron 12 Markdown: README raíz; README Clarity, MATLAB, Orchestrator y Analyzer; ARCHITECTURE Orchestrator; y los seis PRD históricos. El encargo original `update-audit-docs.md` permanece sin modificaciones.

Se crearon ocho páginas, sin archivos vacíos:

| Ruta nueva/final | Categoría / audiencia / propósito | Evidencia y decisión |
|---|---|---|
| `docs/README.md` | Índice / todos / navegación | Inventario; crear punto común |
| `docs/architecture/overview.md` | Arquitectura / desarrolladores / cuatro apps y límites | CLI, workspace y módulos; consolidar transversal |
| `docs/architecture/data-flow.md` | Guía de artefactos / operadores / rutas y etapas | output_paths, pipeline, exporters y analyzer; consolidar |
| `docs/architecture/contracts.md` | Referencia / integradores / versiones, coordenadas y restricciones | Productores, validadores, tests y matriz ramas; consolidar |
| `docs/development/setup.md` | Guía / desarrolladores / entorno y dependencias locales | pyproject, lockfile, MATLAB prepare_raven_project; consolidar |
| `docs/development/testing.md` | Guía / desarrolladores / pruebas existentes y límites | Config pytest y suites inspeccionadas; consolidar |
| `docs/PRD/README.md` | Índice histórico / mantenedores / gestión CLOSED / IMPLEMENTED | Decisión responsable y seis históricos; crear |
| `docs/maintenance/documentation-audit.md` | Mantenimiento / revisores / inventario, evidencia y cierre | Revisión de cuatro apps, develop/HEAD y controles; crear |

No se movieron ni eliminaron documentos; la información transversal de ARCHITECTURE se consolidó sin conservar afirmaciones obsoletas como vigentes. Las reglas operativas AGENTS y el registry se preservaron. Notas PRD insertadas tras el título conservan el texto original y su contexto histórico, salvo la nota editorial y espacios de separación.

Controles ejecutados durante reorganización: inventario global de 25 Markdown (17 originales + 8 nuevos), comprobación de 126 enlaces locales sin destinos inexistentes, codificación UTF-8 sin caracteres de reemplazo y `git diff --check` sin errores. Se preservaron los marcadores literales del contrato documental de degradación. La revisión independiente corroboró 126 enlaces relativos y fragmentos sin errores de rutas ni anchors, y ejecutó el test documental existente con `.venv/Scripts/python.exe -B -m pytest -s -p no:cacheprovider apps/orchestrator/tests/test_degraded_output_docs_contract.py -q`: **2 passed**. Se corrigió también la referencia inexistente del README local del orquestador. No se atribuye ejecución a suites runtime.

Salvaguardas: diff de archivos rastreados exclusivamente `.md`; archivos nuevos únicamente `.md`, incluido el encargo no rastreado preexistente. Ninguna modificación a implementación, tests, configs, dependencias, scripts, assets o datos. No se inicializó OpenSpec, no se realizaron simulaciones ni commits.


## Cierre de aceptación

Entrega **CLOSED / IMPLEMENTED** del encargo documental, con validación independiente. Inventario cubre todos los README, ARCHITECTURE, AGENTS y PRD encontrados; navegación enlaza cuatro aplicaciones; contratos y diferencias se respaldan por evidencia; todos los seis PRD anteriores indican cierre histórico. El encargo mantiene su texto original y su estado de origen como referencia de instrucciones, mientras su índice y este cierre registran el resultado.

Revalidación final: cambios rastreados y archivos nuevos exclusivamente Markdown; sin cambios operativos. No hay contradicciones materiales sin resolver que requieran decisión del usuario. Permanecen límites de runtime local, acústica y QA dinámica explícitos arriba. La futura migración OpenSpec y el eventual mantenimiento del contexto AGENTS/registry quedan fuera de esta entrega, sin reabrir features históricas.
